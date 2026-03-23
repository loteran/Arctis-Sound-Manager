# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.1] - 23 March 2026

### Fixed

- **Boost Volume non-functional**: Sonar EQ configs were written to `pipewire.conf.d/` but the apply worker restarted `filter-chain` (wrong service). Configs now go to `filter-chain.conf.d/` and only `filter-chain` is restarted, preserving active audio streams.
- **Stale config cleanup**: `check_and_fix_stale_configs()` now removes orphaned sonar configs from `pipewire.conf.d/`.

### Added

- **Remove favorite preset**: right-click on a favorite slot to remove it from favorites.

## [2.7.0] - 22 March 2026

### Fixed

- **PipeWire deadlock (zero links)**: Sonar EQ filter-chain configs in `filter-chain.conf.d/` caused a WirePlumber deadlock on PipeWire 1.6.x — passive nodes from the separate filter-chain service prevented the ALSA sink from ever creating ports. All configs are now loaded by the main PipeWire daemon via `pipewire.conf.d/`.
- **Micro EQ deadlock**: the microphone filter-chain used `media.class = Audio/Sink` + `node.target` on the capture side and `Audio/Source/Virtual` on the playback side — an inverted pattern that deadlocked PipeWire. Fixed to use the correct source pattern: `node.passive = true` + `target.object` on capture, `media.class = Audio/Source` on playback (per PipeWire docs).
- **Game EQ channel mismatch**: Game EQ was 2ch stereo, bypassing HeSuVi 7.1 virtual surround. Now generates 8ch (FL FR FC LFE RL RR SL SR) with single filter nodes (PipeWire auto-duplicates per channel) and targets `effect_input.virtual-surround-7.1-hesuvi`.
- **Video router service crash loop**: systemd unit still referenced old `lam-router` binary after the v2.6.0 rename to `asm-router`.
- **Stale config detection**: `check_and_fix_stale_configs()` now detects and fixes 2ch game configs, old `Audio/Source/Virtual` micro configs, and removes stale copies from `filter-chain.conf.d/`.

### Changed

- `apply_sonar_channel()` restarts `pipewire` instead of `filter-chain` to avoid the WirePlumber deadlock.
- Chat EQ remains 2ch stereo with L/R filter pairs targeting ALSA directly.
- Deprecated `node.target` replaced with `target.object` for micro EQ capture props.

### Added

- 56 unit tests (+9 new): 8ch game, 2ch chat, HeSuVi target, auto-dup, source pattern, stale micro detection.

## [2.4.0] - 19 March 2026

### Fixed

- **Sonar EQ filters not loading**: filter-chain configs used `label = gain` (unsupported in PipeWire 1.6.x), causing silent module load failure. Replaced with `bq_highshelf` at 10 Hz as a transparent master gain substitute.
- **Race condition in EQ apply**: replaced hardcoded `msleep(900)` with active polling that waits for the Sonar sink to actually appear in PipeWire before setting default sink metadata.
- **Silent apply failures**: `_ApplyWorker` now checks `systemctl restart` return code and logs errors instead of always reporting success.
- **Stale config auto-repair**: Sonar page startup detects configs with broken `label = gain` and regenerates them automatically.
- **Boost dB range**: clamped to [-12, +12] dB to prevent digital clipping from corrupted config files.
- **pw-metadata JSON injection**: use `json.dumps()` instead of f-string formatting.
- **Critical swap bug** in `status_parser_fn.percentage()`: inverted min/max swap was a no-op.
- **Division by zero** in percentage parser when `perc_max == perc_min`.
- **Substring matching** in video router and pw_utils caused wrong sink routing.
- **Thread safety**: added `threading.RLock` for device state in core engine.
- **DBus connection leaks**: added `dbus_bus.disconnect()` in `finally` blocks.
- **Unbounded thread creation**: replaced `Thread()` with `ThreadPoolExecutor(max_workers=4)`.
- **Wayland SIGSEGV**: systray menu rebuilt instead of cleared to avoid use-after-free.
- **Mutable default argument** in `DeviceConfig.__init__`.
- **Startup order**: daemon now starts core engine before DBus manager.
- **Orphaned asyncio tasks**: cancelled on device disconnect.

### Added

- 43 unit tests covering Sonar config generation, PipeWire utils, settings, video router, and utilities.
- CI workflow fixes: added system library dependencies (libusb, libudev).
- README: documented system library requirements per distro.

## [2.3.1] - 15 March 2026

### Fixed

- **Critical crash fix**: `asm-gui` crashed with SIGSEGV (signal 11) when the tray menu received a status update while the popup was open, causing the entire GNOME session to crash and preventing re-login. Root cause: `menu.clear()` was called via `on_new_status()` while the Qt Wayland popup surface was already alive, leaving dangling paint events that dereferenced a freed `QWaylandWindow`. Fix: menu is now rebuilt in `start_polling()` (connected to `aboutToShow`) so the menu is always reconstructed before the Wayland surface is created, never while it is visible.

## [2.0.0] - 15 March 2026

### Added

- **Sonar EQ system v2.0**: full parametric EQ via PipeWire filter-chain biquad filters
- Interactive EQ curve widget with draggable bands
- Game / Chat / Microphone tabs for per-output EQ profiles
- Preset system with save/load support
- Macro sliders: Bass, Voice, Treble
- Spatial Audio toggle
- Volume Boost and Smart Volume controls

### Changed

- Full English translation of all UI strings (i18n)
- Video router rewritten with event-driven pulsectl loop (no more polling)
- README updated with screenshots for all GUI pages

### Fixed

- Audio restart race condition when toggling filter-chain (split into two phases)
- `video_router`: wrap `event_listen_stop` in lambda to match pulsectl callback signature

---

## [1.2.0] - 11 March 2026

### Fixed

- Initialize device on awake after sleep
