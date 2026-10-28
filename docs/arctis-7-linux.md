---
title: Arctis 7 on Linux
description: >-
  The Arctis 7 and Arctis 7+ play sound on Linux out of the box. Arctis
  Sound Manager adds sidetone, battery, the auto-off timer, ChatMix and a Sonar-style equalizer, natively on PipeWire.
---

# Arctis 7 on Linux

The Arctis 7 is one of the most common SteelSeries headsets on Linux, and one
of the easiest: plug in the dongle and it shows up as a sound card, sometimes
as two. What it lacks is everything SteelSeries Engine and GG handled on
Windows — sidetone, the battery level, the auto-off timer, and an equalizer.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) (ASM)
speaks the headset's own USB protocol and brings those back, natively — no
Wine, no vendor driver.

## What you get

**Arctis 7 (2017 and 2019) and Arctis Pro 2019**

- Sidetone
- Auto-off timer
- Battery level
- The ChatMix dial, read live

**Arctis 7+ family**

All of the above, plus microphone volume, the brightness of the microphone's
mute LED, the mute state, and whether the headset is charging.

**Audio, on PipeWire — every model**

- **Game / Chat / Media / Output channels** as real sound devices, with a mixer
  and automatic routing of applications
- **ChatMix** balancing Game against Chat — see
  [Arctis ChatMix on Linux](arctis-chatmix-linux.md)
- A **Sonar-style equalizer** per channel, with GG's game presets — see
  [SteelSeries Sonar on Linux](steelseries-sonar-linux.md)
- **Spatial audio** through HRIR convolution
- Microphone noise suppression, noise gate and compressor

## Which models

- **Arctis 7** (USB ID `1038:1260`) and **Arctis 7 2019** (`1038:12ad`)
- **Arctis Pro** 2019 (`1038:1252`): audio features only (channels, Sonar EQ, spatial audio); it has no vendor channel for battery or headset settings
- **Arctis 7+**, including the PS5, Xbox and Destiny editions

The [supported devices page](device_support.md) lists every USB ID; the
Arctis Pro Wireless and Arctis Pro with GameDAC have profiles of their own.
To check yours:

```
lsusb | grep 1038
```

## Getting it

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

The script detects your distribution and installs the native package — Arch,
Fedora, Nobara, Debian, Ubuntu, Bazzite and SteamOS are covered. The
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation)
has the per-distribution commands.

Something not working? The [troubleshooting page](arctis-linux-troubleshooting.md)
covers the common cases.
