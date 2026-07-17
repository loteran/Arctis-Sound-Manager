# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
channel_volumes.py — Persistence of user-set virtual-sink volumes.

The Arctis virtual sinks (Game / Chat / Media) are ``pw-loopback`` processes
(see :mod:`arctis_sound_manager.loopback_manager`). Whenever a loopback is torn
down and re-created — on a PipeWire socket change, an EQ-mode switch, a config
regeneration, or a watchdog restart of a dead process — the fresh sink comes up
at the PipeWire default (100%), silently discarding the level the user had set.
From the user's point of view the Game/Chat volume "jumps to 100% on its own"
after a while (issue #134).

The GUI applies volume changes directly to the live sink, so on its own it never
remembers anything. This module gives both the GUI and the daemon a single small
JSON store, keyed by the sink's stable ``node.name`` (``Arctis_Game`` /
``Arctis_Chat`` / ``Arctis_Media``), so the daemon can re-assert the saved level
every time it (re)creates a loopback. ASM already owns the loopback→EQ link
(issue #100); this makes it own the sink volume too.

The file lives next to the other per-channel state
(``channel_output_devices.json``) at
``~/.config/arctis_manager/channel_volumes.json`` and holds a flat
``{node_name: percent}`` mapping with integer percents in ``[0, 100]``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("ChannelVolumes")

CHANNEL_VOLUMES_FILE = (
    Path.home() / ".config" / "arctis_manager" / "channel_volumes.json"
)

# Stable node.name values for the three virtual sinks the user can control.
KNOWN_SINK_NODES = ("Arctis_Game", "Arctis_Chat", "Arctis_Media")


def _clamp_pct(value: int) -> int:
    """Clamp *value* into the inclusive ``[0, 100]`` percent range."""
    return max(0, min(100, int(value)))


def load_channel_volumes() -> dict[str, int]:
    """Return the persisted ``{node_name: percent}`` map (empty if none/broken).

    Never raises: a missing, unreadable or malformed file yields ``{}`` so
    callers can treat "no saved volume" and "cannot read saved volume"
    identically — in both cases there is simply nothing to re-apply.
    """
    if not CHANNEL_VOLUMES_FILE.exists():
        return {}
    try:
        raw = json.loads(CHANNEL_VOLUMES_FILE.read_text())
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %r", CHANNEL_VOLUMES_FILE, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for node, pct in raw.items():
        if isinstance(node, str) and isinstance(pct, (int, float)):
            result[node] = _clamp_pct(int(pct))
    return result


def save_channel_volume(node_name: str, pct: int) -> None:
    """Persist *pct* for the sink *node_name*, merging with existing entries.

    Written atomically (temp file + ``replace``) so a crash mid-write cannot
    leave a truncated JSON that ``load_channel_volumes`` would then discard.
    Best-effort: I/O errors are logged, not raised — failing to remember a
    volume must never break the volume change the user just made.
    """
    data = load_channel_volumes()
    data[node_name] = _clamp_pct(pct)
    try:
        CHANNEL_VOLUMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHANNEL_VOLUMES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(CHANNEL_VOLUMES_FILE)
    except OSError as exc:
        logger.warning("Could not persist volume for %s: %r", node_name, exc)
