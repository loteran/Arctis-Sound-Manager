# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
