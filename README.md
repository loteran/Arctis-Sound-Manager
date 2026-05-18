# Arctis Sound Manager

[![Latest release](https://img.shields.io/github/v/release/loteran/Arctis-Sound-Manager)](https://github.com/loteran/Arctis-Sound-Manager/releases/latest)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)](https://github.com/loteran/Arctis-Sound-Manager)
[![Crowdin](https://badges.crowdin.net/arctis-sound-manager/localized.svg)](https://crowdin.com/project/arctis-sound-manager)

A Linux GUI for SteelSeries Arctis headsets — device settings, 4-channel audio mixer (Game / Chat / Media / Output), automatic media routing, and a full **Sonar EQ** system powered by PipeWire filter-chain.

> Based on [Arctis Sound Manager](https://github.com/elegos/Linux-Arctis-Manager) by elegos.

> 💬 **[Share your experience in Discussions](https://github.com/loteran/Arctis-Sound-Manager/discussions)** — feedback helps improve compatibility for everyone!
> ☕ [Buy me a coffee](https://ko-fi.com/loteran) if you find it useful!

---

## Table of contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Supported devices](#supported-devices)
- [Installation](#installation)
- [First launch](#first-launch)
- [Upgrading](#upgrading)
- [How the mixer works](#how-the-mixer-works)
- [Virtual surround 7.1](#virtual-surround-71)
- [Translations](#translations)
- [Community stats](#community-stats)
- [Uninstall](#uninstall)
- [Reporting a bug](#reporting-a-bug)
- [Development](#development)

---

## Features

### 🎚️ Audio mixer

- **4-channel mixer** — separate Game, Chat, Media and Output virtual sinks
- **Automatic media routing** — browsers and video players (Firefox, VLC, mpv…) are automatically routed to the Media sink
- **Smart stream adoption** — apps running when ASM starts are pulled into `Arctis_Media` instead of staying on a non-Arctis sink
- **Manual stream control** — move any audio stream between channels via G / C / M / O buttons; choices persist across restarts
- **True multichannel output** — route apps directly to HDMI or external output (5.1 / 7.1 native passthrough)
- **Volume sliders** per channel with live percentage display
- **Native PipeWire support** — detects apps that bypass PulseAudio (mpv, Haruna…)

### 🎛️ EQ & audio processing

- **Sonar EQ** — full SteelSeries Sonar-style parametric EQ (Game / Chat / Micro channels):
  - Interactive EQ curve, up to 10 bands per channel
  - 312 Game presets, 8 Chat, 14 Mic — searchable, 9 favorite slots
  - Macro sliders: Bass / Voice / Treble (±12 dB)
  - **Spatial Audio** — HeSuVi virtual 7.1 surround with Immersion (0–12 dB) and Distance (reverb) sliders
  - **Volume Boost** — up to +12 dB gain at the end of the filter chain
  - **Smart Volume** — dynamic compressor (Quiet / Balanced / Loud)
  - All changes applied live via PipeWire biquad nodes
- **Custom 10-band EQ** — per-band gain (31 Hz – 16 kHz), save/load presets
- **Audio Profiles** — save and restore your complete configuration in one click (EQ mode, presets, macro values, Spatial Audio, volumes); instant switching from the Home page or system tray

### 🎧 Device control

- **ANC / Transparent mode** — reflects the physical button state in real time
- **Device status** — battery, mic mute, sidetone, and more
- **DAC OLED display** _(Arctis Nova Pro Wireless / X)_:
  - Toggle original vs custom display mode
  - Brightness, screen timeout, scroll speed
  - Choose and reorder elements: Time, Battery, Profile, EQ Preset, Weather
  - Per-element font size (7–30 pt) · Built-in weather with °C / °F selector

### ⚙️ App & system

- **Self-healing deps** — at startup ASM checks every system component (LADSPA plugins, HRIR, PipeWire, wpctl, udev rules…); a one-click dialog installs anything missing with a single `pkexec` prompt
- **Check for updates** — in-app button forces an immediate GitHub check; installs via terminal (pacman / dnf / apt) or in-app wheel (pipx)
- **One-click bug reports** — auto-uploads a full diagnostic as a GitHub gist and opens a pre-filled issue
- **Built-in diagnostics** — `asm-daemon --verify-setup` and `asm-cli diagnose -o file.txt`
- **Community translations** — new languages from [Crowdin](https://crowdin.com/project/arctis-sound-manager) download automatically on startup, no release needed
- **Help page** — built-in manual in English, French and Spanish
- **`ARCTIS_LOG_LEVEL` env var** — `debug` / `info` / `warning`, honored by daemon, GUI and router

---

## Screenshots

| Home — mixer | Sonar EQ |
|---|---|
| ![Home](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_home.png) | ![Sonar](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_sonar.png) |

| Custom EQ | Settings |
|---|---|
| ![Equalizer](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_equalizer.png) | ![Settings](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_settings.png) |

| Headset status | DAC — OLED display control |
|---|---|
| ![Headset](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_headset.png) | ![DAC](https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/docs/images/screenshot_dac.png) |

---

## Supported devices

<!-- STATS:DEVICES:START -->
| Device | Working | Users | Product ID(s) |
|---|---|---|---|
| Arctis 1 / 7X / 7P Wireless |  |  | 12b3, 12b6, 12d5, 12d7 |
| Arctis 7 / 7 2019 / Pro 2019 / Pro GameDAC | ✅ | 1 | 1260, $\color{royalblue}{\textbf{12ad}}$, 1252, 1280 |
| Arctis 7+ / PS5 / Xbox / Destiny | ✅ | 1 | $\color{royalblue}{\textbf{220e}}$, 2212, 2216, 2236 |
| Arctis 9 Wireless | ✅ | 2 | $\color{royalblue}{\textbf{12c2}}$ |
| Arctis Pro Wireless | ✅ | 1 | $\color{royalblue}{\textbf{1290}}$, $\color{royalblue}{\textbf{1294}}$ |
| Arctis Nova Pro Wireless / X | ✅ | 15 | $\color{royalblue}{\textbf{12e0}}$, $\color{royalblue}{\textbf{12e5}}$ |
| Arctis Nova Pro Wired / Xbox Wired | ✅ |  | $\color{royalblue}{\textbf{12cb}}$, 12cd |
| Arctis Nova 3 | ✅ | 3 | $\color{royalblue}{\textbf{12ec}}$ |
| Arctis Nova 3P / 3X Wireless | ✅ | 3 | $\color{royalblue}{\textbf{2269}}$, 226d |
| Arctis Nova 5 / 5X | ✅ | 2 | $\color{royalblue}{\textbf{2232}}$, 2253 |
| Arctis Nova 7 Gen 1 | ✅ | 1 | $\color{royalblue}{\textbf{2202}}$, 2206, 223a, 227a, 22ab, 22a4 |
| Arctis Nova 7 Gen 2 | ✅ | 13 | $\color{royalblue}{\textbf{22a1}}$, $\color{royalblue}{\textbf{227e}}$, 2258, $\color{royalblue}{\textbf{229e}}$, 22a9, $\color{royalblue}{\textbf{22a5}}$ |
| Arctis Nova 7P | ✅ | 3 | 220a, $\color{royalblue}{\textbf{22a7}}$ |
| Arctis Nova Elite |  |  | 2244, 2249 |
<!-- STATS:DEVICES:END -->

> ✅ Confirmed by at least one opted-in user · PIDs in blue confirmed by telemetry

---

## Installation

All native packages (AUR / COPR / PPA) pull in every dependency automatically. After installing, run `asm-setup` once — it configures udev rules, systemd services, PipeWire and downloads the HRIR file.

<details>
<summary><strong>Arch Linux / CachyOS / Manjaro (AUR)</strong></summary>

```bash
paru -S arctis-sound-manager
asm-setup
```
</details>

<details>
<summary><strong>Fedora / Nobara (COPR)</strong></summary>

```bash
sudo dnf copr enable loteran/arctis-sound-manager
sudo dnf install arctis-sound-manager
asm-setup
```
</details>

<details>
<summary><strong>Debian / Ubuntu (PPA)</strong></summary>

```bash
sudo add-apt-repository ppa:loteran/arctis-sound-manager
sudo apt update && sudo apt install arctis-sound-manager
asm-setup
```

> Ubuntu 24.04 (Noble) is the currently supported series. Other series may work via the `.deb` in each [GitHub release](https://github.com/loteran/Arctis-Sound-Manager/releases).
</details>

<details>
<summary><strong>Immutable distros (Bazzite, SteamOS, Silverblue)</strong></summary>

ASM runs inside a [Distrobox](https://distrobox.it/) container and is exported transparently to the host. Run the script for your distro directly — no need to clone:

**Bazzite** (full deps including noise-suppression-for-voice):
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/distrobox/bazzite.sh)
```

**SteamOS / Steam Deck:**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/distrobox/steamos.sh)
```
> udev rules in `/etc/udev/rules.d/` are reset on major SteamOS updates — re-run the script after each upgrade.

**Fedora Silverblue / Kinoite:**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/distrobox/silverblue.sh)
```

Each script is idempotent. Flags: `--reinstall` (rebuild container), `--no-services` (skip systemd).
</details>

<details>
<summary><strong>Other distros (from source)</strong></summary>

Install system deps first:
```bash
# Debian / Ubuntu
sudo apt install pipx libusb-1.0-0 libpulse0 libudev1 swh-plugins noise-suppression-for-voice curl

# Fedora / Nobara
sudo dnf install pipx libusb1 pulseaudio-libs systemd-libs ladspa-swh-plugins curl
sudo dnf copr enable uriesk/noise-suppression-for-voice
sudo dnf install noise-suppression-for-voice
```

Then install ASM:
```bash
git clone --branch main https://github.com/loteran/Arctis-Sound-Manager.git
cd Arctis-Sound-Manager
bash scripts/install.sh
```

> **PATH tip:** if `asm-cli` or `asm-daemon` are not found after install, run:
> `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc`

> **USB permissions:** if the headset isn't detected, ASM shows a one-click **Install rules** dialog at startup. Manual fallback: `sudo asm-cli udev write-rules --force --reload`
</details>

---

## First launch

After `asm-setup` completes, launch the GUI with:

```bash
asm-gui
```

Or find **Arctis Sound Manager** in your application launcher (KDE, GNOME, etc.).

> The daemon (`asm-manager.service`) starts automatically at login — the GUI is separate and must be opened manually the first time.

### System tray mode

```bash
asm-gui --systray
```

Starts minimised to the system tray. From there you can switch Audio Profiles, open the full window, or quit. This is the recommended mode for daily use.

### Autostart at login

The easiest way is the in-app toggle: **Settings → General → Start with system** — ASM handles the correct setup for your desktop environment automatically.

If the toggle doesn't work for your DE, use the manual method:

<details>
<summary><strong>KDE Plasma / GNOME (systemd user session)</strong></summary>

```bash
systemctl --user enable --now asm-gui-tray.service
```

A `asm-gui-tray.service` unit is installed by `asm-setup`. If missing:

```bash
asm-cli desktop write
systemctl --user daemon-reload
systemctl --user enable --now asm-gui-tray.service
```
</details>

<details>
<summary><strong>Hyprland / Sway / other wlroots compositors</strong></summary>

Add to `~/.config/hypr/hyprland.conf` (or your compositor's startup config):

```ini
exec-once = asm-gui --systray
```
</details>

<details>
<summary><strong>i3 / bspwm / other X11 WMs</strong></summary>

Add to your WM startup file (`~/.config/i3/config`, `~/.xinitrc`, etc.):

```bash
asm-gui --systray &
```

Or create an XDG autostart entry:

```bash
asm-cli desktop write   # writes ~/.config/autostart/arctis-sound-manager.desktop
```
</details>

---

## Upgrading

<details>
<summary><strong>Arch / CachyOS / Manjaro</strong></summary>

```bash
paru -Syu arctis-sound-manager && asm-setup
```
</details>

<details>
<summary><strong>Fedora (COPR)</strong></summary>

```bash
sudo dnf upgrade arctis-sound-manager && asm-setup
```
</details>

<details>
<summary><strong>Debian / Ubuntu (PPA)</strong></summary>

```bash
sudo apt update && sudo apt upgrade arctis-sound-manager && asm-setup
```
</details>

<details>
<summary><strong>From source</strong></summary>

```bash
cd Arctis-Sound-Manager && git pull
pipx install --force .
asm-cli desktop write
asm-cli udev write-rules --force --reload
systemctl --user daemon-reload && systemctl --user restart arctis-manager.service
```

> `pipx upgrade` may fail on wheel installs — use `pipx install --force .` instead.
</details>

---

## How the mixer works

ASM creates 3 virtual sinks on top of your Arctis device plus one external output:

| Sink | Default use | Button |
|---|---|---|
| **Arctis_Game** | Games, general audio | G |
| **Arctis_Chat** | Voice apps (Discord, TeamSpeak…) | C |
| **Arctis_Media** | Browsers, video players | M |
| **External Output** | HDMI, USB speakers, sound card… (5.1 / 7.1 native) | O |

The **media router** (`asm-router`) automatically moves browsers and video players to `Arctis_Media`. Manual placements via G / C / M / O buttons are saved as persistent overrides.

The External Output card routes audio **directly** to the physical sink, bypassing virtual stereo sinks — this preserves true 5.1 / 7.1 passthrough. Configure it in **Settings → Audio → External Output Device**.

---

## Virtual surround 7.1

Virtual 7.1 surround is included automatically with the install — it deploys a PipeWire HeSuVi filter-chain and downloads the HRIR file. **57 HRIR profiles** are bundled and selectable from the **Settings** tab with instant apply.

```
                  ┌──────────────────────────┐
 7.1 audio source │  Virtual Surround Sink   │
 (game, movie…) ──►  (PipeWire filter-chain) ├──► Headset (stereo)
                  └──────────────────────────┘
```

> Also available as a **standalone repo**: [arctis-virtual-surround](https://github.com/loteran/arctis-virtual-surround) — install virtual surround independently with a single `bash install.sh`.

<details>
<summary>Manual setup (alternative)</summary>

```bash
bash scripts/setup-surround.sh
```

This installs the filter-chain config in `~/.config/pipewire/filter-chain.conf.d/`, downloads the HRIR file, and enables the `filter-chain` systemd user service.

Advanced: replace `~/.local/share/pipewire/hrir_hesuvi/hrir.wav` with any 14-channel HeSuVi-compatible WAV and restart filter-chain manually.
</details>

---

## Translations

[![Crowdin](https://badges.crowdin.net/arctis-sound-manager/localized.svg)](https://crowdin.com/project/arctis-sound-manager)

ASM supports community translations via [Crowdin](https://crowdin.com/project/arctis-sound-manager). No release is needed — the app checks GitHub on every startup and silently downloads any updated `.ini` files.

| Language | Status |
|---|---|
| English | Source (always complete) |
| Français | Bundled |
| Español | Bundled |
| _Your language?_ | [Contribute on Crowdin ↗](https://crowdin.com/project/arctis-sound-manager) |

**How it works:**
- Every day at 06:00 UTC, a GitHub Action pulls approved translations from Crowdin and opens a PR
- On each startup, ASM fetches updated files and saves them to `~/.config/arctis_manager/lang/`
- The language selector in Settings updates immediately — no restart needed
- Languages below **80% coverage** fall back string-by-string to English

To request a new language, open a [GitHub issue](https://github.com/loteran/Arctis-Sound-Manager/issues). See [CONTRIBUTING.md](CONTRIBUTING.md) for full details.

---

## Community stats

<!-- STATS:META:START -->
_Based on **43** unique users (**263** anonymous data points) — last updated 2026-05-18_
<!-- STATS:META:END -->

> Anonymous usage data shared voluntarily by opted-in users.
> [View interactive dashboard →](https://loteran.github.io/Arctis-Sound-Manager/stats)

<details>
<summary>Tested distributions</summary>

<!-- STATS:TESTED_DISTROS:START -->
| Distribution | Install method | Users |
|---|---|---|
| CachyOS | 🎯 AUR | 👥 20 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 5 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 3 |
| Artix Linux | 🎯 AUR | 👥 3 |
| Linux Mint 22.3 | 🎯 PPA | 👥 2 |
| Arch Linux | 🎯 AUR | 👥 2 |
| Ubuntu 26.04 LTS | 🎯 PPA | 👥 1 |
| Ubuntu 24.04.4 LTS | 🎯 PPA | 👥 1 |
| PikaOS 4 | 📦 Source | 👥 1 |
| Manjaro Linux | 🎯 AUR | 👥 1 |
| Garuda Linux | 🎯 AUR | 👥 1 |
| Fedora Linux 44 (Workstation Edition) | 🎯 COPR | 👥 1 |
| Fedora Linux 43 (Workstation Edition) | 🎯 COPR | 👥 1 |
| EndeavourOS | 🎯 AUR | 👥 1 |
<!-- STATS:TESTED_DISTROS:END -->
</details>

<details>
<summary>Most used headsets & distros</summary>

<!-- STATS:HEADSETS:START -->
| Headset | Installs |
|---|---|
| Arctis Nova Pro Wireless | 15 |
| Arctis Nova 7 (Gen 2) | 13 |
| Arctis Nova 3 | 3 |
| Arctis Nova 7P (Gen 2) | 3 |
| Arctis 9 Wireless | 2 |
| Arctis Nova 5 Wireless | 2 |
| Arctis 7+ | 1 |
| Arctis 7/Pro Gaming | 1 |
| Arctis Nova 5X (PID 2255) | 1 |
| Arctis Nova 7 (Gen 1) | 1 |
| Arctis Pro Wireless | 1 |
<!-- STATS:HEADSETS:END -->

<!-- STATS:DISTROS:START -->
| Distribution | Installs |
|---|---|
| CachyOS | 20 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 5 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 3 |
| Artix Linux | 3 |
| Linux Mint 22.3 | 2 |
| Arch Linux | 2 |
| Ubuntu 26.04 LTS | 1 |
| Ubuntu 24.04.4 LTS | 1 |
| PikaOS 4 | 1 |
| Manjaro Linux | 1 |
| Garuda Linux | 1 |
| Fedora Linux 44 (Workstation Edition) | 1 |
| Fedora Linux 43 (Workstation Edition) | 1 |
| EndeavourOS | 1 |
<!-- STATS:DISTROS:END -->
</details>

---

## Uninstall

Native packages ship a cleanup hook — `paru -R` / `dnf remove` / `apt remove` removes PipeWire configs and restarts pipewire automatically. Audio profiles in `~/.config/arctis_manager/profiles/` are **preserved** by default.

```bash
paru -R arctis-sound-manager        # Arch / CachyOS / Manjaro
sudo dnf remove arctis-sound-manager  # Fedora
sudo apt remove arctis-sound-manager  # Debian / Ubuntu
```

For source/pipx installs or a full wipe, use the standalone uninstaller:

```bash
bash scripts/uninstall.sh           # interactive, auto-detects install method
# or without cloning:
curl -fsSL https://raw.githubusercontent.com/loteran/Arctis-Sound-Manager/main/scripts/uninstall.sh | bash
```

<details>
<summary>Uninstall flags</summary>

```bash
--pipx          # only the pipx install
--pkg           # only the distro package
--all           # both (for duplicate installs)
--all --purge   # also wipe PipeWire configs, HRIR, systemd units, udev rules
--yes           # non-interactive
```

`--purge` still preserves `~/.config/arctis_manager/profiles/`. A final prompt offers to delete them too for a true clean slate.
</details>

---

## Reporting a bug

### In-app reporter (recommended)

Open ASM → **Help page** → **Report a Bug**. The dialog collects a full diagnostic (version, libs, distro, PipeWire state, USB devices, udev rules, last 100 log lines) and either:

- **Submits automatically** via `gh` CLI — uploads as a secret gist and opens a pre-filled issue
- **Opens GitHub** manually — saves the report to `~/.cache/arctis-sound-manager/reports/` for drag-and-drop

### CLI equivalents

```bash
asm-daemon --verify-setup           # preflight checks, exits 0/1 with per-distro install hints
asm-cli diagnose -o /tmp/asm.txt    # full local diagnostic dump

ARCTIS_LOG_LEVEL=debug systemctl --user restart arctis-manager
journalctl --user -u arctis-manager -f
```

### Tips for a good report

- Describe **expected vs actual behaviour** — "Game channel silent after switching to Sonar EQ" beats "audio broken"
- Include **steps to reproduce**
- One issue per report

→ [Open a new issue](https://github.com/loteran/Arctis-Sound-Manager/issues/new)

---

## Development

```bash
python src/arctis_sound_manager/scripts/daemon.py          # daemon
python src/arctis_sound_manager/scripts/gui.py --no-enforce-systemd  # GUI
python src/arctis_sound_manager/scripts/video_router.py    # media router
```

<details>
<summary>Project structure</summary>

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
│   ├── device_page.py          # General settings (startup toggle, language)
│   ├── equalizer_page.py       # EQ mode toggle (Custom / Sonar) + 10-band sliders
│   ├── sonar_page.py           # Sonar EQ (Game/Chat/Micro tabs, presets, Spatial Audio, Boost)
│   ├── eq_curve_widget.py      # Interactive parametric EQ curve (biquad RBJ)
│   ├── anc_widget.py           # ANC / Transparent mode indicator
│   ├── settings_widget.py      # Per-device settings panel (D-Bus backed)
│   ├── profile_bar.py          # Profile chip bar + SaveProfileDialog
│   ├── help_page.py            # Built-in user manual (EN/FR/ES)
│   ├── presets/                # 334 bundled Sonar presets (312 Game, 8 Chat, 14 Mic)
│   └── theme.py                # Color constants
├── lang/                  # Translation files (.ini, one per language)
├── lang_updater.py        # Background GitHub translation checker
├── i18n.py                # Translation singleton with EN fallback
├── profile_manager.py     # Audio profile: snapshot, save/load/apply
├── sonar_to_pipewire.py   # PipeWire filter-chain config generator
├── oled_renderer.py       # OLED screen image renderer (PIL)
├── oled_manager.py        # OLED scroll/animation loop
└── devices/               # Per-device configuration YAMLs

scripts/
├── install.sh             # Main installer (source installs)
├── distrobox/             # Distrobox installers (Bazzite, SteamOS, Silverblue)
├── setup-surround.sh      # Standalone virtual surround setup
└── pipewire/              # PipeWire config templates
```
</details>

---

## 💬 Share your experience

Tried ASM on your headset or distro? Found a bug or have a feature idea?

**[Join GitHub Discussions →](https://github.com/loteran/Arctis-Sound-Manager/discussions)**

Your feedback helps improve compatibility for everyone — especially for headsets not yet confirmed working.
