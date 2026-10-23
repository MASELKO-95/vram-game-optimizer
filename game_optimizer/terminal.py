from __future__ import annotations

from dataclasses import dataclass, replace
import curses
import json
import locale
import sys
import unicodedata

from .config import APPS, PROFILES, PROFILE_LABELS, config_file, profile_from_dict
from .desktop import detect_desktop
from .session import Session, plan, restore
from .state import Store, atomic_json
from .steam import installed_games, native_steam_running


def configure_terminal_locale() -> None:
    # curses only needs character handling. LC_ALL also reads unrelated KDE
    # categories, e.g. LC_MEASUREMENT=en_PL.UTF-8, which may not be generated.
    # Explicit fallbacks also work when LANG or LC_ALL themselves are invalid.
    for name in ("", "C.UTF-8", "C.utf8", "en_US.UTF-8", "pl_PL.UTF-8"):
        try:
            locale.setlocale(locale.LC_CTYPE, name)
        except locale.Error:
            continue
        encoding = locale.nl_langinfo(locale.CODESET).replace("-", "").replace("_", "").lower()
        if encoding == "utf8":
            return
    raise locale.Error("Nie znaleziono dostępnego locale UTF-8 do wyświetlenia menu.")


def clipped(text: str, width: int) -> str:
    result, used = [], 0
    for char in str(text):
        if not char.isprintable():
            char = " "
        size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


@dataclass
class MenuState:
    options: list[tuple[str, str]]
    position: int = 0
    query: str = ""

    def visible(self) -> list[tuple[str, str]]:
        return [item for item in self.options if self.query.casefold() in item[1].casefold()]

    def move(self, offset: int) -> None:
        count = len(self.visible())
        self.position = (self.position + offset) % count if count else 0

    def selected(self) -> str | None:
        choices = self.visible()
        return choices[min(self.position, len(choices) - 1)][0] if choices else None

    def filter(self, query: str) -> None:
        self.query = query
        self.position = 0


