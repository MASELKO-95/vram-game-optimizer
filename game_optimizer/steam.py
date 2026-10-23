from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from .config import ENTRY, PROFILES, Profile
from .system import Process, environment, executable, is_flatpak, own_processes


STEAM_CLIENT_NAMES = frozenset(("steam", "steamwebhelper", "steam-runtime-launcher-service",
                               "steam-runtime-supervisor", "srt-logger"))


def native_steam_running() -> bool:
    return any(executable(pid) == "steam" and not is_flatpak(pid) for pid in own_processes())


def matches_game(proc: Process, appid: str, env: dict[str, str] | None = None) -> bool:
    env = environment(proc.pid) if env is None else env
    if not any(env.get(key) == appid for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID")):
        return False
    return (executable(proc.pid) or proc.name) not in STEAM_CLIENT_NAMES


def game_processes(appid: str) -> list[Process]:
    return [proc for proc in own_processes().values() if matches_game(proc, appid)]


def parse_vdf(raw: str) -> dict:
    """Parse Steam's quoted KeyValues format, including escaped paths."""
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[{}]|//[^\n]*', raw)
    index = 0

    def unquote(value: str) -> str:
        if not value.startswith('"'):
            raise ValueError("Nieprawidłowy klucz VDF.")
        return re.sub(r'\\([\\"])', r'\1', value[1:-1])

    def block(nested: bool = False) -> dict:
        nonlocal index
        result = {}
        while index < len(tokens):
            key = tokens[index]
            index += 1
            if key.startswith("//"):
                continue
            if key == "}":
                if not nested:
                    raise ValueError("Nadmiarowy nawias VDF.")
                return result
            key = unquote(key)
            while index < len(tokens) and tokens[index].startswith("//"):
                index += 1
            if index >= len(tokens):
                raise ValueError("Brak wartości VDF.")
            value = tokens[index]
            index += 1
            result[key] = block(True) if value == "{" else unquote(value)
        if nested:
            raise ValueError("Niedomknięty blok VDF.")
        return result

    return block()


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    directory: Path
    flatpak: bool = False


def installed_games(home: Path | None = None) -> list[SteamGame]:
    home = home or Path.home()
    roots = [(home / ".local/share/Steam", False), (home / ".steam/steam", False),
             (home / ".var/app/com.valvesoftware.Steam/.local/share/Steam", True)]
    libraries = {}
    for root, flatpak in roots:
        if not root.is_dir():
            continue
        libraries[root.resolve()] = flatpak
        try:
            config = parse_vdf((root / "steamapps/libraryfolders.vdf").read_text())
            for value in config.get("libraryfolders", {}).values():
                folder = value.get("path") if isinstance(value, dict) else value
                if isinstance(folder, str) and folder.startswith("/"):
                    libraries[Path(folder).resolve()] = flatpak
        except (OSError, ValueError):
            pass
    games = {}
    for library, flatpak in libraries.items():
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            try:
                app = parse_vdf(manifest.read_text())["AppState"]
                appid, name = app["appid"], app["name"]
                install_name = app["installdir"]
                if not appid.isdecimal() or Path(install_name).name != install_name:
                    continue
                directory = library / "steamapps/common" / install_name
                if not directory.is_dir() or not int(app.get("StateFlags", "0")) & 4:
                    continue
                if appid == "228980" or re.search(r"^(Proton|Steam Linux|Steamworks|Steam Controller)", name, re.I):
                    continue
                games.setdefault(appid, SteamGame(appid, name, directory, flatpak))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return sorted(games.values(), key=lambda game: game.name.casefold())


def profile_arguments(profile: Profile) -> list[str]:
    args = ["--profile", profile.name]
    defaults = PROFILES[profile.name]
    for field, positive, negative in (
        ("compositor", "--compositor", "--no-compositor"),
        ("plasma", "--plasma", "--no-plasma"),
        ("free_ram", "--free-ram", "--no-free-ram"),
        ("gamemode", "--gamemode", "--no-gamemode"),
        ("priority", "--priority", "--no-priority"),
        ("scope", "--scope", "--no-scope"),
    ):
        if getattr(profile, field) != getattr(defaults, field):
            args.append(positive if getattr(profile, field) else negative)
    for field, flag in (("nice", "--nice"), ("cpu_weight", "--cpu-weight"),
                        ("io_weight", "--io-weight"), ("memory_low_mib", "--memory-low-mib")):
        if getattr(profile, field) != getattr(defaults, field):
            args.extend([flag, str(getattr(profile, field))])
    for app in profile.close_apps:
        args.extend(["--close-app", app])
    return args


def launch_options(profile: Profile, entry: Path = ENTRY) -> str:
    return shlex.join([str(entry), "run", *profile_arguments(profile)]) + " -- %command%"
