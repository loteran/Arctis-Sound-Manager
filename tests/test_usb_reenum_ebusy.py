# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the EBUSY fix: stale libusb handle released on same-device re-enumeration.

The Nova Pro Wireless DAC re-enumerates on boot, wake and replug. The old
handle must be disposed via usb.util.dispose_resources() before the new one is
claimed, otherwise interface 4 stays locked and every subsequent transfer fails
with errno 16 (EBUSY).
"""

import threading
from unittest.mock import MagicMock, call, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────


def _make_device_config(vendor_id=0x1038):
    """Return a minimal DeviceConfiguration mock."""
    cfg = MagicMock()
    cfg.vendor_id = vendor_id
    cfg.command_interface_index = [0, 0]
    cfg.listen_interface_indexes = [1]
    cfg.dial_interface_index = 2
    cfg.dial_interface_candidates = []
    return cfg


def _make_engine_with_stale_handle(device_config):
    """Build a CoreEngine-like object that already has a USB handle + config.

    Uses __new__ to skip the real __init__ (which would try to open USB/audio).
    Only the attributes touched by _release_usb_handle() and
    configure_virtual_sinks() are populated.
    """
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._device_lock = threading.RLock()
    engine.usb_device = MagicMock(name='stale_handle')
    engine.device_config = device_config
    engine.oled_manager = None
    return engine


# ── _release_usb_handle ────────────────────────────────────────────────────


def test_release_usb_handle_calls_dispose_resources():
    """dispose_resources must be called on the stale handle."""
    dc = _make_device_config()
    engine = _make_engine_with_stale_handle(dc)
    stale = engine.usb_device

    with patch('usb.util.release_interface'), \
         patch('usb.util.dispose_resources') as mock_dispose, \
         patch('usb.core.find', return_value=None):
        engine._release_usb_handle()

    mock_dispose.assert_called_once_with(stale)


def test_release_usb_handle_clears_usb_device():
    """After release, usb_device must be None so the caller can assign the fresh handle."""
    dc = _make_device_config()
    engine = _make_engine_with_stale_handle(dc)

    with patch('usb.util.release_interface'), \
         patch('usb.util.dispose_resources'), \
         patch('usb.core.find', return_value=None):
        engine._release_usb_handle()

    assert engine.usb_device is None


def test_release_usb_handle_noop_when_no_device():
    """_release_usb_handle must be a safe no-op when usb_device is None."""
    dc = _make_device_config()
    engine = _make_engine_with_stale_handle(dc)
    engine.usb_device = None

    with patch('usb.util.dispose_resources') as mock_dispose:
        engine._release_usb_handle()

    mock_dispose.assert_not_called()


def test_release_usb_handle_stops_oled_manager():
    """If an OledManager is running, it must be stopped before the handle is disposed."""
    dc = _make_device_config()
    engine = _make_engine_with_stale_handle(dc)
    oled = MagicMock()
    engine.oled_manager = oled

    with patch('usb.util.release_interface'), \
         patch('usb.util.dispose_resources'), \
         patch('usb.core.find', return_value=None):
        engine._release_usb_handle()

    oled.stop.assert_called_once()
    assert engine.oled_manager is None


def test_release_usb_handle_releases_all_interfaces():
    """release_interface must be called for every interface returned by _all_used_interfaces."""
    dc = _make_device_config()
    dc.command_interface_index = [0, 0]
    dc.listen_interface_indexes = [1, 2]
    dc.dial_interface_index = 3
    dc.dial_interface_candidates = []

    engine = _make_engine_with_stale_handle(dc)

    released = []

    def fake_release(dev, iface):
        released.append(iface)

    with patch('usb.util.release_interface', side_effect=fake_release), \
         patch('usb.util.dispose_resources'), \
         patch('usb.core.find', return_value=None):
        engine._release_usb_handle()

    assert set(released) == {0, 1, 2, 3}


def test_teardown_releases_every_claimed_interface():
    """teardown() must release the same interface set kernel_detach claimed.

    kernel_detach claims *all* of _all_used_interfaces (command, listeners,
    dial candidates) — claiming is what stops the kernel rebinding usbhid and
    turning every transfer into EIO. teardown() used to release only the
    command interface, then asked kernel_attach to hand every interface back
    to the kernel while this process still held the rest.
    """
    dc = _make_device_config()
    dc.command_interface_index = [0, 0]
    dc.listen_interface_indexes = [1, 2]
    dc.dial_interface_index = 3
    dc.dial_interface_candidates = [4]

    engine = _make_engine_with_stale_handle(dc)
    engine.loopback_manager = MagicMock()
    engine.redirect_audio_on_disconnect = MagicMock()
    engine.kernel_attach = MagicMock(return_value=True)
    engine._device_ready = True
    engine._warned_no_out_endpoint = False
    engine.device_status = None
    engine._active_extra_dial_interfaces = []

    released = []

    with patch('usb.util.release_interface', side_effect=lambda dev, i: released.append(i)), \
         patch('usb.util.dispose_resources'), \
         patch('usb.core.find', return_value=None), \
         patch('arctis_sound_manager.core.device_state'):
        engine.teardown()

    assert set(released) == {0, 1, 2, 3, 4}, (
        "teardown must release every interface kernel_detach claimed, not just the command one"
    )


def test_release_usb_handle_dispose_called_even_if_release_raises():
    """dispose_resources must run even if release_interface raises USBError."""
    import usb.core

    dc = _make_device_config()
    engine = _make_engine_with_stale_handle(dc)
    stale = engine.usb_device

    with patch('usb.util.release_interface', side_effect=usb.core.USBError("busy")), \
         patch('usb.util.dispose_resources') as mock_dispose, \
         patch('usb.core.find', return_value=None):
        engine._release_usb_handle()

    mock_dispose.assert_called_once_with(stale)
