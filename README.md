# Arctis Sound Manager

A Linux GUI for SteelSeries Arctis headsets — manages device settings and provides a 3-channel audio mixer (Game / Chat / Media) with automatic media routing.

> Based on [Linux Arctis Manager](https://github.com/elegos/Linux-Arctis-Manager) by elegos.

---

## Features

- **3-channel audio mixer** — separate Game, Chat and Media virtual sinks
- **Automatic media routing** — browsers (Firefox, Chromium…) and video players (VLC, mpv, Haruna…) are automatically routed to the Media sink
- **Manual stream control** — move any audio stream between channels on the fly via the GUI
- **Persistent routing** — manual moves are remembered across app restarts
- **Native PipeWire support** — detects apps that bypass PulseAudio (mpv, Haruna…)
- **Volume sliders** per channel with live percentage display
- **Device status** — battery, mic, EQ and more depending on your device

## Supported Devices

| Device | Mixer | Advanced features | Product ID(s) |
|---|---|---|---|
| Arctis 7 / Gen 2 | ❌ | ❌ | 1260, 12ad |
| Arctis 7+ / PS5 / Xbox / Destiny | ❌ | ❌ | 220e, 2212, 2216, 2236 |
| Arctis Nova 3 | ❌ | ❌ | 12ec |
| Arctis Nova 5 | ❌ | ❌ | 2232, 2253 |
| Arctis Nova 7P | ❌ | ❌ | 220a |
| Arctis Nova 7X | ❌ | ❌ | 12d7 |
| Arctis Nova 9 | ❌ | ❌ | 12c2 |
| **Arctis Nova Pro Wireless / X** | ✅ | ✅ | 12e0, 12e5 |
| Arctis Pro GameDAC | ❌ | ❌ | 1280 |
| Arctis Pro Wireless | ❌ | ❌ | 1290 |

---

## Requirements

- Linux with **PipeWire** (+ `pipewire-pulse`)
- **Python 3.10+**
- `uv` — [installation guide](https://docs.astral.sh/uv/getting-started/installation/)
- `pipx` — install with your package manager:
  ```bash
  # Arch / CachyOS / Manjaro
  sudo pacman -S python-pipx

  # Debian / Ubuntu
  sudo apt install pipx

  # Fedora
  sudo dnf install pipx
  ```

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/loteran/Arctis-Sound-Manager.git
cd Arctis-Sound-Manager

# 2. Run the installer
bash scripts/install.sh
```

The installer will:
- Build and install the package via `pipx`
- Install udev rules for USB device access (requires sudo)
- Create desktop entries
- Enable the `arctis-manager` systemd user service (device daemon)
- Enable the `arctis-video-router` systemd user service (media auto-routing)

After installation, launch the GUI from your application menu or run:
```bash
lam-gui
```

---

## How the mixer works

The app creates 3 virtual audio sinks on top of your physical Arctis device:

| Sink | Default use | Color |
|---|---|---|
| **Arctis_Game** | Games, general audio | Teal |
| **Arctis_Chat** | Voice apps (Discord, TeamSpeak…) | Blue |
| **Arctis_Media** | Browsers, video players | Orange |

The **media router** (`lam-router`) runs as a background service and automatically moves browsers and video players to `Arctis_Media`. Any app not in the list stays on whichever sink it was placed on.

To **manually move** an app stream, click the **G / C / M** buttons on its tag in the GUI. The choice is saved and respected even after the app restarts.

---

## Uninstall

```bash
# Stop and disable services
systemctl --user disable --now arctis-manager.service
systemctl --user disable --now arctis-video-router.service

# Remove service files
rm ~/.config/systemd/user/arctis-manager.service
rm ~/.config/systemd/user/arctis-video-router.service

# Remove desktop entries and udev rules
lam-cli desktop remove
sudo rm /usr/lib/udev/rules.d/91-steelseries-arctis.rules

# Remove user config
rm -rf ~/.config/arctis_manager

# Uninstall the package
pipx uninstall linux-arctis-manager
```

---

## Development

```bash
# Run the daemon
uv run lam-daemon

# Run the GUI (without enforcing systemd)
uv run lam-gui --no-enforce-systemd

# Run the media router
uv run lam-router
```

### Project structure

```
src/linux_arctis_manager/
├── scripts/
│   ├── daemon.py          # lam-daemon: device manager service
│   ├── gui.py             # lam-gui: graphical interface
│   ├── video_router.py    # lam-router: media auto-routing service
│   └── cli.py             # lam-cli: setup utilities
├── gui/
│   ├── home_page.py       # Audio mixer (Game/Chat/Media cards)
│   ├── components.py      # Reusable widgets
│   └── theme.py           # Color constants
├── pw_utils.py            # Native PipeWire stream detection
├── pactl.py               # PulseAudio virtual sink management
└── devices/               # Per-device configuration files
```
