from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import os
import queue
import shlex
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import (APPS, ENTRY, PROFILES, PROFILE_LABELS, Profile, config_file,
                     profile_from_dict, state_directory)
from .desktop import detect_desktop
from .state import Store, atomic_json
from .steam import installed_games, launch_options, profile_arguments
from .system import System, diagnostic


BG = "#10151d"
CARD = "#19222e"
TEXT = "#e8edf4"
MUTED = "#a8b8ca"
ACCENT = "#82e7bd"


class ScrollablePage(ttk.Frame):
    """Keep launch controls accessible on smaller laptop displays."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, padding=(4, 17))
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self.layout)
        self.canvas.bind("<Configure>", self.layout)

    def layout(self, _event=None):
        self.canvas.itemconfigure(self.window, width=max(1, self.canvas.winfo_width()))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        overflow = self.content.winfo_reqheight() > self.canvas.winfo_height()
        if overflow and not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y")
        elif not overflow and self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.system = System()
        self.desktop = detect_desktop()
        self.workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-optimizer")
        self.events: queue.Queue = queue.Queue()
        self.closing = False
        self.refreshing = False
        self.child = None
        self.games = []
        self.profile_name = tk.StringVar(value="vram")
        self.command = tk.StringVar()
        self.status = tk.StringVar(value="Gotowy do gry · pulpit działa normalnie")
        self.message = tk.StringVar(value="")
        self.checks = {key: tk.BooleanVar() for key in ("compositor", "plasma", "free_ram", "gamemode", "priority")}
        self.memory_protection = tk.BooleanVar()
        self.app_checks = {key: tk.BooleanVar(value=False) for key in APPS}
        self.extra_profile = PROFILES["vram"]
        self.read_settings()
        self.configure_style()
        root.title("VRAM Game Optimizer — Linux")
        width = min(1050, root.winfo_screenwidth() - 60)
        height = min(880, root.winfo_screenheight() - 70)
        root.geometry(f"{max(900, width)}x{max(700, height)}")
        root.minsize(900, 700)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build()
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind(sequence, self.scroll_page, add="+")
        self.refresh_options()
        self.submit("games", installed_games)
        self.submit("recover", lambda: self.cli_capture(["recover"]))
        root.after(100, self.poll)
        root.after(150, self.refresh)

    def configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("DejaVu Sans", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=CARD)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Metric.TLabel", background=CARD, font=("DejaVu Sans", 21, "bold"))
        style.configure("Title.TLabel", font=("DejaVu Sans", 23, "bold"))
        style.configure("SubTitle.TLabel", font=("DejaVu Sans", 12, "bold"))
        style.configure("TButton", padding=(13, 9), background="#29374a", foreground=TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#3a4c64")], foreground=[("disabled", "#667589")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#10261c", font=("DejaVu Sans", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#a0f3d1"), ("disabled", "#395b50")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, padding=(0, 5))
        style.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT), ("!selected", "#314154")])
        style.configure("TRadiobutton", background=CARD, foreground=TEXT, padding=5)
        style.map("TRadiobutton", background=[("active", CARD)], indicatorcolor=[("selected", ACCENT)])
        style.configure("TEntry", fieldbackground="#202c3a", foreground=TEXT, padding=8, insertcolor=TEXT,
                        bordercolor="#3a4c64", lightcolor="#3a4c64", darkcolor="#3a4c64")
        style.configure("TCombobox", fieldbackground="#202c3a", foreground=TEXT, padding=8, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", "#202c3a")], foreground=[("readonly", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0, bordercolor="#2b3747", lightcolor="#2b3747", darkcolor="#2b3747")
        style.configure("TNotebook.Tab", padding=(20, 10), background=CARD, foreground=MUTED,
                        bordercolor="#2b3747", lightcolor="#2b3747", darkcolor="#2b3747")
        style.map("TNotebook.Tab", background=[("selected", "#29374a")], foreground=[("selected", ACCENT)])
        style.configure("Memory.Horizontal.TProgressbar", troughcolor="#2b3747", background=ACCENT, borderwidth=0)
        style.configure("TLabelframe", background=BG, bordercolor="#2b3747")
        style.configure("TLabelframe.Label", background=BG, foreground=MUTED)

    def read_settings(self):
        try:
            data = json.loads(config_file().read_text())
            profile = profile_from_dict(data.get("profile", {}))
            self.command.set(data.get("command", ""))
        except (OSError, ValueError, TypeError):
            profile = PROFILES["vram"]
        self.set_profile(profile)

    def set_profile(self, profile: Profile):
        self.extra_profile = profile
        self.profile_name.set(profile.name)
        for key, var in self.checks.items():
            available = ((key != "compositor" or bool(self.desktop.compositor_backend))
                         and (key != "plasma" or bool(self.desktop.shell_service))
                         and (key != "free_ram" or bool(self.desktop.ram_services)))
            var.set(getattr(profile, key) and available)
        self.memory_protection.set(profile.memory_low_mib > 0)
        for key, var in self.app_checks.items():
            var.set(key in profile.close_apps)

    def current_profile(self) -> Profile:
        values = {key: var.get() for key, var in self.checks.items()}
        values["memory_low_mib"] = (self.extra_profile.memory_low_mib or 2048) if self.memory_protection.get() else 0
        return replace(self.extra_profile, name=self.profile_name.get(), **values,
                       close_apps=tuple(key for key, var in self.app_checks.items() if var.get())).validate()

    def choose_profile(self):
        # Explicit application selections persist across profile changes.
        chosen = tuple(key for key, var in self.app_checks.items() if var.get())
        self.set_profile(replace(PROFILES[self.profile_name.get()], close_apps=chosen))
        self.refresh_options()

    def build(self):
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="VRAM Game Optimizer", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"{self.desktop.label} · {self.desktop.session.upper()}", foreground=ACCENT).pack(side="right", pady=10)
        ttk.Label(outer, text="Zwolnij pamięć tła na czas gry. Po grze przywróć pulpit.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 20))
        metrics = ttk.Frame(outer)
        metrics.pack(fill="x", pady=(0, 20))
        self.metric_vars = {}
        self.bars = {}
        for index, (key, title, initial) in enumerate((
            ("vram", "VRAM · KARTA GRAFICZNA", "Odczyt…"),
            ("ram", "RAM · DOSTĘPNY DLA PROGRAMÓW", "Odczyt…"),
            ("session", "SESJA GRY", "Pulpit"),
        )):
            card = ttk.Frame(metrics, style="Card.TFrame", padding=16)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 12 if index < 2 else 0))
            metrics.columnconfigure(index, weight=1, uniform="metrics")
            ttk.Label(card, text=title, style="CardMuted.TLabel", font=("DejaVu Sans", 8, "bold")).pack(anchor="w")
            main, sub = tk.StringVar(value=initial), tk.StringVar(value=" ")
            self.metric_vars[key] = (main, sub)
            ttk.Label(card, textvariable=main, style="Metric.TLabel").pack(anchor="w", pady=(9, 3))
            subtitle = ttk.Label(card, textvariable=sub, style="CardMuted.TLabel", wraplength=280)
            subtitle.pack(anchor="w")
            card.bind("<Configure>", lambda event, label=subtitle: label.configure(wraplength=max(120, event.width - 32)))
            if key != "session":
                bar = ttk.Progressbar(card, style="Memory.Horizontal.TProgressbar", maximum=100)
                bar.pack(fill="x", pady=(11, 0))
                self.bars[key] = bar
        notebook = ttk.Notebook(outer)
        self.notebook = notebook
        notebook.pack(fill="both", expand=True)
        self.pages = [ScrollablePage(notebook) for _ in range(3)]
        self.play_tab, self.steam_tab, self.ram_tab = [page.content for page in self.pages]
        self.log_tab = ttk.Frame(notebook, padding=(4, 17))
        for frame, label in [*zip(self.pages, ("Graj", "Steam", "Zwalnianie RAM")),
                             (self.log_tab, "Pomiary i dziennik")]:
            notebook.add(frame, text=label)
        self.build_play()
        self.build_steam()
        self.build_ram()
        self.build_log()
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, textvariable=self.status, foreground=ACCENT, wraplength=620).pack(side="left")
        ttk.Button(footer, text="Przywróć pulpit teraz", command=self.restore_now).pack(side="right")
        ttk.Label(outer, textvariable=self.message, style="Muted.TLabel", wraplength=960).pack(anchor="w", pady=(7, 0))

    def build_play(self):
        tab = self.play_tab
        cards = ttk.Frame(tab)
        cards.pack(fill="x", pady=(0, 12))
        for column, (key, (label, description)) in enumerate(PROFILE_LABELS.items()):
            card = ttk.Frame(cards, style="Card.TFrame", padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10 if column < 2 else 0))
            cards.columnconfigure(column, weight=1, uniform="profiles")
            ttk.Radiobutton(card, text=label, variable=self.profile_name, value=key,
                            command=self.choose_profile).pack(anchor="w")
            ttk.Label(card, text=description, style="CardMuted.TLabel", wraplength=258).pack(anchor="w", pady=(4, 0))
        tweaks = ttk.Frame(tab)
        tweaks.pack(fill="x", pady=(0, 8))
        entries = (("compositor", "Wyłącz obsługiwane efekty X11"), ("plasma", "Zatrzymaj panel i tapetę Plasmy (KDE)"),
                   ("priority", "Wysoki priorytet CPU i I/O"), ("gamemode", "Użyj GameMode, jeśli dostępny"))
        for index, (key, label) in enumerate(entries):
            checkbox = ttk.Checkbutton(tweaks, text=label, variable=self.checks[key], command=self.refresh_options)
            checkbox.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 24))
            if (key == "compositor" and not self.desktop.compositor_backend) or (key == "plasma" and not self.desktop.shell_service):
                checkbox.configure(state="disabled")
        ttk.Label(tab, text="Program gry lub komenda", style="SubTitle.TLabel").pack(anchor="w", pady=(9, 8))
        command_row = ttk.Frame(tab)
        command_row.pack(fill="x")
        ttk.Entry(command_row, textvariable=self.command).pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Button(command_row, text="Wybierz plik…", command=self.browse).pack(side="right")
        ttk.Label(tab, text="Np. /ścieżka/do/gry --fullscreen. Ścieżkę ze spacjami ujmij w cudzysłów.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(7, 12))
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(0, 12))
        self.launch_button = ttk.Button(actions, text="Uruchom grę z profilem", style="Accent.TButton", command=self.launch_game)
        self.launch_button.pack(side="left", padx=(0, 10))
        ttk.Button(actions, text="Zapisz ustawienia", command=self.save_settings).pack(side="left")
        ttk.Button(actions, text="Pokaż plan zmian", command=self.show_plan).pack(side="right")
        ttk.Label(tab, text="Dla gier Steam przejdź do zakładki Steam i dodaj wygenerowane opcje uruchamiania. "
                  "Nie wpisuj tutaj steam -applaunch: ten proces nie odzwierciedla czasu działania gry.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(2, 7))
        ttk.Label(tab, text=self.desktop.summary + " Nice −5 wymaga uprawnień; bez nich program korzysta z dostępnych wag systemd.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w")

    def build_steam(self):
        tab = self.steam_tab
        ttk.Label(tab, text="Jednorazowa konfiguracja gry w Steam", style="SubTitle.TLabel").pack(anchor="w")
        ttk.Label(tab, text="1. Wybierz profil w zakładce Graj.  2. Skopiuj poniższy tekst.\n"
                  "3. Steam → gra → Właściwości → Ogólne → Opcje uruchamiania.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(9, 15))
        self.options_text = tk.Text(tab, height=4, wrap="word", bg=CARD, fg=ACCENT, relief="flat",
                                    padx=12, pady=12, font=("DejaVu Sans Mono", 10))
        self.options_text.pack(fill="x")
        ttk.Button(tab, text="Kopiuj opcje uruchamiania", style="Accent.TButton", command=self.copy_options).pack(anchor="w", pady=(10, 16))
        ttk.Label(tab, text="Zainstalowane gry (odczyt bibliotek Steam)", style="SubTitle.TLabel").pack(anchor="w", pady=(4, 8))
        self.game_picker = ttk.Combobox(tab, state="readonly")
        self.game_picker.pack(fill="x")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 14))
        ttk.Button(row, text="Uruchom wybraną grę w Steam", command=self.open_steam_game).pack(side="left")
        ttk.Button(row, text="Steam oszczędny", command=self.open_light_steam).pack(side="left", padx=10)
        ttk.Label(tab, text="Uruchamianie z profilem działa po wklejeniu opcji do gry. Zachowaj własne argumenty, jeśli już ich używasz. "
                  "Program nie edytuje plików Steam.", style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(0, 12))
        ttk.Label(tab, text="Steam oszczędny: uruchamia wcześniej zamknięty klient poleceniem steam -silent -cef-disable-gpu. "
                  "Ogranicza użycie GPU przez interfejs, ale klient i CEF nadal działają. "
                  "Nakładkę Steam możesz osobno wyłączyć we właściwościach gry.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w")

    def build_ram(self):
        tab = self.ram_tab
        ttk.Label(tab, text="Zwalnianie RAM jest opcjonalne", style="SubTitle.TLabel").pack(anchor="w")
        indexing = ttk.Checkbutton(tab, text="Zatrzymaj indeksowanie Baloo na czas gry (KDE)", variable=self.checks["free_ram"],
                                  command=self.refresh_options)
        indexing.pack(anchor="w", pady=(15, 0))
        if not self.desktop.ram_services:
            indexing.configure(state="disabled")
        ttk.Checkbutton(tab, text="Poproś o ochronę RAM gry (MemoryLow, domyślnie 2 GiB)", variable=self.memory_protection,
                        command=self.refresh_options).pack(anchor="w")
        ttk.Label(tab, text="MemoryLow pomaga przy presji pamięci tylko przy odpowiedniej konfiguracji grup nadrzędnych. "
                  "Nie rezerwuje pamięci na wyłączność i nie zwiększa ilości RAM ani VRAM.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(5, 19))
        ttk.Label(tab, text="Aplikacje do zamknięcia i ponownego uruchomienia", style="SubTitle.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Zapisz pracę przed uruchomieniem gry. Program zamknie zaznaczone aplikacje. "
                  "Ponowne uruchomienie aplikacji nie odtworzy niezapisanych danych.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(8, 10))
        options = ttk.Frame(tab)
        options.pack(fill="x")
        for index, (key, app) in enumerate(APPS.items()):
            ttk.Checkbutton(options, text=app.label, variable=self.app_checks[key],
                            command=self.refresh_options).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 60))
        ttk.Label(tab, text="Liczy się RAM dostępny (MemAvailable), który obejmuje pamięć możliwą do odzyskania. "
                  "Program nie opróżnia cache dysku i nie zamraża aplikacji: samo zamrożenie nie zwolni ich pamięci.",
                  style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(19, 0))

    def build_log(self):
        tab = self.log_tab
        self.report_var = tk.StringVar(value="Pomiar różnicy pojawi się po pierwszej sesji gry.")
        ttk.Label(tab, textvariable=self.report_var, wraplength=900, foreground=ACCENT).pack(anchor="w", pady=(0, 10))
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(frame, bg=CARD, fg=TEXT, relief="flat", wrap="word", height=9,
                                padx=12, pady=12, font=("DejaVu Sans Mono", 9))
        scrollbar = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set, state="disabled")
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="Odśwież diagnostykę", command=lambda: self.refresh(force=True)).pack(side="left")
        ttk.Label(row, text="Dziennik: ~/.local/state/game-optimizer", style="Muted.TLabel").pack(side="right")

    def refresh_options(self):
        if not hasattr(self, "options_text"):
            return
        options = launch_options(self.current_profile())
        self.options_text.configure(state="normal")
        self.options_text.delete("1.0", "end")
        self.options_text.insert("1.0", options)
        self.options_text.configure(state="disabled")

    def scroll_page(self, event):
        if isinstance(event.widget, (tk.Text, ttk.Combobox)):
            return
        current = self.notebook.select()
        page = next((page for page in self.pages if str(page) == current), None)
        if page and page.content.winfo_reqheight() > page.canvas.winfo_height():
            step = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
            page.canvas.yview_scroll(step * 3, "units")

    def copy_options(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(launch_options(self.current_profile()))
        self.message.set("Skopiowano. Wklej tekst do opcji uruchamiania gry w Steam.")

    def save_settings(self):
        try:
            atomic_json(config_file(), {"version": 1, "profile": self.current_profile().to_dict(), "command": self.command.get()})
            self.message.set("Zapisano ustawienia.")
        except OSError as exc:
            messagebox.showerror("Ustawienia", str(exc), parent=self.root)

    def browse(self):
        path = filedialog.askopenfilename(title="Wybierz program gry", parent=self.root)
        if path:
            self.command.set(shlex.quote(path))

    def launch_game(self):
        try:
            command = shlex.split(self.command.get())
            if not command:
                raise ValueError("Wybierz plik gry lub wpisz komendę.")
            if Path(command[0]).name in ("steam", "steam-runtime", "steam-native", "flatpak"):
                raise ValueError("Dla Steam użyj zakładki Steam i wygenerowanych opcji uruchamiania gry.")
            if self.child is not None and self.child.poll() is None:
                raise ValueError("Gra uruchomiona w tym oknie nadal działa.")
            directory = state_directory()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(directory / "launcher.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a") as handle:
                self.child = subprocess.Popen([sys.executable, str(ENTRY), "run", *profile_arguments(self.current_profile()),
                                               "--", *command], start_new_session=True, stdin=subprocess.DEVNULL,
                                              stdout=handle, stderr=subprocess.STDOUT)
            self.save_settings()
            self.status.set("Uruchamianie gry…")
            self.message.set("Możesz zamknąć to okno. Proces nadzorujący przywróci pulpit po grze.")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Uruchamianie gry", str(exc), parent=self.root)

    def open_steam_game(self):
        index = self.game_picker.current()
        if index < 0 or not self.games:
            self.message.set("Nie wybrano gry lub nie znaleziono zainstalowanych gier.")
            return
        game = self.games[index]
        if game.flatpak:
            self.message.set("Ta gra używa Steam Flatpak. Wrapper hosta wymaga dodatkowych uprawnień; obsługiwany jest natywny Steam.")
            return
        try:
            subprocess.Popen(["steam", f"steam://rungameid/{game.appid}"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.message.set("Wysłano uruchomienie do Steam. Profil zadziała, jeśli wklejono opcje do tej gry.")
        except OSError as exc:
            self.message.set(str(exc))

    def open_light_steam(self):
        self.submit("message", lambda: self.cli_capture(["steam-light"]))

    def restore_now(self):
        self.submit("message", lambda: self.cli_capture(["restore"]))

    def show_plan(self):
        args = ["run", *profile_arguments(self.current_profile()), "--dry-run"]
        self.submit("plan", lambda: self.cli_capture(args))

    def cli_capture(self, args):
        try:
            result = subprocess.run([sys.executable, str(ENTRY), *args], capture_output=True, text=True, timeout=55)
            return (result.stdout + "\n" + result.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)

    def submit(self, kind, callback):
        def work():
            try:
                self.events.put((kind, callback()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
                if kind == "diagnostic":
                    self.events.put(("refresh-complete", None))
        if not self.closing:
            self.workers.submit(work)

    def refresh(self, force=False):
        if self.closing:
            return
        if not self.refreshing:
            self.refreshing = True
            self.submit("diagnostic", diagnostic)
        if not force:
            self.root.after(5000, self.refresh)

    def update_metrics(self, data):
        gpus, ram = data["memory"]["gpus"], data["memory"]["ram"]
        if gpus:
            # Display the dedicated GPU with the largest memory budget.
            gpu = max(gpus, key=lambda value: value["total_mib"])
            self.metric_vars["vram"][0].set(f"{gpu['free_mib']:.0f} MiB")
            self.metric_vars["vram"][1].set(f"Wolne z {gpu['total_mib']:.0f} MiB · {gpu['name'].replace('NVIDIA GeForce ', '')}\nUżywane: {gpu['used_mib']:.0f} MiB")
            self.bars["vram"]["value"] = 100 * gpu["used_mib"] / max(1, gpu["total_mib"])
        else:
            self.metric_vars["vram"][0].set("Brak odczytu")
            self.metric_vars["vram"][1].set("Sterownik nie udostępnił pomiaru VRAM.")
        if ram:
            self.metric_vars["ram"][0].set(f"{ram['available_mib'] / 1024:.1f} GiB")
            self.metric_vars["ram"][1].set(f"Dostępne z {ram['total_mib'] / 1024:.1f} GiB RAM")
            self.bars["ram"]["value"] = 100 * (1 - ram["available_mib"] / max(1, ram["total_mib"]))
        self.update_session()

    def update_session(self):
        store = Store()
        try:
            data = store.load()
        except (OSError, ValueError):
            data = None
        if data:
            phase = data.get("phase")
            names = {"preparing": "Przygotowanie", "starting": "Start gry", "running": "Gra działa",
                     "restoring": "Przywracanie", "restore-failed": "Ponów odzysk"}
            self.metric_vars["session"][0].set(names.get(phase, "Aktywna"))
            elapsed = max(0, int(time.time() - data["started"]))
            self.metric_vars["session"][1].set(f"{elapsed // 60:02}:{elapsed % 60:02} · profil {data['profile']['name']}")
            self.status.set("Sesja aktywna · po grze nastąpi przywrócenie pulpitu" if phase != "restore-failed" else
                            "Przywracanie wymaga ponownej próby — użyj przycisku obok")
        else:
            self.metric_vars["session"][0].set("Pulpit")
            self.metric_vars["session"][1].set("Brak aktywnego profilu")
            self.status.set("Gotowy do gry · pulpit działa normalnie")
        self.launch_button.configure(state="disabled" if data else "normal")
        try:
            report = json.loads((store.directory / "last-report.json").read_text())
            before = {gpu["id"]: gpu for gpu in (report.get("before") or {}).get("gpus", [])}
            entries = []
            for gpu in (report.get("prepared") or {}).get("gpus", []):
                if gpu["id"] in before:
                    diff = gpu["free_mib"] - before[gpu["id"]]["free_mib"]
                    entries.append(f"Wolny VRAM przed uruchomieniem gry: zmiana {diff:+.0f} MiB")
            first, second = (report.get("before") or {}).get("ram", {}), (report.get("prepared") or {}).get("ram", {})
            if first and second:
                entries.append(f"Dostępny RAM: zmiana {second['available_mib'] - first['available_mib']:+.0f} MiB")
            if entries:
                self.report_var.set(" · ".join(entries) + "\nPomiar całego systemu, przed rozpoczęciem gry; nie jest testem FPS.")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        content = ""
        for name in ("session.log", "launcher.log"):
            try:
                with (store.directory / name).open("rb") as handle:
                    handle.seek(0, 2)
                    length = handle.tell()
                    handle.seek(max(0, length - 14000))
                    content += f"{name}\n" + handle.read().decode(errors="replace") + "\n"
            except OSError:
                pass
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", content or "Dziennik pojawi się po uruchomieniu gry.")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def poll(self):
        if self.closing:
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "diagnostic":
                self.refreshing = False
                self.update_metrics(value)
            elif kind == "refresh-complete":
                self.refreshing = False
            elif kind == "games":
                self.games = value
                self.game_picker["values"] = [f"{game.name}  ·  {game.appid}" for game in value]
                if value:
                    self.game_picker.current(0)
            elif kind == "plan":
                window = tk.Toplevel(self.root)
                window.title("Plan zmian — bez uruchamiania gry")
                text = tk.Text(window, bg=CARD, fg=TEXT, width=95, height=30, padx=15, pady=15)
                text.pack(fill="both", expand=True)
                text.insert("1.0", value)
                text.configure(state="disabled")
            elif kind in ("message", "error"):
                self.message.set(value)
        if self.child is not None and self.child.poll() is not None:
            code = self.child.returncode
            self.child = None
            if code:
                self.message.set(f"Uruchamianie zakończone kodem {code}. Szczegóły w dzienniku.")
            self.update_session()
        self.root.after(150, self.poll)

    def close(self):
        self.closing = True
        self.workers.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> int:
    try:
        root = tk.Tk(className="GameOptimizer")
    except tk.TclError as exc:
        print(f"Nie można otworzyć okna: {exc}\nW terminalu użyj game-optimizer --help.", file=sys.stderr)
        return 1
    App(root)
    root.mainloop()
    return 0
