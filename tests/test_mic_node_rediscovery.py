# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #206: the mic node is discovered once, and an empty result stuck.

The report, and this file with it, first put the cause down to PipeWire taking
longer than the 8 x 0.5 s retry budget in _discover_physical_nodes(). That is
not what happens, and test_mic_gets_one_look_not_the_retry_budget below pins
the real shape: the loop returns on the first pass where a *sink* matches, and
reports whatever the source lookup yielded on that same pass. With the sink
present immediately, which is the usual case, the microphone gets exactly one
look. The budget is never spent on it, so raising `attempts` would have changed
nothing.

The reporter's journal shows the daemon detaching and reattaching the USB
kernel driver twice right after connect to push on-device EQ, with the mic node
appearing after that churn: enough to miss that single look. The empty result
was cached in device_state for the rest of the session:
resolve_micro_input_source() returned "", ensure_micro_capture_link()
short-circuited before ever asking for a link, and the watchdog retried a link
it could never attempt. Nothing re-ran the discovery, so only a reconnect could
clear it, racing the same window again.

His hardware node was present and healthy the whole time; a manual `pw-link`
worked. The daemon simply never looked again.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import device_state
from arctis_sound_manager.core import CoreEngine


@pytest.fixture(autouse=True)
def clean_state():
    device_state.clear()
    yield
    device_state.clear()


def _engine(discovered_source):
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._device_lock = __import__("threading").RLock()
    engine.usb_device = MagicMock(idProduct=0x22a1)
    engine.device_config = MagicMock(vendor_id=0x1038)
    engine._discover_physical_nodes = MagicMock(
        return_value=("sink_game", "sink_chat", discovered_source))
    return engine


def _connected(physical_in: str):
    device_state.set_current_device(
        physical_out_game="alsa_output.game", physical_out_chat="alsa_output.chat",
        physical_in=physical_in, spatial_engine="hesuvi", device_name="Nova 7",
    )


def test_an_empty_mic_node_is_looked_up_again():
    """The state the reporter was stuck in until a full reinstall."""
    _connected(physical_in="")
    engine = _engine("alsa_input.usb-SteelSeries_Arctis_Nova_7-00.mono-fallback")

    asyncio.run(engine._rediscover_physical_input_if_missing())

    assert device_state.get_physical_in() == (
        "alsa_input.usb-SteelSeries_Arctis_Nova_7-00.mono-fallback")
    engine._discover_physical_nodes.assert_called_once()


def test_a_known_mic_node_costs_nothing():
    """Runs on every watchdog tick, so the normal case must not look."""
    _connected(physical_in="alsa_input.already-known")
    engine = _engine("alsa_input.something-else")

    asyncio.run(engine._rediscover_physical_input_if_missing())

    engine._discover_physical_nodes.assert_not_called()
    assert device_state.get_physical_in() == "alsa_input.already-known"


def test_the_other_device_state_fields_survive_the_update():
    """Rewriting device_state must not lose the sinks or the device name."""
    _connected(physical_in="")
    engine = _engine("alsa_input.mic")

    asyncio.run(engine._rediscover_physical_input_if_missing())

    assert device_state.get_physical_out_game() == "alsa_output.game"
    assert device_state.get_physical_out_chat() == "alsa_output.chat"
    assert device_state.get_device_name() == "Nova 7"
    assert device_state.get_spatial_engine() == "hesuvi"


def test_still_absent_leaves_the_state_alone():
    _connected(physical_in="")
    engine = _engine(None)

    asyncio.run(engine._rediscover_physical_input_if_missing())

    assert device_state.get_physical_in() == ""


def test_no_device_means_no_lookup():
    engine = _engine("alsa_input.mic")
    engine.usb_device = None

    asyncio.run(engine._rediscover_physical_input_if_missing())

    engine._discover_physical_nodes.assert_not_called()


def test_a_failing_lookup_never_escapes():
    """This runs inside the watchdog; raising here would stop every other hop."""
    _connected(physical_in="")
    engine = _engine("alsa_input.mic")
    engine._discover_physical_nodes.side_effect = RuntimeError("pulse is down")

    asyncio.run(engine._rediscover_physical_input_if_missing())

    assert device_state.get_physical_in() == ""


def test_mic_gets_one_look_not_the_retry_budget():
    """The `attempts` budget belongs to the sink, and the mic rides along.

    Pinned because the comment that used to sit above the watchdog call said
    the opposite: that the mic had 8 x 0.5 s and PipeWire was simply slower.
    Anyone acting on that would widen `attempts` and change nothing at all,
    because the loop has already returned. What actually decides the mic's
    fate is whether it happens to be enumerated on the one pass where the
    sink matches.

    This is not asking for the behaviour to change: routing should not wait on
    a capture node. It exists so the next reader sees why the fix is a second
    look later, not a bigger window here.
    """
    source_calls = {"n": 0}

    def _sinks(**_kw):
        sink = MagicMock()
        sink.name = "alsa_output.arctis"
        return sink, sink

    def _source(**_kw):
        # The mic shows up on the 4th pass. It never gets asked a 4th time.
        source_calls["n"] += 1
        if source_calls["n"] < 4:
            return None
        node = MagicMock()
        node.name = "alsa_input.arctis"
        return node

    # Not MagicMock(spec=CoreEngine): pa_audio_manager is set in __init__, so a
    # class-spec'd mock refuses it. The method is called unbound on a stand-in
    # carrying only what it actually reads.
    engine = MagicMock()
    engine.pa_audio_manager.get_arctis_sinks_classified.side_effect = _sinks
    engine.pa_audio_manager.get_physical_source.side_effect = _source
    engine.device_config.product_ids = [0x22A1]

    game, _chat, source = CoreEngine._discover_physical_nodes(
        engine, 0x1038, 0x22A1, attempts=8, delay=0.0,
    )

    assert game == "alsa_output.arctis"
    assert source is None, "the mic would have to be found on the sink's pass"
    assert source_calls["n"] == 1, (
        f"the mic was looked up {source_calls['n']} times: the loop now spends "
        f"its retry budget on the source too. That is a real improvement, but "
        f"it changes when the mic becomes available, so update the comments in "
        f"core.py that say the mic only ever gets one look."
    )
