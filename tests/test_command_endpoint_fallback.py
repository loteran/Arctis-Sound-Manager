# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_command_endpoint_fallback.py — get_command_endpoint_address robustness.

Verifies that get_command_endpoint_address does NOT raise and returns 0
(HID SET_REPORT fallback) when the declared command interface is missing from
the USB device — the exact scenario that caused the daemon crash for Arctis
Nova Elite users (issue #100, wrong interface index in YAML).
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from arctis_sound_manager.config import CommandTransport
from arctis_sound_manager.core import CoreEngine


def _make_engine_with_missing_interface() -> MagicMock:
    """Build a minimal engine stub whose guess_interface_endpoint raises,
    simulating a declared interface that is absent on the real USB device."""
    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine._warned_no_out_endpoint = False

    # device_config: command_transport == INTERRUPT so the endpoint lookup path
    # is exercised (transport != INTERRUPT would return 0 trivially).
    cfg = MagicMock()
    cfg.command_transport = CommandTransport.INTERRUPT
    cfg.command_interface_index = [3, 0]   # interface 3, alt 0
    engine.device_config = cfg

    # Minimal USB device mock (idVendor/idProduct for warning formatting).
    usb_dev = MagicMock()
    usb_dev.idVendor = 0x1038
    usb_dev.idProduct = 0x2244
    engine.usb_device = usb_dev

    # guess_interface_endpoint raises because the declared interface does not
    # exist on the hardware — this is the root cause of the issue #100 crash.
    engine.guess_interface_endpoint = MagicMock(
        side_effect=Exception(
            "Failed to find interface for device: 1038:2244 (interface: 3, alternate setting: 0)"
        )
    )

    # logger must not blow up on .warning() / .debug()
    engine.logger = MagicMock()

    return engine


def test_get_command_endpoint_address_returns_0_when_interface_missing():
    """get_command_endpoint_address must not raise and must return 0 (SET_REPORT
    fallback) when guess_interface_endpoint raises because the USB interface
    declared in the YAML is absent on the real device."""
    engine = _make_engine_with_missing_interface()

    result = CoreEngine.get_command_endpoint_address(engine)

    assert result == 0, (
        f"Expected 0 (SET_REPORT fallback) but got {result!r}. "
        "The daemon would have crashed without the try/except guard."
    )
    # The warning must have been emitted (once).
    engine.logger.warning.assert_called_once()
    assert engine._warned_no_out_endpoint is True


def test_get_command_endpoint_address_suppresses_repeated_warning():
    """Second call after the warning flag is set must use debug() not warning()."""
    engine = _make_engine_with_missing_interface()
    engine._warned_no_out_endpoint = True  # simulate already-warned state

    result = CoreEngine.get_command_endpoint_address(engine)

    assert result == 0
    engine.logger.warning.assert_not_called()
    engine.logger.debug.assert_called()
