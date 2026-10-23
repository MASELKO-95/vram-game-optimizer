from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import io
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from .config import APPS, gpu_vendor
from .intel import read_intel_gpus


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class Process:
    pid: int
    start: int
    ppid: int
    pgrp: int
    state: str
    name: str
    rss_mib: float


def read_process(pid: int, root: Path = Path("/proc")) -> Process | None:
    try:
        raw = (root / str(pid) / "stat").read_text()
        end = raw.rfind(")")
        fields = raw[end + 2:].split()
        return Process(pid, int(fields[19]), int(fields[1]), int(fields[2]), fields[0],
                       raw[raw.find("(") + 1:end],
                       int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 1048576)
    except (OSError, ValueError, IndexError):
        return None


def process_alive(identity: dict | None) -> bool:
    if not identity:
        return False
    proc = read_process(identity["pid"])
    return bool(proc and proc.start == identity["start"] and proc.state not in ("Z", "X"))


def identity(pid: int) -> dict:
    proc = read_process(pid)
    if proc is None:
        raise RuntimeError(f"Nie można odczytać procesu {pid}.")
    return {"pid": proc.pid, "start": proc.start}


def own_processes() -> dict[int, Process]:
    found = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        proc = read_process(int(path.name))
        if proc and proc.state not in ("Z", "X"):
            found[proc.pid] = proc
    return found


def environment(pid: int) -> dict[str, str]:
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
        return dict(item.decode(errors="replace").split("=", 1)
                    for item in raw.split(b"\0") if b"=" in item)
    except OSError:
        return {}


