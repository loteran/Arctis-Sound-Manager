# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.78] - 19 June 2026

### Added

- **3 new Sonar EQ presets from SteelSeries GG 113.0.0** — *Pure*, *Precision* and *Immersion* from the official SteelSeries GG 113.0.0 preset list are now bundled for the Game, Chat and Media channels.

### Fixed

- **Per-channel EQ apply no longer interrupts the sibling channel** — applying a preset or sliders on the Game channel used to restart both the Game **and** Media loopbacks (and vice-versa), causing a ~1.5 s audio cut on the channel that was not touched. The apply-worker now calls the new `RecreateLoopbackSingle` D-Bus method to restart only the loopback for the channel that was actually edited. Chat remains unaffected as before. (issue #85 — thanks @cookiekiller!)
- **`weather_*` settings no longer spam `[ERROR] Unknown general setting configuration`** — `weather_enabled`, `weather_location`, `weather_lat`, `weather_lon`, `weather_units` and `weather_city_display` are stored in `GeneralSettings` but had no matching `ConfigSetting` entry, so every `SetSetting` D-Bus call hit the unknown-key branch. They are now handled as a dedicated special case before the whitelist lookup. (issue #85)
- **Routing override for Discord now matches `application.process.binary` as fallback** — `reapply_routing_overrides` previously matched only on `application.name`; Discord's Electron renderer sets this to `"WEBRTC VoiceEngine"` rather than `"Discord"`. The matcher now falls back to `application.process.binary` when `application.name` does not match, so a `"Discord": "Arctis_Chat"` entry in `routing_overrides.json` is respected on first connection and after every filter-chain restart. (issue #85)

## [1.1.77] - 18 June 2026

### Fixed

- **Loopback watchdog recreated loopbacks every 5 s, breaking Discord routing** — `_loopback_watchdog` treated a `None` link target (loopback not yet wired by WirePlumber) as a mislink and immediately recreated the loopback. This caused Discord's audio streams to lose their target sink every 5–10 seconds and repeatedly fall back to the system default, ignoring any `routing_overrides` entries. A grace period of 3 consecutive ticks (15 s) is now applied before acting on a `None` link: transient PipeWire graph states are ignored, while genuinely orphaned loopbacks (stuck unlinked for > 15 s) are still recovered. (#84)

## [1.1.76] - 17 June 2026

### Fixed

- **Update banner persisted after upgrading via package manager** — the "update available" banner on the Home page was never hidden when a re-check found no newer version: `on_update_available` returned early on an empty result without calling `_update_banner.hide()`. The banner would therefore remain visible for the rest of the session even after the user updated externally (apt/pacman/dnf). Now the banner is explicitly hidden when the check confirms the installed version is current. Additionally, the manual "Check for updates" button in the Settings page now also drives the Home page banner (via `DevicePage.sig_update_result`), so clicking "Check for updates" after an external upgrade instantly clears the stale notification without requiring a restart.

## [1.1.75] - 16 June 2026

### Fixed

- **Arctis Nova Pro Omni — battery display showed values above 1000%** — the Omni firmware reports headset and GameDAC battery as a direct 0–100 percentage value, but `nova_pro_omni.yaml` had `perc_max: 8` (inherited from the Nova Pro Wireless which uses a 0–8 discrete scale). The formula `raw × 100 ÷ 8` turned a 92% charge into 1150% and a 98% charge into 1225%. Setting `perc_max: 100` for both `headset_battery_charge` and `charge_slot_battery_charge` corrects the display.

## [1.1.74] - 13 June 2026

### Added

- **Headset name on Channels tab** — the connected headset model name is now displayed above the status pills. If the headset is offline, the last known name is shown in gray.
- **Ko-fi support button in sidebar** — a compact Ko-fi button and "You like it ? Support me !" label appear at the bottom of the sidebar, below the ASM logo.
- **ASM logo in sidebar** — the logo is displayed above the support section and links to the GitHub repository when clicked.

### Changed

- **Help moved into main navigation** — the Help button is now part of the primary sidebar nav (below Settings) instead of floating at the bottom.
- **EQ mode switch button inline** — the "Switch to …" button on the Equalizer tab is now compact and placed on the same line as the current mode label.
- **"Check for updates" moved to top of Settings** — the button is now in the header row alongside the language selector.
- **Sidebar icons slightly reduced** — icon size reduced from 50 px to 44 px for a more compact sidebar.

### Fixed

- **Chat channel silent after filter-chain restart** — the loopback watchdog now also recreates a loopback when its playback node is completely unlinked (orphaned), not only when it is linked to the wrong target. Previously, `Arctis_Chat_sink_out` could end up with no PipeWire link at all after a filter-chain restart, causing permanent silence on the Chat channel until a manual service restart.
- **Discord loses Chat routing after filter-chain restart** — `recreate_loopbacks_game_media` now skips recreating the Chat loopback (keeping `Arctis_Chat` alive in Discord's device list), and `reapply_routing_overrides` restores saved PA stream routing after any Chat recreation.

## [1.1.73] - 13 June 2026

### Fixed

- **USB autosuspend not disabled for already-connected dongles** — `udevadm trigger` does not retroactively apply sysfs attributes to USB interfaces that are already active when the udev rule is installed or updated. Devices plugged in before the update kept autosuspend enabled, causing `[Errno 19] No such device` disconnects every 2–3 minutes and the GUI to never show the headset as Connected. After `udevadm trigger`, ASM now also writes `on` directly to `/sys/bus/usb/devices/*/power/control` for every connected SteelSeries device via sysfs, taking effect immediately without requiring a replug. (#80)
- **Switching an app back to Output (HDMI) channel silenced audio** — clicking G/C/M in the mixer called `pw-metadata 0 default.configured.audio.sink` to change the PipeWire global default sink. This caused the `effect_input.sonar-output-eq` filter-chain node to re-target its output to the newly chosen virtual sink instead of HDMI; switching back to O routed the stream into the EQ chain whose physical output had been silently redirected. The global default must never be changed for per-app routing — `pulse.sink_input_move()` alone is sufficient and is now the only call made.
- **`NameError: TEXT_SECONDARY` crash after successful preset import via link** — `_set_status()` in the import dialog used the bare constant `TEXT_SECONDARY` instead of `_theme.c("TEXT_SECONDARY")`, crashing the dialog after every successful link import at the status update step.
- **`"Invalid or unsupported link: 'data'"` when importing an ASM JSON preset file** — ASM-exported JSON files have the format `{"parametricEQ": {…}}` with no top-level `"data"` key, but `_finalize_import()` unconditionally accessed `info["data"]`, raising `KeyError: 'data'` which was caught and displayed as the link error. The file importer now detects the format and wraps raw preset data automatically.

## [1.1.72] - 13 June 2026

### Added

- **Line Out controls for Nova Pro Wireless and Nova Elite** — the physical 3.5 mm LINE OUT jack on the GameDAC Gen 2 station is now configurable directly from the ASM DAC tab. A new **Station Output** section appears above the OLED options and is automatically hidden for headsets without a station DAC. The section contains:
  - **Line Out Mode** button group: *Speaker* (amplified output for passive speakers) or *Stream* (digital controlled output).
  - **Left / Right / Aux volume** sliders (0–100 %) — visible only when mode is set to *Stream*, using a single multi-value HID command `[0x47, left, right, aux]` per adjustment.
- **OLED display support for Nova Elite** — the GameDAC Gen 2 station screen (custom time, battery, EQ preset, weather, etc.) is now activated for the Nova Elite. Parameters match the Nova Pro Wireless (interface 4, report_id `0x06`, wvalue `0x0300`, 128×64 px) as both use the same hardware; a USB capture from a Nova Elite user will confirm if adjustments are needed.

### Fixed

- **Nova Elite settings invisible in GUI** — all 10 settings using `type: discrete_map` (ANC mode, EQ preset, screensaver, line out, Bluetooth auto-mute, home screen view/options) were silently skipped by the config loader because `discrete_map` is not a valid `SettingType`. All corrected to `button_group`, which is the standard type used by every other YAML for this pattern.
- **Nova Elite YAML contained speculative protocol values** — the command prefix (`0x06`), command interface (`[3, 0]`), status request (`0x06b0`), and response mappings were copied from the Nova Pro Wireless without hardware verification. All values have been rewritten from a real USB capture (elegos/Linux-Arctis-Manager upstream v2.4.1): prefix `0x01`, interface `[0, 3]`, 46-command `device_init`, 23 response mapping entries, correct `headset_battery_charge` range (0–100 instead of 0–8).
- **OledManager activated on Nova Elite with wrong parameters** — adding `gamedac` to the Nova Elite status representation triggered `OledManager` using Nova Pro Wireless defaults (wrong interface/wvalue), which could send frames to the wrong USB endpoint. `OledManager` now requires an explicit `oled:` section in the device YAML to activate.

### Fixed (Theme system)

- **`$XDG_CONFIG_HOME` not respected** — all user-data paths in `constants.py` (settings, lang, device configs, systemd/dinit service folders) and the theme storage path in `theme.py` were hardcoded to `~/.config`. They now check `$XDG_CONFIG_HOME` first, matching the XDG Base Directory spec and fixing config resolution for NixOS users and anyone with a custom home layout.
- **Saving a theme could crash the GUI** — `save_user_theme()` called `mkdir` and `open` with no error handling; a `PermissionError` (e.g. read-only home on NixOS, NFS mount) propagated uncaught into the Qt slot and terminated the GUI. I/O errors are now caught, logged, and surfaced to the user as a `QMessageBox.critical` dialog instead.
- **Theme load errors silently swallowed at startup** — the module-level `reload_user_themes()` call was wrapped in `except Exception: pass`, hiding any startup failure. It now logs a `WARNING` with a full traceback so problems are visible in `journalctl`.

### Fixed (NixOS)

- **Nix flake eval error after v1.1.69** — the `lib.optionalAttrs { … } // { … }` expression in `module.nix` was missing enclosing parentheses, causing the attribute-set merge (`//`) to be parsed as a top-level expression and the flake to fail evaluation entirely. (#79, PR #83 — thanks @Svenum!)
- **`lib.mkForce` required for `LADSPA_PATH` on filter-chain** — nixpkgs' pipewire module already sets `LADSPA_PATH` on the filter-chain service, silently overriding the plain assignment added in v1.1.69. The value is now set with `lib.mkForce` to guarantee it takes effect. (#79, PR #83 — thanks @Svenum!)
- **GUI crash on Nix with `ModuleNotFoundError: theme_editor_page`** — `theme_editor_page.py` (Theme Editor page) was never committed to git. Any Nix build — which fetches only tracked files — therefore produced a package missing this module, crashing `asm-gui` at startup with an `ImportError`. File is now committed. (#79)

### Changed

- **Multi-value `update_sequence` support** — `CoreEngine._resolve_update_sequence()` now resolves `'settings.<name>'` tokens in addition to `'value'` and raw integers, enabling a single slider change to send a complete multi-parameter HID command referencing the current values of sibling settings.
- **Conditional widget visibility** — `QSettingsWidget` gains `_apply_conditional_visibility()`, evaluated after each panel refresh and each user interaction. Settings with `visible_when: {key: value}` are automatically shown or hidden in real time based on other settings in the same section (e.g. stream volume sliders appear only when Line Out Mode is *Stream*).

## [1.1.71] - 13 June 2026

### Added

- **Arctis Nova Pro Wireless X (PID `0x225d`)** — third hardware variant of the Nova Pro Wireless X, identified in the upstream elegos/Linux-Arctis-Manager project. Uses the same protocol as the existing `0x12e0`/`0x12e5` variants; all features (EQ, ANC, mic, surround) work identically.

## [1.1.70] - 13 June 2026

### Fixed

- **Device shown as "No device detected" in GUI for always-connected headsets** — devices without an `online_status` block (e.g. Arctis Nova 3 family) produced an empty D-Bus status dict even after the daemon had successfully initialised them, causing the GUI to display "No device detected" indefinitely. A sentinel `headset_power_status: online` is now injected when `_device_ready` is True and no status representation is defined. (#80)
- **"Failed to find sink Arctis_Game" error at startup** — `redirect_audio` was called immediately after spawning the pw-loopback processes, before PipeWire had registered the virtual sink. The method now retries up to 5 times (× 0.4 s) before giving up, eliminating the race condition on normal hardware. The final failure is now logged as WARNING instead of ERROR since the sink may legitimately be absent if loopback creation failed upstream. (#80)

## [1.1.69] - 13 June 2026

### Fixed

- **Game and Media channels silent on NixOS module installs** — two root causes addressed:
  1. `LADSPA_PATH` was not injected on the `filter-chain` user service when `services.pipewire.extraLadspaPackages` was used (available on newer nixpkgs). `extraLadspaPackages` only sets the path on `pipewire.service` and `wireplumber`, not on the separate `filter-chain` process, so `plate_1423` (Distance reverb) was not found and the entire HeSuVi surround graph failed to load. `LADSPA_PATH` is now always set explicitly on `filter-chain`. (#79)
  2. No HRIR file was seeded on a fresh module-only install: `asm-setup` is not run, the daemon defaults `hrir_id` to null, and the GUI HRIR picker only copies the tiny bundled stubs. A new `arctis-firstrun-seed` oneshot systemd user service (ordered before `filter-chain` and `arctis-manager`) now copies `EAC_Default.wav` from the package store into `~/.local/share/pipewire/hrir_hesuvi/hrir.wav` if absent, and creates the `.setup_done` sentinel. The HRIR WAV is now shipped in the Nix package closure. (#79, contributed by @nobodys-tools)
- **HeSuVi surround graph crashes when `plate_1423` (swh-plugins) is absent** — on distros without `swh-plugins` installed, the Distance reverb LADSPA node was generated unconditionally, causing `filter-chain` to exit immediately. The Distance effect is now silently disabled when `plate_1423.so` is not found in the standard LADSPA directories, giving working surround without the reverb rather than a crash-loop. (#79)

## [1.1.68] - 12 June 2026

### Fixed

- **`asm-setup` crashed on NixOS with `FileNotFoundError: update-desktop-database`** — NixOS and other immutable distros don't ship this tool. The call already used `check=False` but `subprocess.Popen` raises `FileNotFoundError` before the return-code check when the binary is absent. The call is now wrapped in `try/except FileNotFoundError` and silently skipped. (#79)
- **HRIR file from Nix store (3.6 KB stub) passed validation and was never replaced** — the Nix store copy has a valid RIFF header in its first 4 bytes but is far too short to be a usable 7.1 impulse response, causing Game and Media channels to be silent. `_hrir_valid()` now requires the file to exceed 10 KB in addition to having a RIFF header. `asm-setup` also tries the bundled `hrir_assets/` profiles first (no network required) before downloading from GitHub. (#79)

## [1.1.67] - 12 June 2026

### Fixed

- **Microphone monitor source selected instead of real mic** — on some setups PipeWire exposes a monitor source before the real capture source, causing ASM to route apps to the monitor loopback instead of the physical microphone. The physical-source lookup now filters out `device.class = monitor` entries. (#80)
- **Mic audio bypassed Sonar EQ** — applications could connect directly to the ALSA source and skip the `sonar-micro-eq` filter chain. ASM now sets `priority.session = 1010` on the Sonar virtual source and explicitly sets it as the default PulseAudio source at startup, so apps land on the EQ pipeline by default.
- **D-Bus status returned `{}` when device was connected but status bytes unresolved** — if the device was ready but `device_status` was `None`, the GUI showed "No device detected" even though the headset was online. The D-Bus `GetStatus` method now synthesises an online sentinel from `online_status.online_value` in that case.
- **USB autosuspend disconnected dongle every 2–3 minutes** — the generated udev rules now include `ATTR{power/control}="on"` for all supported devices, preventing the kernel from suspending the USB dongle. (#80)
- **Arctis Pro Wireless not detected — wrong response byte mapping** — the YAML `starts_with` values matched the request bytes (`0x41aa`, `0x40aa`) instead of the real 32-byte HID response. Hardware capture confirmed `byte[0] = 0x04` = online, `byte[0] = 0x02` = offline, `byte[1]` = battery (0–4). Both mapping entries are corrected. (#81)
- **Arctis Pro Wireless — audio sinks and mic not found** — the device uses PID `0x1290` for HID but `0x1294` for the ALSA audio card. `_discover_physical_nodes` searched only the HID PID, so sinks and source fell back to vendor-only matching. The fix searches all PIDs listed in `product_ids`. (#81)

## [1.1.66] - 12 June 2026

### Fixed

- **HRIR selection silently failed on NixOS (and any read-only source)** — `apply_hrir_choice` used `shutil.copy2` which preserves source file permissions and mtime. Since HRIR WAVs live in the Nix store (read-only, epoch timestamp), the copied `hrir.wav` was also read-only; any subsequent profile selection in Settings → Spatial Audio would fail silently and the convolver would load an invalid file, leaving the Game and Media channels silent while Chat (which bypasses the convolver) still worked. Fixed by unlinking the destination before copying and using `shutil.copy` (no metadata). (#79)
- **Uninstaller failed on Bazzite / immutable distros when run via `curl | bash`** — the Distrobox delegation path resolved `SCRIPT_DIR` from `BASH_SOURCE[0]`, which is stdin when piped through curl. The local `scripts/distrobox/uninstall.sh` was never found and the script exited with an error. Now fetches both `_common.sh` and `distrobox/uninstall.sh` to a temporary directory and delegates from there when the local path is unavailable.

### Documentation

- Added a dedicated **NixOS** installation section to the README covering both flake and classic (non-flake) paths, with a prominent warning that **Settings → Spatial Audio must be opened on first launch** to select an HRIR profile — without this step the Game and Media channels are silent.

## [1.1.65] - 10 June 2026

### Fixed

- **OLED splash screen persisted over custom display on devices without a live PipeWire session** — the refresh loop slept for the full 5 s `REFRESH_INTERVAL` even when the 3 s splash had already expired, leaving a ~2 s gap where the device firmware reclaimed the OLED and showed its native UI alongside ASM's splash content. The loop now wakes immediately at splash expiry so the first custom-display frame is pushed without delay. (#76)
- **`No interrupt OUT endpoint` warning flooded the journal every 2 s** — for devices without an interrupt OUT endpoint on their command interface (e.g. Arctis 1 Wireless), ASM correctly falls back to HID SET_REPORT but was logging a WARNING on every status poll. The warning is now emitted once per device attach; subsequent occurrences are downgraded to DEBUG. The flag is reset on teardown so the warning reappears on reconnect. (#74)
- **`recreate_loopbacks` storm when `filter-chain.service` crashed repeatedly** — if the SteelSeries Sonar filter-chain service crashed and restarted every few seconds (common in Distrobox), each `pw-loopback` exit triggered a D-Bus `RecreateLoopbacks` call, causing a rapid stop/start cycle with no net benefit. Calls are now debounced to at most once per 5 s; legitimate Sonar ↔ simple mode switches are unaffected. (#74)

## [1.1.64] - 10 June 2026

### Added

- **Multi-theme support** — ASM now ships 5 built-in colour themes (SteelSeries, Dark, Light, Ocean, Forest). The active theme is persisted in settings and can be switched live from the device page without restarting the application; every page, dialog and tray menu follows the change immediately.
- **Enhanced bug report diagnostics — PipeWire / container runtime** — the automatic bug report now collects the data needed to diagnose audio-routing failures in Distrobox and other container environments: `$PIPEWIRE_RUNTIME_DIR`, `$PULSE_SERVER`, container type (Distrobox / Flatpak / Snap / Docker / native), status of `pipewire`, `pipewire-pulse`, `wireplumber` and `filter-chain` user services, Arctis-filtered PipeWire nodes from `pw-dump` (with `pactl` fallback), PipeWire sources, and the last journal entries for `pipewire` and `filter-chain`. A missing or empty Arctis-nodes section in a report is now a direct indicator that PipeWire cannot see the device — eliminating the back-and-forth follow-up that issue #74 required.

## [1.1.63] - 9 June 2026

### Fixed

- **Arctis Nova Pro Wired — OLED screen never updated and the daemon spammed `OLED USB error: [Errno 110] Operation timed out`** — the GameDAC Gen 2 firmware validates the HID SET_REPORT `wValue` strictly, but ASM sent report id 0 in its low byte, so the base station rejected every OLED transfer. The `wValue` is now built as `(report_type << 8) | report_id` (0x0306 for image frames, 0x0206 for brightness/return-to-UI), matching the protocol proven by working third-party tools. (#76)
- **Arctis Nova Pro Wired — headset not detected after boot/login until audio activity (e.g. joining a call) woke the device** — a device already plugged in at startup fires no udev `add` event, so a single failed initial scan was never retried. The daemon now re-scans periodically until the device is fully configured instead of waiting for a hotplug or USB wake event. (#76)
- **Arctis Nova Pro Wired — audio near-inaudible unless volume was cranked to ~95%** — control-transfer device commands carried the same incorrect `wValue`, so the high-gain initialisation never reached the GameDAC. Commands now embed the correct report id in the `wValue` for devices that require it, opt-in per device so the Nova 7 family and others are unaffected. (#76)

## [1.1.62] - 9 June 2026

### Fixed

- **Font rendering on Bazzite/Linux — all text displayed as blocks** — Qt's QSS parser does not support the CSS Level 4 `system-ui` keyword; it treated it as a literal font name, found nothing, and fell back to a font rendering all glyphs as tofu squares. Replaced with `sans-serif`, which Qt resolves correctly via fontconfig on all Linux distributions.

## [1.1.61] - 8 June 2026

### Added

- **Automatic Sonar preset sync** — ASM now downloads new SteelSeries Sonar presets automatically at startup (once per 24 h). A  lists all official presets; the new  fetches it, detects missing presets, and downloads them silently in background to .
- **3 new Sonar game presets**: Forza Horizon 6, Lineage 2, Deep Rock Galactic: Rogue Core.
- **GitHub Action** —  auto-regenerates  whenever preset files are pushed to  or .

### Fixed

- **GPL-3.0 licence headers** — added missing headers to all 26 files in  and ; corrected inline format in .

### Changed

- **Preset file naming** — removed 52 duplicate preset files using the old  separator convention; the canonical  variant is kept in all cases.

## [1.1.60] - 8 June 2026

### Fixed

- **Fedora COPR install failed with a `python3-dbus-next` file conflict** — the RPM bundled the `dbus_next` module, which collides with Fedora's own `python3-dbus-next` package (shipped since F40). ASM now bundles `dbus-next` only on distros whose repos don't provide it, and depends on the system package otherwise: Fedora ≥ 40 use `python3-dbus-next`, older Fedora still bundle, and the Debian/Ubuntu build auto-detects availability per series. (#73)

## [1.1.59] - 7 June 2026

### Fixed

- **Arctis Nova Pro Omni: gain and mic sidetone now work** — re-analysing the USB capture by opcode showed the Omni uses opcode `0x18` for sidetone (not `0x39`, which it ignores) and shifted gain values (`low=0x00`/`high=0x01` instead of `0x01`/`0x02`). The device profile is corrected accordingly. (#70)

### Added

- **Arctis Nova Pro Omni: OLED screen support** — the OLED driver is now parameterised per device (HID interface, report id, SET_REPORT wValue, dimensions) instead of being hard-coded for the Nova Pro Wireless. The Omni's screen is driven on interface 3 with report id `0x01` / wValue `0x0200`, which also stops the `OLED Resource busy` errors it was logging. The Nova Pro Wireless path is unchanged.

## [1.1.58] - 6 June 2026

### Fixed

- **Arctis Nova Pro Omni controls had no effect** — a real Windows USB capture of SteelSeries GG driving the Omni (firmware 1.32.0) revealed its HID control commands lead with report id `0x01`, not `0x06` (Nova Pro Wireless) nor `0x07` (previously inferred from third-party OLED drivers). The device profile now leads every command with `0x01` and polls status with `0x01b0`; the per-setting opcodes are byte-for-byte identical to the Nova Pro Wireless. Gain, mic, sidetone, EQ, ANC and the other controls should now take effect on the Omni. (#70)

## [1.1.57] - 6 June 2026

### Fixed

- **OLED weather (and all custom-display content) could vanish after the DAC re-enumerated** — the Arctis Nova Pro Wireless re-enumerates on the USB bus at boot, wake and replug. The daemon overwrote its libusb handle without releasing the previous one, so the stale handle kept the interface claimed and every later transfer failed with `EBUSY` (Resource busy), blanking the custom OLED display and blocking device commands. The handle is now fully released (`dispose_resources`) before re-claiming on same-device re-enumeration.
- **"Show city" weather toggle on the DAC tab could not be turned off** — `oled_show_weather_city` had no matching configuration entry, so the D-Bus `SetSetting` call was rejected and the toggle snapped back to on. Added the missing entry.
- **Vertical OLED scroll no longer triggers when the content essentially fits** — the layout height was over-counted (separator double-count plus excess weather padding); the natural height now mirrors the real render and a small scroll deadzone was added, so a near-full screen stays still instead of jittering.

### Changed

- **OLED EQ-mode badge** — the Sonar / Custom-EQ indicator next to the clock is now a bold "S" / "C" without the surrounding box.
- **Per-channel output dropdown** — the default (follow-system) option is now labelled "Headset" instead of "Default".

### Added

- **Reverse-engineering tooling** — a Windows USB capture script and a SteelSeries capture parser under `scripts/reverse-engineering/` to help decode opcodes for new DACs (Nova Pro Omni).

## [1.1.56] - 6 June 2026

### Fixed

- **Arctis Nova Pro Omni controls** — the Omni (`0x2290`) now has its own device profile targeting the correct USB layout: control interface `3` (the 64-byte HID endpoint) with HID `SET_REPORT` transport. In 1.1.55 it reused the Nova Pro Wireless profile (interface `4` with an interrupt-OUT endpoint the Omni does not expose), so the UI appeared but the controls had no effect. DAC command opcodes are still assumed identical to the Nova Pro Wireless, pending confirmation.

## [1.1.55] - 6 June 2026

### Added

- **Arctis Nova Pro Omni support** — added product ID `0x2290` to the Arctis Nova Pro Wireless device profile. Audio routing (Game/Chat/Media mixer, HeSuVi spatial surround, Sonar EQ) and HID controls are enabled by reusing the Nova Pro Wireless DAC protocol. The status protocol is assumed identical and not yet confirmed by a USB capture, so some reported status values may be inaccurate until verified.

## [1.1.54] - 3 June 2026

### Added

- **Arctis GameBuds / GameBuds X support** — initial device support for the Arctis GameBuds (PS5/PC, `0x230a`) and GameBuds X for Xbox (`0x2317`) via their 2.4 GHz USB dongle. Supported controls: mic volume, sidetone (Off/Low/Medium/High), noise cancelling mode (Off/Transparency/ANC), ANC/transparency level, volume limiter, wear sense, and auto power-off (up to 90 min). Protocol reverse-engineered from a USB capture; battery level and connection status are not yet available (status protocol unknown).

## [1.1.53] - 2 June 2026

### Added

- **Left-click the tray icon to open the window** — a single left-click on the Arctis Sound Manager system-tray icon now opens the main window (launching it the first time, raising and focusing it afterwards). Previously the window could only be reached through the tray's right-click context menu. Right-click still shows the context menu.

## [1.1.52] - 2 June 2026

### Fixed

- **Microphone filter-chain crashed PipeWire when ClearCast AI noise cancellation was enabled (#69)** — the rnnoise LADSPA plugin (`librnnoise_ladspa`) aborts when it is fed buffers at a sample rate it was not built for, taking down the whole `filter-chain` service (`pipewire -c filter-chain.conf`) and leaving the system with no audio until reboot. The mic filter-chain is now pinned to 48 kHz — the rate rnnoise expects — so the plugin always receives valid buffers.
- **Periodic ~1 s microphone dropouts in apps using echo cancellation (#68)** — Discord/Teams/Steam WebRTC echo cancellation drifted against the virtual mic source's clock and reset roughly once per second, silencing the mic for a split second at a steady cadence. Pinning the mic filter-chain to 48 kHz and locking its quantum (`node.lock-quantum`) removes the resampling and clock drift, so app-side echo cancellation can stay enabled without stuttering.

## [1.1.51] - 2 June 2026

### Fixed

- **Arctis 7+ reported as offline while actually connected** — the power-status parser only recognised a single "online" byte (`0x02`), but the Arctis 7+ firmware reports several online sub-states (`0x00`, `0x02`, `0x03`). When the headset was in any of the unmatched states it was treated as powered off, so online detection and audio redirection failed. The parser now enumerates all online states explicitly via `int_str_mapping` (thanks @Michsior14 — #67).

### Security

- Bumped **pygments** to 2.20.0 (fixes a low-severity ReDoS advisory in the GUID regex) and **pytest** to 9.0.3 (dev dependency).

## [1.1.50] - 2 June 2026

### Added

- **Arctis Nova 5X (PID `0x2255`) support** — the 0x2255 variant is now declared in the device YAML, so it is detected and managed like the other Nova 5/5X models. Its udev rule is generated automatically at build time.
- **Unmapped PIDs are surfaced in red in the README device table** — any Product ID reported by telemetry but not yet declared in a device YAML is now listed automatically in red, so it can be triaged and promoted into a YAML. The list self-cleans: a PID drops out once it lands in a YAML. The device table also gained an `lsusb -d 1038:` snippet so users can find their own Product ID.

## [1.1.49] - 1 June 2026

### Fixed

- **RNNoise mic noise-suppression dependency failed to install on Ubuntu (#65)** — the plugin is not packaged for Ubuntu or its derivatives (Linux Mint, Pop!_OS, elementary, KDE neon), yet ASM suggested an apt package that has no candidate there. Those distros now build the LADSPA plugin from source in one click (Install / Copy cmd), and the dependency is no longer treated as blocking — the rest of ASM works without it. Debian keeps the apt package; Fedora and Arch are unchanged. The "Copy cmd" button also now preserves command quoting correctly.

### Changed

- On the DAC OLED, the EQ-mode badge (**S** = Sonar, **C** = Custom EQ) is now shown to the right of the clock instead of on its own line.

## [1.1.48] - 31 May 2026

Hardening release from a full code audit (no new features).

### Fixed

- **Microphone Background / Impact noise reduction could not be turned off** — same `Qt.CheckState` truthiness bug as the Spatial Audio toggle in 1.1.47; the OFF state is now registered correctly.
- **GUI could crash (SIGABRT) on a quick EQ-mode toggle or when applying a preset from the tray** — the background apply thread could be destroyed while still running. Thread cleanup now waits for the thread to fully stop (same class as the Smart Volume slider crash fixed in 1.1.47).
- **The daemon froze for up to several seconds while reloading its configuration** (e.g. on `ReloadConfigs` / loopback recreation) because blocking device discovery ran on the asyncio event loop. That work is now offloaded to a worker thread, keeping the daemon responsive.

### Changed

- The daemon no longer imports any GUI/Qt code: `EqBand` and the filter labels moved to a Qt-free `eq_types` module, so the background service is lighter and headless-safe.
- Added timeouts to `systemctl`/`dinitctl`/`udevadm` calls so a degraded init system can no longer hang the autostart/setup paths, and a corrupt profile is now logged instead of silently skipped.
- Hardened a race when a device is (re)configured while it disconnects, and a couple of dialog timers that could fire on already-closed windows.

## [1.1.47] - 31 May 2026

### Fixed

- **App audio channel assignments were "randomly forgotten" (#64)** — moving an app to a channel (e.g. LibreWolf → Media) was only persisted for apps the router was already tracking; an app sitting on the default channel was never recorded, so the manual move was never saved and the app kept reverting. Every observed stream's placement is now recorded, so a later manual move is detected and persisted. Also added LibreWolf, Tor Browser, Waterfox, Floorp, Mullvad Browser, Thorium and ungoogled-chromium to the browsers auto-routed to the Media channel.
- **GUI crash when dragging the Smart Volume → Level slider quickly (#63)** — the apply worker (a QThread) could be destroyed while still running during rapid re-applies, aborting the application. Worker cleanup now happens only after the thread has fully stopped.
- **Spatial Audio (and the microphone noise-canceling / noise-gate / compressor) toggles did not save their OFF state (#62)** — a PySide6 quirk (`bool(Qt.CheckState.Unchecked)` is `True`) meant turning these off was never recorded, so e.g. Spatial Audio OFF was lost when saving a custom preset. The OFF state is now persisted correctly.

### Changed

- The virtual-sink loopback watchdog now also recovers loopbacks that are alive but linked to the wrong output (e.g. bound to another audio device instead of their Sonar EQ node), not just crashed ones — restoring sound on the affected channel automatically.

## [1.1.46] - 30 May 2026

### Fixed

- **Discord (and other apps) lost their audio sink when changing a Sonar profile** — applying a Sonar profile or EQ used to restart the whole PipeWire stack, tearing down every node. Apps that do not re-enumerate their output devices when the PulseAudio server connection drops (Discord/Electron in particular) ended up silent until restarted. Profile and EQ changes now restart only the filter-chain service and recreate the virtual-sink loopbacks dynamically, leaving pipewire/pipewire-pulse untouched, so connected apps keep their sink and audio.
- **Virtual sinks showed a generic "pw-loopback-NNN" name** in application output pickers and mixers — the Arctis Game/Chat/Media sinks now expose their proper "Arctis … Game/Chat/Media" name and can be selected as an output device (e.g. as Discord's Chat output).
- **Discord appeared as "WEBRTC VoiceEngine"** in a channel's application list — applications that report a generic audio-engine name now fall back to their process binary, so Discord shows as "Discord".

### Changed

- The Arctis Game/Chat/Media virtual-sink loopbacks are now created and owned dynamically by the daemon (instead of a static `pipewire.conf.d` file), so they can be recreated after an EQ change without a full PipeWire restart. The 7.1 surround (HeSuVi) chain for Game and Media is unchanged. The daemon cleans up its loopbacks on shutdown, and a watchdog respawns any loopback whose process crashes.

## [1.1.45] - 29 May 2026

### Fixed

- **Arctis 7X: daemon crashed with "Failed to find command interface endpoint"** — some Arctis 7X (and other Arctis 1 family) firmwares expose the vendor HID command interface with an interrupt IN endpoint only, no OUT endpoint. `get_command_endpoint_address()` raised an exception instead of falling back, crashing the whole daemon before the GUI could appear. ASM now detects the missing OUT endpoint and routes commands over HID SET_REPORT (control transfer) automatically, which is the correct path for such devices (issue #59).

## [1.1.44] - 29 May 2026

### Fixed

- **dinit (Artix): Sonar mode toggle crashed** — switching Game/Chat ↔ Sonar called `systemctl --user restart …` with no dinit branch, raising `FileNotFoundError: systemctl` on systems without systemd. The toggle now works on dinit (issue #25).
- **dinit (Artix): EQ / Sonar changes had no effect** — applying a new equalizer mode ran `dinitctl start pipewire-filter-chain`, but `start` is a no-op when the service is already running, so the freshly generated config was never reloaded (systemd correctly used `restart`). All config-reload paths now use `restart` on both init systems — this is the root cause of the "EQ does nothing" reports on dinit (issue #25).

### Changed

- **Centralised init-system handling** — introduced `service_control.py`, a single abstraction over `systemctl --user` and `dinitctl`. The logical→real service-name mapping (notably `filter-chain` → `pipewire-filter-chain` on dinit) and the start/restart distinction now live in one place instead of being copy-pasted across ~25 call sites in 11 files. Service calls no longer crash when the init manager binary is absent; they log and skip. This removes ~230 lines of duplicated branching and makes the issue #25 class of bugs impossible to reintroduce per-site. Added a regression test suite (`tests/test_service_control.py`).

## [1.1.43] - 28 May 2026

### Fixed

- **Distrobox container fails to start after reboot on Bazzite / SteamOS / Silverblue** — `/run/asm-hidraw` lives on a tmpfs and is wiped at every boot; the `sudo mkdir -p` in the install script only ran at container creation time, so on the next boot `crun` could not stat the bind-mount source and refused to start the container (`crun: cannot stat /run/asm-hidraw: No such file or directory`). A `systemd-tmpfiles` drop-in (`/etc/tmpfiles.d/asm-hidraw.conf`) is now installed on the host, recreating the directory at `sysinit.target` well before Distrobox starts. Added `ConditionPathIsDirectory=/run/asm-hidraw` to `arctis-manager.service` to prevent crash-loops if the directory is somehow absent. Affects all three Distrobox installers (bazzite.sh, steamos.sh, silverblue.sh — issue #59).

## [1.1.34] - 18 May 2026

### Fixed

- **Wireless headset disconnect/reconnect loop** — when the USB dongle stays plugged in but the RF link drops (headset powered off or out of range), ASM retried USB commands thousands of times per second with no back-off, preventing the dongle firmware from completing RF re-association. Errors with errno 19 (ENODEV) now trigger a 1-second back-off; after ~10 s of consecutive failures ASM voluntarily releases the handle to let the dongle reconnect cleanly. Removed a redundant `request_device_status()` call that was firing on every loop iteration instead of the intended 2-second cadence (issue #49).

## [1.1.33] - 18 May 2026

### Fixed

- **ClearCast crash on startup when noise cancellation was enabled** — `_NoiseCancelingCard` connected the toggle signal before creating `self._slider`; PySide6 fires `checkStateChanged` synchronously during `setChecked()`, so restoring an enabled state from the previous session triggered `_set_enabled` before the slider existed, raising `AttributeError` (issue #51).
- **README: First launch section** — clarified that the daemon starts automatically but the GUI must be opened separately (`asm-gui` or via the app launcher), documented `--systray` mode and per-DE autostart methods.

## [1.1.32] - 18 May 2026

### Fixed

- **Audio redirect broken for 8 wireless models** — `is_device_online()` compared `on_off` parser values (`'on'`/`'off'`) against YAML `online_value: online` literally; added aliasing so `'on'` ↔ `'online'` and `'off'` ↔ `'offline'` are treated as equivalent (discussion #48).
- **Streams stay on dead loopbacks after disconnect** — `redirect_audio()` changed the default sink but did not migrate active streams; now iterates `sink_input_list()` and moves every stream sitting on an ASM-owned sink, and persists the choice via `pw-metadata` for PipeWire restart survival (issue #50).
- **`babel` missing from packaging metadata** — added to PKGBUILD depends, RPM Requires, debian/control Depends, system deps checker, and packaging drift check.

## [1.1.31] - 16 May 2026

### Fixed

- **Microphone routed to wrong device on startup** — when the Arctis headset is not yet detected when the filter-chain config is written, `target.object` was left empty and PipeWire would bind the virtual microphone to the first available source (e.g. a DualSense controller mic). `check_and_fix_stale_configs()` now detects this condition at daemon startup and patches the target in-place, triggering a filter-chain restart.

## [1.1.30] - 15 May 2026

### Added

- **Per-channel output device selection** — each audio channel card (Game / Chat / Media) now has a dropdown to route that channel to a specific output sink; systray → Output Routing mirrors the same selection (issue #46).
- **Output device routing saved in profiles** — optional checkbox in Save Profile dialog captures the current per-channel routing.
- **Language filter** — only languages with ≥ 80 % translation coverage appear in the language selector; names are now displayed in their native script via babel (Français, Deutsch, Polski…) instead of 2-letter codes.

### Fixed

- Sonar EQ channel names displayed in English instead of the active language.
- Applications section misaligned on channels without output device selection.
- Duplicate HDMI entries in output device lists.
- Crowdin sync: excluded English from translation download target to prevent source file overwrite on each sync.

## [1.1.4] - 8 May 2026

### Fixed

- **dinit: filter-chain and video-router stuck stopped after asm-setup** — when `asm-setup` rewrites service files on disk, dinit keeps the old in-memory definition (without `restart = true`) until explicitly reloaded. Added `dinitctl reload` for stopped services before starting them so dinit always picks up the current file (issue #25).
- **dinit: D-Bus collision on re-login** — the guard preventing a second `asm-daemon` from starting used `dinitctl status`, which could miss the race window between dinit's boot-sequence auto-start and `asm-setup`'s explicit start. Replaced with `pgrep -f asm-daemon` against the actual running process.
- **dinit: filter-chain start race after pipewire restart** — `asm-setup` now waits 0.5 s after a successful `dinitctl restart pipewire` before starting dependants, giving the PipeWire socket time to come up.

## [1.0.99] - 6 May 2026

### Added

- **Adaptive multi-DE autostart — Hyprland, Sway, XDG, systemd, dinit** — the "Launch at startup" toggle now automatically selects the right method for the detected desktop environment / init system. On Hyprland it writes `exec-once = asm-gui --systray` into `~/.config/hypr/hyprland.conf` (using a comment marker for clean enable/disable). On Sway it writes `exec asm-gui --systray` into `~/.config/sway/config`. On dinit systems the existing `dinitctl enable` calls are used. On systemd-based DEs (GNOME, KDE, XFCE, …) the `arctis-gui.service` approach is kept. Any other setup (i3, unknown, non-systemd distros) falls back to an XDG `.desktop` file in `~/.config/autostart/`. Switching DEs cleans up stale entries from the previous method. Fixes the issue reported by users running Hyprland/CachyOS where the toggle had no effect (closes #30).

## [1.0.98] - 05 May 2026

### Added

- **Distrobox hot-plug (issue #26)** — the headset can now be plugged in *after* the container is created without requiring `--reinstall`. A new udev rule (`90-asm-hidraw-symlink.rules`) maintains `/run/asm-hidraw/` as a stable directory of symlinks to SteelSeries hidraw nodes; the container mounts this directory with `rslave` propagation so additions and removals on the host are immediately visible inside.
- **libusb / PyUSB access from container** — `/dev/bus/usb` is now bind-mounted into the container (`rslave`), giving PyUSB full access to the USB device tree. Previously only `/dev/hidraw*` was passed, which blocked the libusb backend entirely.
- **Fedora Silverblue/Kinoite variants auto-detected** — `distrobox-install.sh` now correctly routes Bluefin, Aurora, Sericea, Onyx, and Cosmic Atomic to `silverblue.sh` (Fedora COPR container) instead of incorrectly using the Arch/AUR container.
- **Container health check** — a 30-second readiness probe runs after container creation; if the container fails to respond, the script aborts with a clear error and recovery instructions before attempting the install.
- **PipeWire management socket mounted** — `pipewire-0-manager` is now forwarded into the container alongside `pipewire-0` and `pulse/`, enabling `pw-cli` management operations from inside the container.
- **PipeWire host restart after install** — the installer now restarts `pipewire`, `pipewire-pulse`, and `wireplumber` on the host so that filter-chain configs written by `asm-setup` are picked up immediately. Can be suppressed with `ASM_RESTART_PIPEWIRE=0`.

### Fixed

- **`pacman-key --init` idempotent** — the keyring initialisation now checks for an existing `pubring.gpg` before running; re-running the installer no longer risks corrupting a healthy keyring on SteamOS or Arch containers.
- **`gamescope-session.target` conditional** — systemd units no longer unconditionally declare `WantedBy=gamescope-session.target` on desktop distros where that target does not exist. Bazzite and Silverblue get `graphical-session.target` only; SteamOS/Steam Deck gets both.
- **`udevadm trigger` scope narrowed** — the post-install trigger now targets only devices with `idVendor=1038` (SteelSeries) instead of re-triggering every USB device on the system.
- **`steamos-readonly` always re-enabled** — a `trap RETURN` guarantees `steamos-readonly enable` is called on exit from `asm_install_udev_rules`, even if an intermediate command fails, preventing the filesystem from being left in read-write mode.
- **paru build directory cleaned on failure** — the temporary directory used to clone and build `paru-bin` is now removed via `trap EXIT` even when `makepkg` fails.
- **Uninstall removes hot-plug udev rule** — `uninstall.sh --remove-udev` now also deletes `90-asm-hidraw-symlink.rules` in addition to the device-specific rules file.

## [1.0.97] - 05 May 2026

### Added

- **Sonar Media tab (issue #29)** — new **Media** EQ tab placed between Game and Chat in the Sonar page. The channel runs through the HeSuVi 7.1 surround pipeline (same routing as Game), giving music and video apps the full spatial-audio treatment. The complete 300+ preset library (`[Game]` tag — games, music genres, movies, podcasts) is available from day one. Boost Volume and Smart Volume cards are wired up identically to Game and Chat.
- **`Arctis_Media` → `sonar-media-eq`** — the Media virtual sink now routes through its own dedicated EQ filter-chain node instead of bypassing Sonar entirely. Stream restore after filter-chain restart remaps `effect_input.sonar-media-eq ↔ Arctis_Media` correctly.

## [1.0.96] - 05 May 2026

### Added

- **dinit support (Artix Linux / issue #25)** — ASM now works end-to-end on init systems other than systemd. `asm-setup` detects dinit via `/proc/1/comm` and writes service files to `~/.config/dinit.d/` instead of calling `systemctl`. All service management calls in the GUI (Sonar EQ restarts, autostart toggle, systray shutdown) are branched: systemd systems are completely unaffected, dinit systems get the correct `dinitctl start/stop/enable` calls.
- **`_ensure_dinit_boot_target()`** — on Artix dinit-userservd there is no default user `boot` service, so `dinitctl enable` silently failed ("service 'boot' has no waits-for.d directory"). `asm-setup` now creates a minimal `~/.config/dinit.d/boot` + `boot.d/` when absent (never overwrites an existing one), matching the upstream dinit getting-started guide.
- **`asm-diag-dinit` diagnostic script** — reports init system, service file presence, running/enabled state (via `waits-for.d` symlink inspection), filter-chain, virtual sinks, udev rules and D-Bus in a single read-only pass. Useful for triage on any non-systemd distro.
- **`init_system.py` — `is_dinit_service_enabled(svc)`** — replaces the non-existent `dinitctl is-enabled` subcommand (dinit only has `is-started`/`is-failed`). Enabled state is determined by walking `*.waits-for.d/` and `boot.d/` symlinks across all known service directories.

### Fixed

- **`asm-setup` crashed on non-systemd init** — every service management call was hardwired to `systemctl` without checking for its presence. On Artix/dinit the setup crashed immediately with `FileNotFoundError: [Errno 2] No such file or directory: 'systemctl'`. A `_has_systemctl()` guard now skips all systemd-specific steps cleanly on non-systemd systems.
- **Autostart toggle broken on dinit** — the GUI checked autostart state using `dinitctl is-enabled` which doesn't exist as a subcommand. The toggle always showed "disabled". Now uses `is_dinit_service_enabled()` (symlink inspection) for correct detection, and `dinitctl enable/disable` for mutation.

## [1.0.95] - 04 May 2026

### Added

- **OLED horizontal marquee for EQ preset name** — when the EQ preset name overflows the 128 px display width, the text now scrolls left pixel-by-pixel. It pauses 2 s at the start (readability), scrolls to the end, pauses 2 s, then snaps back. Speed is controlled by the new **Scroll Speed (Horizontal)** slider (0 = disabled, 1–5 = slow to fast).
- **OLED horizontal marquee for Profile name** — same marquee behaviour applied to the active profile name line.
- **DAC tab — Scroll Speed (Horizontal) slider** — new `oled_eq_scroll_speed` setting controls the horizontal marquee speed for both EQ preset and profile name lines. The existing scroll slider is renamed to **Scroll Speed (Vertical)**.

### Fixed

- **OLED brightness reset by headset firmware** — when `oled_brightness` was set to a value other than the headset default and `screen_timeout = 0`, the headset firmware silently overrode the brightness back to its own value every few seconds. ASM now re-asserts the configured brightness level every 5 s in the refresh loop (only when the screen is active), keeping the setting stable without interfering with the screen-timeout dim-to-black behaviour.
- **DAC tab slider spacing** — the default `QHBoxLayout` content margins caused excessive vertical padding between slider rows. Margins are now explicitly set to 4 px top/bottom, halving the visual gap.

## [1.0.94] - 4 May 2026

### Fixed

- **Arctis 7 2019 (0x12ad) failed to initialize — no battery, no chatmix (issue #28)** — `arctis_7.yaml` was missing `command_transport: ctrl_output`, the field that tells the HID layer to use SET_REPORT on the control endpoint. All four product IDs sharing that YAML (`0x1260`, `0x12ad`, `0x1252`, `0x1280`) were affected. Adding `ctrl_output` aligns the Arctis 7 family with every other device YAML in the codebase (Arctis 7+, Arctis 9, Nova 3/5/7).
- **Virtual sinks (Arctis_Game / Arctis_Chat / Arctis_Media) routed to system default instead of Sonar EQ nodes (issue #28)** — the bundled `10-arctis-virtual-sinks.conf` installed by `install.sh` / `asm-setup` had no `node.target` in its `playback.props` blocks. PipeWire therefore routed all three loopback outputs to the default sink, bypassing the Sonar EQ filter-chain entirely and breaking chatmix separation. Each sink now points to its Sonar EQ input node (`effect_input.sonar-game-eq` / `effect_input.sonar-chat-eq`).
- **Virtual Surround sink silent on fresh install — `can't start graph: No such file or directory` (issue #28)** — `sink-virtual-surround-7.1-hesuvi.conf` referenced the HRIR WAV as the relative path `hrir_hesuvi/hrir.wav`. PipeWire instantiates filter-chains without a stable working directory, so the file was never found. The template now uses the placeholder `${HRIR_DIR}`, which `install.sh`, `setup-surround.sh`, and `asm-setup` substitute with the absolute path `~/.local/share/pipewire/hrir_hesuvi` at deploy time — the same directory the installer downloads the WAV to.

## [1.0.86] - 30 April 2026

### Added

- **System Deps dialog at GUI startup** — a runtime self-healing dialog that detects every missing system component ASM relies on (LADSPA `plate_1423` + `librnnoise`, HRIR file, `filter-chain.service`, PipeWire ≥ 1.0, `wpctl`, `pkexec`, `pyudev`/`pulsectl`/`PySide6`/`pyusb`/`PIL`/`ruamel.yaml`/`dbus-next`, `dbus-send`, `pw-metadata`, `curl`, D-Bus session, udev rules, `gh` CLI). Each missing dep shows a severity badge (BLOCKING / DEGRADED / OPTIONAL), what feature breaks if it stays missing, and an `Install`/`Run` button that runs the right `pkexec dnf|apt-get|pacman install …` for the detected distro. **Install all missing** groups by package manager so a single polkit prompt fixes the whole list. **Don't show again until ASM is upgraded** writes a version-aware skip marker that auto-resets on the next ASM upgrade. **Copy cmd** falls back to clipboard mode for unsupported distros. Triggered after the existing udev / telemetry dialogs at T+2.5 s on first launch — no nag.
- **`asm-daemon --verify-setup` runs the same dep registry** — headless preflight now logs each missing component with its severity tier and per-distro install command, plus the detected distro on the summary line. Exit 1 on BLOCKING/DEGRADED, 0 if only OPTIONAL — safe to wire into systemd `ExecStartPre`.
- **`scripts/check-deps-drift.py` CI guard** — AST-walks `src/`, fails the wheel-install-test matrix on every supported distro if a new third-party `import` or `subprocess.run([…])` is added without a matching `DepCheck` entry. Closes the silent-failure trap class issue #23 was about: any new external dep is forced through the runtime checker too, so the GUI dialog never goes blind.

### Fixed

- **Spatial Audio silently inaudible on Fedora (issue #23)** — `Recommends: swh-plugins` in the spec was a no-op on Fedora because the package is named `ladspa-swh-plugins` there; DNF silently pulled in nothing and the HeSuVi filter-chain failed to load `plate_1423` (reverb) on every Fedora install. The spec now hard-`Requires: ladspa-swh-plugins`, and the dep is also a `Depends:` (not `Recommends:`) on Debian + a `depends=` (not `optdepends=`) on Arch — DNF/APT auto-pull it on `upgrade`, no manual `dnf install` needed.
- **Audio bypassed Sonar EQ + HeSuVi chain on first run (issue #23)** — `asm-setup` deployed a static `10-arctis-virtual-sinks.conf` with no `node.target`, so WirePlumber connected the Game/Chat virtual sinks straight to the physical headset, skipping the Sonar EQ and HeSuVi nodes entirely. The drift-checker (`check_and_fix_stale_configs`) was only invoked from `SonarPage.__init__` in the GUI, so users who never opened the Equalizer page kept the broken layout forever. The daemon now runs the drift-check from `configure_virtual_sinks()` at startup, and the check now flags `needs_pw_restart=True` when it rewrites the virtual-sinks file (which lives in `pipewire.conf.d/`, not `filter-chain.conf.d/`, so a filter-chain restart alone wouldn't reload it).
- **Ghost Arctis virtual sinks survived `paru -R arctis-sound-manager` (issue #24)** — the AUR `post_remove` hook only echoed cleanup instructions that buried `paru`/`yay` output drowned, so users on CachyOS reported phantom Game/Chat/Media devices remaining after uninstall. The AUR/RPM/DEB hooks now actually run a cleanup as `$SUDO_USER` (via `su -l`) that removes `~/.config/pipewire/pipewire.conf.d/10-arctis-virtual-sinks.conf` + the chat/media/HeSuVi siblings + stale `~/.config/systemd/user/arctis-*.service` copies, then restarts pipewire so the ghost sinks vanish immediately. Audio profiles in `~/.config/arctis_manager/profiles/` are preserved (matches the `scripts/uninstall.sh --purge` contract).
- **No soft deps remain** — `noise-suppression-for-voice` (rnnoise LADSPA, drives the ClearCast toggle), `swh-plugins` (Arch), and `curl` (asm-setup HRIR download) were promoted from `Recommends:` / `optdepends=` to hard requirements across all three packagers. Any feature ASM exposes in the GUI now has a hard package dep behind it — the runtime checker is the safety net for users who later remove a package by hand or ride an immutable distro that didn't replay the upgrade transaction.

## [1.0.85] - 27 April 2026

### Fixed

- **Sonar Output preset switch dropped Firefox / Arctis_Media routing (issue #22)** — restarting the PipeWire filter-chain to apply a new Output EQ preset (e.g. *Music – Punchy*) tears down the `Arctis_Game/Chat/Media` virtual sinks momentarily. The stream-restore loop only waited for the EQ filter node to reappear before re-issuing `pactl move-sink-input`, so when the Arctis virtual sinks were not yet back the moves silently failed and streams stayed orphaned on the system default. The apply worker now waits up to 4 s per saved `Arctis_*` target sink before issuing the move-back commands.
- **`asm-cli arctis-usb-info` traceback on EACCES (issue #22)** — when udev rules were missing or not yet applied to the currently-attached dongle, reading `device.manufacturer` raised `usb.core.USBError: [Errno 13] Access denied` and aborted the diagnostic. Now wrapped with `try/except`; prints `(no permission)` with a hint to run `asm-setup` instead of crashing the whole CLI report.

## [1.0.84] - 27 April 2026

### Fixed

- **`TypeError` crash in `on_settings_received` when the daemon flagged a USB EACCES (issue #22)** — `UdevRulesDialog(parent=self, ...)` was called from `QMainApp`, which inherits from `QObject`, not `QWidget`, so `QDialog.__init__` rejected the parent and the GUI tracebacked instead of opening the "Apply now" dialog. Pass `self.main_window` (the actual `QMainWindow`) so the runtime fix-permissions flow shipped in v1.0.81 finally fires on Nobara/Fedora setups where the dongle was plugged in before the udev rules took effect.

## [1.0.83] - 26 April 2026

### Added

- **Bug report — full system diagnostic** — the report payload now includes Python library versions (pulsectl / pyudev / pyusb / dbus-next / ruamel-yaml / PySide6 / pillow via `importlib.metadata`), multi-install detection (rpm + pacman + apt + pipx + every `asm-daemon` binary in `$PATH`), the actual content of the active udev rules file with `is_udev_rules_valid()`'s verdict, the full PipeWire sink list, `wpctl status`, the USB monitor backend (pyudev event-driven vs polling fallback) and the D-Bus session bus path. Goes from a 6 kB report to a ~13 kB one — every block is actionable for triage.
- **Bug report — short URL body + full file attachment** — GitHub's `?body=` query param is capped around 8 kB; the expanded report no longer fits. ASM now writes the full diagnostic to `~/.cache/arctis-sound-manager/reports/bug-report-YYYYMMDD-HHMMSS.md`, opens the GitHub issue editor with a short summary (~600 chars) and an "Open folder" button so the user can drag-and-drop the diagnostic file into the issue editor.
- **Bug report — one-click auto-submit via `gh` CLI** — when the user has `gh auth status` configured, a "Submit automatically (gh CLI)" button uploads the diagnostic as a secret gist, creates the issue with a link to the gist, and opens the new issue URL in the browser. Zero clicks past the dialog. Falls back to the manual drag-and-drop flow on any failure.
- **Bug report — free-form description** — the dialog has a dedicated "Describe what happened" field at the top with a placeholder example. The text is prepended as a `## What happened` markdown block to BOTH the URL body and the full diagnostic file so the maintainer sees it first.

### Fixed

- **Channel button label `H` → `O`** — the 4th audio channel was renamed from "HDMI" to "Output" because it can target any external sink (HDMI, USB speakers, sound card…), not just HDMI. The drop button on application pills was already `O`, but the help-page text in the three locales (en/fr/es) still referenced the old `G / C / M / H` shortcut and "H sends it to HDMI" — updated to `G / C / M / O` and "O sends it to your external Output (HDMI, USB speakers, sound card…)".

## [1.0.82] - 26 April 2026

### Fixed

- **Multi-device routing (issue #20)** — on systems with several audio outputs (Logitech G560, Razer Kiyo, internal speakers, etc.) plus an Arctis headset, apps that were already running when ASM started used to stay glued to their previous sink because `_auto_route()` only matched a hardcoded list of browser/game/chat app names. Now `video_router.py` adopts any orphan stream onto `Arctis_Media` when Arctis is the default sink — Spotify, VLC, Steam and friends finally end up in the headset on first launch. Streams already on an Arctis sink (virtual or filter-chain) are left untouched, and the adoption is saved as a routing override so manual KDE-mixer placements take over from there. Same logic applied to native PipeWire streams (mpv, haruna).

## [1.0.81] - 26 April 2026

### Added

- **Runtime "Apply now" popup on USB EACCES** — restores the v1.0.71 behaviour lost during the v1.0.79 merge. The existing startup dialog only fires when the udev rules file is missing/incomplete on disk, but if the rules are correct AND the headset was plugged in before they took effect (typical after a `paru -Syu` or `dnf upgrade`), the daemon used to hit EACCES on `kernel_detach` and the GUI was silent. Users had to figure out replug or `sudo asm-cli udev reload-rules` on their own. Now `CoreEngine.kernel_detach` flags `permission_error=True` per-interface on errno 13, the daemon exposes that flag through `GetSettings`, and the GUI opens `UdevRulesDialog(mode="reload")` automatically — one click runs `asm-cli udev reload-rules` with a single pkexec prompt and the device becomes accessible without unplugging.
- **`UdevRulesDialog` two modes**: `write` (existing — install missing rules) and `reload` (new — re-trigger udev on the currently-attached device). Both share the same one-prompt elevation flow.

### Fixed

- **`debian/build-deb.sh` referenced stale unit paths** (`debian/*.service`) that had moved to `systemd/*.service` in v1.0.79. The .deb job in `release.yaml` failed at build, which cascade-skipped `aur-update` and `copr-build`, leaving AUR / COPR users on v1.0.78 even though the v1.0.79 / v1.0.80 tags were pushed. Updated to use the canonical `systemd/` paths.

### Hardened

- **CI drift detector** now also validates that every relative source path in `debian/build-deb.sh`, `debian/rules`, `aur/PKGBUILD` and the RPM spec actually exists in the repo. Catches the exact regression described above before merge — verified by sim-test.

## [1.0.80] - 26 April 2026

### Added

- **`scripts/uninstall.sh`** — dedicated uninstaller. Detects every install method on the system (rpm / pacman / apt / pipx) and lets the user pick which one(s) to remove. When both pipx and a distro package coexist, offers a 1/2/3 menu (pipx only / distro only / both). `--purge` wipes settings, PipeWire configs, HRIR data, user systemd units and the manually-written udev rules in /etc, **but preserves audio profiles** (`~/.config/arctis_manager/profiles/` and the active-profile pointer) so a future reinstall picks them right back up. A separate explicit confirm offers to delete the profiles too.
- **Profiles capture DAC tab settings** — audio profiles now snapshot and restore the full DAC state in addition to EQ/macros/spatial/volumes: OLED brightness, screen timeout, scroll speed, custom-display toggle, show-element toggles, display order, per-element font sizes, weather configuration. Older profile files (pre-v1.0.80) load with an empty DAC block — restoring them is a no-op for the DAC side, exactly as before.
- **`scripts/check-packaging-drift.py`** — CI-enforced drift detector. Runs on every PR/push across the 8-distro matrix and fails the build if pyproject.toml / PKGBUILD / .SRCINFO / RPM spec are out of sync, if any generator (udev rules, AppStream metainfo, debian/changelog) produces output that differs from the committed file, or if a pyproject dep is missing from PKGBUILD `depends`, the spec's `Requires:` lines, or debian/control `Depends:`. Closes the class of bug that left AppStream stuck at v1.0.4 and debian/changelog at v1.0.27 for months.

### Fixed

- **Packaging drifts surfaced by the new check** — added `pillow` to the three packagers (was in pyproject but missing everywhere), added `python3-pulsectl` to debian/control, regenerated debian/changelog (14 versions behind) and AppStream metainfo (no v1.0.79 entry).
- **`udevadm trigger` scope alignment** — AUR install hook and debian postinst now narrow the trigger to `--subsystem-match=usb` like the RPM spec and asm-cli already do. Cosmetic; functional behaviour unchanged.

## [1.0.79] - 26 April 2026

Cross-distro robustness sprint (22 commits, four phases applied: bloquants, runtime, cross-distro, qualité de vie). Promoted from the 1.0.79b develop pre-release.

### Fixed

- **udev (RPM)**: `udevadm trigger` now uses `--action=add` so device permissions are actually re-evaluated after upgrade on Fedora/Nobara/RHEL (was the same fix as cli.py / aur / debian).
- **udev**: `is_udev_rules_valid()` is now content-based (parses real rule lines) instead of substring-matching `'uaccess'`/`'1038'` — no more false positives from comments, no more false negatives from a stale file at one path masking a valid file at another.
- **udev paths**: `UDEV_RULES_PATHS` now includes `/run/udev/rules.d/` (NixOS, runtime overlays) and `/lib/udev/rules.d/` (pre-usrmerge Debian).
- **monitor**: `pyudev` is now optional. When the import or netlink setup fails (containers, restricted sandboxes, NixOS modules), the monitor falls back to a 2s polling loop scoped to vendor 0x1038 instead of crashing the daemon at startup.
- **core**: `kernel_detach`/`kernel_attach` no longer crash on a single failing interface (device unplugged mid-detach, permission denied) — the loop continues so init still runs on the interfaces that did claim.
- **core**: `init_device()` retries each command once on `usb.core.USBError` and logs N/total progress. A persistent failure logs at ERROR but doesn't abort the rest of the init list.
- **core**: log a WARNING when a vendor 0x1038 device appears but no YAML matches — easy to spot new/firmware-bumped PIDs in journalctl/bug reports.
- **dbus**: daemon retries `MessageBus().connect()` (5×, 5s timeout each) and explicitly checks `request_name()` reply — a queued/conflicting daemon now fails with a clear "another asm-daemon is running" error instead of silently ignoring all GUI calls.
- **dbus (gui)**: `dbus_request_async` now wraps connect+call in `asyncio.wait_for` (5s) and emits a new `sig_daemon_alive` signal after 3 consecutive failures so views can show a banner instead of silently freezing.
- **daemon**: SIGTERM/SIGINT registered via `loop.add_signal_handler` and now actually cancels long-lived asyncio tasks (`core_loop`, `dbus_awake`) — daemon exits promptly even if a worker is mid-blocking-call.
- **settings**: `general_settings.yaml` reads catch corruption (move to `.broken`, fall back to defaults) and writes are now atomic (tmp+rename+fsync). A killed process can no longer leave a half-written file.
- **config**: a single malformed device YAML used to crash the entire daemon. We now log+skip the offending file and load the rest. Real cross-family duplicate PIDs are warned (same-family overrides between HOME and SRC are ignored — by design).
- **gui**: clear errors instead of XCB tracebacks when DISPLAY/WAYLAND_DISPLAY is missing or the Qt platform plugin failed to load (suggests `qt6-wayland` per distro).
- **pactl**: `PulseAudioManager` retries the initial connection 12× with 0.5→4s exponential backoff so the daemon survives the boot race against pipewire-pulse instead of crashing.
- **setup**: `asm-setup` refuses to run as root (silent failure mode where `systemctl --user` and `~/.config` go to the wrong user), and validates every device YAML in `~/.config/arctis_manager/devices/` at the end so a previous interrupted run doesn't leave broken files behind.

### Added

- **`asm-cli diagnose [-o file]`**: one-shot bug-report dump (versions, USB tree, udev state, sinks, journalctl, redacted settings, last 100 service log lines). Local-only.
- **`asm-daemon --verify-setup`**: preflight checks (YAMLs, udev, PulseAudio, D-Bus, USB monitor backend) with a clear OK/FAIL summary and exit code, safe to run before launching the daemon or in CI.
- **`ARCTIS_LOG_LEVEL` env var** (debug/info/warn/error/numeric): bump verbosity for bug reports without rebuilding or passing flags. Honored by daemon, GUI and video-router.
- **i18n EN fallback**: missing translation keys in non-en locales now fall back to the en string instead of showing the raw key, with a log-once warning per missing key for translators.

### Changed (packaging)

- **AppStream metainfo**: `<releases>` block is now generated from `CHANGELOG.md` at build (`scripts/generate_metainfo_releases.py`) — was frozen at v1.0.4 (61 versions behind), GNOME Software / KDE Discover finally show recent updates. Wired into AUR/PKGBUILD, RPM .spec and debian/rules.
- **debian/changelog**: now generated from `CHANGELOG.md` at build (`scripts/generate_debian_changelog.py`) — was frozen at v1.0.27 (39 versions behind).
- **systemd units**: single source of truth in `systemd/*.service`. PKGBUILD and the .spec used to embed three heredocs each; debian shipped 2/3 (arctis-gui.service was missing from the .deb). Now all three packagers install from the same files.
- **udev rules**: single shared generator (`arctis_sound_manager.udev_rules`) used by both `asm-cli udev write/dump-rules` and `scripts/generate_udev_rules.py` — output is byte-identical regardless of which entry point produced it.
- **pyproject.toml**: version is now bumped automatically by the release workflow (was stuck at 1.0.66 while PKGBUILD/spec advanced to 1.0.78).

### CI

- Cross-distro pytest + udev generator smoke runs on every PR/push across Fedora 42/43, Ubuntu 24.04/25.10, Debian trixie/bookworm, Arch and CachyOS.

## [1.0.78] - 25 April 2026

### Fixed

- **Weather city search**: typing in the city field was interrupted every few hundred milliseconds because `update_settings` unconditionally overwrote `_city_input` with the saved value on every daemon poll. The field is now only updated when it does not have focus.

## [1.0.77] - 25 April 2026

### Fixed

- **Headset page**: device name in the "Devices" card flickered between the i18n status category label ("Headset" / "Casque") and the actual device name ("Arctis Nova Pro Wireless"). `update_status` was overwriting `_device_name_label` on every status poll; the label is now only set by `update_settings`.

## [1.0.76] - 25 April 2026

### Fixed

- **GUI fails to open** (regression in 1.0.69): `generate_hesuvi_conf()` raised `UnboundLocalError: cannot access local variable '_log'` when no Arctis device was attached. The early-return guard called the module-level `_log`, but a later `import logging as _log` inside the same function rebinds `_log`, which Python treats as a function-local for the entire scope. Replaced the local rebinding with a direct `_log.warning(...)` so the module-level logger stays accessible.

## [1.0.75] - 25 April 2026

### Changed

- **First version published on PyPI** — install / upgrade is now a one-liner that always pulls the latest:
  ```
  pipx install arctis-sound-manager --force
  ```

## [1.0.74] - 25 April 2026

### Added

- **Automatic PyPI publish** on every tagged release via PyPA's `gh-action-pypi-publish` action using OIDC Trusted Publishers (no token in repo secrets). Once the project is published once, end-users can install/upgrade with the canonical one-liner `pipx install arctis-sound-manager --force` instead of having to track release URLs.

### Changed

- Release workflow writes the deterministic AUR source tarball to `build/aur/` instead of `dist/`, so the hyphen-named archive doesn't get scooped up by twine when publishing to PyPI. The GitHub release upload list now reflects the new path.

## [1.0.73] - 25 April 2026

### Added

- **`scripts/clean-reinstall.sh`** — a self-contained, `curl | bash`-friendly script that audits every existing ASM install (rpm + dpkg + pacman + pipx + orphan binaries in `$PATH`), uninstalls all of them, then installs the latest release via the user's chosen method, runs `asm-setup` and verifies that the daemon is active. Solves the dup-binary mess that built up across mixed install methods (RPM + pipx --force etc.).
- **Multi-install detection in the in-app updater**: clicking "Install update" now checks whether ASM is currently installed by more than one method (rpm + pipx, etc.) and refuses to upgrade until the conflict is resolved, opening a clear dialog that points to `clean-reinstall.sh` with a copy-to-clipboard command.

### Fixed

- **In-app updater now runs `asm-setup` after a pipx upgrade** (via the new `FirstRunDialog`), so udev rules are reloaded and PipeWire restarted on the new binary. Previously only `asm-cli desktop write` was called, which left udev permissions stale on already-attached devices.

### Changed

- `update_checker.detect_install_method()` now wraps a new `detect_all_install_methods() -> list[InstallMethod]` so callers can detect duplicate installations rather than silently picking the first match.

## [1.0.72] - 25 April 2026

### Added

- **Auto-run `asm-setup` on first GUI launch** for installs that didn't go through a distro-package post-install hook (typically `pipx`). When the GUI starts and `~/.config/arctis_manager/.setup_done` is missing, a new `FirstRunDialog` opens, runs `asm-setup` as a subprocess and streams its output live in the dialog so the user can follow what's happening (HRIR download, services, udev install). Distro packages already shipped `/etc/xdg/autostart/asm-first-run.desktop` for this — pipx users were left out, which was the root cause behind issue #22.

## [1.0.71] - 25 April 2026

### Added

- **`asm-setup` now always reloads + triggers udev rules**, even when the rules file on disk is already valid. Previously it printed `[ok] udev rules already valid — skipping` and exited, leaving the currently-attached dongle without the new permissions — which was the root cause behind issue #22 ("no device detected after upgrade").
- **GUI dialog "Apply now"** when the daemon detects an EACCES on the USB device. The daemon now exposes a `permission_error` flag over D-Bus; when the GUI sees it, it surfaces a dialog explaining that the rules exist but were not applied to the connected device, with a one-click button that runs `asm-cli udev reload-rules` (with pkexec/sudo) and then asks the daemon to re-scan via the existing `ReloadConfigs` D-Bus method.

### Changed

- `CoreEngine` exposes a new `permission_error: bool` attribute set by `_log_usb_access_error` and cleared on the next successful `kernel_detach`. `ArctisManagerDbusSettingsService.GetSettings` now returns it under the `permission_error` key.

## [1.0.70] - 25 April 2026

### Fixed

- **Daemon crash on USB permission errors** (#22): when `is_kernel_driver_active()` or `detach_kernel_driver()` raised a `USBError` (typically `[Errno 13] Access denied` because udev rules were not yet applied to a device that was already plugged in), the entire daemon crashed at startup. The GUI then failed to appear because the systemd service was in a failed state.
  - `kernel_detach()` and `kernel_attach()` now catch `USBError` and return a status flag instead of propagating the exception.
  - On `EACCES`, a clear error message is logged with three concrete remediation steps (replug, `udevadm reload-rules` + `trigger`, or reinstall via the distro package).
  - `configure_virtual_sinks()` checks the detach result and bails out cleanly so the daemon stays alive even if a device is currently inaccessible.

## [1.0.69] - 24 April 2026

### Fixed

- **Remove all remaining hardcoded Nova Pro Wireless defaults** (follow-up to #21): `device_state.py` used Nova Pro Wireless ALSA node names and the string "Arctis Nova Pro Wireless" as initial values AND as post-disconnect values. `sonar_to_pipewire.py` had the same strings as fallback constants in dead-code `except` branches. On a system without a Nova Pro Wireless, any code path that generated a filter-chain config before a device was attached (or after a disconnect) would write configs pointing to a sink that does not exist — same class of bug as #21, just at a different moment.
  - `device_state` now defaults to empty strings and exposes `is_device_set()` so callers can distinguish "no device" from "device ready".
  - All PipeWire config generators in `sonar_to_pipewire.py` (`generate_sonar_eq_conf` for device-dependent channels, `generate_sonar_micro_conf`, `generate_virtual_sinks_conf`, `generate_hesuvi_conf`) now skip generation with a clear warning when no device is attached, instead of writing configs with a hardcoded wrong target.
  - Removed the dead-code `_PHYSICAL_OUT` / `_PHYSICAL_IN` constants and the `"Arctis Nova Pro Wireless"` string fallbacks from `sonar_to_pipewire.py`.

## [1.0.68] - 24 April 2026

### Fixed

- **Audio routing on non-Nova-Pro-Wireless devices** (#21): when the ALSA sink lookup failed for the attached device, the daemon fell back to a hardcoded `alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo` sink name. On any other headset (e.g. Arctis Nova 7, Nova 3, Nova 5…), this sink does not exist, so the generated virtual sinks routed audio into the void and the headset disappeared from KDE / GNOME audio trays.
  - The PID comparison in `pactl.py` now parses both sides as integers, so it matches regardless of hex casing or `0x` prefix differences across PipeWire versions.
  - `configure_virtual_sinks` now retries the sink lookup for up to ~4 s, giving PipeWire time to enumerate a freshly-attached USB audio card at boot.
  - If the exact PID is never exposed by PipeWire (some distros/kernels), ASM now falls back to any SteelSeries sink instead of the wrong hardcoded Pro Wireless default.
  - If no sink is found at all, ASM now logs a clear error with remediation steps and skips virtual sink setup instead of silently breaking audio.

## [1.0.67] - 24 April 2026

### Changed

- **Anonymous telemetry**: each submission now includes an `installation_id` — a salted SHA-256 hash of `/etc/machine-id` (irreversible, no personal data). The server uses this ID to deduplicate unique users exactly, instead of grouping by `(distro, headset)`. Falls back to a random UUID stored in `~/.config/arctis_manager/telemetry.yaml` on systems where `/etc/machine-id` is unreadable.
- **Stats dashboard & README**: all usage stats (distributions, headsets, versions, totals) are now computed per unique installation instead of per raw event. The KPI previously labelled "Data points" is now "Unique users".

## [1.0.65] - 22 April 2026

### Fixed

- **DEB build**: fixed CI release workflow failure — `debian/91-steelseries-arctis.rules` was removed from the repo but `build-deb.sh` still referenced it as a static file. The script now generates udev rules at build time via `uv run python3 scripts/generate_udev_rules.py`.

## [1.0.60] - 21 April 2026

### Changed

- **udev rules (RPM/DEB)**: rules are now generated at build time from device YAMLs via `scripts/generate_udev_rules.py` — no more hardcoded rules that drift out of sync with the device definitions. `debian/91-steelseries-arctis.rules` removed.
- **`asm-cli udev dump-rules`**: new subcommand that prints the generated udev rules to stdout (used by packaging scripts).
- **`generate_udev_rules_content()`**: extracted as a public function in `cli.py` for reuse by both `write-rules` and `dump-rules`.

## [1.0.59] - 21 April 2026

### Fixed

- **udev rules (RPM/DEB)**: add missing PID `0x1294` for Arctis Pro Wireless — the audio PID was never included in the installed rules file, causing `[Errno 13] Access denied` on Fedora/Nobara.
- **udev rules (RPM/DEB)**: remove `ENV{DEVTYPE}=="usb_device"` condition from all rules — this condition can fail silently on some kernels, preventing rules from matching.
- **udev rules (RPM/DEB)**: sync all PIDs with device yamls — add missing PIDs for Arctis 1/7X/7P Wireless, Arctis Pro 2019, Nova 3P/3X, Nova 7 Gen 1/Gen 2.

## [1.0.53] - 18 April 2026

### Fixed

- **Telemetry**: added `User-Agent` header to outgoing requests — Cloudflare was silently returning 403 to `Python-urllib`, so no data was ever reaching the stats endpoint.

## [1.0.52] - 18 April 2026

### Fixed

- **udev rules**: clicking "Install rules" in the GUI now triggers a **single** password prompt instead of three — the file write, `udevadm control --reload-rules` and `udevadm trigger` are now bundled into one elevated shell script.

## [1.0.50] - 17 April 2026

### Added

- **73 new Sonar presets** imported from SteelSeries Sonar v1.91 — game titles, Music and Movie categories (all names sanitized, no special characters).

## [1.0.49] - 17 April 2026

### Added

- **2 new Sonar presets**: Podcast [Game], Podcast [Chat] — imported from SteelSeries Sonar v1.91.

## [1.0.48] - 17 April 2026

### Added

- **Anonymous telemetry** (opt-in): on first launch, ASM asks whether you want to share your Linux distribution, headset model and ASM version once per day. No personal data or IP address is stored. Can be toggled at any time in Settings.
- **Community stats dashboard**: aggregated usage data is publicly visible at [loteran.github.io/Arctis-Sound-Manager/stats](https://loteran.github.io/Arctis-Sound-Manager/stats).
- **Auto-updated README**: the Supported Devices and Tested Distributions tables in the README are now updated automatically every week from telemetry data. Devices with 5+ user reports are promoted from ⚠️ to ✅ automatically.

## [1.0.42] - 16 April 2026

### Added

- **Dial interface auto-detection**: the daemon now probes USB interfaces at startup to find the dial interface automatically, with a per-device cache to avoid re-probing on reconnect.

### Fixed

- **Arctis Nova 7**: moved PID `0x227a` to the correct `nova_7_discrete_battery` device definition.
- **CI**: fixed `aur-update` and `copr-build` sync jobs rejecting push to develop when the branch advanced since checkout — now checkout develop directly before applying changes.

## [1.0.41] - 16 April 2026

### Fixed

- **Arctis Pro Wireless**: added alternate USB PID `0x1294` to device definition and introduced HID-aware device detection — the daemon now automatically selects the USB interface that exposes an HID class (bInterfaceClass == 3), fixing `[Errno 13] Access denied` on systems where the control interface is exposed under a different PID.

## [1.0.40] - 15 April 2026

### Fixed

- **udev rules**: removed `GROUP="plugdev"` from generated rules — the group does not exist on Fedora/RHEL and caused the entire rule line to be silently ignored, leaving the device inaccessible. `MODE="0666"` + `TAG+="uaccess"` are sufficient on all distros.
- **Bug report dialog**: fixed clipboard copy (added `activateWindow()` + explicit `QClipboard.Mode.Clipboard` to fix silent failure on Wayland/VM); "Copy to clipboard" button now shows "Copied!" feedback for 2 s.
- **Bug report dialog**: "Open GitHub issue" button now copies the report to the clipboard and opens a blank issue (title only) instead of encoding the full report in the URL, which exceeded GitHub's URL length limit.
- **RPM (Fedora)**: filter auto-generated versioned `python dist` requirements for `ruamel-yaml` and `pyudev` — Fedora 43 ships slightly older versions, causing `dnf install` to fail without `--nodeps`.

### Added

- **In-app update**: the update button now detects the install method (rpm/pacman/apt/pipx) and shows the correct package-manager upgrade command (`dnf upgrade` / `paru -Syu` / `apt upgrade`) with a "Copy command" button instead of silently installing via pipx on managed installs.

## [1.0.38] - 14 April 2026

### Fixed

- **Presets: `God of War Ragnarök`** : renommé en `God of War Ragnarok` — le caractère `ö` causait également une erreur bsdtar lors du packaging.

## [1.0.37] - 14 April 2026

### Fixed

- **AUR: packaging** : `uv pip install --prefix` remplace `uv pip download` (sous-commande inexistante). Plus besoin de `python-pip` en makedep.
- **Presets: noms de fichiers** : suppression des caractères ™ et ® dans les noms de fichiers de presets — causaient des erreurs bsdtar lors du packaging (impossible de traduire en UTF-8 dans l'environnement fakeroot).

## [1.0.36] - 12 April 2026

### Fixed

- **Toutes les distros: permissions USB** : règles udev générées avec `plugdev` group et sans `ENV{DEVTYPE}` check — fonctionne désormais sur Fedora, Ubuntu et toutes distros sans `TAG+="uaccess"` (cross-distro fix).

## [1.0.35] - 12 April 2026

### Fixed

- **Toutes les distros: règles udev malformées** : une règle par PID au lieu de plusieurs PIDs séparés par `|` dans `ATTRS{idProduct}` — syntaxe multi-valeur non supportée par toutes les versions de udev.

## [1.0.34] - 12 April 2026

### Fixed

- **Ubuntu/Debian: `python3-pulsectl` and `dbus-next` absent des dépôts** : les deux bibliothèques sont maintenant bundlées directement dans le `.deb` (comme `dbus-next` l'était déjà). Plus aucune dépendance apt non résolvable.
- **Ubuntu 22.04: `python3-pyside6` absent des dépôts** : le `postinst` détecte l'absence de PySide6 et installe automatiquement `pyside6` via `pip3 --user` pour l'utilisateur réel (`$SUDO_USER`).
- **Toutes les distros: `wireplumber` absent des dépendances** : ajouté dans `Depends` (`.deb`), `Requires` (RPM) et `depends` (AUR).
- **Fedora: `python3-dbus-next` absent des dépôts Fedora** : bundlé dans le RPM via `pip install --no-deps` dans `%install`.
- **AUR: `python-dbus-next` et `python-pulsectl` AUR-only** : bundlés dans le paquet AUR via `pip install`, retirés des `depends` — `makepkg -si` fonctionne sans AUR helper.
- **`asm-setup` jamais lancé automatiquement** : le `postinst`/`%post`/`.install` détecte la session D-Bus active (`/run/user/$UID/bus`) et lance `asm-setup` immédiatement pour l'utilisateur réel. Fallback via `/etc/xdg/autostart/asm-first-run.desktop` si aucune session active.
- **`asm-setup` relancé à chaque login via autostart** : `asm-setup` écrit un flag `~/.config/arctis_manager/.setup_done` à la fin de l'installation ; l'autostart vérifie ce flag et ne tourne qu'une seule fois.
- **AUR: pas d'AUR helper sur install fraîche** : le `.install` vérifie si `yay` ou `paru` est présent et installe `yay-bin` automatiquement si aucun n'est trouvé.

## [1.0.32] - 11 April 2026

### Fixed

- **HeSuVi config deployed to wrong directory in asm-setup** (AUR/COPR/DEB): `sink-virtual-surround-7.1-hesuvi.conf` was copied to `pipewire.conf.d/` instead of `filter-chain.conf.d/`, recreating the duplicate-node bug (#14) on every package install.
- **filter-chain.service missing on Fedora and Ubuntu**: the service is not shipped by default on these distros. A bundled `filter-chain.service` is now included in all packages and auto-installed by `asm-setup` and `install.sh` when the system service is absent.
- **Hardcoded Nova Pro Wireless node.target in PipeWire static configs**: `10-arctis-virtual-sinks.conf` and `sink-virtual-surround-7.1-hesuvi.conf` contained a hardcoded ALSA sink name — all three virtual sinks (Game/Chat/Media) were silent on startup for any headset other than Nova Pro Wireless. Removed; sinks now connect to the default sink until the daemon reconfigures them.
- **plate_1423 LADSPA plugin always injected in HeSuVi config**: the Distance reverb node (requires `swh-plugins`) was generated unconditionally, causing virtual surround to fail silently when `swh-plugins` is not installed. Now only generated when Distance > 0.
- **`APPLICATIONS_PATH` not created before desktop entry write**: `asm-cli desktop write` crashed with `FileNotFoundError` on a fresh install where `~/.local/share/applications/` did not exist.
- **`ensure_systemd_unit()` crash on non-systemd distros**: `FileNotFoundError` uncaught when `systemctl` is absent, crashing the GUI at startup. Added `shutil.which('systemctl')` guard and try/except.
- **PipeWire restarted after filter-chain in asm-setup**: PipeWire must restart before filter-chain is enabled so it picks up new configs. Order corrected.
- **Duplicate desktop launcher on package installs**: `asm-setup` now skips `asm-cli desktop write` when a system-level desktop entry already exists (AUR/COPR/DEB).

### Improved

- `install.sh`: `set -euo pipefail`, wheel presence check, HRIR file size validation after download, filter-chain service auto-detection across distros.
- `setup-surround.sh`: aligned with `install.sh` (correct `filter-chain.conf.d/` target, HRIR validation, filter-chain service detection, `set -euo pipefail`).
- `arctis-manager.service`: added `After=pipewire.service pipewire-pulse.service` and `Wants=pipewire.service` in all templates.
- `debian/postinst`: complete instructions pointing to `asm-setup` to enable all services.

## [1.0.27] - 10 April 2026

### Fixed

- **Silent Game channel persists after Sonar mode switch** (issue #14, root cause): `install.sh` deploys the HeSuVi config to `pipewire.conf.d/` (loaded by the main PipeWire process). When Sonar EQ is enabled, ASM also generates a dynamic config into `filter-chain.conf.d/`. Both files register the same node name (`effect_input.virtual-surround-7.1-hesuvi`), causing a conflict that leaves the game EQ routing to a non-existent node — audio disappears silently. Fixed by:
  - `generate_hesuvi_conf()` now removes the static copy from `pipewire.conf.d/` when writing the dynamic version.
  - `check_and_fix_stale_configs()` now detects and removes the conflict on daemon startup.
  - `ensure_sonar_eq_configs()` now validates existing config content (channel count, target sink) and regenerates stale files, not just missing ones.

## [1.0.26] - 10 April 2026

### Fixed

- **Silent Game channel on first Sonar mode switch** (issue #14): when switching to Sonar EQ for the first time, `Arctis_Game` was rerouted to `effect_input.sonar-game-eq` before `sonar-game-eq.conf` existed. The node was never created, leaving the Game sink connected to nothing. Fixed by calling `ensure_sonar_eq_configs()` before `generate_virtual_sinks_conf()` in the mode-switch worker, and from `check_and_fix_stale_configs()` at startup.

## [1.0.25] - 10 April 2026

### Fixed

- **`install.sh` PATH regression** (issue #3): `export PATH="$HOME/.local/bin:$PATH"` was only executed inside the `uv` install block. On a reinstall (uv already present), all `asm-cli` and `asm-daemon` calls silently failed with *command not found*, preventing udev rule installation, service file creation, and `systemctl enable`. PATH is now exported unconditionally at the top of the script.

## [1.0.21] - 9 April 2026

### Fixed

- **PPA source build**: replaced `debuild -- --no-pre-clean` with `dpkg-buildpackage -S --no-pre-clean` — `debuild` was injecting `--rules-target` before the extra argument, calling `debian/rules --no-pre-clean` as a make target (exit 2). `dpkg-buildpackage` accepts `--no-pre-clean` directly and skips the clean step so `debian/wheels/` survives into the source package.

## [1.0.20] - 9 April 2026

### Fixed

- **PPA build failure** (`Failed to build` on Launchpad): `debuild -S` runs `debian/rules clean` before packaging the source, deleting `debian/wheels/` — Launchpad received a source package without wheels and couldn't download them (no internet in sandbox). Fixed by:
  - Adding `--no-pre-clean` to `debuild -S` so wheels survive source packaging
  - Removing `debian/wheels` from `override_dh_auto_clean` so Launchpad's pre-build clean doesn't destroy them either

## [1.0.19] - 9 April 2026

### Fixed

- **PPA upload workflow**: removed `oracular` (Ubuntu 24.10, EOL since July 2025) from the target matrix — Launchpad no longer accepts uploads for this series. Only `noble` (24.04 LTS) is targeted.

## [1.0.18] - 9 April 2026

### Fixed

- **PPA upload workflow**: `sed` pattern `(.*)` in basic regex matched only literal parentheses, leaving the previous distroseries name in place — Launchpad rejected uploads with `Unable to find distroseries: oracular noble`. Fixed by replacing the entire first changelog line.

## [1.0.17] - 9 April 2026

### Fixed

- **Arctis Nova Pro Wired config** (`0x12cb` / `0x12cd`): corrected two issues found via hardware testing (`asm-cli tools arctis-devices`):
  - `command_transport: ctrl_output` — device has no interrupt OUT endpoint, commands must be sent via HID SET_REPORT
  - `command_padding.length: 16` — IN endpoint MaxPacketSize is 16 bytes (was incorrectly set to 64)

## [1.0.16] - 8 April 2026

### Added

- **Arctis Nova Pro Wired support** (`0x12cb`) and its Xbox variant (`0x12cd`): sidetone, mic volume, mic LED brightness, audio gain (low/high), ChatMix, EQ. Protocol confirmed from reverse-engineering of the Nova Pro family (shared HID interface 4, same 0x06-prefixed command set as Nova Pro Wireless).

## [1.0.15] - 8 April 2026

### Fixed

- **Context-aware privilege elevation** (`sudo_it`): the elevation tool is now chosen based on the execution context rather than tried blindly in a fixed order:
  - **Terminal (TTY)** → `sudo` first (native prompt), then graphical tools as fallback
  - **GUI / no TTY** → DE-specific graphical tools (`kdesu` on KDE, `lxqt-sudo` on LXQt) then `pkexec` — `sudo` is never attempted without a TTY
  - **Headless / no display** → `sudo` only (service context, NOPASSWD sudoers)
  - Prints an actionable hint when no tool is found in headless context

## [1.0.14] - 8 April 2026

### Added

- **udev rules check at startup**: GUI shows a dialog at startup when udev rules are missing or invalid, with an "Install rules" button that runs the fix automatically (requires administrator password). The daemon also logs a warning on the same condition.

### Fixed

- **`sudo_it()` fallback**: now tries `sudo` if `pkexec` is unavailable or fails, and catches `CalledProcessError` instead of crashing.
- **`write_udev_rules()` blocking `input()`**: removed interactive prompt that caused hangs in non-interactive installs (scripts, TTY-less environments).
- **`write_udev_rules()` fragile `echo` escaping**: replaced shell `echo "..."` (broken on special chars like `!`, `$`) with a temp file + `cp` approach when writing with elevated privileges.
- **`reload_udev_rules()` unhandled exception**: `CalledProcessError` from `udevadm` is now caught and reported cleanly.

## [1.0.13] - 4 April 2026

### Fixed

- **`DeviceConfiguration` uninitialized attributes**: `device_init` and `status` could raise `AttributeError` on devices missing these optional YAML fields — now always initialized to `None`.
- **Settings SELECT widget crash**: `IndexError`/`KeyError` when options list is empty or not yet loaded — callback now guards bounds before calling.
- **`device_state` thread safety**: module-level globals now protected by `threading.Lock`, preventing torn reads between USB monitor thread and main loop.
- **PipeWire `node.target = ""`**: output channel EQ configs omitted the property entirely when target is empty (invalid SPA JSON value).
- **`command_interface_index` type annotation**: was annotated as `tuple[int, int]`, actual type is `list[int]`.

## [1.0.12] - 4 April 2026

### Fixed

- **LADSPA control parameters**: noise gate, RNNoise noise cancellation, and SC4M compressor were silently broken — missing `=` signs in PipeWire filter-chain control syntax caused all mic processing nodes to have no effect.
- **`check_and_fix_stale_configs`**: output channel stale configs were incorrectly routed to the physical device instead of passthrough.
- **`get_physical_source()` retry**: microphone source detection now retries 15× on PulseAudio error, matching sink detection resilience.
- **D-Bus wrapper**: all async D-Bus methods now guard against connection failure; the status/settings polling loop no longer crashes permanently on a transient D-Bus error.

## [1.0.11] - 4 April 2026

### Added

- **HeSuVi universal surround**: Spatial Audio (7.1 HeSuVi) now works on all supported headsets, not just the Nova Pro Wireless.
- **Device-aware PipeWire configs**: ALSA node names are discovered dynamically at device connect, fixing audio routing for Nova 5, Nova 7, Arctis 7, Arctis 9, Arctis Pro Wireless, and more.
- **Microphone source discovery**: physical microphone node auto-detected by USB vendor/product ID.

### Fixed

- Audio routing configs were hardcoded for the Nova Pro Wireless ALSA names, causing broken Sonar EQ and surround for all other headsets.

## [1.0.7] - 1 April 2026

### Added

- **One-click update**: "Update now" button in the update banner downloads and installs the latest version automatically via pipx/pip, then restarts the daemon and GUI.

## [1.0.6] - 1 April 2026

### Added

- **Debian/Ubuntu packaging**: `.deb` package built automatically and attached to GitHub releases.
- **Launchpad PPA**: `ppa:loteran/arctis-sound-manager` for easy `apt install` on Ubuntu 24.04+.
- **PPA upload workflow**: GitHub Actions automatically uploads source packages to PPA on each release.

## [1.0.4] - 29 March 2026

### Added

- **External output volume**: HDMI/sound card volume card on home page with auto-detection of non-SteelSeries sinks.
- **Stream routing to output**: "O" button on app tags routes streams through the output EQ node.
- **Sonar Output EQ**: new Equalizer tab with 8ch 7.1 parametric EQ for external output (no HeSuVi surround).
- **Configurable output device**: Settings dropdown to select external output sink.
- **Update notification**: banner on home page when a newer release is available (checks GitHub once/day).
- **Clean exit**: Exit from systray stops all ASM services and restarts pipewire.

### Fixed

- **Output EQ not applying**: filter-chain capture node used `Audio/Sink/Internal`; changed to `Audio/Sink` for the output channel.
- **Audio loss on preset apply**: output channel now only restarts `filter-chain` instead of full pipewire stack.
- **Apps invisible after routing to output**: streams routed through EQ now appear on the Output card.
- **Settings layout**: fixed label widths, toggle alignment, dropdown sizing.

## [1.0.1] - 29 March 2026

### Fixed

- **ANC/Transparent mode stuck**: device init sequence hardcoded ANC off, preventing GUI toggle when settings had a different saved value. Init now uses `settings.noise_cancelling` / `settings.transparent_level` references.

## [1.0.0] - 28 March 2026

### Added

- **Sonar EQ system**: full parametric EQ via PipeWire filter-chain biquad filters
- Interactive EQ curve widget with draggable bands
- Game (8ch 7.1) / Chat (2ch stereo) / Microphone tabs
- Preset system with 297+ presets, save/load, favorites
- Macro sliders: Bass, Voice, Treble
- Spatial Audio toggle with HeSuVi 7.1 virtual surround
- Volume Boost and Smart Volume (dynamic compressor SC4M)
- Micro processing: AI Noise Cancellation (rnnoise), Noise Gate, Compressor
- ANC / Transparent mode control from GUI
- 4-channel audio mixer (Game / Chat / Media)
- Automatic media routing via asm-router
- Device status: battery, mic mute, sidetone
- 10-band Custom EQ with save/load presets
- Full i18n: English, French, Spanish
- AUR package
- 56 unit tests
