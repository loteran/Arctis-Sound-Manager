# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the iProduct diagnostic fallback (Phase 1-3).

When a PID matches no YAML, the daemon now logs the USB iProduct string
so users can report both PID and product name in issues. The matching
behaviour itself must not change — PID remains the only selector.
"""

from unittest.mock import MagicMock, call, patch

import pytest


# ── USBDevicesMonitor ────────────────────────────────────────────────────────


def _make_monitor():
    """Build a USBDevicesMonitor skipping __init__ (no pyudev/netlink needed)."""
    from arctis_sound_manager.usb_devices_monitor import USBDevicesMonitor

    mon = USBDevicesMonitor.__new__(USBDevicesMonitor)
    mon.logger = MagicMock()
    mon._stopping = False
    mon._on_connect_callbacks = []
    mon._on_disconnect_callbacks = []
    mon._backend = 'polling'
    mon._known_devices = {}
    return mon


class TestUSBMonitorCallbackSignature:
    """Callbacks must receive (vid, pid, product_name)."""

    def test_on_connect_propagates_product_name(self):
        """_on_connect must pass product_name to every registered callback."""
        mon = _make_monitor()
        cb = MagicMock()
        mon.register_on_connect(cb)

        mon._on_connect(0x1038, 0x12E0, 'Arctis_Nova_Pro_Wireless')

        cb.assert_called_once_with(0x1038, 0x12E0, 'Arctis_Nova_Pro_Wireless')

    def test_on_connect_empty_name_default(self):
        """_on_connect with default product_name must pass ''."""
        mon = _make_monitor()
        cb = MagicMock()
        mon.register_on_connect(cb)

        mon._on_connect(0x1038, 0x12E0)

        cb.assert_called_once_with(0x1038, 0x12E0, '')

    def test_on_disconnect_propagates_product_name(self):
        """_on_disconnect must pass product_name to every registered callback."""
        mon = _make_monitor()
        cb = MagicMock()
        mon.register_on_disconnect(cb)

        mon._on_disconnect(0x1038, 0x12E0, 'Arctis_Nova_7_Wireless')

        cb.assert_called_once_with(0x1038, 0x12E0, 'Arctis_Nova_7_Wireless')

    def test_multiple_callbacks_all_receive_name(self):
        """All registered callbacks receive the same product_name."""
        mon = _make_monitor()
        cb1 = MagicMock()
        cb2 = MagicMock()
        mon.register_on_connect(cb1)
        mon.register_on_connect(cb2)

        mon._on_connect(0x1038, 0x9999, 'Some_Headset')

        assert cb1.call_args == call(0x1038, 0x9999, 'Some_Headset')
        assert cb2.call_args == call(0x1038, 0x9999, 'Some_Headset')

    def test_callback_exception_does_not_block_others(self):
        """A callback raising must not prevent subsequent callbacks from firing."""
        mon = _make_monitor()
        bad_cb = MagicMock(side_effect=ValueError('boom'))
        good_cb = MagicMock()
        mon.register_on_connect(bad_cb)
        mon.register_on_connect(good_cb)

        mon._on_connect(0x1038, 0x12E0, 'X')

        good_cb.assert_called_once_with(0x1038, 0x12E0, 'X')


class TestUSBMonitorPollingSnapshot:
    """The polling snapshot must include product names from pyusb."""

    def test_snapshot_returns_dict_with_names(self):
        """_snapshot must return {(vid, pid): name} from d.product."""
        mon = _make_monitor()

        fake_dev1 = MagicMock()
        fake_dev1.idVendor = 0x1038
        fake_dev1.idProduct = 0x12E0
        fake_dev1.product = 'Arctis Nova Pro Wireless'

        fake_dev2 = MagicMock()
        fake_dev2.idVendor = 0x1038
        fake_dev2.idProduct = 0x1234
        fake_dev2.product = None  # device without iProduct string

        mock_usb = MagicMock()
        mock_usb.find.return_value = [fake_dev1, fake_dev2]

        result = mon._snapshot(mock_usb)

        assert result == {
            (0x1038, 0x12E0): 'Arctis Nova Pro Wireless',
            (0x1038, 0x1234): '',
        }

    def test_snapshot_returns_empty_dict_on_error(self):
        """_snapshot must return last known snapshot on USB error."""
        mon = _make_monitor()
        mon._known_devices = {(0x1038, 0xAAAA): 'previous'}

        mock_usb = MagicMock()
        mock_usb.find.side_effect = RuntimeError('USB error')

        result = mon._snapshot(mock_usb)

        assert result == {(0x1038, 0xAAAA): 'previous'}




# ── CoreEngine.on_device_connected ──────────────────────────────────────────


class TestOnDeviceConnectedDiagnostic:
    """on_device_connected must include iProduct in the 'no match' warning."""

    def _make_engine(self, device_configs):
        """Build a CoreEngine-like with mocked internals."""
        from arctis_sound_manager.core import CoreEngine

        engine = CoreEngine.__new__(CoreEngine)
        engine.logger = MagicMock()
        engine.device_configurations = device_configs
        engine._detect_lock = MagicMock()
        engine._detect_lock.locked.return_value = False
        return engine

    def _make_config(self, vendor_id=0x1038, product_ids=None, name='Test Device'):
        cfg = MagicMock()
        cfg.vendor_id = vendor_id
        cfg.product_ids = product_ids or []
        cfg.name = name
        cfg.known_unsupported_product_ids = {}
        return cfg

    def test_warning_includes_iproduct_when_no_match(self):
        """When no YAML matches, the warning must include the iProduct string."""
        engine = self._make_engine([self._make_config(product_ids=[0x9999])])

        engine.on_device_connected(0x1038, 0x12E0, 'Arctis_Nova_7_Wireless')

        # Find the warning call
        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'appeared but no device YAML matches' in str(c)
        ]
        assert len(warning_calls) == 1
        msg = warning_calls[0][0][0]
        assert 'Arctis_Nova_7_Wireless' in msg
        assert '1038:12e0' in msg

    def test_warning_omits_iproduct_when_empty(self):
        """When product_name is empty, the warning must not include iProduct."""
        engine = self._make_engine([self._make_config(product_ids=[0x9999])])

        engine.on_device_connected(0x1038, 0x12E0, '')

        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'appeared but no device YAML matches' in str(c)
        ]
        assert len(warning_calls) == 1
        msg = warning_calls[0][0][0]
        assert 'iProduct' not in msg

    def test_pid_match_skips_warning(self):
        """When PID matches, the warning must NOT be emitted (matching unchanged)."""
        engine = self._make_engine([self._make_config(product_ids=[0x12E0])])
        engine.configure_virtual_sinks = MagicMock()

        engine.on_device_connected(0x1038, 0x12E0, 'Some_Headset')

        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'appeared but no device YAML matches' in str(c)
        ]
        assert len(warning_calls) == 0
        engine.configure_virtual_sinks.assert_called_once()

    def test_non_steelseries_vendor_silent(self):
        """Non-SteelSeries vendors must not trigger the 'no match' warning."""
        engine = self._make_engine([self._make_config(vendor_id=0x046d)])

        engine.on_device_connected(0x046d, 0x1234, 'Some_Mouse')

        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'appeared but no device YAML matches' in str(c)
        ]
        assert len(warning_calls) == 0

    def test_known_unsupported_uses_name_from_config(self):
        """known_unsupported_product_ids must still work and include the YAML name."""
        cfg = self._make_config(
            product_ids=[0x2208],
            name='SteelSeries Arctis Nova Pro Omni',
        )
        cfg.known_unsupported_product_ids = {0x2209: 'USB-2 position'}
        engine = self._make_engine([cfg])

        engine.on_device_connected(0x1038, 0x2209, 'Arctis_Nova_Pro_Omni')

        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'ASM cannot control it' in str(c)
        ]
        assert len(warning_calls) == 1
        assert 'Arctis Nova Pro Omni' in warning_calls[0][0][0]
        assert 'USB-2 position' in warning_calls[0][0][0]

    def test_message_asks_for_product_name(self):
        """The 'no match' warning must ask users to report the product name."""
        engine = self._make_engine([self._make_config(product_ids=[0x9999])])

        engine.on_device_connected(0x1038, 0x12E0, 'Arctis_X')

        warning_calls = [
            c for c in engine.logger.warning.call_args_list
            if 'appeared but no device YAML matches' in str(c)
        ]
        assert 'product name' in warning_calls[0][0][0]
