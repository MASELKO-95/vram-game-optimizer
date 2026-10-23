# VRAM Game Optimizer v0.3.0-nvidia

First public NVIDIA preview for Linux.

## Included

- Steam game discovery across installed libraries.
- Arrow-key terminal menus with game filtering and profile selection.
- NVIDIA VRAM and graphics/compute process reporting through `nvidia-smi`.
- Temporary desktop cleanup on supported desktop environments.
- Available CPU/I/O priorities, optional GameMode and optional RAM protection.
- Selected background-app shutdown and restart.
- Recovery journal, crash watchdog and manual desktop restoration.
- Optional Tk interface and per-user installer.
- BSD-3-Clause license.

## Downloads

- `vram-game-optimizer-0.3.0-nvidia-linux.tar.gz`
- `vram-game-optimizer-0.3.0-nvidia-linux.zip`
- `SHA256SUMS`

The archives contain the application and its Python source. Install Python 3.10+ and run `./game-optimizer` from the extracted directory. Native Steam should already be running and signed in. NVIDIA monitoring requires a working `nvidia-smi`.

The current application interface is Polish. The main README and these release notes are English.

## Validation and current scope

The reference system is Manjaro, KDE Plasma/X11 and an NVIDIA RTX 3070 Ti with 8 GiB VRAM. This release does not claim a measured FPS improvement or a fixed amount of recovered VRAM. Xfce and MATE compositor adapters still require validation in real sessions. Steam Flatpak integration is pending.

Desktop and process optimizations share a common core. The NVIDIA label identifies the first GPU monitoring and validation target.

## Next vendor releases

AMD is the next planned vendor release. Basic AMD DRM/sysfs memory reading is already present, with hardware validation and additional telemetry pending.

Intel support is under evaluation for integrated and discrete graphics. A separate Intel download will be published if it needs additional vendor support.

Only the NVIDIA release assets are available in this release.
