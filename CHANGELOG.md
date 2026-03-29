# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
