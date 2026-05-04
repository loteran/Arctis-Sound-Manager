# ASM Distrobox — per-distro installer scripts

Use these scripts to install Arctis Sound Manager on immutable distros via [Distrobox](https://distrobox.it/).

## Which script for which distro?

| Host distro | Script |
|---|---|
| Bazzite | `bash scripts/distrobox/bazzite.sh` |
| Fedora Silverblue / Kinoite | `bash scripts/distrobox/silverblue.sh` |
| SteamOS (Steam Deck) | `bash scripts/distrobox/steamos.sh` |
| Unknown / other | Use the scripts above directly; choose Arch (bazzite) or Fedora (silverblue) |

The main `scripts/distrobox-install.sh` auto-detects your distro and delegates to the right script.

## What each install script does

1. **Container** — creates a Distrobox container with `/dev/hidraw*` devices and PipeWire sockets mounted
2. **Install** — installs ASM inside the container (AUR for Arch-based, COPR for Fedora)
3. **Export** — exports `asm-daemon`, `asm-gui`, `asm-cli`, `asm-setup`, `asm-router` and the desktop entry to the host
4. **Systemd units** — writes user unit files to `~/.config/systemd/user/` (with correct `WantedBy` targets including `gamescope-session.target` for Game Mode)
5. **udev rules** — generates rules from the container and installs them on the host via `sudo`
6. **Verify** — checks PipeWire is accessible from inside the container
7. **Enable** — enables and starts all three ASM services

## Options (all scripts)

| Option | Effect |
|---|---|
| `--reinstall` | Remove the existing container and reinstall from scratch |
| `--no-services` | Install but do not enable/start systemd services |
| `-h` / `--help` | Show usage |

## Uninstall

```bash
bash scripts/distrobox/uninstall.sh
# Keep the container but remove services and exports:
bash scripts/distrobox/uninstall.sh --keep-container
# Also remove udev rules:
bash scripts/distrobox/uninstall.sh --remove-udev
```

## Diagnostics

```bash
bash scripts/distrobox-diag.sh
```

**Privacy note:** the diagnostic report redacts your hostname, device serial numbers, MAC addresses, and audio sink names before writing to disk. Review the output file before attaching it to a GitHub issue.

## Files in this directory

| File | Description |
|---|---|
| `_common.sh` | Shared library sourced by all scripts (not executable) |
| `bazzite.sh` | Installer for Bazzite (Arch container) |
| `silverblue.sh` | Installer for Silverblue / Kinoite (Fedora 41 container) |
| `steamos.sh` | Installer for SteamOS / Steam Deck (Arch container) |
| `uninstall.sh` | Uninstaller |
| `README.md` | This file |
