# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GameBuds charging case, read on the side of the driven device.

The case is its own USB product with no audio. The two failures worth pinning:
it must never be taken for the device ASM drives (Linux Arctis Manager's v3
models it as one more device, and unplugging it there tears down the dongle's
sinks), and its frames must be read at ASM's offsets, not the spec's.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import charging_case as cc
from arctis_sound_manager.config import ChargingCaseConfig, load_device_configurations
from arctis_sound_manager.dbus_service import ArctisManagerDbusStatusService
from arctis_sound_manager.udev_rules import load_devices, render_rules

DEVICES = Path(__file__).resolve().parent.parent / 'src' / 'arctis_sound_manager' / 'devices'
CASE = ChargingCaseConfig(product_ids=[0x230c, 0x231a], hid_usage_page=0xffc0,
                          command_interface_index=3)


def _gamebuds():
    return next(c for c in load_device_configurations() if c.charging_case)


# ── Profile ──────────────────────────────────────────────────────────────────

def test_the_case_is_declared_but_never_a_driven_product():
    config = _gamebuds()
    assert config.charging_case.product_ids == [0x230c, 0x231a]
    assert not set(config.charging_case.product_ids) & set(config.product_ids)


def test_udev_grants_the_case_the_same_access():
    rules = render_rules(load_devices([DEVICES]))
    assert 'ATTRS{idProduct}=="230c"' in rules
    assert 'ATTRS{idProduct}=="231a"' in rules


# ── Frames ───────────────────────────────────────────────────────────────────

def test_battery_frame_reads_case_and_both_buds():
    # cmd, left 80 %, right 75 %, case 42 %, left/right charging, case charging
    frame = [0xB7, 80, 75, 42, 1, 2, 1] + [0] * 57
    assert cc.parse_frame(frame) == {
        'case_left_battery_charge': 80, 'case_left_charging_status': 'charging',
        'case_right_battery_charge': 75, 'case_right_charging_status': 'plugged_in',
        'case_battery_charge': 42, 'case_charging_status': 'charging'}


def test_unseated_bud_readings_are_hidden():
    state = {'case_left_battery_charge': 80, 'case_left_charging_status': 'charging',
             'case_right_battery_charge': 0, 'case_right_charging_status': 'discharging',
             'case_battery_charge': 42, 'buds_in_case': 'left'}
    assert cc.without_unseated_buds(state) == {
        'case_left_battery_charge': 80, 'case_left_charging_status': 'charging',
        'case_battery_charge': 42, 'buds_in_case': 'left'}


def test_buds_and_lid_frames():
    assert cc.parse_frame([0xB8, 1, 0]) == {'buds_in_case': 'left'}
    assert cc.parse_frame([0xB8, 1, 1]) == {'buds_in_case': 'both'}
    assert cc.parse_frame([0xBC, 0]) == {'case_lid_status': 'open'}
    assert cc.parse_frame([0xBC, 1]) == {'case_lid_status': 'closed'}


def test_out_of_range_bytes_are_dropped_not_shown():
    assert cc.parse_frame([0xB7, 200, 200, 200, 9, 9, 9]) == {}
    assert cc.parse_frame([0xB8, 3, 0]) == {}
    assert cc.parse_frame([0xBC, 7]) == {}
    assert cc.parse_frame([0x42, 1, 2, 3]) == {}
    assert cc.parse_frame([]) == {}


# ── Interface ────────────────────────────────────────────────────────────────

def test_the_push_interface_is_not_mistaken_for_the_command_one():
    """0xffc1 is vendor-defined too; only the exact page counts."""
    assert cc.choose_interface({0: 0x000c, 2: 0xffc1, 4: 0xffc0}, CASE) == 4


def test_unknown_pages_fall_back_to_the_profile():
    assert cc.choose_interface({0: None, 2: None}, CASE) == 3
    assert cc.choose_interface({2: 0xffc0, 3: 0xffc0}, CASE) == 3


# ── Monitor ──────────────────────────────────────────────────────────────────

class _FakeCase:
    """Answers each SET_REPORT with the matching reply on the IN endpoint,
    preceded once by an unsolicited lid frame."""

    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.endpoint = SimpleNamespace(bEndpointAddress=0x84, wMaxPacketSize=64)
        self.queue: list[list[int]] = [[0xBC, 0]]
        self.sent: list[tuple] = []
        self.detached = self.attached = False
        self.replies = {0xB7: [0xB7, 50, 60, 33, 0, 0, 2],
                        0xB8: [0xB8, 1, 1],
                        0xBC: [0xBC, 1]}

    def get_active_configuration(self):
        return {(3, 0): [self.endpoint]}

    def is_kernel_driver_active(self, iface):
        return True

    def detach_kernel_driver(self, iface):
        self.detached = True

    def attach_kernel_driver(self, iface):
        self.attached = True

    def ctrl_transfer(self, request_type, request, value, index, data):
        self.sent.append((request, value, index, data[0], len(data)))
        self.queue.append(self.replies[data[0]])
        if len(self.sent) == 3:
            self.stop.set()

    def read(self, address, size, timeout):
        import usb.core
        if not self.queue:
            raise usb.core.USBTimeoutError('timeout')
        return self.queue.pop(0)


