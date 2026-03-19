# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

- **Critical crash fix**: `lam-gui` crashed with SIGSEGV (signal 11) when the tray menu received a status update while the popup was open, causing the entire GNOME session to crash and preventing re-login. Root cause: `menu.clear()` was called via `on_new_status()` while the Qt Wayland popup surface was already alive, leaving dangling paint events that dereferenced a freed `QWaylandWindow`. Fix: menu is now rebuilt in `start_polling()` (connected to `aboutToShow`) so the menu is always reconstructed before the Wayland surface is created, never while it is visible.

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
