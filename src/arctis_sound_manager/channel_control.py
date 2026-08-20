# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Adjust a channel's volume from outside the GUI (issue #193).

The request was Sonar's keybind system: turn a dial on a macropad, have one
channel go up or down by a fixed step, without alt-tabbing. On Wayland an
application cannot claim a key combination for itself — the compositor owns
that, and the GlobalShortcuts portal only lets an app *ask* for a trigger it
may not get, when the portal exists at all (KDE and Hyprland have it, GNOME
has been without one for a long time).

Every desktop, on the other hand — and X11, and every window manager — can
already bind an arbitrary key to a *command*. So the channels are exposed as
commands. A macropad sending F20 is then bound the same way the user binds
everything else, on any desktop, with no portal involved and nothing for ASM
to keep in sync with the compositor.

Deliberately independent of the running daemon: the volume is applied through
PipeWire directly, so a bound key works whether or not the GUI is open, and
the value is persisted through the same file the daemon restores from, so the
change survives the channel being rebuilt.
"""

from __future__ import annotations

import logging
import re

from arctis_sound_manager.channel_volumes import (load_channel_volumes,
                                                  save_channel_volume)
from arctis_sound_manager.loopback_manager import DEFAULT_SINKS

logger = logging.getLogger(__name__)

# Accepted channel names, mapped to the sink applications actually play into.
CHANNELS: dict[str, str] = {
    entry["channel"]: entry["capture_name"] for entry in DEFAULT_SINKS
}

# What one argument may look like: +5, -10, 50, mute, unmute, toggle.
_STEP_RE = re.compile(r"^([+-])(\d{1,3})$")
_ABSOLUTE_RE = re.compile(r"^(\d{1,3})$")
_MUTE_WORDS = {"mute": True, "unmute": False, "toggle": None}


class ChannelError(Exception):
    """Raised for anything the caller got wrong — bad channel, bad action."""


def _clamp(pct: int) -> int:
    return max(0, min(100, pct))


def _find_sink(pulse, node_name: str):
    for sink in pulse.sink_list():
        if sink.proplist.get("node.name", "") == node_name:
            return sink
    return None


def apply(channel: str, action: str) -> str:
    """Apply one *action* to one *channel*. Returns a line describing the result.

    Raises ChannelError on invalid input, and RuntimeError when the channel is
    not currently present — a channel that does not exist yet is a state worth
    reporting rather than silently succeeding.
    """
    channel = channel.lower()
    if channel not in CHANNELS:
        raise ChannelError(
            f"unknown channel {channel!r} — pick one of: "
            + ", ".join(sorted(CHANNELS))
        )
    node_name = CHANNELS[channel]

    import pulsectl

    with pulsectl.Pulse("asm-channel-control") as pulse:
        sink = _find_sink(pulse, node_name)
        if sink is None:
            raise RuntimeError(
                f"channel {channel!r} is not there right now "
                f"(no sink named {node_name}) — is ASM running?"
            )

        if action in _MUTE_WORDS:
            wanted = _MUTE_WORDS[action]
            if wanted is None:
                wanted = not bool(sink.mute)
            pulse.mute(sink, wanted)
            return f"{channel}: {'muted' if wanted else 'unmuted'}"

        current = round(pulse.volume_get_all_chans(sink) * 100)

        step = _STEP_RE.match(action)
        absolute = _ABSOLUTE_RE.match(action)
        if step:
            sign, amount = step.group(1), int(step.group(2))
            target = _clamp(current + (amount if sign == "+" else -amount))
        elif absolute:
            target = _clamp(int(absolute.group(1)))
        else:
            raise ChannelError(
                f"unknown action {action!r} — expected +N, -N, a number, "
                "mute, unmute or toggle"
            )

        pulse.volume_set_all_chans(sink, target / 100)

    # Persisted after the connection closes: the daemon restores this file when
    # it rebuilds a channel, so a key-bound change is not lost on the next
    # loopback recreation.
    save_channel_volume(node_name, target)
    return f"{channel}: {current}% → {target}%"


def show() -> list[str]:
    """One line per channel: its level now, and whether it is muted."""
    import pulsectl

    saved = load_channel_volumes()
    lines: list[str] = []
    with pulsectl.Pulse("asm-channel-control") as pulse:
        for channel, node_name in CHANNELS.items():
            sink = _find_sink(pulse, node_name)
            if sink is None:
                remembered = saved.get(node_name)
                extra = f" (remembered: {remembered}%)" if remembered is not None else ""
                lines.append(f"{channel:6s} absent{extra}")
                continue
            pct = round(pulse.volume_get_all_chans(sink) * 100)
            lines.append(f"{channel:6s} {pct:3d}%" + ("  muted" if sink.mute else ""))
    return lines


def apply_all(pairs: list[str]) -> list[str]:
    """Apply a whole list of ``channel action channel action …`` arguments.

    Chaining was asked for explicitly: one key that moves several channels at
    once. Each pair is applied in order, and a failure on one is reported
    without abandoning the rest — a bound key doing half its job is better
    than a bound key that stops at the first channel that happens to be absent.
    """
    if not pairs or len(pairs) % 2 != 0:
        raise ChannelError(
            "expected pairs of CHANNEL ACTION, e.g. 'game +5' or 'game +5 chat -5'"
        )

    results: list[str] = []
    for channel, action in zip(pairs[0::2], pairs[1::2]):
        try:
            results.append(apply(channel, action))
        except (ChannelError, RuntimeError) as exc:
            results.append(f"{channel}: {exc}")
    return results
