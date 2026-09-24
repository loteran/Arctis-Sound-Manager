# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Nova Pro Omni's ChatMix knob was inert after every boot: the DAC
abandons its sonar-present / software-ChatMix mode switch when the next init
frame arrives within milliseconds, and ASM sent them 6 ms apart. Profiles can
now ask for a settle time with `['sleep', <ms>]`, and a frame whose USB write
fails is retried once — which the code claimed before but never did, because
send_command() does not raise.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import usb.core
from ruamel.yaml import YAML

from arctis_sound_manager import core as core_mod
from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine, init_entry_pause_seconds

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
_yaml = YAML(typ="safe")


def _omni_raw():
    return _yaml.load(DEVICES_DIR / "nova_pro_omni.yaml")


def _engine(cfg) -> MagicMock:
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine.device_config = cfg
    engine.logger = MagicMock()
    engine.sent = []
    engine.get_command_endpoint_address.return_value = 0
    engine.translate_init_bytes = lambda b: list(b)
    engine.send_command.side_effect = lambda cmd, endpoint: engine.sent.append(list(cmd))
    return engine


@pytest.mark.parametrize("entry, expected", [
    (["sleep", 1000], 1.0),
    (["sleep", 250], 0.25),
    ([0x01, 0x8d, 0x01], None),
    (["status.request"], None),
    (["sleep", "1000"], None),   # a string is not a duration
    (["sleep", True], None),
])
def test_pause_entry_recognised(entry, expected):
    assert init_entry_pause_seconds(entry) == expected


def test_init_sequence_pauses_and_does_not_send_the_pause():
    cfg = MagicMock()
    cfg.device_init = [[0x01, 0x8d, 0x01], ["sleep", 1000], [0x01, 0x49, 0x01]]
    cfg.time_between_commands_ms = None
    engine = _engine(cfg)
    order: list = []
    engine.send_command.side_effect = lambda cmd, ep: order.append(("send", list(cmd)))

    with patch.object(core_mod.time, "sleep", side_effect=lambda s: order.append(("sleep", s))):
        CoreEngine._send_device_init_sequence(engine)

    assert order == [("send", [0x01, 0x8d, 0x01]), ("sleep", 1.0), ("send", [0x01, 0x49, 0x01])]


def test_failed_write_is_retried_once():
    cfg = MagicMock()
    cfg.device_init = [[0x01, 0x8d, 0x01]]
    cfg.time_between_commands_ms = None
    engine = _engine(cfg)
    engine.send_command.side_effect = [False, True]

    CoreEngine._send_device_init_sequence(engine)

    assert engine.send_command.call_count == 2
    engine.logger.warning.assert_called_once()
    engine.logger.error.assert_not_called()


def test_persistent_failure_is_reported_and_sequence_continues():
    cfg = MagicMock()
    cfg.device_init = [[0x01, 0x8d, 0x01], [0x01, 0x49, 0x01]]
    cfg.time_between_commands_ms = None
    engine = _engine(cfg)
    engine.send_command.side_effect = [False, False, True]

    CoreEngine._send_device_init_sequence(engine)

    assert engine.send_command.call_count == 3
    engine.logger.error.assert_called_once()
    assert engine.send_command.call_args_list[-1].args[0] == [0x01, 0x49, 0x01]


def test_send_command_returns_false_on_usb_error():
    cfg = DeviceConfiguration(_omni_raw())
    engine = MagicMock()
    engine.device_config = cfg
    engine._usb_write_lock = threading.Lock()
    engine._last_usb_write_monotonic = 0.0
    engine.usb_device.ctrl_transfer.side_effect = usb.core.USBError("boom")
    engine._command_interface_number.return_value = 3

    assert CoreEngine.send_command(engine, [0x01, 0x8d, 0x01], 0) is False
    engine.logger.warning.assert_called_once()

    engine.usb_device.ctrl_transfer.side_effect = None
    assert CoreEngine.send_command(engine, [0x01, 0x8d, 0x01], 0) is True


def test_omni_settles_after_each_mode_switch():
    init = _omni_raw()["device"]["device_init"]
    for opcode in (0x8d, 0x49):
        index = next(i for i, e in enumerate(init) if e[:2] == [0x01, opcode])
        assert init_entry_pause_seconds(init[index + 1]) >= 0.5, (
            f"no settle after 0x{opcode:02x}: the DAC abandons the mode switch")
