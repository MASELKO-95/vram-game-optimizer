<p align="center">
  <img src="assets/game-optimizer.svg" alt="VRAM Game Optimizer" width="104">
</p>

<h1 align="center">VRAM Game Optimizer</h1>

<p align="center"><strong>More room for your games. Your desktop back when you're done.</strong></p>

<p align="center">
  <a href="https://github.com/MASELKO-95/vram-game-optimizer/releases/tag/v0.3.0-nvidia">NVIDIA release</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#gpu-support--roadmap">GPU roadmap</a> ·
  <a href="docs/README.pl.md">Polski</a> ·
  <a href="LICENSE">BSD-3-Clause</a>
</p>

A lightweight Linux tool that helps reclaim VRAM and RAM used by your desktop and background apps. Choose a Steam game with the arrow keys, select a profile, and launch it from your terminal. The optimizer applies supported desktop changes and process priorities, then restores the previous desktop state when the game closes.

**The first public release targets NVIDIA GPUs.** AMD is the next planned vendor release. Intel support will be evaluated for integrated and discrete graphics, with a separate release if additional support is needed.

## Download

**v0.3.0-nvidia · NVIDIA preview · Linux**

| Download | Use |
| --- | --- |
| [NVIDIA Linux package — tar.gz](https://github.com/MASELKO-95/vram-game-optimizer/releases/download/v0.3.0-nvidia/vram-game-optimizer-0.3.0-nvidia-linux.tar.gz) | Recommended for Linux |
| [NVIDIA Linux package — ZIP](https://github.com/MASELKO-95/vram-game-optimizer/releases/download/v0.3.0-nvidia/vram-game-optimizer-0.3.0-nvidia-linux.zip) | Alternative archive |
| [SHA-256 checksums](https://github.com/MASELKO-95/vram-game-optimizer/releases/download/v0.3.0-nvidia/SHA256SUMS) | Verify your download |
| [Release notes](https://github.com/MASELKO-95/vram-game-optimizer/releases/tag/v0.3.0-nvidia) | Features and current limits |

The archives contain the application and its Python source. They require **Python 3.10+** on your system. A virtual environment and pip packages are unnecessary.

## What it does

- Finds installed Steam games across your libraries and mounted drives.
- Offers a terminal menu with arrow-key selection and name filtering.
- Temporarily suspends supported X11 desktop compositing.
- Can stop KDE Plasma's panel and wallpaper while the game runs.
- Applies available CPU/I/O priorities and optional GameMode integration.
- Optionally closes selected background apps to free RAM and GPU resources.
- Measures available VRAM before the game starts, then restores the desktop after it closes.
- Keeps a recovery journal and a separate watchdog for launcher crashes.

## Quick start

Requirements:

- Linux with Python 3.10+.
- A working NVIDIA driver and `nvidia-smi` for NVIDIA memory monitoring.
- The native Steam client, already running and signed in.
- Optional: GameMode; Tk for the graphical interface.

Extract the NVIDIA package and run:

```bash
tar -xzf vram-game-optimizer-0.3.0-nvidia-linux.tar.gz
cd vram-game-optimizer-0.3.0-nvidia
./game-optimizer
```

Select a game with **↑ / ↓**, press **Enter**, then select its optimization profile. Steam stays running, and the optimizer tracks the selected game's processes. Steam launch options do not need to be edited.

The application interface is currently in **Polish**. These instructions and the release notes are available in English.

| Key | Action |
| --- | --- |
| ↑ / ↓, Enter | Select a game or profile |
| Type a name or AppID | Filter the game list |
| Backspace | Remove filter text |
| Esc | Go back or exit |
| F2 | Select background apps to close |
| F5 | Refresh Steam libraries |
| F6 | Show diagnostics |
| F8 | Restore the desktop |

If you downloaded the ZIP and the launcher is not executable, run `chmod +x game-optimizer` once.

## Profiles

| Profile | Behavior |
| --- | --- |
| `balanced` | Suspends supported X11 effects and applies available priorities; keeps the panel and wallpaper |
| `vram` | Adds temporary Plasma panel/wallpaper shutdown on KDE |
| `vram-ram` | Adds supported indexing suspension and a request for RAM protection |

The optional app list includes Discord, Spotify, Telegram, Firefox and Chromium. **Save your work before selecting apps to close.** Restarting an app cannot guarantee recovery of unsaved data or its previous session.

## GPU support & roadmap

| Vendor | Release status | Scope |
| --- | --- | --- |
| **NVIDIA** | **First public preview: v0.3.0-nvidia** | VRAM totals, usage, free memory and graphics/compute process reporting through `nvidia-smi` |
| **AMD** | **Next vendor release planned** | A basic DRM/sysfs memory reader is already present; hardware validation and vendor-specific improvements are pending |
| **Intel** | **Under evaluation** | Assess telemetry and memory handling for integrated and discrete graphics; publish a dedicated release if needed |

Future vendor releases will be offered as downloads on the [Releases page](https://github.com/MASELKO-95/vram-game-optimizer/releases). AMD and Intel release packages are not available yet.

Desktop cleanup, Steam integration, process priorities and app management share a common core. Vendor releases will focus on the monitoring and validation needed for each GPU family.

## Desktop support

| Desktop/session | Available desktop changes |
| --- | --- |
| KDE Plasma / X11 | Suspend KWin compositing; optionally stop Plasma and Baloo |
| KDE Plasma / Wayland | Optionally stop Plasma and Baloo; keep the display server active |
| Xfce / X11 with Xfwm4 | Temporarily disable compositing; keep the panel and wallpaper |
| MATE / X11 with Marco | Temporarily disable compositing; keep the panel and wallpaper |
| GNOME, Cinnamon, Budgie, Pantheon | Common CPU/I/O, GameMode and selected-app optimizations |
| LXQt, LXDE, i3, Openbox | Common CPU/I/O, GameMode and selected-app optimizations |
| Sway, Hyprland and other Wayland sessions | Common optimizations while preserving the compositor |

Xfce requires `xfconf-query` and `xprop`. MATE requires `gsettings`, `dconf` and `xprop`. The actual window manager is checked before changing Xfwm4 or Marco preferences. These two adapters still need validation in real desktop sessions.

The reference machine for this release is an **NVIDIA RTX 3070 Ti with 8 GiB VRAM, Manjaro and KDE Plasma on X11**. VRAM savings and FPS gains depend on the game, driver, desktop and background apps; no fixed improvement is claimed.

## Useful commands

```bash
# Open the terminal menu
./game-optimizer

# List installed games, then launch one by AppID
./game-optimizer games
./game-optimizer steam 70 --profile vram

# Preview changes without applying them
./game-optimizer run --profile vram-ram --dry-run

# Diagnose memory and desktop support
./game-optimizer diagnose

# Restore the desktop immediately
./game-optimizer restore

# Open the optional graphical interface
./game-optimizer gui
```

Replace `70` with an installed game's AppID. Games outside Steam can use `./game-optimizer run --profile vram -- /path/to/game`.

## Install for your user

Run the extracted package directly, or install a copy and desktop-menu entry:

```bash
python3 tools/install.py
```

The installer uses `~/.local` and requires no administrator privileges. On Arch/Manjaro, optional packages can be installed with:

```bash
sudo pacman -S tk gamemode lib32-gamemode
```

`lib32-gamemode` requires the multilib repository. **Run the optimizer as your regular user.**

## Recovery and practical limits

The optimizer journals changes before applying them and restores services that it stopped. A watchdog continues monitoring if the main launcher crashes. `restore` also works while the game is running; `recover` retries an orphaned session when its game is no longer running.

- A graphical session still needs GPU memory for its display server, driver and frame buffers.
- GNOME/Cinnamon shells and Wayland compositors stay active.
- Native Steam is supported; Steam Flatpak integration is pending.
- Negative nice values and cgroup CPU/I/O/RAM controls depend on system permissions and configuration.
- RAM protection uses `MemoryLow` where available. It does not add physical RAM or VRAM.
- Wine helpers, launchers or crash reporters can delay automatic restoration.
- Simultaneous loss of both watchdog processes requires another launch or manual recovery.

Settings: `~/.config/game-optimizer/settings.json`. Session journal and logs: `~/.local/state/game-optimizer/`. Standard XDG config/state environment variables are supported.

For advanced options, Steam wrappers and recovery details, see the [Polish usage guide](docs/README.pl.md).

## Build a release package

```bash
python3 -B tools/build_release.py
```

The NVIDIA archives and `SHA256SUMS` are written to `dist/`. Packages contain the application, documentation, icon and installer.

## License

[BSD 3-Clause](LICENSE).
