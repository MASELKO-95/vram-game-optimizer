from __future__ import annotations

from pathlib import Path
from typing import Callable
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time

from .config import ALLOWED_SERVICES, APPS, ENTRY, Profile, profile_from_dict
from .desktop import (compositor_snapshot, detect_desktop,
                      persistent_compositor_action, restore_compositor, set_compositor)
from .state import BusyError, Store, atomic_json, boot_id, desktop_key
from .system import (Result, System, app_processes, environment, identity, own_processes,
                     process_alive, read_process, session_type)
from .steam import SteamGame, game_processes, matches_game, native_steam_running


TOKEN_VARIABLE = "GAME_OPTIMIZER_SESSION"


def log(message: str, directory: Path | None = None) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except (BrokenPipeError, OSError):
        pass
    if directory:
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (directory / "session.log").open("a") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def send_signal(target: dict, signum: int) -> None:
    """Avoid signalling a different process after PID reuse."""
    fd = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            fd = os.pidfd_open(target["pid"])
            if process_alive(target):
                signal.pidfd_send_signal(fd, signum)
        elif process_alive(target):
            os.kill(target["pid"], signum)
    except ProcessLookupError:
        pass
    finally:
        if fd is not None:
            os.close(fd)


def members(data: dict) -> list:
    token = data["token"]
    owner = data.get("owner", {}).get("pid")
    child = data.get("child", {})
    group = data.get("pgrp")
    cgroup = data.get("cgroup", "")
    known = {(item["pid"], item["start"]) for item in data.get("steam_processes", [])}
    found = []
    for proc in own_processes().values():
        if proc.pid in (os.getpid(), owner):
            continue
        in_group = bool(group and proc.pgrp == group and proc.start >= child.get("start", 0))
        in_cgroup = False
        if cgroup and cgroup != "/":
            try:
                raw = Path(f"/proc/{proc.pid}/cgroup").read_text()
                in_cgroup = any(line.split(":", 2)[-1] == cgroup or
                                line.split(":", 2)[-1].startswith(cgroup + "/")
                                for line in raw.splitlines())
            except OSError:
                pass
        env = environment(proc.pid)
        in_steam = bool(data.get("steam_appid") and matches_game(proc, data["steam_appid"], env))
        if (in_group or in_cgroup or env.get(TOKEN_VARIABLE) == token or in_steam
                or (proc.pid, proc.start) in known):
            found.append(proc)
    return found


def desktop_matches(data: dict) -> bool:
    before, current = data.get("login_session", ""), os.environ.get("XDG_SESSION_ID", "")
    return (data.get("boot_id") == boot_id() and data.get("desktop_key") == desktop_key()
            and not (before and current and before != current))


def clean_desktop_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key in (TOKEN_VARIABLE, "SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID",
                   "LD_PRELOAD", "LD_LIBRARY_PATH") or key.startswith("STEAM_COMPAT_"):
            env.pop(key, None)
    return env


