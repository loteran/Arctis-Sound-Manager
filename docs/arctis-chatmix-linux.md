---
title: Arctis ChatMix on Linux
description: >-
  The ChatMix dial does nothing on Linux out of the box, because the balancing
  happens on the PC and not in the headset. Here is why, and how Arctis Sound
  Manager makes the wheel work again with PipeWire.
---

# Arctis ChatMix on Linux

Plug an Arctis headset into a Linux machine and the ChatMix dial turns freely
and changes nothing. The hardware is not at fault, and neither is the driver:
the dial was never meant to do the work itself.

[Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager) makes it
work again, natively on PipeWire.

## Why the wheel does nothing

On Windows, a SteelSeries headset presents **two separate sound cards**: one for
game audio, one for chat. Applications are assigned to one or the other, and the
dial does not touch either device — it reports its position over USB, and
SteelSeries GG turns that into a volume balance between the two.

Remove the software and the dial goes inert on Windows too. It is a sensor, not
a volume control.

Linux receives that sensor reading perfectly well. What is missing is the half
that listens to it, knows which application is game and which is chat, and moves
their volumes in opposite directions.

## What Arctis Sound Manager does

It supplies that missing half.

**It reads the dial** over USB HID, continuously. The control is analogue, so
its raw value drifts by a step or two while nobody is touching it; ASM filters
that jitter out rather than nudging your volumes on its own.

**It provides the channels to balance.** Game, Chat, Media and Output exist as
ordinary PipeWire output devices, so you assign applications the usual way — in
your desktop's sound settings, in `pavucontrol`, or in the application itself.
Your voice client goes to Chat, your game to Game, and the dial finally has two
things to balance.

**It applies the balance live** as the wheel turns, in the PipeWire graph. No
device switching, no interruption to what is already playing.

One quirk worth knowing if you read the logs or the code: the firmware calls the
dial's two halves *chat mix* and *media mix*, and its **"media" half drives the
Game channel**. The naming is the headset's, not ours — and taking it at face
value is how audio that should follow the system default ends up filed as game
audio, unbalancing the very dial you are turning.

## Which headsets have it

Every Arctis with a physical ChatMix dial, including the Nova Pro Wireless, Nova
Pro Wired, Nova Pro Omni, Nova Elite, Nova 7 and Nova 5 families. The
[supported devices page](device_support.md) lists every model with its USB
Product ID.

Headsets without the dial still get the four channels and the mixer — you
balance them from the application instead of the wheel.

## Getting it

```
curl -fsSL https://loteran.github.io/Arctis-Sound-Manager/install.sh | bash
```

The script detects your distribution and installs the native package. Arch,
Fedora, Nobara, Debian, Ubuntu, Bazzite and SteamOS are covered; the
[project page](https://github.com/loteran/Arctis-Sound-Manager#installation) has
the per-distribution commands if you would rather run them yourself.

If the dial moves but the balance is erratic, that is worth a
[bug report](https://github.com/loteran/Arctis-Sound-Manager/issues) — the
filtering is tuned per model and some report their position differently.
