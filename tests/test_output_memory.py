# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for output device preference and fallback.

The scenario driving this: the Output channel is on Bluetooth earbuds, the
earbuds go back in their case, audio must land on the headset by itself — and
when the earbuds return, the channel must go back to them without the user
choosing again. A single stored device id can satisfy at most one of those
three, which is why preference is an ordered list.
"""
from __future__ import annotations

import json

from arctis_sound_manager.output_memory import MAX_REMEMBERED, OutputMemory

BUDS = "bluez_output.30_96_10_49_54_E2.1"
HEADSET = "Arctis Nova 7"
SPEAKERS = "alsa_output.pci-0000_00_1f.3.analog-stereo"


def test_chosen_device_is_used_while_present():
    mem = OutputMemory()
    mem.remember(BUDS)

    assert mem.resolve([BUDS, HEADSET], fallback=HEADSET) == BUDS


def test_falls_back_when_the_choice_disappears():
    """The earbuds go in their case: audio must not follow them into silence."""
    mem = OutputMemory()
    mem.remember(BUDS)

    assert mem.resolve([HEADSET], fallback=HEADSET) == HEADSET


def test_returns_to_the_choice_when_it_comes_back():
    """The whole point of remembering: no second trip to the settings."""
    mem = OutputMemory()
    mem.remember(BUDS)
    assert mem.resolve([HEADSET], fallback=HEADSET) == HEADSET

    assert mem.resolve([BUDS, HEADSET], fallback=HEADSET) == BUDS


def test_fallback_does_not_overwrite_the_preference():
    """Resolving to the headset must not demote the earbuds — otherwise the
    first disconnect silently erases what the user asked for."""
    mem = OutputMemory()
    mem.remember(BUDS)
    mem.resolve([HEADSET], fallback=HEADSET)

    assert mem.preferred == BUDS


def test_latest_choice_outranks_earlier_ones():
    mem = OutputMemory()
    mem.remember(HEADSET)
    mem.remember(BUDS)

    assert mem.resolve([BUDS, HEADSET], fallback=HEADSET) == BUDS


def test_older_choice_wins_over_an_unknown_device():
    """A device never chosen must not outrank one that was."""
    mem = OutputMemory()
    mem.remember(HEADSET)

    assert mem.resolve([SPEAKERS, HEADSET], fallback=None) == HEADSET


def test_unknown_devices_only_when_nothing_known_is_present():
    mem = OutputMemory()
    mem.remember(BUDS)

    assert mem.resolve([SPEAKERS], fallback=None) == SPEAKERS


def test_no_devices_at_all_resolves_to_none():
    assert OutputMemory().resolve([], fallback=HEADSET) is None


def test_is_fallback_distinguishes_a_stand_in_from_a_choice():
    mem = OutputMemory()
    mem.remember(BUDS)

    assert mem.is_fallback(HEADSET) is True
    assert mem.is_fallback(BUDS) is False


def test_is_fallback_is_false_with_no_history():
    """Nothing was ever chosen, so nothing is a substitute for it."""
    assert OutputMemory().is_fallback(HEADSET) is False


def test_reselecting_does_not_duplicate():
    mem = OutputMemory()
    mem.remember(BUDS)
    mem.remember(HEADSET)
    mem.remember(BUDS)

    assert mem.order == [BUDS, HEADSET]


def test_history_is_bounded():
    mem = OutputMemory()
    for i in range(MAX_REMEMBERED * 2):
        mem.remember(f"device-{i}")

    assert len(mem.order) == MAX_REMEMBERED
    assert mem.order[0] == f"device-{MAX_REMEMBERED * 2 - 1}"


def test_forget_drops_a_device():
    mem = OutputMemory()
    mem.remember(BUDS)
    mem.remember(HEADSET)
    mem.forget(HEADSET)

    assert mem.order == [BUDS]


def test_empty_id_is_ignored():
    mem = OutputMemory()
    mem.remember("")

    assert mem.order == []


# ── persistence ────────────────────────────────────────────────────────────────

def test_round_trip(tmp_path):
    path = tmp_path / "output_preference.json"
    mem = OutputMemory()
    mem.remember(HEADSET)
    mem.remember(BUDS)
    mem.save(path)

    assert OutputMemory.load(path).order == [BUDS, HEADSET]


def test_missing_file_is_empty(tmp_path):
    assert OutputMemory.load(tmp_path / "nope.json").order == []


def test_corrupt_file_is_empty_not_fatal(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")

    assert OutputMemory.load(path).order == []


def test_unexpected_shape_is_ignored(tmp_path):
    path = tmp_path / "odd.json"
    path.write_text(json.dumps({"order": "not-a-list"}))

    assert OutputMemory.load(path).order == []


def test_non_string_entries_are_dropped(tmp_path):
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps({"order": [BUDS, 42, None, HEADSET]}))

    assert OutputMemory.load(path).order == [BUDS, HEADSET]
