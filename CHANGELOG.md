# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
