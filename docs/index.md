---
title: Arctis Sound Manager
description: >-
  A Linux application for SteelSeries Arctis headsets: Sonar-style equalizer,
  ChatMix, a Game/Chat/Media/Output mixer, sidetone, ANC, battery and OLED —
  built on PipeWire.
---

<p align="center">
  <img src="images/asm-logo.png" alt="Arctis Sound Manager logo" width="150">
</p>

# Arctis Sound Manager

**SteelSeries GG and Sonar are Windows-only.** Arctis Sound Manager is the Linux
application that replaces them: a parametric equalizer per channel, a
Game / Chat / Media / Output mixer, a working ChatMix dial, spatial audio, and
the headset's own settings — sidetone, ANC, inactivity timeout, battery, OLED.

It is built on PipeWire and speaks to the headset over its USB protocol, so it
needs neither Wine nor a vendor driver.

[Install](https://github.com/loteran/Arctis-Sound-Manager#installation) ·
[Latest release](https://github.com/loteran/Arctis-Sound-Manager/releases/latest) ·
[Source](https://github.com/loteran/Arctis-Sound-Manager) ·
[Discussions](https://github.com/loteran/Arctis-Sound-Manager/discussions)

## Watch the presentation

<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;max-width:100%;">
  <iframe src="https://www.youtube-nocookie.com/embed/j8nwJNg1EzA" title="Arctis Sound Manager — SteelSeries GG &amp; Sonar for Linux" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" allow="accelerometer; encrypted-media; gyroscope; picture-in-picture" allowfullscreen loading="lazy"></iframe>
</div>

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "VideoObject",
  "name": "Arctis Sound Manager — SteelSeries GG & Sonar for Linux (free & open source)",
  "description": "A free, open-source replacement for SteelSeries GG and Sonar on Linux: channel mixer, Sonar parametric EQ, spatial audio, microphone processing, battery, ANC, sidetone, base station OLED, streamer mode and clips for 20+ Arctis headsets, built on PipeWire.",
  "thumbnailUrl": "https://i.ytimg.com/vi/j8nwJNg1EzA/maxresdefault.jpg",
  "uploadDate": "2026-11-02T22:00:00+01:00",
  "duration": "PT2M48S",
  "embedUrl": "https://www.youtube.com/embed/j8nwJNg1EzA",
  "contentUrl": "https://www.youtube.com/watch?v=j8nwJNg1EzA"
}
</script>

## Install

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

Detects your distribution and installs the native package — Arch, Fedora,
Nobara, Debian, Ubuntu, Bazzite, SteamOS. Prefer running the commands yourself?
They are on the
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation).

Check your model on the [supported devices](device_support.md) page first: every
headset is listed with its USB Product ID.

## What it replaces

- **[SteelSeries headset on Linux](steelseries-headset-linux.md)** — what works
  out of the box, what needs GG, and which headsets are supported.
- **[SteelSeries Sonar on Linux](steelseries-sonar-linux.md)** — what Sonar
  actually does, what is reproduced, and what is not.
- **[Arctis ChatMix on Linux](arctis-chatmix-linux.md)** — why the dial does
  nothing out of the box, and how it is made to work again.

By headset:
[Arctis Nova Pro Wireless](arctis-nova-pro-wireless-linux.md) ·
[Arctis Nova 7](arctis-nova-7-linux.md) ·
[Arctis 7](arctis-7-linux.md)

## Documentation

- [Supported devices](device_support.md) — every supported headset, with its USB Product ID.
- [Troubleshooting](arctis-linux-troubleshooting.md) — no sound, ChatMix doing nothing, headset
  not recognised, the GUI failing to start.
- [Video Clips](clips.md) — the opt-in screen recorder: installing it, removing it, and what
  its per-channel audio tracks are for.
- [Device configuration file specs](device_configuration_file_specs.md) — how a headset is described
  in YAML, and what it takes to add a new one.
- [D-Bus interface](dbus.md) — the daemon's D-Bus API, for scripting and integrations.
- [Hardware questions](HARDWARE-QUESTIONS.md) — what to capture when a headset is not yet supported.
- [Community stats](stats/) — which headsets and distributions people actually run ASM on.

## Community

- [ASM Presets](https://loteran.github.io/asm-presets/) — browse and share community EQ presets.
- [ASM Themes](https://loteran.github.io/asm-presets/#themes) — browse and share colour themes,
  previewed on a miniature of the app.
- [Discord](https://discord.gg/tbG4D5AnVz) — get help, share presets, report what works.
- [Crowdin](https://crowdin.com/project/arctis-sound-manager) — help translate ASM into your language.

Arctis Sound Manager is free software, released under the
[GPL-3.0](https://github.com/loteran/Arctis-Sound-Manager/blob/main/LICENSE).
