# Arctis Sound Manager

A Linux GUI for SteelSeries Arctis headsets — manages device settings and provides a 4-channel audio mixer (Game / Chat / Media / HDMI) with automatic media routing, and a full **Sonar EQ** system powered by PipeWire filter-chain.

> Based on [Arctis Sound Manager](https://github.com/elegos/Linux-Arctis-Manager) by elegos.

---

## Features

- **4-channel audio mixer** — separate Game, Chat, Media and HDMI virtual sinks
- **True HDMI surround** — route any app directly to your HDMI output (5.1 / 7.1 native)
- **Automatic media routing** — browsers (Firefox, Chromium…) and video players (VLC, mpv, Haruna…) are automatically routed to the Media sink
- **Manual stream control** — move any audio stream between channels on the fly via the G / C / M / H buttons
- **Persistent routing** — manual moves are remembered across app restarts
- **Native PipeWire support** — detects apps that bypass PulseAudio (mpv, Haruna…)
- **Volume sliders** per channel with live percentage display
- **Sonar EQ** — full SteelSeries Sonar-style parametric EQ system (v2.0):
  - Interactive EQ curve with up to 10 bands per channel (Game / Chat / Micro)
  - 297 Game presets, 8 Chat, 14 Mic imported from Sonar — searchable, with 9 favorite slots
  - Macro sliders: Basses / Voix / Aigus (±12 dB)
  - **Spatial Audio** — routes Game channel through HeSuVi virtual 7.1 surround, with **Immersion** (0–12 dB gain) and **Distance** (plate reverb) sliders
  - **Boost de Volume** — up to +12 dB gain node at the end of the filter chain
  - **Smart Volume** — dynamic compressor (Quiet / Balanced / Loud) to even out volume differences
  - All changes applied live via PipeWire filter-chain (biquad nodes)
- **10-band equalizer** — Custom mode: per-band gain (31 Hz to 16 kHz), save/load presets
- **ANC / Transparent mode indicator** — reflects the physical button state (Off / Transparent / ANC) in real time
- **Device status page** — battery, mic mute, sidetone, and more depending on your device
- **Help page** — built-in user manual in English, French and Spanish
- **Virtual surround 7.1** — optional HeSuVi filter-chain for stereo headsets

## Screenshots

### Home — 4-channel audio mixer (Game / Chat / Media / HDMI)
![Home](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_home.png)

### Sonar — Parametric EQ (Game / Chat / Micro) with presets, Spatial Audio and Boost
![Sonar](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_sonar.png)

### Equalizer — Custom mode 10-band EQ with presets
![Equalizer](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_equalizer.png)

### Headset / DAC — Device info and live status
![Headset](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_headset.png)

### Settings — Device configuration
![Settings](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_settings.png)

---

## Supported Devices

| Device | Mixer | Advanced features | Product ID(s) |
|---|---|---|---|
| Arctis 1 / 7X / 7P Wireless | ⚠️ | ⚠️ | 12b3, 12b6, 12d5, 12d7 |
| Arctis 7 / 7 2019 / Pro 2019 / Pro GameDAC | ⚠️ | ⚠️ | 1260, 12ad, 1252, 1280 |
| Arctis 7+ / PS5 / Xbox / Destiny | ❓ | ❓ | 220e, 2212, 2216, 2236 |
| Arctis 9 Wireless | ⚠️ | ⚠️ | 12c2 |
| Arctis Pro Wireless | ⚠️ | ⚠️ | 1290 |
| **Arctis Nova Pro Wireless / X** | ✅ | ✅ | 12e0, 12e5 |
| Arctis Nova 3 | ⚠️ | ⚠️ | 12ec |
| Arctis Nova 5 / 5X | ⚠️ | ⚠️ | 2232, 2253 |
| Arctis Nova 7 Gen 1 | ⚠️ | ⚠️ | 2202, 2206, 223a, 227a, 22a4 |
| Arctis Nova 7 Gen 2 | ⚠️ | ⚠️ | 22a1, 227e, 2258, 229e, 22a9, 22a5 |
| Arctis Nova 7P | ❓ | ❓ | 220a, 22a7 |

> ✅ Fully supported · ⚠️ Config available, community testing welcome · ❓ Not yet supported

---

## Requirements

- Linux with **PipeWire** (+ `pipewire-pulse`)
- **Python 3.10+**
- System libraries: `libusb`, `libpulse`, `libudev`
- `pipx` — install everything with your package manager:
  ```bash
  # Arch / CachyOS / Manjaro
  sudo pacman -S python-pipx libusb libpulse noise-suppression-for-voice

  # Debian / Ubuntu
  sudo apt install pipx libusb-1.0-0 libpulse0 libudev1 libnoise-suppression-for-voice

  # Fedora
  sudo dnf install pipx libusb1 pulseaudio-libs systemd-libs noise-suppression-for-voice
  ```

---

## Optional: Virtual Surround 7.1

> The virtual surround setup is also available as a **standalone repo**: [arctis-virtual-surround](https://github.com/loteran/arctis-virtual-surround).
> Use it if you want to install virtual surround independently of Arctis Sound Manager, or on a fresh OS with a single command (`bash install.sh`).
> It also includes WirePlumber priority rules to ensure the Arctis is always preferred over HDMI as the default sink.

Virtual 7.1 surround is now **included automatically** with `install.sh` — it downloads the HRIR file and deploys the PipeWire filter-chain config. No separate setup needed.

It works by creating a PipeWire filter-chain sink that applies HRTF convolution (HeSuVi) to any 7.1 source and outputs stereo to your headset.

```
                  ┌──────────────────────────┐
 7.1 audio source │  Virtual Surround Sink   │
 (game, movie…) ──►  (PipeWire filter-chain) ├──► Headset (stereo)
                  └──────────────────────────┘
```

### Manual setup (alternative)

If you prefer to set up surround separately, or if you only want the standalone surround without the full Arctis Sound Manager:

```bash
bash scripts/setup-surround.sh
```

The setup script will:
- Install the PipeWire filter-chain config in `~/.config/pipewire/filter-chain.conf.d/`
- Download a default HRIR file (KEMAR Gardner 1995) into `~/.local/share/pipewire/hrir_hesuvi/`
- Enable and start the `filter-chain` systemd user service

After setup, a new audio sink called **"Virtual Surround Sink"** appears in your system. Route it to your headset output from your desktop audio settings.

> **Custom HRIR profiles**: You can replace `~/.local/share/pipewire/hrir_hesuvi/hrir.wav` with any 14-channel HeSuVi-compatible WAV file from the [HeSuVi project](https://github.com/nicehash/HeSuVi/tree/master/hrir/44), then restart the service: `systemctl --user restart filter-chain.service`

---

## Installation

### Arch Linux / CachyOS / Manjaro (AUR)

```bash
paru -S arctis-sound-manager
```

Then run the post-install setup to deploy user configs and download the HRIR file:

```bash
asm-setup
```

### Other distros (from source)

```bash
# 1. Clone the repository
git clone --branch main https://github.com/loteran/Arctis-Sound-Manager.git
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
- Copy device configs to `~/.config/arctis_manager/devices/` (required for Sonar EQ mode switch)
- Download the default HRIR file for virtual surround (HeSuVi)
- Enable the `filter-chain` systemd user service (required for Sonar EQ and virtual surround)

After installation, launch the GUI from your application menu or run:
```bash
asm-gui
```

---

## How the mixer works

The app creates 3 virtual audio sinks on top of your physical Arctis device, plus direct access to your HDMI output:

| Sink | Default use | Button |
|---|---|---|
| **Arctis_Game** | Games, general audio | G |
| **Arctis_Chat** | Voice apps (Discord, TeamSpeak…) | C |
| **Arctis_Media** | Browsers, video players | M |
| **HDMI** | Direct native surround (5.1 / 7.1) | H |

The **media router** (`asm-router`) runs as a background service and automatically moves browsers and video players to `Arctis_Media`. Any app not in the list stays on whichever sink it was placed on.

To **manually move** an app stream, click the **G / C / M / H** buttons on its tag in the GUI. The choice is saved and respected even after the app restarts.

> **HDMI note**: The HDMI card routes audio **directly** to the physical HDMI sink, bypassing the virtual stereo sinks. This preserves true 5.1 / 7.1 channel output for games and movies. Use it when your display or AV receiver supports surround sound.

### Configuring a different output for the HDMI card

By default the HDMI card targets any sink whose name contains `hdmi-surround`. If your output has a different name (e.g. HDMI stereo, DisplayPort, USB DAC…), find it with:

```bash
pactl list sinks short
```

Example output:
```
91    alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo   ...
1605  alsa_output.pci-0000_09_00.1.hdmi-surround                              ...
```

Then open `src/arctis_sound_manager/gui/home_page.py` and change the `SINK_HDMI` constant to match a unique fragment of your sink name:

```python
# Before (default)
SINK_HDMI = "hdmi-surround"

# Example: DisplayPort output
SINK_HDMI = "hdmi-stereo"

# Example: USB DAC
SINK_HDMI = "USB_Audio"
```

The fragment just needs to be unique enough to match only the desired sink. After editing, restart the GUI.

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
asm-cli desktop remove
sudo rm /usr/lib/udev/rules.d/91-steelseries-arctis.rules

# Remove user config
rm -rf ~/.config/arctis_manager

# Uninstall the package
pipx uninstall arctis-sound-manager

# Disable filter-chain service (Sonar EQ)
systemctl --user disable --now filter-chain.service

# Optional: remove virtual surround config
rm ~/.config/pipewire/filter-chain.conf.d/sink-virtual-surround-7.1-hesuvi.conf
```

---

## Development

```bash
# Run the daemon
python src/arctis_sound_manager/scripts/daemon.py

# Run the GUI (without enforcing systemd)
python src/arctis_sound_manager/scripts/gui.py --no-enforce-systemd

# Run the media router
python src/arctis_sound_manager/scripts/video_router.py
```

### Project structure

```
src/arctis_sound_manager/
├── scripts/
│   ├── daemon.py          # asm-daemon: device manager service
│   ├── gui.py             # asm-gui: graphical interface
│   ├── video_router.py    # asm-router: media auto-routing service
│   └── cli.py             # asm-cli: setup utilities
├── gui/
│   ├── home_page.py       # Audio mixer (Game/Chat/Media/HDMI cards)
│   ├── headset_page.py    # Device info and live status
│   ├── equalizer_page.py  # EQ mode toggle (Custom / Sonar) + 10-band sliders
│   ├── sonar_page.py      # Sonar EQ UI (Game/Chat/Micro tabs, presets, Spatial Audio, Boost)
│   ├── eq_curve_widget.py # Interactive parametric EQ curve widget (biquad RBJ)
│   ├── anc_widget.py      # ANC / Transparent mode indicator
│   ├── help_page.py       # Built-in user manual (EN/FR/ES)
│   ├── components.py      # Reusable widgets
│   └── theme.py           # Color constants
├── sonar_to_pipewire.py   # PipeWire filter-chain config generator (Sonar EQ)
├── pw_utils.py            # Native PipeWire stream detection
├── pactl.py               # PulseAudio virtual sink management
└── devices/               # Per-device configuration files

scripts/
├── install.sh                              # Main installer
├── setup-surround.sh                       # Optional virtual surround setup
├── pipewire/
│   ├── 10-arctis-virtual-sinks.conf           # Native PipeWire loopback sinks (Game/Chat/Media)
│   └── sink-virtual-surround-7.1-hesuvi.conf  # HeSuVi 7.1 virtual surround filter-chain
└── arctis-video-router.service             # Systemd service for asm-router
```
If you want to buy me a coffee ;) --> https://ko-fi.com/loteran
