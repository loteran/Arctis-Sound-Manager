# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""SET_REPORT wValue computation for control-transfer device commands (issue #76).

The Nova Pro Wired GameDAC firmware validates the HID SET_REPORT wValue strictly:
its low byte must carry the report id (0x06) that prefixes every command. ASM used
to hardcode 0x0200/0x0300 (report id 0), so the Wired silently dropped every command
(e.g. the high-gain init), leaving audio near-inaudible until cranked to ~95%.

The fix derives wValue = (report_type << 8) | command_report_id. Devices without a
command_report_id (Nova 7 family etc. — unnumbered reports) keep the legacy 0x0200.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.config import CommandTransport
from arctis_sound_manager.core import CoreEngine


def _make_engine(transport: CommandTransport, command_report_id):
    engine = object.__new__(CoreEngine)
    engine._usb_write_lock = threading.Lock()
    engine.usb_device = MagicMock()
    engine.device_config = SimpleNamespace(
        command_transport=transport,
        command_report_id=command_report_id,
        command_interface_index=[4, 0],
        command_padding=SimpleNamespace(filler=0x00, length=16),
    )
    return engine


@pytest.mark.parametrize(
    "transport, report_id, expected_wvalue",
    [
        (CommandTransport.CTRL_OUTPUT, 0x06, 0x0206),   # Nova Pro Wired
        (CommandTransport.CTRL_OUTPUT, None, 0x0200),   # legacy unnumbered (Nova 7 etc.)
        (CommandTransport.CTRL_FEATURE, 0x06, 0x0306),  # numbered feature report
        (CommandTransport.CTRL_FEATURE, None, 0x0300),  # legacy feature
    ],
)
def test_send_command_wvalue(transport, report_id, expected_wvalue):
    engine = _make_engine(transport, report_id)
    engine.send_command([0x06, 0x27, 0x02], endpoint=0)

    engine.usb_device.ctrl_transfer.assert_called_once()
    args = engine.usb_device.ctrl_transfer.call_args.args
    # ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, data)
    assert args[1] == 0x09, "bRequest must be SET_REPORT (0x09)"
    assert args[2] == expected_wvalue
    assert args[3] == 4, "wIndex must be the command interface number"
    # The report id still leads the data payload (numbered-report convention).
    assert list(args[4])[0] == 0x06


def test_interrupt_transport_does_not_use_ctrl_transfer():
    """Interrupt-OUT devices (e.g. Nova Pro Wireless) must bypass ctrl_transfer."""
    engine = _make_engine(CommandTransport.CTRL_OUTPUT, None)
    engine.send_command([0x06, 0x27, 0x02], endpoint=0x04)  # non-zero endpoint
    engine.usb_device.write.assert_called_once()
    engine.usb_device.ctrl_transfer.assert_not_called()
