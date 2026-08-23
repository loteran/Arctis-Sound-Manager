---
title: SteelSeries Sonar on Linux
description: >-
  SteelSeries Sonar and GG are Windows-only. Here is what they actually do,
  what Linux gives you instead, and how Arctis Sound Manager rebuilds the
  equalizer and the per-application audio channels on PipeWire.
---

# SteelSeries Sonar on Linux

SteelSeries Sonar ships as part of **SteelSeries GG**, and GG is Windows-only.
There is no Linux build, no Wine path that works — GG drives the headset over
USB HID and installs an audio driver stack Windows owns — and no announced plan
for one. Plugging an Arctis headset into a Linux machine gives you sound and
nothing else: no equalizer, no separate game and chat channels, no ChatMix, no
sidetone control.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) is a
Linux application that rebuilds those features natively, using PipeWire for the
audio side and the headset's own USB protocol for the device side.

## What Sonar actually does

Worth separating, because only part of it is about the headset at all:

**A parametric equalizer**, applied per audio channel rather than to the whole
system. This is pure software — it runs on the PC, not in the headset.

**Separate virtual audio devices** — Game, Chat, Media, Aux — so a game, a voice
client and a music player each land on their own channel and can be balanced
independently. Also pure software: Windows sees several sound cards that do not
exist.

**Device settings** — sidetone, ANC, inactivity timeout, EQ presets stored in
the headset. This part *is* the headset, over USB HID.

Nothing here needs Windows. It needs an equalizer engine, virtual audio devices,
and knowledge of the USB protocol.

## What Arctis Sound Manager does instead

**The equalizer** is built on the PipeWire filter-chain: a parametric EQ per
channel, applied live, with presets. Because it is a PipeWire graph rather than
a driver, it works with whatever else you run — no exclusive mode, no resampling
surprise.

**The channels** are real PipeWire nodes: Game, Chat, Media and Output appear as
ordinary output devices, so you set them per application in your desktop's sound
settings, `pavucontrol`, or the app itself. A four-channel mixer balances them,
and media routing can follow the headset automatically when it powers on.

**The device settings** are spoken over USB HID: sidetone, ANC, inactivity
timeout, battery, ChatMix, and the OLED screen on models that have one. Each
headset is described in a YAML file, so support is data rather than code — see
[the device configuration specs](device_configuration_file_specs.md).

Spatial audio is there too, through HeSuVi-compatible HRIR convolution — the
Linux equivalent of Sonar's virtual surround.

## What is not the same

Straight answers, because being sold a like-for-like replacement helps nobody:

- **Presets are not shared with GG.** Sonar's presets live in SteelSeries'
  format on Windows. ASM has its own, plus a
  [community preset site](https://loteran.github.io/asm-presets/).
- **Sonar's AI noise cancellation is not reproduced.** Microphone noise
  suppression is available through standard Linux filters, which are not the
  same model and do not sound identical.
- **PipeWire is required.** PulseAudio-only systems are out of scope; every
  modern desktop distribution ships PipeWire.

## Getting it

One command, which detects your distribution and installs the native package:

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

Arch, Fedora, Nobara, Debian, Ubuntu, Bazzite and SteamOS are covered, along
with a per-distribution package list on the
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation).

Check your model first on the
[supported devices page](device_support.md) — every headset is listed with its
USB Product ID, so you can confirm before installing.
