from __future__ import annotations

from pathlib import Path
import fcntl
import hashlib
import json
import os
import tempfile

from .config import state_directory


class BusyError(RuntimeError):
    pass


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def desktop_key() -> str:
    # Steam may omit XDG_SESSION_ID or append the X11 screen number.
    # Identify the display consistently across launcher and recovery processes.
    display = os.environ.get("DISPLAY", "")
    if ":" in display:
        host, number = display.rsplit(":", 1)
        display = host + ":" + number.split(".", 1)[0]
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").split(",guid=", 1)[0]
    values = [str(os.getuid()), display, os.environ.get("WAYLAND_DISPLAY", ""), bus]
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or state_directory()
        self.path = self.directory / "session.json"
        self.lock = None

    def acquire(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory / "session.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        self.lock = os.fdopen(fd, "a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.release()
            raise BusyError("Trwa już sesja gry. Druga sesja nie zmieni pulpitu.") from None

    def release(self) -> None:
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Nieobsługiwany format dziennika sesji.")
        return data

    def save(self, data: dict) -> None:
        atomic_json(self.path, data)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def release_requested(self, token: str) -> bool:
        return (self.directory / f"release-{token}").is_file()
