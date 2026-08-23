# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #206: the mic node is discovered once, and an empty result stuck.

_discover_physical_nodes() resolves the device's ALSA capture node at connect
time with a fixed budget of 8 x 0.5 s. PipeWire can take longer than that —
the reporter's journal shows the daemon detaching and reattaching the USB
kernel driver twice right after connect to push on-device EQ, and the mic node
appearing after that churn. The empty result was cached in device_state for the
rest of the session: resolve_micro_input_source() returned "",
ensure_micro_capture_link() short-circuited before ever asking for a link, and
the watchdog retried a link it could never attempt. Nothing re-ran the
discovery, so only a reconnect could clear it — racing the same window again.

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
