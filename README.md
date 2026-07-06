<p align="center">
  <img src="docs/images/asm-logo.png" alt="Arctis Sound Manager logo" width="150">
</p>

<h1 align="center">Arctis Sound Manager</h1>

<p align="center"><a href="https://ko-fi.com/W7W31VXIVC"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="ko-fi"></a></p>


[![Latest release](https://img.shields.io/github/v/release/loteran/Arctis-Sound-Manager)](https://github.com/loteran/Arctis-Sound-Manager/releases/latest)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)](https://github.com/loteran/Arctis-Sound-Manager)
[![Crowdin](https://badges.crowdin.net/arctis-sound-manager/localized.svg)](https://crowdin.com/project/arctis-sound-manager)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2?logo=discord&logoColor=white)](https://discord.gg/f7CcrFHFA)
[![ASM Presets](https://img.shields.io/badge/EQ%20Presets-Community%20site-FB4A00)](https://loteran.github.io/asm-presets/)

A Linux GUI for SteelSeries Arctis headsets — device settings, 4-channel audio mixer (Game / Chat / Media / Output), automatic media routing, and a full **Sonar EQ** system powered by PipeWire filter-chain.

**Supported Arctis headsets on Linux:** Arctis Nova Pro Wireless, Arctis Nova Pro Wired, Arctis Nova Pro Omni, Arctis Nova Elite, Arctis Nova 7 (Gen 1 & Gen 2), Arctis Nova 7P, Arctis Nova 5 / 5X, Arctis Nova 3 / 3P / 3X, Arctis 7, Arctis 7+, Arctis 9 Wireless, Arctis Pro Wireless, Arctis 1 / 7X / 7P Wireless, and Arctis GameBuds / GameBuds X — full list with Product IDs in [Supported devices](#supported-devices).

> 🎚️ **[ASM Presets](https://loteran.github.io/asm-presets/)** — browse and share community EQ presets!
> 💬 **[Join the Discord](https://discord.gg/f7CcrFHFA)** — chat, share presets and get help from the community!
> 📝 **[Share your experience in Discussions](https://github.com/loteran/Arctis-Sound-Manager/discussions)** — feedback helps improve compatibility for everyone!


---

## Table of contents

- [Arctis Sound Manager](#arctis-sound-manager)
  - [Table of contents](#table-of-contents)
  - [Features](#features)
    - [🎚️ Audio mixer](#️-audio-mixer)
    - [🎛️ EQ \& audio processing](#️-eq--audio-processing)
    - [🎧 Device control](#-device-control)
    - [⚙️ App \& system](#️-app--system)
  - [Screenshots](#screenshots)
  - [Supported devices](#supported-devices)
  - [Installation](#installation)
  - [First launch](#first-launch)
    - [System tray mode](#system-tray-mode)
    - [Autostart at login](#autostart-at-login)
  - [Upgrading](#upgrading)
  - [How the mixer works](#how-the-mixer-works)
  - [Sharing and downloading EQ presets](#sharing-and-downloading-eq-presets)
    - [Exporting a preset](#exporting-a-preset)
    - [Importing a preset](#importing-a-preset)
    - [Community site — ASM Presets](#community-site--asm-presets)
  - [Virtual surround 7.1](#virtual-surround-71)
  - [Themes](#themes)
  - [Translations](#translations)
  - [Community stats](#community-stats)
  - [Uninstall](#uninstall)
  - [Reporting a bug](#reporting-a-bug)
    - [In-app reporter (recommended)](#in-app-reporter-recommended)
    - [CLI equivalents](#cli-equivalents)
    - [Tips for a good report](#tips-for-a-good-report)
  - [Development](#development)
  - [Project structure](#project-structure)
  - [💬 Share your experience](#-share-your-experience)
  - [Contributors](#contributors)

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
- **Themes** — 5 built-in color themes plus a custom theme editor with live preview and `.ini` import/export ([details](#themes))
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
| Arctis 1 / 7X / 7P Wireless | ✅ | 3 | $\color{royalblue}{\textbf{12b3}}$, 12b6, 12d5, $\color{royalblue}{\textbf{12d7}}$ |
| Arctis 7 / 7 2019 / Pro 2019 / Pro GameDAC | ✅ | 8 | 1260, $\color{royalblue}{\textbf{12ad}}$, 1252, 1280 |
| Arctis 7+ / PS5 / Xbox / Destiny | ✅ | 10 | $\color{royalblue}{\textbf{220e}}$, $\color{royalblue}{\textbf{2212}}$, 2216, 2236 |
| Arctis 9 Wireless | ✅ | 7 | $\color{royalblue}{\textbf{12c2}}$ |
| Arctis Pro Wireless | ✅ | 19 | $\color{royalblue}{\textbf{1290}}$, $\color{royalblue}{\textbf{1294}}$ |
| Arctis Nova Pro Wireless / X | ✅ | 109 | $\color{royalblue}{\textbf{12e0}}$, $\color{royalblue}{\textbf{12e5}}$, $\color{royalblue}{\textbf{225d}}$ |
| Arctis Nova Pro Wired / Xbox Wired | ✅ | 11 | $\color{royalblue}{\textbf{12cb}}$, $\color{royalblue}{\textbf{12cd}}$ |
| Arctis Nova Pro Omni | ✅ | 4 | $\color{royalblue}{\textbf{2290}}$ |
| Arctis Nova 3 | ✅ | 3 | $\color{royalblue}{\textbf{12ec}}$ |
| Arctis Nova 3P / 3X Wireless | ✅ | 11 | $\color{royalblue}{\textbf{2269}}$, $\color{royalblue}{\textbf{226d}}$ |
| Arctis Nova 5 / 5X | ✅ | 22 | $\color{royalblue}{\textbf{2232}}$, $\color{royalblue}{\textbf{2253}}$, $\color{royalblue}{\textbf{2255}}$, $\color{royalblue}{\textbf{2264}}$ |
| Arctis Nova 7 Gen 1 | ✅ | 19 | $\color{royalblue}{\textbf{2202}}$, $\color{royalblue}{\textbf{2206}}$, 223a, 227a, 22ab, $\color{royalblue}{\textbf{22a4}}$ |
| Arctis Nova 7 Gen 2 | ✅ | 47 | $\color{royalblue}{\textbf{22a1}}$, $\color{royalblue}{\textbf{227e}}$, 2258, $\color{royalblue}{\textbf{229e}}$, 22a9, $\color{royalblue}{\textbf{22a5}}$ |
| Arctis Nova 7P | ✅ | 13 | $\color{royalblue}{\textbf{220a}}$, $\color{royalblue}{\textbf{22a7}}$ |
| Arctis Nova Elite | ✅ | 6 | $\color{royalblue}{\textbf{2244}}$, 2249, $\color{royalblue}{\textbf{2270}}$ |
| Arctis GameBuds / GameBuds X | ✅ | 6 | $\color{royalblue}{\textbf{230a}}$, $\color{royalblue}{\textbf{2317}}$ |
<!-- STATS:DEVICES:END -->

> ✅ Confirmed by at least one opted-in user
> 🔵 Product IDs shown in **blue** are actively in use — detected on real users' devices through the app's anonymous usage stats
> 🔴 Seen in telemetry but not yet declared in a device YAML — support pending

**Find your headset's Product ID** (the 4 hex digits after `1038:`):

```bash
lsusb -d 1038:
```

If your PID isn't listed above, ASM doesn't recognise your headset yet — here's how to get it added.

<details>
<summary><b>My headset isn't recognised — how to add it (create the device YAML)</b></summary>

ASM describes each headset with a small YAML "device profile". Adding a new headset means creating (or extending) one of these. Many "new" headsets are just an existing model with a fresh Product ID (e.g. a new colorway) — in that case adding the single PID is enough.

**1. Collect your device's USB details**

```bash
lsusb -d 1038:                         # your PID = the 4 hex digits after "1038:"
lsusb -v -d 1038:XXXX > lsusb.log      # full interface/endpoint layout (replace XXXX with your PID)
asm-cli diagnose -o asm-diagnose.txt   # ASM's view of the device
```

**2. New variant of a model already in the table?**

Open the matching profile in `~/.config/arctis_manager/devices/` (e.g. `nova_elite.yaml`) and add your PID under `product_ids:`:

```yaml
product_ids:
  - 0x2244
  - 0x2270   # ← your PID
```

Then restart the daemon and test:

```bash
systemctl --user restart arctis-manager.service
```

If the device controls respond, it works. If the daemon log shows `Failed to find interface`, your command interface differs: set `command_interface_index: [<interface>, <alt_setting>]` to the HID interface from your `lsusb -v` that carries the 64-byte interrupt endpoint (often `[3, 0]`).

> ℹ️ Edits under `~/.config/arctis_manager/devices/` are perfect for testing, but may be overwritten on update — do step 3 to make support permanent.

**3. Open an issue so it ships for everyone**

[Open an issue](https://github.com/loteran/Arctis-Sound-Manager/issues/new) and attach `lsusb.log` + `asm-diagnose.txt` (and mention whether the quick edit above worked). Brand-new models can need their HID control protocol reverse-engineered from a Windows USB capture, but the `lsusb` layout is enough to get most variants added in the next release.

</details>

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

**Troubleshooting — text displays as blocks (font rendering issue)**

If the ASM interface shows only squares instead of text, your fontconfig cache is likely corrupted. Run inside the Distrobox container:
```bash
rm -rf ~/.cache/fontconfig/*
fc-cache -f -v
```
Then restart ASM. *(Thanks to [H0DG3](https://github.com/H0DG3) for identifying this fix!)*
</details>

<details>
<summary><strong>NixOS (flakes or classic)</strong></summary>

ASM ships a first-class NixOS module (`nix/module.nix`) that wires up udev rules, systemd user services, PipeWire filter-chain, and all LADSPA plugins automatically. The module replaces `asm-setup`, so you normally don't need to run it — but since v1.1.68 it is safe to do so (the crash on the missing `update-desktop-database` tool has been removed), which is useful for the HRIR repair described below.

**Flake-based config** — add to your `flake.nix`:

```nix
inputs.arctis-sound-manager = {
  url = "github:loteran/Arctis-Sound-Manager?dir=nix";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

Then in your `nixosSystem` modules:

```nix
inputs.arctis-sound-manager.nixosModules.default

{
  services.pipewire = { enable = true; alsa.enable = true; pulse.enable = true; };
  services.arctis-sound-manager.enable = true;
}
```

**Classic config** (flakes disabled):

```bash
git clone https://github.com/loteran/Arctis-Sound-Manager.git /etc/nixos/Arctis-Sound-Manager
```

In `/etc/nixos/configuration.nix`:

```nix
imports = [ /etc/nixos/Arctis-Sound-Manager/nix/module.nix ];
services.pipewire = { enable = true; alsa.enable = true; pulse.enable = true; };
services.arctis-sound-manager.enable = true;
```

Then rebuild and replug the headset once so udev applies the new rules:

```bash
sudo nixos-rebuild switch
```

> **⚠️ Spatial Audio first run — required for Game & Media audio:** open ASM → **Settings → Spatial Audio** and pick any HRIR profile. This copies the selected WAV to `~/.local/share/pipewire/hrir_hesuvi/hrir.wav` (profiles are bundled, no download needed). **Without this step, the Game and Media channels are silent** — the filter-chain convolver fails to load and only the Chat channel (which has no convolver) produces audio.

> **Stub HRIR file (installs older than v1.1.68):** the HRIR file shipped in the Nix store is a read-only 3.6 KB stub (epoch timestamp) that leaves Game and Media silent. Since v1.1.68, `asm-setup` detects undersized HRIR files and replaces them automatically. On an older install, repair it manually:
>
> ```bash
> rm ~/.local/share/pipewire/hrir_hesuvi/hrir.wav && asm-setup
> ```

> **KDE audio popup:** the small "Audio Volume" widget in the KDE taskbar only shows physical hardware sinks. The ASM virtual channels (Arctis_Game / Arctis_Chat / Arctis_Media) appear in `pavucontrol` → Playback tab or in KDE System Settings → Audio → Applications. Use those to route each app to the right channel.

Full module docs and options: [`nix/README.md`](nix/README.md).
</details>

<details>
<summary><strong>Other distros (from source)</strong></summary>

Install system deps first:
```bash
# Debian / Ubuntu
sudo apt install pipx libusb-1.0-0 libpulse0 libudev1 swh-plugins curl
# noise-suppression-for-voice (rnnoise LADSPA) is not packaged for Debian/Ubuntu —
# ASM builds it from source automatically when you enable ClearCast mic suppression.

# Fedora / Nobara
sudo dnf install pipx libusb1 pulseaudio-libs systemd-libs ladspa-swh-plugins curl
sudo dnf copr enable lkiesow/noise-suppression-for-voice
sudo dnf install ladspa-realtime-noise-suppression-plugin
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

> The daemon (`arctis-manager.service`) starts automatically at login — the GUI is separate and must be opened manually the first time.

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
systemctl --user enable --now arctis-gui.service
```

An `arctis-gui.service` unit is installed by `asm-setup`. If missing:

```bash
asm-cli desktop write
systemctl --user daemon-reload
systemctl --user enable --now arctis-gui.service
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

## Sharing and downloading EQ presets

ASM lets you share any Sonar EQ preset with one click and import presets shared by the community.

### Exporting a preset

1. Open the **Sonar EQ** tab and select or create a preset.
2. Click the **Share** button (export icon) next to the preset name.
3. Choose how to share:
   - **Copy link** — copies an `arctis-asm://import?data=…` deep link to your clipboard.
   - **Save as file** — saves a `.json` file you can send directly.
   - **Publish to community** — opens a browser form pre-filled with your preset data; sign in with GitHub and submit.

### Importing a preset

1. Open the **Sonar EQ** tab.
2. Click **Import preset** (import icon).
3. Paste one of the supported link formats, then click **Import**:
   - **ASM deep link** — `arctis-asm://import?data=…` (generated by ASM's Share button)
   - **SteelSeries community link** — `https://www.steelseries.com/deeplink/gg/sonar/config/v1/import?url=…` (links from the SteelSeries community or GG software)

ASM automatically detects the link format, downloads the preset if needed, and saves it locally.

> You can also double-click an `arctis-asm://` link in a browser if you registered the URL handler with `asm-setup`.

### Community site — ASM Presets

**[loteran.github.io/asm-presets](https://loteran.github.io/asm-presets/)** — browse, vote for, and download EQ presets shared by the community.

| Action | How |
|---|---|
| Browse presets | Open the site — no login required |
| Filter by channel / device | Use the search bar and dropdowns |
| Import a preset into ASM | Click **Import in ASM** on any card |
| Vote for a preset | Click the ♡ button (requires GitHub login) |
| Publish a preset | Click **Share a Preset**, sign in with GitHub, fill in the form |
| Delete your preset | Click 🗑 on your own preset card (only visible when logged in) |

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

## Themes

ASM ships **5 built-in color themes**: SteelSeries (default), Aurora Glass, Neon Pulse, Slate Premium and Arctic. Switch themes from the dropdown in **Settings → Interface Theme** — the change applies live to every page, dialog and the tray menu, no restart needed.

### Custom themes

Click **Create theme** in Settings to open the theme editor:

- **15 color pickers** grouped into Backgrounds, Accents, Text and Audio channels
- **Live preview** — the whole app restyles as you pick colors
- Start from scratch or duplicate any existing theme via **Duplicate from**

User themes are saved as `.ini` files in `~/.config/arctis-sound-manager/themes/`.

### Import / Export

- **Export theme** — saves any theme as an `.ini` file you can share
- **Import theme** — loads a shared `.ini` file and adds it to your theme list

> Built-in themes cannot be edited or deleted — duplicate one into a custom theme instead.

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
_Based on **301** unique users (**58259** anonymous data points) — last updated 2026-07-06_
<!-- STATS:META:END -->

> Anonymous usage data shared voluntarily by opted-in users.
> [View interactive dashboard →](https://loteran.github.io/Arctis-Sound-Manager/stats)

<details>
<summary>Tested distributions</summary>

<!-- STATS:TESTED_DISTROS:START -->
| Distribution | Install method | Users |
|---|---|---|
| CachyOS | 🎯 AUR | 👥 139 |
| Arch Linux | 🎯 AUR | 👥 53 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 20 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 13 |
| Linux Mint 22.3 | 🎯 PPA | 👥 12 |
| Ubuntu 26.04 LTS | 🎯 PPA | 👥 9 |
| Ubuntu 24.04.4 LTS | 🎯 PPA | 👥 6 |
| Pop!_OS 24.04 LTS | 🎯 PPA | 👥 6 |
| Garuda Linux | 🎯 AUR | 👥 6 |
| Fedora Linux 44 (Workstation Edition) | 🎯 COPR | 👥 6 |
| Manjaro Linux | 🎯 AUR | 👥 5 |
| Bazzite | 📦 Source | 👥 4 |
| NixOS 26.11 (Zokor) | 📦 Source | 👥 3 |
| EndeavourOS | 🎯 AUR | 👥 3 |
| Artix Linux | 🎯 AUR | 👥 3 |
| SteamOS | 📦 Source | 👥 2 |
| NixOS 26.05 (Yarara) | 📦 Source | 👥 2 |
| Ubuntu 25.10 | 🎯 PPA | 👥 1 |
| SUSE Linux Enterprise Server for SAP Applications 12 SP2 | 📦 Source | 👥 1 |
| Russian Nuclear Submarine Ballast Management Controller BMC (OpenBMC Project Reference Distro) 2.8.2-34 | 📦 Source | 👥 1 |
| PikaOS 4 | 📦 Source | 👥 1 |
| Fedora Linux 43 (Workstation Edition) | 🎯 COPR | 👥 1 |
| Fedora Linux 43 (KDE Plasma Desktop Edition) | 🎯 COPR | 👥 1 |
| Fedora Linux 43 (Cinnamon) | 🎯 COPR | 👥 1 |
| Fedora Linux 41 (Workstation Edition) | 🎯 COPR | 👥 1 |
| Debian GNU/Linux 13 (trixie) | 🎯 PPA | 👥 1 |
<!-- STATS:TESTED_DISTROS:END -->
</details>

<details>
<summary>Most used headsets & distros</summary>

<!-- STATS:HEADSETS:START -->
| Headset | Installs |
|---|---|
| Arctis Nova Pro Wireless | 109 |
| Arctis Nova 7 (Gen 2) | 47 |
| Arctis Nova 5 Wireless | 22 |
| Arctis Pro Wireless | 19 |
| Arctis Nova 7 (Gen 1) | 19 |
| Arctis Nova 3 | 14 |
| Arctis Nova Pro Wired | 11 |
| Arctis Nova 7P (Gen 2) | 10 |
| Arctis 7+ | 10 |
| Arctis 7/Pro Gaming | 8 |
| Arctis 9 Wireless | 7 |
| Arctis Nova Elite | 6 |
| Arctis GameBuds | 5 |
| Arctis Nova Pro Omni | 4 |
| Arctis Nova 7P (Gen 1) | 3 |
| Arctis 1/7X/7P Wireless | 3 |
| Arctis GameBuds X | 1 |
<!-- STATS:HEADSETS:END -->

<!-- STATS:DISTROS:START -->
| Distribution | Installs |
|---|---|
| CachyOS | 139 |
| Arch Linux | 53 |
| Nobara Linux 43 (KDE Plasma Desktop Edition) | 20 |
| Fedora Linux 44 (KDE Plasma Desktop Edition) | 13 |
| Linux Mint 22.3 | 12 |
| Ubuntu 26.04 LTS | 9 |
| Ubuntu 24.04.4 LTS | 6 |
| Pop!_OS 24.04 LTS | 6 |
| Garuda Linux | 6 |
| Fedora Linux 44 (Workstation Edition) | 6 |
| Manjaro Linux | 5 |
| Bazzite | 4 |
| NixOS 26.11 (Zokor) | 3 |
| EndeavourOS | 3 |
| Artix Linux | 3 |
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

---

## Project structure

<details>
<summary><strong>Show project structure</strong></summary>

```
src/arctis_sound_manager/
│
├── scripts/                       # Entry points (installed as CLI commands)
│   ├── daemon.py                  # asm-daemon  — device manager service
│   ├── gui.py                     # asm-gui     — graphical interface
│   ├── video_router.py            # asm-router  — automatic media routing
│   ├── cli.py                     # asm-cli     — udev / desktop / diagnose tools
│   └── setup.py                   # asm-setup   — post-install automation
│
├── gui/                           # PySide6 user interface
│   ├── main_app.py                # Main window + sidebar navigation
│   ├── home_page.py               # Audio mixer (Game / Chat / Media / Output cards)
│   ├── sonar_page.py              # Sonar EQ — presets, Spatial Audio, macro sliders
│   ├── equalizer_page.py          # Custom EQ mode toggle + 10-band sliders
│   ├── headset_page.py            # Device info and live status (battery, mic, ANC)
│   ├── device_page.py             # App settings (language, startup, update check)
│   ├── dac_page.py                # GameDAC OLED control (clock, weather, brightness)
│   ├── help_page.py               # Built-in user manual (EN / FR / ES)
│   ├── theme.py                   # Theme engine — built-in + user themes, QSS
│   ├── theme_editor_page.py       # Custom theme editor with live preview
│   ├── eq_curve_widget.py         # Interactive parametric EQ curve (biquad RBJ)
│   ├── sonar_toggle_widget.py     # Sonar ↔ custom EQ toggle + spatial audio switch
│   ├── anc_widget.py              # ANC / Transparent mode control
│   ├── profile_bar.py             # Audio profile quick-switch bar
│   ├── systray_app.py             # System tray icon + single-instance IPC
│   ├── tray_eq_presets.py         # Tray menu EQ preset switcher
│   ├── status_widget.py           # Headset status widget (battery, mic, sidetone)
│   ├── settings_widget.py         # Settings panel (theme, language, paths)
│   ├── preset_import_dialog.py    # Preset import (ASM link / SteelSeries link / file)
│   ├── preset_export_dialog.py    # Preset export (copy link / save file / publish)
│   ├── preset_share.py            # Preset sharing helpers (deep link encode/decode)
│   ├── report_dialog.py           # One-click bug report (gist + GitHub issue)
│   ├── system_deps_dialog.py      # Self-healing deps check dialog
│   ├── install_dialogs.py         # Package install confirmation dialogs
│   ├── udev_dialog.py             # udev rules install / reload dialog
│   ├── first_run_dialog.py        # First-run wizard (udev, services, HRIR)
│   ├── telemetry_dialog.py        # Anonymous telemetry opt-in dialog
│   ├── dbus_wrapper.py            # Synchronous D-Bus client (daemon ↔ GUI bridge)
│   ├── components.py              # Reusable UI components (cards, sliders, buttons)
│   ├── ui_utils.py                # UI helpers (DPI scaling, icon loading)
│   ├── base_app.py                # Shared QApplication base
│   ├── main_app_proto_widget.py   # Prototype widget base for pages
│   └── presets/                   # Bundled Sonar presets (Game / Chat / Mic)
│
├── core.py                        # CoreEngine — USB lifecycle, polling, device init
├── config.py                      # Device YAML parsing + settings definitions
├── constants.py                   # XDG-compliant path constants (config, data, lang)
├── device_state.py                # Shared physical-node names for audio routing
├── dbus_service.py                # D-Bus interfaces (Settings / Status / Config)
├── loopback_manager.py            # Dynamic pw-loopback sinks (Arctis_Game/Chat/Media)
├── sonar_to_pipewire.py           # PipeWire filter-chain (EQ) config generator
├── pw_utils.py                    # Native PipeWire stream management + routing overrides
├── pactl.py                       # PulseAudio / PipeWire sink and stream helpers
├── service_control.py             # Single entry point for systemctl / dinitctl
├── profile_manager.py             # Audio profiles — snapshot, save, load, apply
├── settings.py                    # Persistent settings (JSON, XDG-compliant)
├── preset_sync.py                 # EQ preset sync (cloud ↔ local)
├── eq_types.py                    # EQ band data types (EqBand, BandType…)
├── oled_renderer.py               # OLED image renderer (PIL)
├── oled_protocol.py               # OLED HID framing (per-device interface / report id)
├── oled_manager.py                # OLED scroll / animation loop + weather clock
├── weather_service.py             # Weather fetch for the OLED (Open-Meteo)
├── hrir_catalog.py                # HRIR profile catalog (57 bundled profiles)
├── bug_reporter.py                # Diagnostic collector for bug reports
├── diagnose.py                    # asm-cli diagnose — full system diagnostic dump
├── system_deps_checker.py         # Startup self-healing dependency checker
├── udev_rules.py                  # udev rule generation (SteelSeries USB rules)
├── udev_checker.py                # udev rule presence / content verifier
├── update_checker.py              # GitHub release update checker
├── lang_updater.py                # Online translation updater (GitHub → ~/.config)
├── telemetry.py                   # Anonymous opt-in usage telemetry
├── autostart.py                   # Autostart management (systemd / XDG / DE-specific)
├── init_system.py                 # Init system detection (systemd vs dinit)
├── systemd.py                     # systemd user unit helpers
├── status_parser_fn.py            # USB HID status frame parser (battery, mic, ANC…)
├── usb_devices_monitor.py         # USB hotplug monitor (pyudev)
├── cli_tools.py                   # Shared CLI utilities
├── log_setup.py                   # Logging configuration (ARCTIS_LOG_LEVEL)
├── i18n.py                        # Translation singleton with EN fallback
├── utils.py                       # Misc helpers
├── lang/                          # Bundled translation files (.ini, one per language)
└── devices/                       # Per-device configuration YAMLs

scripts/
├── install.sh                     # Source installer
├── uninstall.sh                   # Interactive uninstaller (pipx / pkg / all)
├── setup-surround.sh              # Standalone virtual surround setup
├── distrobox/                     # Distrobox scripts (Bazzite, SteamOS, Silverblue)
├── pipewire/                      # PipeWire config templates
└── reverse-engineering/           # USB capture + opcode-decode helpers (new devices)

nix/                               # NixOS module
├── module.nix                     # NixOS module (udev, systemd, filter-chain, LADSPA)
├── package.nix                    # Nix derivation
└── README.md                      # NixOS-specific documentation
```
</details>

---

## 💬 Share your experience

Tried ASM on your headset or distro? Found a bug or have a feature idea?

**[Join GitHub Discussions →](https://github.com/loteran/Arctis-Sound-Manager/discussions)**

Your feedback helps improve compatibility for everyone — especially for headsets not yet confirmed working.

---

## Contributors

Thanks to everyone who has contributed code, fixes, or community support:

| Contributor | Contribution |
|---|---|
| [Michsior14](https://github.com/Michsior14) | Fix Arctis 7+ power status recognition ([#67](https://github.com/loteran/Arctis-Sound-Manager/pull/67)) |
| [H0DG3](https://github.com/H0DG3) | Identified the fontconfig cache fix for font rendering on Bazzite/Distrobox |
| [cookiekiller](https://github.com/cookiekiller) | Diagnosed the loopback watchdog orphan-recreation bug ([#84](https://github.com/loteran/Arctis-Sound-Manager/issues/84)); detailed analysis and fix suggestions for per-channel EQ coupling, Discord routing override, and weather log spam ([#85](https://github.com/loteran/Arctis-Sound-Manager/issues/85)) |

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

The majority of the source code is original work — Copyright (C) 2026 loteran.

25 files are partially derived from [Linux Arctis Manager](https://github.com/elegos/Linux-Arctis-Manager/) by Giacomo Furlan (elegos), used under GPL-3.0 — Copyright (C) 2022 Giacomo Furlan (elegos).
