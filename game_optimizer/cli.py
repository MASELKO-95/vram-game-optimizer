from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .config import APPS, PROFILES, Profile
from .session import Session, execute_payload, guardian_main, plan, restore
from .state import BusyError
from .steam import installed_games, launch_options, native_steam_running
from .system import diagnostic, executable, own_processes


def profile_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=PROFILES, default="vram", help="Profil optymalizacji")
    for name, description in (
        ("compositor", "Obsługiwany kompozytor X11: KWin, Xfwm4 lub Marco"),
        ("plasma", "Zatrzymanie panelu i tapety Plasmy"),
        ("free-ram", "Zatrzymanie indeksowania Baloo"),
        ("gamemode", "Użycie GameMode, jeżeli jest zainstalowany"),
        ("priority", "Wagi CPU/I/O i próba ustawienia nice"),
        ("scope", "Osobny zakres systemd dla gry"),
    ):
        parser.add_argument("--" + name, action=argparse.BooleanOptionalAction, default=None, help=description)
    parser.add_argument("--nice", type=int, default=None, help="Nice gry: od -10 do 0, domyślnie -5")
    parser.add_argument("--cpu-weight", type=int, default=None, help="Waga CPU: 1–10000")
    parser.add_argument("--io-weight", type=int, default=None, help="Waga I/O: 1–10000")
    parser.add_argument("--memory-low-mib", type=int, default=None, help="Ochrona RAM w cgroup (MiB); 0 wyłącza")
    parser.add_argument("--close-app", choices=APPS, action="append", default=[],
                        help="Zamknij wybraną aplikację i uruchom ją ponownie po grze; zapisz pracę")


def selected_profile(args: argparse.Namespace) -> Profile:
    profile = PROFILES[args.profile]
    values = {}
    for key in ("compositor", "plasma", "free_ram", "gamemode", "priority", "scope", "nice",
                "cpu_weight", "io_weight", "memory_low_mib"):
        value = getattr(args, key)
        if value is not None:
            values[key] = value
    values["close_apps"] = tuple(dict.fromkeys(args.close_app))
    return replace(profile, **values).validate()