@pytest.fixture
def fake_usb(monkeypatch):
    import usb.core
    import usb.util
    stop = threading.Event()
    device = _FakeCase(stop)
    monkeypatch.setattr(usb.core, 'find', lambda **kw: device)
    monkeypatch.setattr(usb.util, 'claim_interface', lambda d, i: None)
    monkeypatch.setattr(usb.util, 'release_interface', lambda d, i: None)
    monkeypatch.setattr(usb.util, 'dispose_resources', lambda d: None)
    monkeypatch.setattr(usb.util, 'find_descriptor',
                        lambda intf, custom_match: intf[0])
    return device, stop


def test_one_poll_reads_all_three_and_gives_the_interface_back(fake_usb, monkeypatch):
    device, stop = fake_usb
    monitor = cc.ChargingCaseMonitor(lambda: [])
    monkeypatch.setattr(monitor, '_interface_pages', lambda d: {3: 0xffc0})
    seen: dict = {}
    real_poll = monitor._poll

    def poll_and_capture(*args):
        real_poll(*args)
        seen.update(monitor._state.get(0x230c, {}))

    poll_and_capture(0x1038, 0x230c, CASE, stop)

    assert [s[3] for s in device.sent] == [0xB7, 0xB8, 0xBC]
    assert all(s[:3] == (0x09, 0x0200, 3) and s[4] == 64 for s in device.sent)
    # The unsolicited lid frame read first is overridden by the real answer.
    assert seen == {'case_battery_charge': 33, 'case_charging_status': 'plugged_in',
                    'case_left_battery_charge': 50, 'case_left_charging_status': 'discharging',
                    'case_right_battery_charge': 60, 'case_right_charging_status': 'discharging',
                    'buds_in_case': 'both', 'case_lid_status': 'closed'}
    assert device.detached and device.attached


def test_state_is_cleared_when_the_case_goes_away(monkeypatch):
    monitor = cc.ChargingCaseMonitor(lambda: [_gamebuds()])

    def poll(vendor_id, product_id, case, stop):
        monitor._state[product_id] = {'case_lid_status': 'open'}
        raise OSError('No such device')

    monkeypatch.setattr(monitor, '_poll', poll)
    monitor._workers[0x230c] = threading.Event()
    monitor._run(0x1038, 0x230c, CASE, monitor._workers[0x230c])

    assert monitor.state_for(_gamebuds()) == {}
    assert 0x230c not in monitor._workers


def test_only_the_owning_profile_sees_the_case():
    gamebuds = _gamebuds()
    monitor = cc.ChargingCaseMonitor(lambda: [gamebuds])
    monitor._state[0x231a] = {'case_battery_charge': 10}
    other = next(c for c in load_device_configurations() if not c.charging_case)

    assert monitor.is_case(0x1038, 0x231a)
    assert not monitor.is_case(0x1038, gamebuds.product_ids[0])
    assert monitor.state_for(gamebuds) == {'case_battery_charge': 10}
    assert monitor.state_for(other) == {}


# ── GetStatus ────────────────────────────────────────────────────────────────

def _status_json(case_state: dict) -> dict:
    gamebuds = _gamebuds()
    core = MagicMock()
    core.device_config = gamebuds
    core.device_status = {'left_connect_status': 3, 'right_connect_status': 3,
                          'left_battery_level': 70, 'right_battery_level': 80}
    core._device_ready = True
    core.charging_case.state_for.return_value = case_state
    service = ArctisManagerDbusStatusService.__new__(ArctisManagerDbusStatusService)
    service.core_engine = core
    return json.loads(service.status_json())


def test_getstatus_carries_the_case_while_plugged_in():
    payload = _status_json({'case_battery_charge': 33, 'case_lid_status': 'closed'})
    assert payload['charging_case'] == {
        'case_battery_charge': {'value': 33, 'type': 'percentage'},
        'case_lid_status': {'value': 'closed', 'type': 'label'},
    }


def test_getstatus_has_no_case_category_without_a_case():
    assert 'charging_case' not in _status_json({})


def test_plugging_the_case_neither_drives_it_nor_reports_it_unknown():
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.device_configurations = [_gamebuds()]
    engine.logger = MagicMock()
    engine.configure_virtual_sinks = MagicMock()

    engine.on_device_connected(0x1038, 0x230c, 'Arctis_GameBuds_Case')

    engine.configure_virtual_sinks.assert_not_called()
    engine.logger.warning.assert_not_called()
