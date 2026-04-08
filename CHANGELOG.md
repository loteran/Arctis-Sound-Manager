# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