def steam_light(print_only: bool = False) -> int:
    command = ["steam", "-silent", "-cef-disable-gpu"]
    if print_only:
        print(" ".join(command))
        return 0
    if os.geteuid() == 0:
        raise ValueError("Nie uruchamiaj Steam jako root.")
    if any(executable(pid) == "steam" for pid in own_processes()):
        raise ValueError("Steam już działa. Zamknij go z menu Steam → Zakończ, a następnie uruchom tryb oszczędny.")
    if not shutil.which("steam"):
        raise ValueError("Nie znaleziono natywnego Steam. Flatpak wymaga oddzielnej integracji z hostem.")
    subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Uruchomiono Steam z ukrytym głównym oknem i wyłączoną akceleracją GPU interfejsu CEF.")
    print("Klient Steam nadal działa. Te flagi nie tworzą trybu całkowicie bez interfejsu.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["__guard"], ["__exec"]):
        internal = argparse.ArgumentParser(prog="game-optimizer " + argv[0])
        if argv[0] == "__guard":
            internal.add_argument("--directory", type=Path, required=True)
            internal.add_argument("--token", required=True)
            args = internal.parse_args(argv[1:])
            return guardian_main(args.directory, args.token)
        internal.add_argument("--ready", type=Path, required=True)
        internal.add_argument("--nice", type=int, required=True)
        internal.add_argument("command", nargs=argparse.REMAINDER)
        args = internal.parse_args(argv[1:])
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return execute_payload(command, args.ready, args.nice) if command else 127
    parser = argparse.ArgumentParser(prog="game-optimizer", description="Tymczasowa optymalizacja gier dla Linux.")
    parser.add_argument("--version", action="version", version=f"VRAM Game Optimizer {__version__}")
    commands = parser.add_subparsers(dest="action")
    commands.add_parser("menu", aliases=["tui"], help="Menu terminalowe: wybór gry i profilu strzałkami")
    commands.add_parser("gui", help="Otwórz aplikację graficzną")
    games = commands.add_parser("games", help="Wypisz gry zainstalowane w bibliotekach Steam")
    games.add_argument("--json", action="store_true")
    steam = commands.add_parser("steam", help="Uruchom wybraną grę przez działający Steam i nadzoruj jej procesy")
    steam.add_argument("appid", help="AppID z polecenia games")
    steam.add_argument("--launch-timeout", type=float, default=180, help="Maksymalny czas oczekiwania na start gry, w sekundach")
    profile_flags(steam)
    run = commands.add_parser("run", help="Uruchom grę i przywróć pulpit po jej zamknięciu")
    profile_flags(run)
    run.add_argument("--dry-run", action="store_true", help="Pokaż plan bez zmiany pulpitu i bez uruchamiania gry")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- komenda gry [argumenty]")
    options = commands.add_parser("steam-options", help="Wypisz opcje uruchamiania do wklejenia w Steam")
    profile_flags(options)
    diagnose = commands.add_parser("diagnose", help="Odczytaj VRAM, RAM i dostępność funkcji")
    diagnose.add_argument("--json", action="store_true", help="Wypisz pełny raport JSON")
    commands.add_parser("restore", help="Przywróć pulpit, także przed zakończeniem gry")
    commands.add_parser("recover", help="Przywróć osieroconą sesję, jeżeli gra już nie działa")
    light = commands.add_parser("steam-light", help="Uruchom zamknięty Steam z ograniczeniem GPU interfejsu")
    light.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action in (None, "menu", "tui"):
            from .terminal import main as terminal_main
            return terminal_main()
        if args.action == "gui":
            from .gui import main as gui_main
            return gui_main()
        if args.action == "games":
            games = installed_games()
            if args.json:
                print(json.dumps({"steam_running": native_steam_running(), "games": [
                    {"appid": g.appid, "name": g.name, "directory": str(g.directory), "flatpak": g.flatpak}
                    for g in games]}, ensure_ascii=False, indent=2))
            else:
                print("Steam: " + ("włączony" if native_steam_running() else "wyłączony — uruchom go przed grą"))
                for game in games:
                    print(f"{game.appid:>10}  {game.name}" + (" [Flatpak — nieobsługiwany]" if game.flatpak else ""))
                if not games:
                    print("Brak zainstalowanych gier w dostępnych bibliotekach Steam.")
            return 0
        if args.action == "steam":
            game = next((g for g in installed_games() if g.appid == args.appid and not g.flatpak), None)
            if game is None:
                raise ValueError("Nie znaleziono zainstalowanej gry o tym AppID w natywnym Steam. Sprawdź polecenie games.")
            return Session(selected_profile(args)).run_steam(game, launch_timeout=args.launch_timeout)
        if args.action == "run":
            profile = selected_profile(args)
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            if args.dry_run:
                print(json.dumps(plan(profile), ensure_ascii=False, indent=2))
                return 0
            if not command:
                parser.error("Podaj grę po --, np. run -- /ścieżka/do/gry")
            return Session(profile).run(command)
        if args.action == "steam-options":
            print(launch_options(selected_profile(args)))
            return 0
        if args.action == "diagnose":
            data = diagnostic()
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
                return 0
            print(f"Pulpit: {data['desktop']}, sesja: {data['session']}")
            print(data["desktop_support"]["summary"])
            if data["desktop_support"]["compositor_backend"]:
                state = "włączony" if data["compositor"] is True else ("wyłączony" if data["compositor"] is False else "brak potwierdzonego odczytu")
                print(f"Kompozytor {data['desktop_support']['compositor_backend']}: {state}")
            for gpu in data["memory"]["gpus"]:
                print(f"{gpu['name']}: używane {gpu['used_mib']:.0f} / {gpu['total_mib']:.0f} MiB VRAM; wolne {gpu['free_mib']:.0f} MiB")
                for proc in gpu.get("processes", []):
                    memory = f"{proc['used_mib']:.0f} MiB" if proc["used_mib"] is not None else "?"
                    print(f"  PID {proc['pid']}: {memory:>10}  {proc['name']}")
            if not data["memory"]["gpus"]:
                print("Brak odczytu VRAM z nvidia-smi lub DRM. Optymalizacja może nadal działać.")
            ram = data["memory"]["ram"]
            if ram:
                print(f"RAM dostępny: {ram['available_mib'] / 1024:.1f} / {ram['total_mib'] / 1024:.1f} GiB")
            print(f"GameMode: {'dostępny' if data['gamemode'] else 'brak (opcjonalny)'}")
            print(f"Zakres systemd: {'dostępny' if data['systemd_scope'] else 'niedostępny'}")
            return 0
        if args.action in ("restore", "recover"):
            return 0 if restore(recover_only=args.action == "recover") else 1
        if args.action == "steam-light":
            return steam_light(args.print_only)
    except (BusyError, ValueError, RuntimeError, OSError) as exc:
        print(f"Game Optimizer: {exc}", file=sys.stderr)
        return 1
    return 0
