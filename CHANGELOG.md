# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
