# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Remembers which output the user prefers, and what to fall back to.

A single saved ``external_output_device`` cannot express what people actually
do. Someone listening on Bluetooth earbuds wants the Output channel on the
earbuds — until the earbuds go back in their case, at which point they want the
headset, and when the earbuds come back they want the earbuds again. A lone
saved value gives one of those and silently breaks the others: pinned to the
earbuds it goes nowhere when they vanish, pinned to the headset it ignores the
choice that was actually made.

So preference is stored as an *order*, not a value. The most recently chosen
device is first, everything chosen before it follows, and resolution picks the
highest-ranked device that is currently present. Falling back and coming back
are then the same operation, and neither loses the user's intent.

Pure logic: no Qt, no PipeWire, no I/O beyond one small JSON file, so the
fallback rules can be tested without any audio hardware.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MEMORY_FILE = Path.home() / ".config" / "arctis_manager" / "output_preference.json"

# How many past choices to keep. Deep enough to survive a laptop's worth of
# docks and headsets, short enough that a device retired years ago cannot
# outrank the one in use.
MAX_REMEMBERED = 12


class OutputMemory:
    """An ordered preference list over output device ids."""

    def __init__(self, order: list[str] | None = None):
        self.order: list[str] = list(order or [])

    # ── persistence ───────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path = MEMORY_FILE) -> "OutputMemory":
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        order = raw.get("order") if isinstance(raw, dict) else None
        if not isinstance(order, list):
            return cls()
        return cls([d for d in order if isinstance(d, str)])

    def save(self, path: Path = MEMORY_FILE) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"order": self.order}, indent=2))
        except OSError as exc:
            log.warning("could not save the output preference: %s", exc)

    # ── preference ────────────────────────────────────────────────────────────

    def remember(self, device_id: str) -> None:
        """Record *device_id* as the most-preferred output."""
        if not device_id:
            return
        if device_id in self.order:
            self.order.remove(device_id)
        self.order.insert(0, device_id)
        del self.order[MAX_REMEMBERED:]

    def forget(self, device_id: str) -> None:
        if device_id in self.order:
            self.order.remove(device_id)

    def resolve(self, available: list[str], fallback: str | None = None) -> str | None:
        """The best present device: highest-ranked known one, else *fallback*.

        *fallback* is used only when nothing remembered is plugged in — it is
        the headset in practice, which is the sensible destination for audio
        that would otherwise have nowhere to go. When even that is absent the
        first available device is taken, because returning None here would
        leave the Output channel pointing at a device that does not exist.
        """
        present = set(available)
        for device_id in self.order:
            if device_id in present:
                return device_id
        if fallback and fallback in present:
            return fallback
        return available[0] if available else None

    def is_fallback(self, resolved: str | None) -> bool:
        """True when *resolved* is not the user's top choice.

        Lets the UI say "temporarily on the headset" rather than implying the
        user picked it — the difference between a considered state and a bug,
        as far as anyone reading the screen is concerned.
        """
        if resolved is None or not self.order:
            return False
        return resolved != self.order[0]

    @property
    def preferred(self) -> str | None:
        return self.order[0] if self.order else None
