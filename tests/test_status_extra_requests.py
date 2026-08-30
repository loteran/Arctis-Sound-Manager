# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Arctis 7's ChatMix dial does not ride along with the battery reply, and the
dongle does not push it: SteelSeries' spec declares `game_chat_status` as a
read — out [0x06, 0x24], back [0x06, 0x24, game, chat]. device_init asked for
it once at startup and nothing ever asked again, so the mix ASM displayed — and
applied to the channel volumes — was frozen at wherever the dial stood when the
daemon started (#220).

`status.extra_requests` is what the poll sends besides the main request.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import usb.core
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _load_config(name: str) -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / name))


def _make_engine(cfg: DeviceConfiguration) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.usb_device = MagicMock()
    engine.logger = MagicMock()
    engine.sent = []
    engine.get_command_endpoint_address.return_value = 0
    engine.send_command.side_effect = lambda cmd, endpoint: engine.sent.append(list(cmd))
    return engine


def test_arctis_7_polls_the_dial_alongside_the_battery():
    cfg = _load_config("arctis_7.yaml")
    engine = _make_engine(cfg)

    CoreEngine.request_device_status(engine)

    assert engine.sent == [[0x0618], [0x0624]]


def test_the_dial_query_is_the_one_the_response_mapping_expects():
    """A query nothing can parse would be a silent no-op."""
    cfg = _load_config("arctis_7.yaml")
    mapped = {m.starts_with for m in cfg.status.response_mapping}

    assert set(cfg.status.extra_requests) <= mapped


def test_profiles_without_extra_requests_send_exactly_one_query():
    cfg = _load_config("nova_3_wireless.yaml")
    engine = _make_engine(cfg)

    CoreEngine.request_device_status(engine)

    assert engine.sent == [[0xB0]]


def test_a_failing_extra_query_does_not_take_the_poll_down():
    """The battery reply already landed — losing the dial must not lose it."""
    cfg = _load_config("arctis_7.yaml")
    engine = _make_engine(cfg)

    def _fail_on_the_dial(cmd, endpoint):
        engine.sent.append(list(cmd))
        if list(cmd) == [0x0624]:
            raise usb.core.USBError("no such device")

    engine.send_command.side_effect = _fail_on_the_dial

    CoreEngine.request_device_status(engine)  # must not raise

    assert engine.sent == [[0x0618], [0x0624]]


def test_a_failing_main_query_still_reaches_the_poll_error_handler():
    cfg = _load_config("arctis_7.yaml")
    engine = _make_engine(cfg)

    def _fail_on_the_battery(cmd, endpoint):
        raise usb.core.USBError("no such device")

    engine.send_command.side_effect = _fail_on_the_battery

    with pytest.raises(usb.core.USBError):
        CoreEngine.request_device_status(engine)