def executable(pid: int) -> str:
    try:
        return Path(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")).name
    except OSError:
        return ""


def is_flatpak(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/root/.flatpak-info").is_file()
    except OSError:
        # An application we cannot inspect must not be an automatic close target.
        return True


def app_processes(app_id: str, processes: dict[int, Process] | None = None) -> list[Process]:
    app = APPS[app_id]
    processes = own_processes() if processes is None else processes
    matching = {pid: proc for pid, proc in processes.items()
                if executable(pid) in app.executables and not is_flatpak(pid)}
    # Terminate application roots, letting them shut down their own subprocesses.
    return [proc for proc in matching.values() if proc.ppid not in matching]


def parse_mib(value: str | None) -> float | None:
    match = re.match(r"^\s*([\d.]+)", value or "")
    return float(match.group(1)) if match else None


def parse_nvidia_xml(raw: str) -> list[dict]:
    root = ET.fromstring(raw)
    gpus = []
    for index, gpu in enumerate(root.findall("gpu")):
        memory = gpu.find("fb_memory_usage")
        if memory is None:
            continue
        total, used, free = (parse_mib(memory.findtext(key)) for key in ("total", "used", "free"))
        if total is None or used is None or free is None:
            continue
        processes = []
        for item in gpu.findall("processes/process_info"):
            try:
                pid = int(item.findtext("pid", ""))
            except ValueError:
                continue
            processes.append({"pid": pid, "name": item.findtext("process_name", "?"),
                              "type": item.findtext("type", "?"),
                              "used_mib": parse_mib(item.findtext("used_memory"))})
        gpus.append({"id": gpu.attrib.get("id", str(index)), "vendor": "NVIDIA",
                     "name": gpu.findtext("product_name", "NVIDIA"), "total_mib": total,
                     "used_mib": used, "free_mib": free, "processes": processes})
    return gpus


def read_amd_gpus(drm_root: Path = Path("/sys/class/drm")) -> list[dict]:
    """Read AMDGPU counters in bytes; GTT is reported separately from VRAM."""
    gpus, seen = [], set()
    for card in sorted(drm_root.glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        try:
            if int((device / "vendor").read_text().strip(), 16) != 0x1002:
                continue
            physical = device.resolve(strict=True)
            if (device / "driver").resolve(strict=True).name != "amdgpu" or physical in seen:
                continue
            total = int((device / "mem_info_vram_total").read_text())
            used = int((device / "mem_info_vram_used").read_text())
            if total <= 0 or used < 0:
                continue
        except (OSError, ValueError):
            continue
        try:
            product = (device / "product_name").read_text().strip()
        except OSError:
            product = ""
        gpu = {"id": physical.name, "vendor": "AMD", "backend": "amdgpu-sysfs", "card": card.name,
               "name": product or f"AMD GPU ({physical.name})", "total_mib": total / 1048576,
               "used_mib": used / 1048576, "free_mib": max(0, total - used) / 1048576, "processes": []}
        for source, key in (("mem_info_gtt_total", "gtt_total_mib"), ("mem_info_gtt_used", "gtt_used_mib")):
            try:
                value = int((device / source).read_text())
                if value >= 0:
                    gpu[key] = value / 1048576
            except (OSError, ValueError):
                pass
        seen.add(physical)
        gpus.append(gpu)
    return gpus


def gpu_memory_text(gpu: dict) -> str:
    """Describe partial readings without presenting shared RAM as free VRAM."""
    name = gpu["name"]
    if gpu.get("memory_kind") == "shared":
        return f"{name}: pamięć współdzielona z RAM; brak osobnej puli VRAM."
    if gpu.get("total_mib") is None:
        return f"{name}: {gpu.get('memory_note') or 'Brak odczytu VRAM.'}"
    if gpu.get("used_mib") is None or gpu.get("free_mib") is None:
        return f"{name}: {gpu['total_mib']:.0f} MiB VRAM; {gpu.get('memory_note') or 'brak odczytu zajętości.'}"
    return (f"{name}: używane {gpu['used_mib']:.0f} / {gpu['total_mib']:.0f} MiB VRAM; "
            f"wolne {gpu['free_mib']:.0f} MiB")


class System:
    def run(self, args: list[str], timeout: float = 12) -> Result:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                    env={**os.environ, "LC_ALL": "C"})
            return Result(result.returncode, result.stdout.strip(), result.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Result(127, "", str(exc))

    def service_state(self, unit: str) -> str:
        result = self.run(["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"])
        return result.stdout if result.ok else "unknown"

    def stop_service(self, unit: str) -> Result:
        return self.run(["systemctl", "--user", "stop", unit], timeout=50)

    def start_service(self, unit: str) -> Result:
        return self.run(["systemctl", "--user", "start", unit], timeout=50)

    def window_manager_name(self) -> str | None:
        result = self.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"])
        match = re.search(r"\b0x[0-9a-fA-F]+\b", result.stdout) if result.ok else None
        if not match or int(match.group(), 16) == 0:
            return None
        result = self.run(["xprop", "-id", match.group(), "_NET_WM_NAME"])
        match = re.search(r'=\s*"([^"\n]+)"', result.stdout) if result.ok else None
        return match.group(1) if match else None

    def compositor_active(self) -> bool | None:
        result = self.run(["busctl", "--user", "get-property", "org.kde.KWin", "/Compositor",
                           "org.kde.kwin.Compositing", "active"])
        if result.ok and result.stdout in ("b true", "b false"):
            return result.stdout == "b true"
        return None

    def compositor_possible(self) -> bool | None:
        result = self.run(["busctl", "--user", "get-property", "org.kde.KWin", "/Compositor",
                           "org.kde.kwin.Compositing", "compositingPossible"])
        if result.ok and result.stdout in ("b true", "b false"):
            return result.stdout == "b true"
        return None

    def set_compositor(self, enabled: bool) -> Result:
        current = self.compositor_active()
        if current is None:
            return Result(1, "", "Nieznany stan kompozytora; pomijam zmianę.")
        if current == enabled:
            return Result(0)
        # Plasma 6 has no suspend/resume methods on /Compositor. Use the
        # verified KWin action, and only toggle after reading the current state.
        result = self.run(["busctl", "--user", "call", "org.kde.kglobalaccel", "/component/kwin",
                           "org.kde.kglobalaccel.Component", "invokeShortcut", "s", "Suspend Compositing"])
        if not result.ok:
            return result
        for _ in range(12):
            if self.compositor_active() == enabled:
                return Result(0)
            time.sleep(0.1)
        return Result(1, "", "KWin nie potwierdził zmiany kompozytora.")

    def ram(self) -> dict:
        values = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, rest = line.split(":", 1)
                values[key] = int(rest.split()[0]) / 1024
        except (OSError, ValueError, IndexError):
            return {}
        return {"total_mib": values.get("MemTotal", 0),
                "available_mib": values.get("MemAvailable", 0),
                "swap_total_mib": values.get("SwapTotal", 0),
                "swap_free_mib": values.get("SwapFree", 0)}

    def nvidia_gpus(self, include_processes: bool = False) -> list[dict]:
        if shutil.which("nvidia-smi"):
            if include_processes:
                result = self.run(["nvidia-smi", "-q", "-x"], timeout=5)
                if result.ok:
                    try:
                        gpus = parse_nvidia_xml(result.stdout)
                        if gpus:
                            return gpus
                    except ET.ParseError:
                        pass
            result = self.run(["nvidia-smi", "--query-gpu=pci.bus_id,name,memory.total,memory.used,memory.free",
                               "--format=csv,noheader,nounits"], timeout=5)
            if result.ok:
                gpus = []
                for row in csv.reader(io.StringIO(result.stdout)):
                    if len(row) != 5:
                        continue
                    total, used, free = [parse_mib(value) for value in row[2:]]
                    if any(value is None for value in (total, used, free)):
                        continue
                    gpus.append({"id": row[0].strip(), "vendor": "NVIDIA", "name": row[1].strip(),
                                 "total_mib": total, "used_mib": used, "free_mib": free,
                                 "processes": []})
                if gpus:
                    return gpus
        return []

    def gpus(self, include_processes: bool = False, vendor: str | None = None) -> list[dict]:
        selected = gpu_vendor() if vendor is None else vendor
        if selected not in ("auto", "nvidia", "amd", "intel"):
            raise ValueError("Nieobsługiwany producent GPU.")
        gpus = self.nvidia_gpus(include_processes) if selected in ("auto", "nvidia") else []
        if selected in ("auto", "amd"):
            gpus.extend(read_amd_gpus())
        if selected in ("auto", "intel"):
            gpus.extend(read_intel_gpus())
        return gpus

    def snapshot(self, include_processes: bool = False) -> dict:
        return {"time": time.time(), "gpus": self.gpus(include_processes), "ram": self.ram()}

    def scope_available(self) -> bool:
        return bool(shutil.which("systemd-run") and self.run(
            ["systemctl", "--user", "show", "--property=Version", "--value"]).ok)

    def cgroup_path(self, unit: str) -> str:
        result = self.run(["systemctl", "--user", "show", unit, "--property=ControlGroup", "--value"])
        return result.stdout if result.ok and result.stdout.startswith("/") else ""

    def create_game_scope(self, unit: str, pids: list[int], properties: dict[str, int]) -> Result:
        if not pids:
            return Result(1, "", "Brak procesów gry.")
        fields = ["PIDs", "au", str(len(pids)), *map(str, pids), "Slice", "s", "app.slice",
                  "CollectMode", "s", "inactive-or-failed", "Delegate", "b", "true"]
        for name, value in properties.items():
            fields.extend([name, "t", str(value)])
        return self.run(["busctl", "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                         "org.freedesktop.systemd1.Manager", "StartTransientUnit", "ssa(sv)a(sa(sv))",
                         unit, "fail", str(4 + len(properties)), *fields, "0"])

    def attach_game_scope(self, unit: str, pids: list[int]) -> Result:
        return self.run(["busctl", "--user", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                         "org.freedesktop.systemd1.Manager", "AttachProcessesToUnit", "ssau",
                         unit, "", str(len(pids)), *map(str, pids)])

    def gamemode_for(self, method: str, pid: int) -> Result:
        if method not in ("QueryStatusByPID", "RegisterGameByPID", "UnregisterGameByPID"):
            raise ValueError("Nieobsługiwana metoda GameMode.")
        result = self.run(["busctl", "--user", "call", "com.feralinteractive.GameMode",
                           "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode",
                           method, "ii", str(os.getpid()), str(pid)])
        if result.ok and result.stdout.startswith("i -"):
            return Result(1, result.stdout, "GameMode odrzucił żądanie dla procesu.")
        return result


def session_type() -> str:
    # WAYLAND_DISPLAY wins over a stale XDG_SESSION_TYPE inherited by a terminal.
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("XDG_SESSION_TYPE") == "x11" or os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def diagnostic(system: System | None = None) -> dict:
    from .desktop import compositor_active, detect_desktop
    system = system or System()
    desktop = detect_desktop()
    processes = own_processes()
    backend = desktop.compositor_backend
    manager_matches = backend not in ("xfwm4", "marco") or (system.window_manager_name() or "").casefold() == backend
    return {"session": session_type(), "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "?"),
            "gpu_vendor": gpu_vendor(),
            "memory": system.snapshot(include_processes=True),
            "desktop_support": desktop.to_dict(),
            "compositor": compositor_active(system, backend) if backend and manager_matches else None,
            "gamemode": bool(shutil.which("gamemoderun")),
            "systemd_scope": system.scope_available(),
            "apps": {key: [{"pid": p.pid, "rss_mib": p.rss_mib} for p in app_processes(key, processes)]
                     for key in APPS}}
