from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import os


ENTRY = Path(__file__).resolve().parent.parent / "game-optimizer"
PLASMA_SERVICE = "plasma-plasmashell.service"
RAM_SERVICES = ("kde-baloo.service", "plasma-baloorunner.service")
ALLOWED_SERVICES = frozenset((PLASMA_SERVICE, *RAM_SERVICES))


@dataclass(frozen=True)
class BackgroundApp:
    label: str
    executables: tuple[str, ...]
    launch: tuple[str, ...]


# An explicit opt-in list. Never terminate arbitrary processes or Steam helpers.
APPS = {
    "discord": BackgroundApp("Discord", ("Discord", "discord"), ("discord",)),
    "spotify": BackgroundApp("Spotify", ("spotify",), ("spotify",)),
    "telegram": BackgroundApp("Telegram", ("telegram-desktop", "Telegram"), ("telegram-desktop",)),
    "firefox": BackgroundApp("Firefox", ("firefox", "firefox-bin"), ("firefox",)),
    "chromium": BackgroundApp("Chromium", ("chromium",), ("chromium",)),
}


@dataclass(frozen=True)
class Profile:
    name: str = "vram"
    compositor: bool = True
    plasma: bool = True
    free_ram: bool = False
    gamemode: bool = True
    priority: bool = True
    scope: bool = True
    nice: int = -5
    cpu_weight: int = 10000
    io_weight: int = 1000
    memory_low_mib: int = 0
    close_apps: tuple[str, ...] = ()

    def validate(self) -> "Profile":
        if self.name not in PROFILES:
            raise ValueError("Nieznany profil.")
        for key in ("compositor", "plasma", "free_ram", "gamemode", "priority", "scope"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"Opcja {key} musi być wartością logiczną.")
        for key in ("nice", "cpu_weight", "io_weight", "memory_low_mib"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"Opcja {key} musi być liczbą całkowitą.")
        if not -10 <= self.nice <= 0:
            raise ValueError("Nice musi mieścić się między -10 a 0.")
        if not 1 <= self.cpu_weight <= 10000 or not 1 <= self.io_weight <= 10000:
            raise ValueError("Wagi CPU i I/O muszą mieścić się między 1 a 10000.")
        if not 0 <= self.memory_low_mib <= 65536:
            raise ValueError("MemoryLow musi mieścić się między 0 a 65536 MiB.")
        if set(self.close_apps) - APPS.keys():
            raise ValueError("Nieznana aplikacja do zamknięcia.")
        return self

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES = {
    "balanced": Profile(name="balanced", plasma=False, cpu_weight=1000),
    "vram": Profile(),
    "vram-ram": Profile(name="vram-ram", free_ram=True, memory_low_mib=2048),
}

PROFILE_LABELS = {
    "balanced": ("Zrównoważony", "Wyłącza obsługiwane efekty X11. Zachowuje panel i tapetę."),
    "vram": ("Maksimum VRAM", "Wyłącza obsługiwane efekty. W KDE zatrzymuje też panel i tapetę."),
    "vram-ram": ("VRAM + RAM", "Dodatkowo zatrzymuje obsługiwane indeksowanie i prosi o ochronę RAM gry."),
}


def profile_from_dict(data: dict) -> Profile:
    name = data.get("name", "vram")
    if name not in PROFILES:
        raise ValueError("Nieznany profil.")
    values = {key: value for key, value in data.items() if key in Profile.__dataclass_fields__}
    if "close_apps" in values:
        values["close_apps"] = tuple(values["close_apps"])
    return replace(PROFILES[name], **values).validate()


def state_directory() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "game-optimizer"


def config_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "game-optimizer/settings.json"
