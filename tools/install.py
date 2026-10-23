#!/usr/bin/env python3
"""Install a self-contained per-user copy, without sudo or package downloads."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


def desktop_quote(value: str) -> str:
    return '"' + ''.join('\\' + char if char in '\\"`$' else char for char in value) + '"'


def install(prefix: Path, autostart: bool = False) -> Path:
    source = Path(__file__).resolve().parent.parent
    prefix = prefix.expanduser().resolve()
    target = prefix / "share/game-optimizer"
    if target == source:
        raise ValueError("Katalog instalacji musi różnić się od katalogu źródeł.")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "game_optimizer", target / "game_optimizer", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("game-optimizer", "README.md", "RELEASE_NOTES.md", "LICENSE"):
        shutil.copy2(source / name, target / name)
    shutil.copytree(source / "docs", target / "docs", dirs_exist_ok=True)
    (target / "assets").mkdir(exist_ok=True)
    shutil.copy2(source / "assets/game-optimizer.svg", target / "assets/game-optimizer.svg")
    (target / "game-optimizer").chmod(0o755)
    binary = prefix / "bin/game-optimizer"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/usr/bin/env python3\nimport runpy, sys\n"
                      f"sys.path.insert(0, {str(target)!r})\n"
                      f"runpy.run_path({str(target / 'game-optimizer')!r}, run_name='__main__')\n")
    binary.chmod(0o755)
    icon = prefix / "share/icons/hicolor/scalable/apps/game-optimizer.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "assets/game-optimizer.svg", icon)
    applications = prefix / "share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "game-optimizer.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=VRAM Game Optimizer\n"
        "Comment=Tymczasowo zwolnij VRAM i RAM tła na czas gry\n"
        f"Exec={desktop_quote(str(binary))} gui\nIcon=game-optimizer\n"
        "Terminal=false\nCategories=Game;Utility;\nStartupNotify=true\nStartupWMClass=GameOptimizer\n")
    if autostart:
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        folder = config / "autostart"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "game-optimizer-recover.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Game Optimizer Recovery\n"
            f"Exec={desktop_quote(str(binary))} recover\nTerminal=false\nNoDisplay=true\n"
            "X-KDE-autostart-after=panel\n")
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--autostart-recovery", action="store_true",
                        help="Sprawdzaj osierocony dziennik przy logowaniu do pulpitu")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Instaluj jako zwykły użytkownik, bez sudo.")
    binary = install(args.prefix, args.autostart_recovery)
    print(f"Zainstalowano: {binary}")
    print("Aplikacja jest dostępna w menu. Opcje Steam generuj z zainstalowanej kopii.")


if __name__ == "__main__":
    main()
