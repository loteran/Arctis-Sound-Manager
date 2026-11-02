# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Nova 5 (#348): the dongle pushes power (0xb9), charging (0xbb) and battery
(0xb7) on interface 5 by itself, as GG's translate_sync_data expects, and a
2 s 0xb0 poll made the headset crackle now and then. The profile maps the
pushes and polls every 5 minutes; the EQ reconcile keeps its 2 s tick.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from ruamel.yaml import YAML

from arctis_sound_manager import core
from arctis_sound_manager.config import DeviceConfiguration, parsed_status
from arctis_sound_manager.core import CoreEngine
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine, _read

_yaml = YAML(typ="safe")


def _nova_5() -> DeviceConfiguration:
    return DeviceConfiguration(_yaml.load(DEVICES_DIR / "nova_5.yaml"))


def test_power_push_frames_set_the_power_status():
    engine = _make_engine(_nova_5())
    _read(engine, [0xb9, 0x02, 0x02])
    assert parsed_status(engine.device_status, engine.device_config)["headset_power_status"] == "off"
    _read(engine, [0xb9, 0x03, 0x02])
    assert parsed_status(engine.device_status, engine.device_config)["headset_power_status"] == "on"


def test_charging_and_battery_push_frames():
    engine = _make_engine(_nova_5())
    _read(engine, [0xb0, 0x03, 0x00, 0x40, 0x03])
    _read(engine, [0xbb, 0x01, 0x02])
    _read(engine, [0xb7, 0x50, 0x02])
    assert engine.device_status["cable_charging"] == 0x01
    assert engine.device_status["headset_battery_charge"] == 0x50


def test_profile_poll_interval():
    assert _nova_5().status.poll_interval_s == 0
    other = DeviceConfiguration(_yaml.load(DEVICES_DIR / "arctis_7.yaml"))
    assert other.status.poll_interval_s == 2.0


def _poll_engine(interval: float):
    engine = CoreEngine.__new__(CoreEngine)
    engine._stopping = False
    engine._device_lock = threading.RLock()
    engine.logger = MagicMock()
    engine.usb_device = object()
    engine.device_config = SimpleNamespace(status=SimpleNamespace(poll_interval_s=interval))
    engine.request_device_status = MagicMock()
    engine.reconcile_hardware_eq_mode = MagicMock()
    engine._note_status_poll_ok = MagicMock()
    return engine


def _run_ticks(monkeypatch, engine, ticks: int, period: float = 2.0):
    clock = [1000.0]
    count = [0]

    async def fake_sleep(seconds):
        clock[0] += seconds
        count[0] += 1
        if count[0] >= ticks:
            engine._stopping = True

    monkeypatch.setattr(core.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(core, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    asyncio.run(CoreEngine._status_poll_loop(engine, period))


def test_long_interval_polls_rarely_but_reconciles_every_tick(monkeypatch):
    engine = _poll_engine(10.0)
    _run_ticks(monkeypatch, engine, 10)          # 20 s of ticks
    assert engine.request_device_status.call_count == 2   # t=2 and t=12
    assert engine.reconcile_hardware_eq_mode.call_count == 10


def test_default_interval_polls_every_tick(monkeypatch):
    engine = _poll_engine(2.0)
    _run_ticks(monkeypatch, engine, 5)
    assert engine.request_device_status.call_count == 5


def test_zero_interval_never_polls_but_reconciles(monkeypatch):
    """#364: a device that pushes every change is not polled at all."""
    engine = _poll_engine(0.0)
    _run_ticks(monkeypatch, engine, 5)
    engine.request_device_status.assert_not_called()
    assert engine.reconcile_hardware_eq_mode.call_count == 5