class Terminal:
    def __init__(self, screen):
        self.screen = screen
        self.desktop = detect_desktop()
        self.colors = False
        curses.set_escdelay(30)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_GREEN)
            self.colors = True
        self.profile_name = "vram"
        self.close_apps: tuple[str, ...] = ()
        try:
            data = json.loads(config_file().read_text())
            preferences = data.get("terminal", data.get("profile", {}))
            profile = profile_from_dict({"name": preferences.get("name", "vram"),
                                         "close_apps": preferences.get("close_apps", [])})
            self.profile_name, self.close_apps = profile.name, profile.close_apps
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def write(self, row: int, text: str, attr: int = 0, column: int = 2) -> None:
        height, width = self.screen.getmaxyx()
        if not 0 <= row < height or column >= width - 1:
            return
        try:
            self.screen.addstr(row, column, clipped(text, max(0, width - column - 2)), attr)
        except curses.error:
            pass

    def select(self, title: str, options: list[tuple[str, str]], subtitle: str = "",
               searchable: bool = False, initial: str | None = None,
               shortcuts: dict[int, str] | None = None) -> str | None:
        state = MenuState(options)
        if initial:
            state.position = next((i for i, item in enumerate(options) if item[0] == initial), 0)
        while True:
            height, width = self.screen.getmaxyx()
            self.screen.erase()
            if height < 12 or width < 48:
                self.write(0, "Powiększ terminal do co najmniej 48×12.", column=0)
                self.write(2, "Esc — powrót", column=0)
            else:
                accent = curses.color_pair(1) if self.colors else curses.A_BOLD
                self.write(1, f"VRAM GAME OPTIMIZER · {self.desktop.label} · {self.desktop.session.upper()} · Steam", accent | curses.A_BOLD)
                self.write(3, title, curses.A_BOLD)
                self.write(4, subtitle)
                choices = state.visible()
                capacity = max(1, height - 11)
                state.position = min(state.position, max(0, len(choices) - 1))
                first = max(0, state.position - capacity + 1)
                for index, (_key, label) in enumerate(choices[first:first + capacity], start=first):
                    selected = index == state.position
                    attr = (curses.color_pair(2) if self.colors else curses.A_REVERSE) if selected else 0
                    self.write(6 + index - first, ("› " if selected else "  ") + label, attr)
                if not choices:
                    self.write(6, "Brak pasujących gier. Backspace usuwa filtr.")
                if searchable:
                    self.write(height - 4, f"Filtr: {state.query or '(wpisz fragment nazwy lub AppID)'}")
                self.write(height - 3, "↑/↓ wybór · Enter zatwierdź · PgUp/PgDn · Esc powrót")
                if shortcuts:
                    self.write(height - 2, "F2 aplikacje RAM · F5 odśwież · F6 diagnostyka · F8 przywróć")
            self.screen.refresh()
            key = self.screen.get_wch()
            if key == curses.KEY_RESIZE:
                continue
            if key in ("\x1b", "\x03") or (not searchable and key in ("q", "Q")):
                return None
            if height < 12 or width < 48:
                continue
            if shortcuts and key in shortcuts:
                return shortcuts[key]
            if key == curses.KEY_UP:
                state.move(-1)
            elif key == curses.KEY_DOWN:
                state.move(1)
            elif key == curses.KEY_PPAGE:
                state.move(-max(1, height - 11))
            elif key == curses.KEY_NPAGE:
                state.move(max(1, height - 11))
            elif key == curses.KEY_HOME:
                state.position = 0
            elif key == curses.KEY_END:
                state.position = max(0, len(state.visible()) - 1)
            elif key in ("\n", "\r", curses.KEY_ENTER):
                selected = state.selected()
                if selected is not None:
                    return selected
            elif searchable and key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                state.filter(state.query[:-1])
            elif searchable and isinstance(key, str) and key.isprintable():
                state.filter(state.query + key)

    def action(self, callback) -> None:
        # Let the existing session runner handle signals in an ordinary terminal.
        curses.def_prog_mode()
        curses.endwin()
        try:
            try:
                callback()
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"Game Optimizer: {exc}", file=sys.stderr)
            try:
                input("\nEnter — wróć do menu… ")
            except EOFError:
                pass
        finally:
            curses.reset_prog_mode()
            self.screen.keypad(True)
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.screen.clear()
            self.screen.refresh()

    def save_preferences(self) -> None:
        try:
            data = json.loads(config_file().read_text())
        except (OSError, ValueError):
            data = {"version": 1}
        if not isinstance(data, dict):
            data = {"version": 1}
        data["terminal"] = {"name": self.profile_name, "close_apps": list(self.close_apps)}
        atomic_json(config_file(), data)

    def applications(self) -> None:
        initial = None
        while True:
            options = [(key, f"[{'x' if key in self.close_apps else ' '}] {app.label}") for key, app in APPS.items()]
            options.append(("back", "Wróć do wyboru gry"))
            chosen = self.select("Opcjonalne zamykanie aplikacji na czas gry", options,
                                 "Zapisz pracę. Ponowne uruchomienie nie odtworzy niezapisanych danych.", initial=initial)
            if chosen in (None, "back"):
                self.save_preferences()
                return
            selected = set(self.close_apps)
            selected.symmetric_difference_update((chosen,))
            self.close_apps = tuple(key for key in APPS if key in selected)
            initial = chosen

    def game_options(self, game) -> None:
        while True:
            options = [(key, f"Uruchom — {PROFILE_LABELS[key][0]}") for key in ("vram", "vram-ram", "balanced")]
            options.extend((("apps", "Zmień listę aplikacji do zamknięcia"), ("plan", "Pokaż plan zmian"), ("back", "Wróć do listy gier")))
            apps = ", ".join(APPS[key].label for key in self.close_apps) or "żadne"
            chosen = self.select(game.name, options, f"AppID {game.appid} · zamykane aplikacje: {apps}",
                                 initial=self.profile_name)
            if chosen in (None, "back"):
                return
            if chosen == "apps":
                self.applications()
            elif chosen == "plan":
                profile = replace(PROFILES[self.profile_name], close_apps=self.close_apps)
                self.action(lambda: print(json.dumps(plan(profile), ensure_ascii=False, indent=2)))
            else:
                self.profile_name = chosen
                self.save_preferences()
                profile = replace(PROFILES[chosen], close_apps=self.close_apps)
                def launch():
                    result = Session(profile).run_steam(game)
                    if result not in (0, 130):
                        print(f"Sesja zakończona kodem {result}.")
                self.action(launch)
                return

    def run(self) -> int:
        games = None
        while True:
            if not native_steam_running():
                selected = self.select("Steam musi być włączony", [("retry", "Sprawdź ponownie"), ("exit", "Wyjdź")],
                                       "Uruchom natywny Steam i zaloguj się. Program pozostawi klienta aktywnego podczas gry.")
                if selected in (None, "exit"):
                    return 0
                games = None
                continue
            if games is None:
                games = [game for game in installed_games() if not game.flatpak]
            options = [(game.appid, f"{game.name}  ·  {game.appid}") for game in games]
            if not games:
                options = [("refresh", "Nie znaleziono zainstalowanych gier — odśwież biblioteki Steam")]
            chosen = self.select(f"Wybierz grę — {len(games)} zainstalowanych", options, "Steam włączony · biblioteki lokalne i dodatkowe dyski",
                                 searchable=True, shortcuts={curses.KEY_F2: "apps", curses.KEY_F5: "refresh",
                                                             curses.KEY_F6: "diagnose", curses.KEY_F8: "restore"})
            if chosen is None:
                return 0
            if chosen == "refresh":
                games = None
            elif chosen == "apps":
                self.applications()
            elif chosen == "restore":
                self.action(restore)
            elif chosen == "diagnose":
                from .cli import main as cli_main
                self.action(lambda: cli_main(["diagnose"]))
            else:
                game = next(game for game in games if game.appid == chosen)
                self.game_options(game)


def main() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Menu wymaga interaktywnego terminala. Uruchom ./game-optimizer w terminalu.\n"
              "Lista bez menu: ./game-optimizer games; okno: ./game-optimizer gui.", file=sys.stderr)
        return 1
    try:
        configure_terminal_locale()
        if Store().path.is_file() and not restore(recover_only=True):
            return 1
        return curses.wrapper(lambda screen: Terminal(screen).run())
    except KeyboardInterrupt:
        return 0
    except curses.error as exc:
        print(f"Nie można otworzyć menu terminalowego: {exc}", file=sys.stderr)
        return 1
    except locale.Error as exc:
        print(f"Nie można ustawić kodowania terminala: {exc}", file=sys.stderr)
        return 1
