# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for per-channel output device resolution.

Choosing a device for Game, Chat or Media must mean "send this channel there".
The home page used to implement it by dragging the channel's *applications*
onto the chosen sink, which the routing-override replay then undid on its next
pass — the selection reverted within seconds and read as a broken control. The
destination is now resolved here and consumed by the same enforcement passes
that already own those links, so there is exactly one owner and nothing to
contest the change.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from arctis_sound_manager import sonar_to_pipewire as s2p

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"
CHAT_PCM = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.mono-chat"
BUDS = "bluez_output.30_96_10_49_54_E2.1"


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """Point the channel-output file at a temporary copy."""
    path = tmp_path / "channel_output_devices.json"
    monkeypatch.setattr(s2p, "CHANNEL_OUTPUTS_FILE", path)

    def write(mapping: dict) -> None:
        path.write_text(json.dumps(mapping))

    return write


def _resolve(channel: str, present: list[str]):
    with patch.object(s2p, "_get_physical_out_game", return_value=HEADSET), \
         patch.object(s2p, "_get_physical_out_chat", return_value=CHAT_PCM), \
         patch("arctis_sound_manager.pw_utils.pw_node_exists",
               side_effect=lambda name, data=None: name in present):
        return s2p.channel_destination(channel)


def test_unset_channel_uses_the_headset(outputs):
    outputs({})

    assert _resolve("game", [HEADSET]) == HEADSET


def test_chat_falls_back_to_its_own_pcm(outputs):
    """Chat has a separate mono PCM on dual-PCM devices; it must not inherit
    the game output."""
    outputs({})

    assert _resolve("chat", [CHAT_PCM]) == CHAT_PCM


def test_chosen_device_is_used(outputs):
    outputs({"game": BUDS})

    assert _resolve("game", [HEADSET, BUDS]) == BUDS


def test_channels_are_independent(outputs):
    """The point of the feature: Game on the earbuds while Media stays put."""
    outputs({"game": BUDS})

    assert _resolve("game", [HEADSET, BUDS]) == BUDS
    assert _resolve("media", [HEADSET, BUDS]) == HEADSET


def test_absent_device_falls_back_rather_than_going_silent(outputs):
    """Earbuds back in their case: the channel must reach the headset, not stay
    linked to a sink that is no longer in the graph."""
    outputs({"game": BUDS})

    assert _resolve("game", [HEADSET]) == HEADSET


def test_device_is_picked_up_again_when_it_returns(outputs):
    """The choice is not erased by a disconnect — no second trip to settings."""
    outputs({"game": BUDS})
    assert _resolve("game", [HEADSET]) == HEADSET

    assert _resolve("game", [HEADSET, BUDS]) == BUDS


def test_choosing_the_headset_explicitly_is_not_a_lookup(outputs):
    """Selecting the headset resolves without asking the graph about it."""
    outputs({"game": HEADSET})

    assert _resolve("game", []) == HEADSET


def test_missing_file_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(s2p, "CHANNEL_OUTPUTS_FILE", tmp_path / "nope.json")

    assert _resolve("game", [HEADSET]) == HEADSET


def test_corrupt_file_is_harmless(outputs, tmp_path):
    (tmp_path / "channel_output_devices.json").write_text("{not json")

    assert _resolve("game", [HEADSET]) == HEADSET


def test_unexpected_shape_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "channel_output_devices.json"
    path.write_text(json.dumps(["not", "a", "mapping"]))
    monkeypatch.setattr(s2p, "CHANNEL_OUTPUTS_FILE", path)

    assert _resolve("game", [HEADSET]) == HEADSET


def test_no_device_attached_resolves_to_nothing(outputs):
    outputs({})
    with patch.object(s2p, "_get_physical_out_game", return_value=""), \
         patch("arctis_sound_manager.pw_utils.pw_node_exists", return_value=False):
        assert s2p.channel_destination("game") == ""