def restore_state(store: Store, data: dict, system: System,
                  emit: Callable[[str], None]) -> bool:
    """Undo only journalled changes, retaining failures for another attempt."""
    if not desktop_matches(data):
        # Xfwm4/Marco change stored preferences. Undo these even after reboot,
        # without starting services or toggling an unrelated compositor.
        for action in data.get("actions", []):
            if persistent_compositor_action(action) and not action.get("restored"):
                try:
                    result = restore_compositor(system, action)
                except (ValueError, OSError) as exc:
                    result = Result(1, "", str(exc))
                if not result.ok:
                    data["phase"] = "restore-failed"
                    store.save(data)
                    emit(f"Nie udało się odzyskać poprzednich ustawień kompozytora: {result.stderr}")
                    return False
                action["restored"] = True
                store.save(data)
        emit("Dziennik pochodzi z innej sesji pulpitu lub poprzedniego uruchomienia systemu. Archiwizuję go.")
        atomic_json(store.directory / "previous-session.json", data)
        store.clear()
        return True
    data["phase"] = "restoring"
    store.save(data)
    # Restore inherited niceness only when it still equals our requested value.
    ready = store.directory / f"payload-{data['token']}.json"
    try:
        payload = json.loads(ready.read_text())
    except (OSError, ValueError):
        payload = {}
    nice_failed = False
    if payload.get("nice_changed"):
        for proc in members(data):
            for task in Path(f"/proc/{proc.pid}/task").glob("[0-9]*"):
                try:
                    if process_alive({"pid": proc.pid, "start": proc.start}) and os.getpriority(os.PRIO_PROCESS, int(task.name)) == payload["nice_after"]:
                        os.setpriority(os.PRIO_PROCESS, int(task.name), payload["nice_before"])
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    emit(f"Nie udało się przywrócić nice PID {proc.pid}: {exc}")
                    nice_failed = True
    failures = []
    for action in reversed(data.get("actions", [])):
        if action.get("restored"):
            continue
        success = False
        try:
            if action["kind"] == "service":
                unit = action["unit"]
                if unit not in ALLOWED_SERVICES:
                    raise ValueError("Usługa spoza listy obsługiwanych elementów pulpitu.")
                if system.service_state(unit) == "active":
                    success = True
                else:
                    result = system.start_service(unit)
                    success = result.ok and system.service_state(unit) == "active"
                    if not success:
                        emit(f"Przywracanie {unit}: {result.stderr or 'usługa nie jest aktywna'}")
            elif action["kind"] == "compositor":
                result = restore_compositor(system, action)
                success = result.ok
                if not success:
                    emit(f"Przywracanie kompozytora: {result.stderr}")
            elif action["kind"] == "app":
                app_id = action["app"]
                app = APPS[app_id]
                if app_processes(app_id):
                    success = True
                else:
                    command = shutil.which(app.launch[0])
                    if command:
                        # The user manager provides the desktop environment,
                        # independent of Steam's runtime library paths.
                        if system.scope_available():
                            result = system.run(["systemd-run", "--user", "--collect", "--quiet",
                                                 "--unit", f"game-optimizer-restore-{app_id}-{secrets.token_hex(4)}",
                                                 "--", command, *app.launch[1:]])
                            success = result.ok
                            if not success:
                                emit(f"Uruchamianie {app.label}: {result.stderr}")
                        else:
                            subprocess.Popen([command, *app.launch[1:]], start_new_session=True,
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, env=clean_desktop_environment())
                            success = True
                    else:
                        emit(f"Brak polecenia do ponownego uruchomienia {app.label}.")
            elif action["kind"] == "scope":
                unit = action["unit"]
                if unit != f"game-optimizer-{data['token']}.scope":
                    raise ValueError("Nieprawidłowa nazwa zakresu gry.")
                active = system.service_state(unit)
                if active in ("inactive", "failed", "unknown"):
                    # A missing unit after its last process exits needs no undo.
                    success = active != "unknown" or not members(data)
                else:
                    result = system.run(["systemctl", "--user", "set-property", "--runtime", unit,
                                         "CPUWeight=100", "IOWeight=100", "MemoryLow=0"])
                    success = result.ok
                    if not success:
                        emit(f"Przywracanie wag zasobów: {result.stderr}")
            elif action["kind"] == "nice":
                target = action["target"]
                if not process_alive(target):
                    success = True
                else:
                    current = os.getpriority(os.PRIO_PROCESS, target["pid"])
                    if current == action["after"]:
                        os.setpriority(os.PRIO_PROCESS, target["pid"], action["before"])
                    success = True
            elif action["kind"] == "gamemode":
                target = action["target"]
                if not process_alive(target):
                    success = True
                else:
                    result = system.gamemode_for("UnregisterGameByPID", target["pid"])
                    success = result.ok
                    if not success:
                        emit(f"Przywracanie GameMode PID {target['pid']}: {result.stderr}")
            else:
                raise ValueError("Nieznana operacja w dzienniku.")
        except (OSError, ValueError, KeyError) as exc:
            emit(f"Błąd przywracania: {exc}")
        if success:
            action["restored"] = True
            store.save(data)
        else:
            failures.append(action)
    if failures or nice_failed:
        data["phase"] = "restore-failed"
        store.save(data)
        emit("Część zmian wymaga ponownej próby: game-optimizer restore. Dziennik został zachowany.")
        return False
    report = {"version": 1, "profile": data["profile"], "started": data["started"],
              "desktop": data.get("desktop"),
              "finished": time.time(), "before": data.get("before"), "prepared": data.get("prepared"),
              "after": system.snapshot(), "exit_code": data.get("exit_code"),
              "actions": data.get("actions", [])}
    atomic_json(store.directory / "last-report.json", report)
    store.clear()
    for prefix, suffix in (("release-", ""), ("guardian-", ".json"), ("payload-", ".json")):
        (store.directory / f"{prefix}{data['token']}{suffix}").unlink(missing_ok=True)
    emit("Przywrócono stan pulpitu sprzed gry.")
    return True


