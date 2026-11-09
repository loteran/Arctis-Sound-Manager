---
title: SteelSeries headset on Linux
description: >-
  A SteelSeries Arctis headset plays sound on Linux out of the box, but its
  settings, equalizer and ChatMix need SteelSeries GG, which is Windows-only.
  Arctis Sound Manager brings them to Linux, natively, on PipeWire.
---

# SteelSeries headset on Linux

Plug a SteelSeries Arctis headset into a Linux machine and it works as a sound
card straight away: audio out, microphone in. Everything else is missing —
sidetone, noise cancelling, the inactivity timer, battery level, the equalizer,
the ChatMix dial, the OLED screen on the base station. On Windows those come
from **SteelSeries GG**, and GG has no Linux build.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) is the
Linux application that fills that gap. It is free, open source (GPL-3.0), runs
natively — no Wine, no vendor driver — and is built on PipeWire.

## What works without it, and what does not

**Works out of the box:** playback and microphone, through the kernel's USB
audio driver. On recent kernels the `hid-steelseries` driver also reports the
battery level of some wireless models.

**Does not:** anything GG does. The headset's settings are sent over USB HID in
a protocol of its own, and the Game / Chat split, the equalizer and ChatMix are
software that runs on the PC, not in the headset. Linux has neither half until
something provides it.

## What Arctis Sound Manager adds

- **Headset settings** — sidetone, active noise cancelling and transparency,
  inactivity timeout, microphone volume, battery (and the spare battery in the
  base, or the GameBuds charging case), the Nova Elite and Pro Omni's
  microphone and Bluetooth EQ presets, the Arctis Pro GameDAC's gain, line
  out and stream mix, the Arctis Pro Wireless' volume limiter and screen
  settings, the Nova Pro Wired's line out and stream mix, the mic volume of the
  Arctis 7 and Arctis 1 Wireless families, the GameBuds' audio source and touch-gesture remapping (8 gestures, 4 more than GG offers, those marked experimental), spoken over the headset's own USB protocol. On the Elite, Omni and Nova Pro Wireless, ASM takes the settings
  the base reports at start, so a change made on the base or in GG is kept.
- **The base station's OLED screen** — the EQ of what is playing, the
  now-playing track, and screen-off while you are away.
- **Game / Chat / Media / Aux / Output channels** as real PipeWire devices, with
  a mixer to balance them, a Mic card for what your apps record, and automatic
  routing of applications — or drag an app onto a channel.
- **A working ChatMix dial**, with an on-screen bar like GG's — see
  [Arctis ChatMix on Linux](arctis-chatmix-linux.md).
- **A Sonar-style equalizer** per channel, with the Sonar game presets
  included and applied automatically when a game starts — see
  [SteelSeries Sonar on Linux](steelseries-sonar-linux.md).
- **Real 7.1** on the Game, Media and Aux channels: spatial audio through HRIR
  convolution on the headset, the counterpart of Sonar's virtual surround, or
  surround passed straight through to a 5.1/7.1 receiver.
- **Streamer mode** — a second, independent mix for OBS, like Sonar's Stream
  mode.
- **Microphone effects** — noise suppression, noise gate, compressor, and the
  Sonar mic presets.
- **Clips** — save the last 30 seconds of a game with a shortcut, one audio
  track per channel; see [Clips](clips.md).
- **Stream Guard** — a Discord screen share carries only the audio you allow,
  not the whole system.
- **Desktop integration** — tray icon, Plasma 6 widget, and `asm-cli status`.

## Supported SteelSeries headsets

- Arctis Nova Pro Wireless, Nova Pro Wired, Nova Pro Omni, Nova Elite
- Arctis Nova 7 and Nova 7P (Gen 1 and Gen 2), Nova 5 Wireless, Nova 4,
  Nova 3 and Nova 3 Wireless
- Arctis 7, 7+, 7P, 7X, 9 Wireless, 1 Wireless, 5, Pro Wireless and
  Pro with GameDAC
- Arctis GameBuds

Model pages go into more detail:
[Nova Pro Wireless](arctis-nova-pro-wireless-linux.md),
[Nova 7](arctis-nova-7-linux.md), [Arctis 7](arctis-7-linux.md).

Each headset is described in a YAML profile, so adding one is data rather than
code; the [device configuration specs](device_configuration_file_specs.md)
explain the format. A headset ASM cannot talk to — or another brand entirely —
can still use the channels, the mixer and the equalizer in generic mode.

## Getting it

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

The script detects your distribution and installs the native package: Arch and
its derivatives (signed pacman repository, with the AUR as fallback), Fedora
and Nobara (COPR), Debian and Ubuntu (PPA / .deb), and Bazzite, SteamOS and
Silverblue through Distrobox. NixOS has its own module. The
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation) has
the per-distribution commands if you would rather run them yourself.

Something not working? See [troubleshooting](arctis-linux-troubleshooting.md).

Missing a model, or a setting that does nothing on yours? Open an
[issue](https://github.com/loteran/Arctis-Sound-Manager/issues) — the
[hardware questions](HARDWARE-QUESTIONS.md) page says what to capture.
