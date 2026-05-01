# Arctis Sound Manager

> 💬 **Tried ASM? [Share your experience in GitHub Discussions](https://github.com/loteran/Arctis-Sound-Manager/discussions)** — feedback helps improve compatibility for everyone!

> ☕ [Buy me a coffee](https://ko-fi.com/loteran) if you find it useful!

A Linux GUI for SteelSeries Arctis headsets — manages device settings and provides a 4-channel audio mixer (Game / Chat / Media / Output) with automatic media routing, and a full **Sonar EQ** system powered by PipeWire filter-chain.

> Based on [Arctis Sound Manager](https://github.com/elegos/Linux-Arctis-Manager) by elegos.

---

## Features

- **4-channel audio mixer** — separate Game, Chat, Media and Output virtual sinks (Output targets any external device: HDMI, USB speakers, sound card…)
- **True multichannel external output** — route any app directly to an HDMI or other external output (5.1 / 7.1 native passthrough)
- **Automatic media routing** — browsers (Firefox, Chromium…) and video players (VLC, mpv, Haruna…) are automatically routed to the Media sink
- **Smart stream adoption** — apps already running when ASM starts are pulled into the headset (`Arctis_Media`) instead of staying glued to a non-Arctis sink. Manual placements you make in your system mixer are remembered as persistent overrides.
- **Manual stream control** — move any audio stream between channels on the fly via the G / C / M / O buttons
- **Persistent routing** — manual moves are remembered across app restarts
- **Native PipeWire support** — detects apps that bypass PulseAudio (mpv, Haruna…)
- **Volume sliders** per channel with live percentage display
- **Sonar EQ** — full SteelSeries Sonar-style parametric EQ system (v2.0):
  - Interactive EQ curve with up to 10 bands per channel (Game / Chat / Micro)
  - 312 Game presets, 8 Chat, 14 Mic bundled — searchable, with 9 favorite slots
  - Macro sliders: Basses / Voix / Aigus (±12 dB)
  - **Spatial Audio** — routes Game channel through HeSuVi virtual 7.1 surround, with **Immersion** (0–12 dB gain) and **Distance** (plate reverb) sliders
  - **Volume Boost** — up to +12 dB gain node at the end of the filter chain
  - **Smart Volume** — dynamic compressor (Quiet / Balanced / Loud) to even out volume differences
  - All changes applied live via PipeWire filter-chain (biquad nodes)
- **10-band equalizer** — Custom mode: per-band gain (31 Hz to 16 kHz), save/load presets
- **ANC / Transparent mode indicator** — reflects the physical button state (Off / Transparent / ANC) in real time
- **Device status page** — battery, mic mute, sidetone, and more depending on your device
- **Audio Profiles** — save and restore your complete audio configuration in one click:
  - Stores EQ mode (Sonar / Custom), active preset per channel, macro slider values, Spatial Audio state and channel volumes
  - Profile bar on the Home page for instant switching; also accessible from the system tray
  - Right-click a profile chip to delete it
- **Launch at startup** — toggle in Settings to enable/disable the daemon and system tray autostart via systemd (`arctis-gui.service`)
- **Check for updates** — button in Settings forces an immediate GitHub check (bypasses the 24 h cache); clicking the result opens the update dialog — opens a terminal for package manager installs (pacman / dnf / apt) or installs the wheel in-app for pipx/pip
- **DAC OLED display** _(Arctis Nova Pro Wireless / X)_ — dedicated DAC page with full OLED screen control:
  - Toggle between original and custom display mode
  - Brightness, screen timeout and scroll speed sliders
  - Choose and reorder display elements: Time, Battery, active Profile, EQ Preset, Weather temperature
  - Per-element font size control (7–30 pt)
  - Built-in weather integration: city lookup, °C / °F selector, auto-refresh
- **Self-healing system deps** _(v1.0.86)_ — at startup ASM checks every system component it relies on (LADSPA plugins, HRIR file, PipeWire ≥ 1.0, `wpctl`, `pkexec`, `pyudev`/`pulsectl`/`PySide6`/…, `dbus-send`, `pw-metadata`, `curl`, D-Bus session, udev rules). If anything is missing, a one-click dialog runs the right `pkexec dnf|apt-get|pacman install …` for your distro — single password prompt fixes the whole batch. **Install all missing**, **Re-check**, **Copy cmd** for unsupported distros, and a version-aware skip option that auto-resets on the next ASM upgrade.
- **Built-in diagnostics** — `asm-daemon --verify-setup` runs the same registry headless and exits 0/1 with a per-distro install hint per missing dep. `asm-cli diagnose -o file.txt` writes a full local-only diagnostic dump for bug reports.
- **One-click bug reports** — when filing an issue, the dialog auto-uploads the full diagnostic as a secret GitHub gist and opens a pre-filled issue linking to it (requires authenticated `gh` CLI). Falls back to a manual drag-and-drop attachment otherwise.
- **`ARCTIS_LOG_LEVEL` env var** — bump verbosity for support tickets without rebuilding: `ARCTIS_LOG_LEVEL=debug systemctl --user restart arctis-manager`. Honored by daemon, GUI and video-router.
- **Help page** — built-in user manual in English, French and Spanish
- **Virtual surround 7.1** — HeSuVi filter-chain included automatically with the install

## Screenshots

### Home — 4-channel audio mixer (Game / Chat / Media / Output)
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

<!-- STATS:DEVICES:START -->
| Device | Status | Users | Product ID(s) |
|---|---|---|---|
| Arctis 1 / 7X / 7P Wireless | ⚠️ |  | 12b3, 12b6, 12d5, 12d7 |
| Arctis 7 / 7 2019 / Pro 2019 / Pro GameDAC | ⚠️ |  | 1260, 12ad, 1252, 1280 |
| Arctis 7+ / PS5 / Xbox / Destiny | ⚠️ |  | 220e, 2212, 2216, 2236 |
| Arctis 9 Wireless | ✅ | 1 | $\color{royalblue}{\textbf{12c2}}$ |
| Arctis Pro Wireless | ⚠️ |  | 1290, 1294 |
| Arctis Nova Pro Wireless / X | ✅ | 3 | $\color{royalblue}{\textbf{12e0}}$, 12e5 |
| Arctis Nova Pro Wired / Xbox Wired | ✅ |  | 12cb, 12cd |
| Arctis Nova 3 | ⚠️ |  | 12ec |
| Arctis Nova 3P / 3X Wireless | ⚠️ |  | 2269, 226d |
| Arctis Nova 5 / 5X | ⚠️ |  | 2232, 2253 |
| Arctis Nova 7 Gen 1 | ✅ | 1 | $\color{royalblue}{\textbf{2202}}$, 2206, 223a, 227a, 22a4 |
| Arctis Nova 7 Gen 2 | ✅ | 2 | $\color{royalblue}{\textbf{22a1}}$, $\color{royalblue}{\textbf{227e}}$, 2258, 229e, 22a9, 22a5 |
| **Arctis Nova 7P** | ✅ |  | 220a, 22a7 |
<!-- STATS:DEVICES:END -->

> ✅ Fully supported · ⚠️ Config available, community testing welcome
> _Users column: anonymous reports from opted-in users. PIDs in blue confirmed by telemetry._

## Tested Distributions

<!-- STATS:TESTED_DISTROS:START -->
| Distribution | Install method | Users |
|---|---|---|
| CachyOS | 🎯 AUR | 👥 4 |
| Ubuntu 24.04.4 LTS | 🎯 PPA | 👥 1 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 1 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 1 |
<!-- STATS:TESTED_DISTROS:END -->

---

## Community Stats

> Anonymous usage data shared voluntarily by users who opted in.
> [View interactive dashboard →](https://loteran.github.io/Arctis-Sound-Manager/stats)

<!-- STATS:META:START -->
_Based on **7** anonymous data points — last updated 2026-05-01_
<!-- STATS:META:END -->

### Most used headsets

<!-- STATS:HEADSETS:START -->
| Headset | Installs |
|---|---|
| Arctis Nova Pro Wireless | 3 |
| Arctis Nova 7 (Gen 2) | 2 |
| Arctis 9 Wireless | 1 |
| Arctis Nova 7 (Gen 1) | 1 |
<!-- STATS:HEADSETS:END -->

### Most used Linux distributions

<!-- STATS:DISTROS:START -->
| Distribution | Installs |
|---|---|
| CachyOS | 4 |
| Ubuntu 24.04.4 LTS | 1 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 1 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 1 |
<!-- STATS:DISTROS:END -->

---

## Requirements

- Linux with **PipeWire ≥ 1.0** (+ `pipewire-pulse`, `wireplumber`)
- **Python 3.10+**
- System libraries: `libusb`, `libpulse`, `libudev`

The native packages on **Arch / CachyOS / Fedora / Debian / Ubuntu** declare every dep as a hard requirement — `paru -S` / `dnf install` / `apt install` pulls in everything ASM features rely on (LADSPA plugins for Spatial Audio + ClearCast mic noise suppression, `curl` for HRIR download, `wireplumber`, etc.) automatically. No optional follow-up `pacman -S` step.

If something later goes missing (manual `dnf remove`, immutable distro that didn't replay an upgrade, …), the **System Deps dialog** at GUI startup detects it and offers a one-click pkexec install — see [Features](#features).

**Other distros (source install)** — install `pipx` + system libraries:
```bash
# Debian / Ubuntu
sudo apt install pipx libusb-1.0-0 libpulse0 libudev1 swh-plugins noise-suppression-for-voice curl

# Fedora
sudo dnf install pipx libusb1 pulseaudio-libs systemd-libs ladspa-swh-plugins noise-suppression-for-voice curl
```

---

## Virtual Surround 7.1

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

Then run the post-install setup — this handles everything automatically:

```bash
asm-setup
```

`asm-setup` will:
- Write the desktop entry and systemd service file
- Install udev rules for USB device access (polkit popup for sudo)
- Download the HRIR file for virtual surround
- Install `filter-chain.service` automatically if not already provided by the system
- Enable and start all required systemd user services (`arctis-manager`, `arctis-video-router`, `filter-chain`)
- Restart PipeWire

### Fedora (COPR)

```bash
sudo dnf copr enable loteran/arctis-sound-manager
sudo dnf install arctis-sound-manager
```

Then run the post-install setup:

```bash
asm-setup
```

`asm-setup` will:
- Write the desktop entry and systemd service file
- Install udev rules for USB device access (polkit popup for sudo)
- Download the HRIR file for virtual surround
- Install `filter-chain.service` automatically (not shipped by default on Fedora)
- Enable and start all required systemd user services (`arctis-manager`, `arctis-video-router`, `filter-chain`)
- Restart PipeWire

### Debian / Ubuntu (PPA)

```bash
sudo add-apt-repository ppa:loteran/arctis-sound-manager
sudo apt update
sudo apt install arctis-sound-manager
```

Then run the post-install setup:

```bash
asm-setup
```

`asm-setup` will:
- Write the desktop entry and systemd service file
- Install udev rules for USB device access (polkit popup for sudo)
- Download the HRIR file for virtual surround
- Install `filter-chain.service` automatically (not shipped by default on Ubuntu)
- Enable and start all required systemd user services (`arctis-manager`, `arctis-video-router`, `filter-chain`)
- Restart PipeWire

> **Ubuntu 24.04 (Noble)** is the currently supported series. Other series may work via the `.deb` attached to each [GitHub release](https://github.com/loteran/Arctis-Sound-Manager/releases).

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
- Create desktop entries and the `arctis-manager.service` systemd user service file
- Enable the `arctis-manager` systemd user service (device daemon)
- Enable the `arctis-video-router` systemd user service (media auto-routing)
- Copy device configs to `~/.config/arctis_manager/devices/` (required for Sonar EQ mode switch)
- Download the default HRIR file for virtual surround (HeSuVi)
- Enable the `filter-chain` systemd user service (required for Sonar EQ and virtual surround)

After installation, launch the GUI from your application menu or run:
```bash
asm-gui
```

> **USB permissions**: ASM has two automatic recovery paths for udev:
> - If the rules file is missing or doesn't cover every PID, the GUI shows an **Install rules** dialog at startup → one click runs `asm-cli udev write-rules --force --reload` with a single pkexec prompt.
> - If the rules are correct but you plugged the headset in **before** they took effect (typical right after `paru -Syu` or `dnf upgrade`), the daemon detects the EACCES and the GUI offers an **Apply now** dialog → one click runs `asm-cli udev reload-rules`. No replug needed.
>
> Manual fallback if you want to do it from a terminal:
> ```bash
> sudo asm-cli udev write-rules --force --reload
> ```

---

## How the mixer works

The app creates 3 virtual audio sinks on top of your physical Arctis device, plus direct access to one external output of your choice:

| Sink | Default use | Button |
|---|---|---|
| **Arctis_Game** | Games, general audio | G |
| **Arctis_Chat** | Voice apps (Discord, TeamSpeak…) | C |
| **Arctis_Media** | Browsers, video players | M |
| **External Output** | Any non-Arctis sink — HDMI, USB speakers, sound card… (5.1 / 7.1 native) | O |

The **media router** (`asm-router`) runs as a background service and automatically moves browsers, video players and any orphan stream onto `Arctis_Media` when Arctis is the default sink. Manual placements you make in your system mixer (KDE / GNOME / pavucontrol) are detected and saved as persistent overrides — they take priority over the auto-routing.

To **manually move** an app stream, click the **G / C / M / O** buttons on its tag in the GUI. The choice is saved and respected even after the app restarts.

> **External Output note**: The Output card routes audio **directly** to the physical sink you pick, bypassing the virtual stereo sinks. This preserves true 5.1 / 7.1 channel output for games and movies on a TV, AV receiver, or external surround speakers.

### Configuring the external output

The Output card (O button) targets the device selected in **Settings → Audio → External Output Device**. ASM auto-detects every non-SteelSeries ALSA sink on your system and lists them in the dropdown — pick HDMI, your USB speakers (Logitech, etc.), or any sound card. No config file editing needed.

### Upgrading

**Arch / CachyOS / Manjaro (AUR):**
```bash
paru -Syu arctis-sound-manager
asm-setup
```

**Fedora (COPR):**
```bash
sudo dnf upgrade arctis-sound-manager
asm-setup
```

**Debian / Ubuntu (PPA):**
```bash
sudo apt update && sudo apt upgrade arctis-sound-manager
asm-setup
```

**From source:**
```bash
cd Arctis-Sound-Manager
git pull
export PATH="$HOME/.local/bin:$PATH"
pipx install --force .
asm-cli desktop write                       # refresh service file with the new binary path
asm-cli udev write-rules --force --reload  # refresh udev rules if devices changed
systemctl --user daemon-reload
systemctl --user restart arctis-manager.service
```

> **Note:** `pipx upgrade arctis-sound-manager` may fail if the package was installed from a temporary wheel (`Unable to parse package spec`). Use `pipx install --force .` from the repo directory instead.
>
> **PATH tip:** if `asm-cli` or `asm-daemon` are not found, add `~/.local/bin` to your PATH permanently: `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`

---

## Uninstall

Native packages (AUR / COPR / PPA) ship a real cleanup hook since v1.0.86 — `paru -R` / `dnf remove` / `apt remove` now removes `~/.config/pipewire/pipewire.conf.d/10-arctis-virtual-sinks.conf` and the chat/media/HeSuVi siblings as your real user, then restarts pipewire so the ghost Arctis sinks vanish immediately. Audio profiles in `~/.config/arctis_manager/profiles/` are **preserved by default** so a future reinstall picks them back up.

```bash
# Arch / CachyOS / Manjaro
paru -R arctis-sound-manager

# Fedora
sudo dnf remove arctis-sound-manager

# Debian / Ubuntu
sudo apt remove arctis-sound-manager
```

For source / pipx installs, or to wipe everything (including profiles + HRIR), use the standalone uninstaller — it auto-detects every install method present (rpm + pacman + apt + pipx + orphan binaries in `$PATH`) and lets you pick which one(s) to remove:

```bash
# Interactive: detects everything, prompts for each install method
bash scripts/uninstall.sh

# Or run remote (no clone needed):
curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/uninstall.sh | bash
```

Useful flags:

```bash
bash scripts/uninstall.sh --pipx          # only the pipx install
bash scripts/uninstall.sh --pkg           # only the distro package (rpm / pacman / apt)
bash scripts/uninstall.sh --all           # both, when you have duplicate installs
bash scripts/uninstall.sh --all --purge   # also wipe settings, PipeWire configs, HRIR,
                                          # user systemd units and the manually-written
                                          # /etc/udev/rules.d/91-steelseries-arctis.rules
                                          # — profiles are STILL preserved
bash scripts/uninstall.sh --yes           # non-interactive (skip confirmations)
```

`--purge` keeps `~/.config/arctis_manager/profiles/` and `.active_profile` so a future `pipx install arctis-sound-manager --force` (or AUR/COPR/PPA reinstall) immediately picks them back up. A separate confirm at the end offers to delete the profiles too if you want a true clean slate.

---

## Reporting a bug

Found something broken? Reports are very welcome — they directly drive fixes. Here's how to get the most useful info across quickly:

### 1. Use the in-app bug reporter (recommended)

Open ASM → **Help page** → **Report a Bug**. The dialog has:

- A **"Describe what happened"** field at the top — write the steps to reproduce and the expected vs actual behaviour. The text is prepended to both the issue body and the diagnostic file.
- A live preview of the **full diagnostic** that will be submitted: ASM version, Python lib versions (pulsectl / pyudev / dbus-next…), distro / kernel / desktop / session, install methods detected (catches duplicate rpm + pipx installs), USB HID devices, PipeWire cards / sinks, `wpctl status`, the udev rules content + verdict, the USB monitor backend, and the last 100 lines of `journalctl --user -u arctis-manager.service`.

Then click one of:

- **Submit automatically (gh CLI) ↗** — appears when `gh auth status` is configured. Uploads the full diagnostic as a secret GitHub gist, creates the issue with a link to it, and opens the new issue URL in your browser. Zero copy-paste.
- **Open GitHub issue ↗** (manual fallback) — saves the diagnostic to `~/.cache/arctis-sound-manager/reports/bug-report-YYYYMMDD-HHMMSS.md` and opens the GitHub editor with a short summary pre-filled. The "Open folder" button highlights the file so you can drag-and-drop it into the issue editor as an attachment.

### 2. Command-line equivalents (if the GUI won't start)

Two commands give you the same data without the dialog:

```bash
# Preflight checks: YAMLs, udev, PulseAudio/PipeWire, D-Bus, USB monitor, every
# system dep ASM relies on (LADSPA plugins, HRIR, filter-chain, …). Exits 0 if
# everything is green, 1 otherwise. Each missing dep prints a per-distro
# `sudo dnf|apt-get|pacman install …` hint. Safe to wire into systemd ExecStartPre.
asm-daemon --verify-setup

# Full local-only diagnostic dump (same content as the GUI's report).
# Nothing is sent anywhere; review it before pasting / attaching.
asm-cli diagnose -o /tmp/asm.txt
cat /tmp/asm.txt
```

For verbose logs in a specific run, set `ARCTIS_LOG_LEVEL`:

```bash
ARCTIS_LOG_LEVEL=debug systemctl --user restart arctis-manager
journalctl --user -u arctis-manager -f
```

### Tips for a good report

- **Describe what you expected vs. what happened** — "Game channel is silent after switching to Sonar EQ" is more useful than "audio broken"
- **Include steps to reproduce** — even something as simple as "open app → switch to Sonar → game audio disappears"
- **One issue per report** — if you have two problems, open two issues; it's much easier to track and close them separately

→ [Open a new issue](https://github.com/loteran/Arctis-Sound-Manager/issues/new)

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
│   ├── cli.py             # asm-cli: setup utilities (udev, desktop, tools)
│   └── setup.py           # asm-setup: post-install automation
├── gui/
│   ├── home_page.py            # Audio mixer (Game/Chat/Media/Output cards)
│   ├── headset_page.py         # Device info and live status
│   ├── device_page.py          # General settings (startup toggle, etc.)
│   ├── equalizer_page.py       # EQ mode toggle (Custom / Sonar) + 10-band sliders
│   ├── sonar_page.py           # Sonar EQ (Game/Chat/Micro tabs, presets, Spatial Audio, Boost)
│   ├── sonar_toggle_widget.py  # Sonar on/off toggle + YAML patch
│   ├── eq_curve_widget.py      # Interactive parametric EQ curve (biquad RBJ)
│   ├── anc_widget.py           # ANC / Transparent mode indicator
│   ├── settings_widget.py      # Per-device settings panel (D-Bus backed)
│   ├── profile_bar.py          # Profile chip bar + SaveProfileDialog (Home page)
│   ├── udev_dialog.py          # Startup dialog when udev rules are missing
│   ├── help_page.py            # Built-in user manual (EN/FR/ES)
│   ├── presets/                # 334 bundled Sonar presets (312 Game, 8 Chat, 14 Mic)
│   ├── components.py           # Reusable widgets
│   └── theme.py                # Color constants
├── profile_manager.py     # Audio profile: snapshot, save/load/apply, pulsectl volumes
├── sonar_to_pipewire.py   # PipeWire filter-chain config generator (Sonar EQ)
├── pw_utils.py            # Native PipeWire stream detection
├── pactl.py               # PulseAudio virtual sink management
├── udev_checker.py        # udev rules validation (used at GUI/daemon startup)
└── devices/               # Per-device configuration files (one YAML per headset)

scripts/
├── install.sh                              # Main installer (source installs)
├── setup-surround.sh                       # Standalone virtual surround setup
├── filter-chain.service                    # Bundled systemd service (auto-installed on distros that don't ship one)
├── pipewire/
│   ├── 10-arctis-virtual-sinks.conf           # PipeWire loopback sinks (Game/Chat/Media)
│   └── sink-virtual-surround-7.1-hesuvi.conf  # HeSuVi 7.1 virtual surround filter-chain
└── arctis-video-router.service             # Systemd service for asm-router
```
If you want to buy me a coffee ;) --> https://ko-fi.com/loteran

---

## 💬 Share your experience

Tried ASM on your headset or distro? Found a bug, have a feature idea, or just want to share how it's working for you?

**Join the discussion → [GitHub Discussions](https://github.com/loteran/Arctis-Sound-Manager/discussions)**

Your feedback helps improve compatibility for everyone — especially for headsets marked ⚠️ that are waiting for community reports.