class Session:
    def __init__(self, profile: Profile, system: System | None = None,
                 store: Store | None = None, emit: Callable[[str], None] | None = None):
        self.profile = profile.validate()
        self.system = system or System()
        self.store = store or Store()
        self.emit = emit or (lambda message: log(message, self.store.directory))
        self.data = {}
        self.signal_received = None
        self.child = None
        self.nice_unavailable = False
        self.gamemode_unavailable = False

    def journal(self, action: dict) -> None:
        self.data["actions"].append(action)
        self.store.save(self.data)

    def prepare(self) -> None:
        profile = self.profile
        desktop = detect_desktop(session_type())
        self.emit(f"Pulpit: {desktop.label} · {desktop.session.upper()}. {desktop.summary}")
        if profile.compositor:
            if desktop.compositor_backend:
                action = compositor_snapshot(self.system, desktop.compositor_backend)
                if action and action["before"]:
                    self.journal(action)
                    result = set_compositor(self.system, desktop.compositor_backend, False)
                    self.emit(f"Wyłączono kompozycję {desktop.compositor_backend}." if result.ok else f"Kompozytor: {result.stderr}")
                elif action is None:
                    self.emit("Brak dostępu do kompozytora lub narzędzia pozwalającego zachować jego ustawienia. Pomijam efekty.")
                else:
                    self.emit("Kompozycja była już wyłączona; nie zmienię tego przy przywracaniu.")
            else:
                self.emit("To środowisko zachowuje kompozytor. Stosuję pozostałe opcje profilu.")
        units = ([desktop.shell_service] if profile.plasma and desktop.shell_service else [])
        if profile.free_ram:
            units.extend(desktop.ram_services)
            if not desktop.ram_services:
                self.emit("Brak obsługiwanych usług indeksowania tego pulpitu. RAM można odzyskać przez wybrane aplikacje.")
        for unit in units:
            if self.system.service_state(unit) != "active":
                self.emit(f"Pomijam nieaktywną lub niedostępną usługę {unit}.")
                continue
            self.journal({"kind": "service", "unit": unit})
            result = self.system.stop_service(unit)
            stopped = self.system.service_state(unit) == "inactive"
            self.emit(f"Zatrzymano {unit}." if result.ok and stopped else
                      f"Zatrzymywanie {unit}: {result.stderr or 'brak potwierdzenia zatrzymania'}")
        for app_id in profile.close_apps:
            targets = app_processes(app_id)
            if not targets:
                continue
            # A restart command must be available before closing anything.
            if not shutil.which(APPS[app_id].launch[0]):
                self.emit(f"Pomijam {APPS[app_id].label}: brak polecenia do przywrócenia.")
                continue
            self.journal({"kind": "app", "app": app_id})
            targets = [{"pid": proc.pid, "start": proc.start} for proc in targets]
            for target in targets:
                try:
                    send_signal(target, signal.SIGTERM)
                except PermissionError:
                    self.emit(f"Brak uprawnień do zamknięcia {APPS[app_id].label}.")
            deadline = time.monotonic() + 8
            while any(process_alive(target) for target in targets) and time.monotonic() < deadline:
                if self.signal_received:
                    break
                time.sleep(0.15)
            if any(process_alive(target) for target in targets):
                self.emit(f"{APPS[app_id].label} nadal działa. Nie wymuszam zamknięcia.")
            else:
                self.emit(f"Zamknięto {APPS[app_id].label}; uruchomię ponownie po grze.")

    def start_guardian(self) -> subprocess.Popen:
        token = self.data["token"]
        guardian = subprocess.Popen([sys.executable, str(ENTRY), "__guard", "--directory",
                                     str(self.store.directory), "--token", token],
                                    start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    close_fds=True)
        path = self.store.directory / f"guardian-{token}.json"
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if path.is_file():
                self.emit("Proces nadzorujący gotowy.")
                return guardian
            if guardian.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError("Nie udało się uruchomić procesu nadzorującego. Pulpit nie został zmieniony.")

    def launch(self, command: list[str]) -> None:
        token = self.data["token"]
        ready = self.store.directory / f"payload-{token}.json"
        payload = [sys.executable, str(ENTRY), "__exec", "--ready", str(ready),
                   "--nice", str(self.profile.nice if self.profile.priority else 0), "--", *command]
        env = {**os.environ, TOKEN_VARIABLE: token}
        scoped = self.profile.scope and self.system.scope_available()
        unit = f"game-optimizer-{token}.scope"
        if self.profile.gamemode and shutil.which("gamemoderun"):
            payload = ["gamemoderun", *payload]
            self.emit("Włączam GameMode; jego ustawienia zależą od konfiguracji systemu.")
        elif self.profile.gamemode:
            self.emit("GameMode nie jest zainstalowany. Pozostałe opcje działają niezależnie.")
        wrapped = payload
        if scoped:
            props = []
            if self.profile.priority:
                props = [f"CPUWeight={self.profile.cpu_weight}", f"IOWeight={self.profile.io_weight}"]
            if self.profile.memory_low_mib:
                available = self.system.ram().get("available_mib", 0)
                amount = min(self.profile.memory_low_mib, max(0, int(available - 2048)), int(available * 0.25))
                if amount:
                    props.append(f"MemoryLow={amount}M")
                    self.emit(f"Żądam MemoryLow={amount} MiB. Skuteczność zależy od delegacji kontrolera i ochrony grup nadrzędnych.")
            wrapped = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--slice=app.slice",
                       f"--unit={unit}", "--expand-environment=no"]
            wrapped.extend(f"--property={prop}" for prop in props)
            wrapped.extend(["--", *payload])
            self.journal({"kind": "scope", "unit": unit})
            self.emit(f"Gra otrzyma własny zakres systemd: CPUWeight={self.profile.cpu_weight if self.profile.priority else 100}.")
        else:
            self.emit("Zakres systemd jest wyłączony lub niedostępny. Uruchamiam bez wag cgroup.")
        self.data["phase"] = "starting"
        self.store.save(self.data)
        self.child = subprocess.Popen(wrapped, env=env, start_new_session=True, close_fds=True)
        proc = read_process(self.child.pid)
        child_identity = {"pid": proc.pid, "start": proc.start} if proc else None
        self.data.update(child=child_identity or {}, pgrp=self.child.pid)
        self.store.save(self.data)
        # Only retry when the payload has never run: never launch a game twice.
        deadline = time.monotonic() + 20
        while not ready.is_file() and self.child.poll() is None and time.monotonic() < deadline:
            if self.signal_received:
                break
            time.sleep(0.05)
        if scoped and self.child.poll() is not None and not ready.is_file() and not self.signal_received:
            self.emit("Systemd nie uruchomił procesu gry. Ponawiam raz bez zakresu zasobów.")
            self.data["actions"][-1]["restored"] = True
            self.store.save(self.data)
            self.child = subprocess.Popen(payload, env=env, start_new_session=True, close_fds=True)
            proc = read_process(self.child.pid)
            child_identity = {"pid": proc.pid, "start": proc.start} if proc else None
            self.data.update(child=child_identity or {}, pgrp=self.child.pid)
            scoped = False
        if scoped:
            self.data["cgroup"] = self.system.cgroup_path(unit)
        self.data["phase"] = "running"
        self.store.save(self.data)

    def launch_steam(self, game: SteamGame, launch_timeout: float) -> None:
        # Persist the wait deadline before sending anything to an existing client.
        self.data.update(phase="waiting-steam", steam_deadline=time.time() + launch_timeout)
        self.store.save(self.data)
        self.child = subprocess.Popen(["steam", "-applaunch", game.appid], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True, close_fds=True)
        self.emit(f"Steam uruchamia: {game.name}. Czekam na procesy gry (do {launch_timeout:g} s).")
        self.emit("Ctrl+C przywróci pulpit i zakończy nadzór; uruchomiona gra może działać dalej.")

    def prioritize_steam(self, current: list) -> None:
        known = {(item["pid"], item["start"]) for item in self.data.get("steam_processes", [])}
        new = [proc for proc in current if (proc.pid, proc.start) not in known]
        if not new:
            return
        self.data.setdefault("steam_processes", []).extend({"pid": p.pid, "start": p.start} for p in new)
        self.store.save(self.data)
        pids = [p.pid for p in new if process_alive({"pid": p.pid, "start": p.start})]
        if self.profile.scope and not self.data.get("steam_scope_failed") and pids:
            unit = f"game-optimizer-{self.data['token']}.scope"
            existing = next((a for a in self.data["actions"] if a["kind"] == "scope" and not a.get("restored")), None)
            if existing is None:
                if self.system.scope_available():
                    props = {}
                    if self.profile.priority:
                        props.update(CPUWeight=self.profile.cpu_weight, IOWeight=self.profile.io_weight)
                    if self.profile.memory_low_mib:
                        available = self.system.ram().get("available_mib", 0)
                        amount = min(self.profile.memory_low_mib, max(0, int(available - 2048)), int(available * .25))
                        if amount:
                            props["MemoryLow"] = amount * 1048576
                    action = {"kind": "scope", "unit": unit}
                    self.journal(action)
                    result = self.system.create_game_scope(unit, pids, props)
                    if result.ok:
                        self.data["cgroup"] = self.system.cgroup_path(unit)
                        self.emit("Priorytety CPU/I/O przypisano do procesów gry, Steam zachowuje swój priorytet."
                                  if self.profile.priority else "Procesy gry umieszczono w osobnym zakresie systemd.")
                    else:
                        # Keep the intent journalled in case the manager accepted
                        # the call but its reply was lost.
                        self.data["steam_scope_failed"] = True
                        self.emit(f"Zakres gry niedostępny; używam pozostałych ustawień: {result.stderr}")
                else:
                    self.data["steam_scope_failed"] = True
                    self.emit("Zakres systemd niedostępny; używam pozostałych ustawień.")
            else:
                result = self.system.attach_game_scope(unit, pids)
                if not result.ok:
                    self.emit("Nie udało się dołączyć części nowych procesów do zakresu gry.")
            self.store.save(self.data)
        for proc in new:
            target = {"pid": proc.pid, "start": proc.start}
            if not process_alive(target):
                continue
            if self.profile.priority and not self.nice_unavailable:
                try:
                    before = os.getpriority(os.PRIO_PROCESS, proc.pid)
                    if before > self.profile.nice:
                        action = {"kind": "nice", "target": target, "before": before, "after": self.profile.nice}
                        self.journal(action)
                        try:
                            os.setpriority(os.PRIO_PROCESS, proc.pid, self.profile.nice)
                        except PermissionError:
                            action["restored"] = True
                            self.store.save(self.data)
                            self.nice_unavailable = True
                            self.emit("Nice bez zmian: brak uprawnień. Pozostałe ustawienia działają niezależnie.")
                except ProcessLookupError:
                    continue
            if self.profile.gamemode and not self.gamemode_unavailable:
                if not (shutil.which("gamemoderun") or shutil.which("gamemoded")):
                    self.gamemode_unavailable = True
                    self.emit("GameMode nie jest zainstalowany (opcjonalny).")
                    continue
                status = self.system.gamemode_for("QueryStatusByPID", proc.pid)
                if status.ok and status.stdout in ("i 0", "i 1"):
                    action = {"kind": "gamemode", "target": target}
                    self.journal(action)
                    result = self.system.gamemode_for("RegisterGameByPID", proc.pid)
                    if not result.ok:
                        after = self.system.gamemode_for("QueryStatusByPID", proc.pid)
                        if after.ok and after.stdout in ("i 0", "i 1"):
                            action["restored"] = True
                            self.store.save(self.data)
                        self.gamemode_unavailable = True
                        self.emit(f"GameMode nie potwierdził aktywacji: {result.stderr}")
                elif not status.ok:
                    self.gamemode_unavailable = True
                    self.emit("GameMode jest niedostępny; kontynuuję bez niego.")

    def run_steam(self, game: SteamGame, launch_timeout: float = 180,
                  interval: float = .5, grace: float = 3) -> int:
        if game.flatpak:
            raise ValueError("Obsługiwany jest natywny Steam; ta gra pochodzi ze Steam Flatpak.")
        if not game.appid.isdecimal() or not 1 <= launch_timeout <= 3600:
            raise ValueError("Nieprawidłowy identyfikator gry lub czas oczekiwania.")
        if not native_steam_running():
            raise ValueError("Steam musi być włączony. Uruchom natywny Steam, zaloguj się i ponów wybór gry.")
        if game_processes(game.appid):
            raise ValueError("Ta gra już działa. Zamknij ją przed uruchomieniem nowej sesji optymalizacji.")
        return self.run(["steam", "-applaunch", game.appid], interval, grace,
                        steam_game=game, launch_timeout=launch_timeout)

    def run(self, command: list[str], interval: float = 0.5, grace: float = 3,
            steam_game: SteamGame | None = None, launch_timeout: float = 180) -> int:
        if os.geteuid() == 0:
            raise ValueError("Uruchom program jako zwykły użytkownik, bez sudo.")
        if not command or not shutil.which(command[0]):
            raise ValueError("Nie znaleziono programu gry. Podaj wykonywalny plik lub poprawną komendę.")
        if steam_game is None and Path(command[0]).name in ("steam", "steam-runtime", "steam-native"):
            raise ValueError("Dla Steam użyj menu terminalowego lub polecenia steam APPID.")
        if os.environ.get(TOKEN_VARIABLE):
            return subprocess.call(command)
        # An existing %command% wrapper joins a session started from the menu.
        # It must not contend for its lock or apply a second desktop profile.
        if steam_game is None:
            active = self.store.load()
            if (active and active.get("steam_appid") and process_alive(active.get("owner"))
                    and desktop_matches(active)
                    and any(os.environ.get(key) == active["steam_appid"]
                            for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"))):
                return subprocess.call(command, env={**os.environ, TOKEN_VARIABLE: active["token"]})
        self.store.acquire()
        previous_handlers = {}
        guardian = None
        try:
            previous = self.store.load()
            if previous:
                if desktop_matches(previous) and (process_alive(previous.get("owner")) or members(previous)):
                    raise BusyError("Poprzednia gra nadal działa. Użyj restore, aby przywrócić pulpit wcześniej.")
                if not restore_state(self.store, previous, self.system, self.emit):
                    raise RuntimeError("Najpierw dokończ przywracanie poprzedniej sesji.")
            self.data = {"version": 1, "token": secrets.token_hex(12), "owner": identity(os.getpid()),
                         "boot_id": boot_id(), "desktop_key": desktop_key(), "started": time.time(),
                         "login_session": os.environ.get("XDG_SESSION_ID", ""),
                         "profile": self.profile.to_dict(), "phase": "preparing", "actions": [],
                         "desktop": detect_desktop().to_dict(),
                         "before": self.system.snapshot()}
            if steam_game:
                self.data.update(steam_appid=steam_game.appid, steam_name=steam_game.name, steam_seen=False)
            self.store.save(self.data)
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda number, _frame: setattr(self, "signal_received", number))
            try:
                guardian = self.start_guardian()
                self.prepare()
                if self.signal_received:
                    return 128 + self.signal_received
                # Give the driver a short opportunity to release background allocations.
                time.sleep(0.6)
                self.data["prepared"] = self.system.snapshot()
                self.store.save(self.data)
                self.report_difference()
                if steam_game:
                    self.launch_steam(steam_game, launch_timeout)
                else:
                    self.launch(command)
                quiet_since = None
                forwarded = None
                while True:
                    current = members(self.data)
                    if steam_game and current:
                        if not self.data["steam_seen"]:
                            self.data.update(steam_seen=True, phase="running")
                            self.store.save(self.data)
                            self.emit("Wykryto procesy gry. Profil pozostaje aktywny do jej zamknięcia.")
                        self.prioritize_steam(current)
                    if self.store.release_requested(self.data["token"]):
                        self.emit("Przywracam pulpit na żądanie. Gra może nadal działać.")
                        return 0
                    if self.signal_received and self.signal_received != forwarded:
                        forwarded = self.signal_received
                        if steam_game:
                            self.emit("Kończę nadzór i przywracam pulpit. Gra Steam może nadal działać.")
                            return 128 + forwarded
                        self.emit("Przekazuję sygnał zakończenia do uruchomionej gry.")
                        for proc in current:
                            try:
                                send_signal({"pid": proc.pid, "start": proc.start}, forwarded)
                            except PermissionError:
                                pass
                    if steam_game:
                        request_code = self.child.poll()
                        if not self.data["steam_seen"]:
                            if request_code not in (None, 0):
                                self.emit("Steam nie przyjął żądania uruchomienia gry.")
                                return request_code if request_code > 0 else 128 - request_code
                            if time.time() >= self.data["steam_deadline"]:
                                self.emit("Nie wykryto uruchomionej gry w wyznaczonym czasie. Przywracam pulpit.")
                                return 124
                        ended = self.data["steam_seen"]
                    else:
                        ended = self.child.poll() is not None
                    if ended and not current:
                        quiet_since = quiet_since or time.monotonic()
                        if time.monotonic() - quiet_since >= grace:
                            break
                    else:
                        quiet_since = None
                    time.sleep(interval)
                result = 0 if steam_game else self.child.returncode
                self.data["exit_code"] = result
                self.store.save(self.data)
                self.emit("Gra Steam zakończyła działanie." if steam_game else f"Gra zakończyła działanie (kod {result}).")
                return result if result >= 0 else 128 - result
            finally:
                restored = restore_state(self.store, self.data, self.system, self.emit)
                if not restored:
                    raise RuntimeError("Przywracanie nie zostało w pełni zakończone. Ponów poleceniem restore.")
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            self.store.release()
            if guardian is not None:
                try:
                    guardian.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    def report_difference(self) -> None:
        before, after = self.data["before"], self.data["prepared"]
        previous = {gpu["id"]: gpu for gpu in before.get("gpus", [])}
        for gpu in after.get("gpus", []):
            if gpu["id"] in previous:
                difference = gpu["free_mib"] - previous[gpu["id"]]["free_mib"]
                self.emit(f"VRAM {gpu['name']}: zmiana wolnej pamięci {difference:+.0f} MiB; wolne {gpu['free_mib']:.0f} MiB.")
        if before.get("ram") and after.get("ram"):
            difference = after["ram"]["available_mib"] - before["ram"]["available_mib"]
            self.emit(f"Zmiana dostępnego RAM: {difference:+.0f} MiB. Pomiar obejmuje cały system.")


def execute_payload(command: list[str], ready: Path, nice: int) -> int:
    """Final wrapper within the game's scope. Preserve argv and working directory."""
    before = os.getpriority(os.PRIO_PROCESS, 0)
    payload = {**identity(os.getpid()), "nice_before": before, "nice_after": min(before, nice),
               "nice_changed": before > nice, "error": None}
    # Write intent first, so a supervisor crash cannot lose recovery information.
    atomic_json(ready, payload)
    if before > nice:
        try:
            os.setpriority(os.PRIO_PROCESS, 0, nice)
        except OSError as exc:
            payload.update(nice_changed=False, error=str(exc))
            print("Game Optimizer: nice bez zmian (brak uprawnień). Wagi CPU systemd nadal mogą działać.",
                  file=sys.stderr, flush=True)
    atomic_json(ready, payload)
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as exc:
        print(f"Game Optimizer: nie udało się uruchomić gry: {exc}", file=sys.stderr)
        return 127


def guardian_main(directory: Path, token: str, interval: float = 0.5, grace: float = 3) -> int:
    store = Store(directory)
    data = store.load()
    if not data or data["token"] != token:
        return 0
    atomic_json(directory / f"guardian-{token}.json", identity(os.getpid()))
    # Never inherit the launcher's foreground job-control signals.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    while True:
        time.sleep(interval)
        data = store.load()
        if not data or data["token"] != token or data.get("phase") == "restore-failed":
            return 0
        if process_alive(data.get("owner")):
            continue
        try:
            store.acquire()
            break
        except BusyError:
            continue
    emit = lambda message: log(message, directory)
    try:
        data = store.load()
        if not data or data["token"] != token:
            return 0
        if not desktop_matches(data):
            return 0 if restore_state(store, data, System(), emit) else 1
        emit("Proces uruchamiający zakończył się nieoczekiwanie. Nadzór przejął proces przywracania.")
        data["owner"] = identity(os.getpid())
        store.save(data)
        quiet_since = None
        watcher = Session(profile_from_dict(data["profile"]), System(), store, emit)
        watcher.data = data
        while data.get("phase") in ("starting", "running", "waiting-steam"):
            if store.release_requested(token):
                break
            current = members(data)
            if current:
                quiet_since = None
                if data.get("steam_appid"):
                    data.update(steam_seen=True, phase="running")
                    watcher.prioritize_steam(current)
                    store.save(data)
            elif data.get("phase") == "waiting-steam" and time.time() < data.get("steam_deadline", 0):
                quiet_since = None
            else:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= grace:
                    break
            time.sleep(interval)
        return 0 if restore_state(store, data, System(), emit) else 1
    finally:
        store.release()


def restore(directory: Path | None = None, recover_only: bool = False,
            system: System | None = None, emit: Callable[[str], None] | None = None) -> bool:
    store = Store(directory)
    emit = emit or (lambda message: log(message, store.directory))
    system = system or System()
    # A fresh installation needs no directories or journal writes.
    if not store.path.is_file():
        emit("Brak zmian do przywrócenia.")
        return True
    try:
        store.acquire()
    except BusyError:
        data = store.load()
        if data and not recover_only:
            atomic_json(store.directory / f"release-{data['token']}", {"requested": time.time()})
            emit("Wysłano żądanie przywrócenia do aktywnej sesji.")
            return True
        emit("Trwa aktywna sesja gry; automatyczne odzyskiwanie niczego nie zmienia.")
        return True
    try:
        data = store.load()
        if not data:
            return True
        if recover_only and desktop_matches(data) and members(data):
            emit("Gra z poprzedniej sesji nadal działa. Możesz przywrócić pulpit przyciskiem lub poleceniem restore.")
            return True
        return restore_state(store, data, system, emit)
    finally:
        store.release()


def plan(profile: Profile, system: System | None = None) -> dict:
    system = system or System()
    desktop = detect_desktop(session_type())
    units = ([desktop.shell_service] if profile.plasma and desktop.shell_service else []) + (list(desktop.ram_services) if profile.free_ram else [])
    snapshot = compositor_snapshot(system, desktop.compositor_backend) if profile.compositor and desktop.compositor_backend else None
    return {"profile": profile.to_dict(), "session": session_type(),
            "desktop": desktop.to_dict(), "compositor_will_suspend": bool(snapshot and snapshot["before"]),
            "services": {unit: system.service_state(unit) for unit in units},
            "apps": {app: len(app_processes(app)) for app in profile.close_apps},
            "memory": system.snapshot(), "gamemode_available": bool(shutil.which("gamemoderun")),
            "scope_available": profile.scope and system.scope_available(), "dry_run": True}
