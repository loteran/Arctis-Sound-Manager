# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for issue #199 — choosing which Arctis device ASM controls.

Before this, CoreEngine._find_hid_device() always returned the *first*
device exposing an HID interface, and configure_virtual_sinks() walked
device_configurations in load order until one matched. Someone who keeps a
GameBuds dongle plugged in next to a Nova Pro Wireless base station had no
say in which one ASM drove — unplugging the other one was the only lever.

These tests cover:
  1. _device_identity() — the replug-stable id used to name a unit in the
     'preferred_device' setting.
  2. _find_all_hid_devices() / _find_preferred_device() / list_connected_devices()
     — the enumeration helpers behind the option list and the match.
  3. configure_virtual_sinks() honouring (or safely ignoring) the preference.
  4. GeneralSettings.preferred_device — default, round-trip, D-Bus validation.
  5. The D-Bus SetSetting path applying a new preference live.
"""
from __future__ import annotations

import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ────────────────────────────────────────────────────────────────

def _make_mock_usb_device(vendor_id: int, product_id: int, bus=None, port_numbers=None) -> MagicMock:
    """A pyusb Device mock with a single HID interface (bInterfaceClass=3).

    Same shape as tests/test_device_detection.py's helper, extended with
    bus/port_numbers so _device_identity() has something to read.
    """
    import usb.core as _usb_core

    intf = MagicMock()
    intf.bInterfaceClass = 3  # USB_CLASS_HID

    # The preferred_device match and the plain first-match fallback can both
    # scan the same physical device in one configure_virtual_sinks() call, so
    # __iter__ must yield a *fresh* iterator every time it's called — a fixed
    # return_value would be exhausted after the first traversal, unlike a real
    # pyusb Device (or Configuration).
    cfg_obj = MagicMock()
    cfg_obj.__iter__ = MagicMock(side_effect=lambda: iter([intf]))

    dev = MagicMock(spec=_usb_core.Device)
    dev.idVendor = vendor_id
    dev.idProduct = product_id
    dev.bus = bus
    dev.port_numbers = port_numbers
    dev.__iter__ = MagicMock(side_effect=lambda: iter([cfg_obj]))
    dev.is_kernel_driver_active = MagicMock(return_value=True)
    dev.detach_kernel_driver = MagicMock()
    dev.attach_kernel_driver = MagicMock()
    dev._ctx = MagicMock()
    return dev


def _make_device_config(vendor_id, product_ids, name, **extra) -> SimpleNamespace:
    """A lightweight stand-in for DeviceConfiguration.

    Only the attributes the code under test actually reads: real profiles
    carry far more, but none of it is relevant to device selection.
    """
    extra.setdefault("generic", False)
    return SimpleNamespace(
        vendor_id=vendor_id,
        product_ids=product_ids,
        name=name,
        listen_interface_indexes=[0],
        dial_interface_index=0,
        dial_interface_candidates=[],
        settings={},
        **extra,
    )


def _fake_find(devices_by_pid: dict):
    """A usb.core.find() stand-in understanding both call shapes CoreEngine uses:
    plain (first match only) and find_all=True (every match)."""
    def fake_find(idVendor=None, idProduct=None, find_all=False, **kwargs):
        matches = devices_by_pid.get((idVendor, idProduct), [])
        if find_all:
            return list(matches)
        return matches[0] if matches else None
    return fake_find


def _make_engine_stub() -> MagicMock:
    stub = MagicMock()
    stub._device_lock = threading.RLock()
    stub._usb_write_lock = threading.Lock()
    return stub


# ── 1. _device_identity ──────────────────────────────────────────────────────

def test_device_identity_uses_vendor_and_product():
    from arctis_sound_manager.core import _device_identity

    dev = SimpleNamespace(idVendor=0x1038, idProduct=0x12ad, bus=None, port_numbers=None)
    assert _device_identity(dev) == "1038:12ad"


def test_device_identity_appends_port_path_when_available():
    from arctis_sound_manager.core import _device_identity

    dev = SimpleNamespace(idVendor=0x1038, idProduct=0x2202, bus=1, port_numbers=(3, 1))
    assert _device_identity(dev) == "1038:2202@1-3.1"


def test_device_identity_distinguishes_two_identical_dongles():
    """Same vendor:product, different USB ports -> different ids."""
    from arctis_sound_manager.core import _device_identity

    dongle_a = SimpleNamespace(idVendor=0x1038, idProduct=0x12ad, bus=1, port_numbers=(1,))
    dongle_b = SimpleNamespace(idVendor=0x1038, idProduct=0x12ad, bus=1, port_numbers=(2,))
    assert _device_identity(dongle_a) != _device_identity(dongle_b)


def test_device_identity_survives_missing_port_info():
    """A device that can't report bus/port info still gets a usable id."""
    from arctis_sound_manager.core import _device_identity

    dev = MagicMock(spec=[])  # no .bus, no .port_numbers, no .idVendor even
    dev.idVendor = 0x1038
    dev.idProduct = 0x2202
    assert _device_identity(dev) == "1038:2202"


# ── 2. enumeration helpers ───────────────────────────────────────────────────

def test_find_all_hid_devices_returns_every_matching_unit():
    from arctis_sound_manager.core import CoreEngine

    dev1 = _make_mock_usb_device(0x1038, 0x12ad, bus=1, port_numbers=(1,))
    dev2 = _make_mock_usb_device(0x1038, 0x12ad, bus=1, port_numbers=(2,))
    engine = _make_engine_stub()

    with patch("usb.core.find", side_effect=_fake_find({(0x1038, 0x12ad): [dev1, dev2]})):
        found = CoreEngine._find_all_hid_devices(engine, 0x1038, [0x12ad])

    assert found == [dev1, dev2]


def test_find_preferred_device_matches_regardless_of_profile_order():
    """The whole point of #199: profile load order must not decide this."""
    from arctis_sound_manager.core import CoreEngine, _device_identity

    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    dev_b = _make_mock_usb_device(0x1038, 0xBBBB, bus=1, port_numbers=(2,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")
    cfg_b = _make_device_config(0x1038, [0xBBBB], "Nova Pro Wireless")

    engine = _make_engine_stub()
    # cfg_a listed FIRST — a plain first-match scan would pick GameBuds.
    engine.device_configurations = [cfg_a, cfg_b]
    engine._find_all_hid_devices = lambda *a: CoreEngine._find_all_hid_devices(engine, *a)

    with patch("usb.core.find", side_effect=_fake_find({
        (0x1038, 0xAAAA): [dev_a], (0x1038, 0xBBBB): [dev_b],
    })):
        found_config, found_dev = CoreEngine._find_preferred_device(engine, _device_identity(dev_b))

    assert found_config is cfg_b
    assert found_dev is dev_b


def test_find_preferred_device_returns_none_when_absent():
    from arctis_sound_manager.core import CoreEngine

    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")
    engine = _make_engine_stub()
    engine.device_configurations = [cfg_a]
    engine._find_all_hid_devices = lambda *a: CoreEngine._find_all_hid_devices(engine, *a)

    with patch("usb.core.find", side_effect=_fake_find({})):
        found_config, found_dev = CoreEngine._find_preferred_device(engine, "1038:dead@1-9")

    assert found_config is None
    assert found_dev is None


def test_list_connected_devices_only_lists_whats_plugged_in():
    """Built from what's actually detected — a headset nobody owns must not
    appear, even though its profile is loaded like every other one."""
    from arctis_sound_manager.core import CoreEngine, _device_identity

    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")
    cfg_b = _make_device_config(0x1038, [0xBBBB], "Nova Pro Wireless")  # not connected
    cfg_generic = _make_device_config(0, [], "Generic audio device", generic=True)

    engine = _make_engine_stub()
    engine.device_configurations = [cfg_a, cfg_b, cfg_generic]
    engine._find_all_hid_devices = lambda *a: CoreEngine._find_all_hid_devices(engine, *a)

    with patch("usb.core.find", side_effect=_fake_find({(0x1038, 0xAAAA): [dev_a]})):
        options = CoreEngine.list_connected_devices(engine)

    assert options == [{"id": _device_identity(dev_a), "name": "GameBuds"}]


# ── 3. configure_virtual_sinks honouring the preference ─────────────────────

def _run_configure(engine, devices_by_pid):
    from arctis_sound_manager.core import CoreEngine

    engine._find_hid_device = lambda *a: CoreEngine._find_hid_device(engine, *a)
    engine._find_all_hid_devices = lambda *a: CoreEngine._find_all_hid_devices(engine, *a)
    engine._find_preferred_device = lambda *a: CoreEngine._find_preferred_device(engine, *a)
    engine.kernel_detach = lambda *a, **k: True
    engine._discover_physical_nodes = lambda *a, **k: (None, None, None)

    with patch("usb.core.find", side_effect=_fake_find(devices_by_pid)), \
         patch.object(CoreEngine, "init_device", lambda *a, **k: None), \
         patch.object(CoreEngine, "new_device_status", lambda *a: MagicMock()), \
         patch("arctis_sound_manager.core.DeviceSettings"), \
         patch("arctis_sound_manager.core.device_state.set_current_device"), \
         patch("arctis_sound_manager.core.PulseAudioManager.get_instance"), \
         patch("arctis_sound_manager.core.OledManager"):
        CoreEngine.configure_virtual_sinks(engine)


def test_configure_virtual_sinks_prefers_the_configured_device_regardless_of_order():
    from arctis_sound_manager.core import _device_identity

    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    dev_b = _make_mock_usb_device(0x1038, 0xBBBB, bus=1, port_numbers=(2,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")
    cfg_b = _make_device_config(0x1038, [0xBBBB], "Nova Pro Wireless")

    engine = _make_engine_stub()
    engine.device_config = None
    engine.usb_device = None
    # GameBuds listed first — the default scan would pick it — but the user
    # prefers the Nova Pro Wireless.
    engine.device_configurations = [cfg_a, cfg_b]
    engine.general_settings = SimpleNamespace(preferred_device=_device_identity(dev_b))

    _run_configure(engine, {(0x1038, 0xAAAA): [dev_a], (0x1038, 0xBBBB): [dev_b]})

    assert engine.device_config is cfg_b
    assert engine.usb_device is dev_b
    messages = [str(c.args[0]) if c.args else "" for c in engine.logger.info.call_args_list]
    assert any("preferred_device setting" in m for m in messages), messages


def test_configure_virtual_sinks_falls_back_and_logs_when_preferred_is_absent():
    """Unplugging the preferred dongle must not lose ASM — the other headset
    (or whatever the default order would have picked) takes over, and the
    daemon says why."""
    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")

    engine = _make_engine_stub()
    engine.device_config = None
    engine.usb_device = None
    engine.device_configurations = [cfg_a]
    engine.general_settings = SimpleNamespace(preferred_device="1038:dead@1-9")

    _run_configure(engine, {(0x1038, 0xAAAA): [dev_a]})

    # Still got a working headset...
    assert engine.device_config is cfg_a
    assert engine.usb_device is dev_a
    # ...and the fallback is logged at a level a bug report will show (info).
    messages = [str(c.args[0]) if c.args else (c.args[0] % c.args[1:] if c.args else "")
                for c in engine.logger.info.call_args_list]
    assert any("not currently connected" in m and "falling back" in m for m in messages), messages


def test_configure_virtual_sinks_unchanged_when_no_preference():
    """No preference set -> the plain first-match-in-load-order behaviour,
    exactly as before this feature existed."""
    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    dev_b = _make_mock_usb_device(0x1038, 0xBBBB, bus=1, port_numbers=(2,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")
    cfg_b = _make_device_config(0x1038, [0xBBBB], "Nova Pro Wireless")
    devices_by_pid = {(0x1038, 0xAAAA): [dev_a], (0x1038, 0xBBBB): [dev_b]}

    # Order 1: GameBuds first -> GameBuds wins.
    engine1 = _make_engine_stub()
    engine1.device_config = None
    engine1.usb_device = None
    engine1.device_configurations = [cfg_a, cfg_b]
    engine1.general_settings = SimpleNamespace(preferred_device=None)
    _run_configure(engine1, devices_by_pid)
    assert engine1.device_config is cfg_a

    # Order 2: Nova Pro Wireless first -> it wins instead. Same devices, same
    # (absent) preference — only the load order changed, exactly like before.
    engine2 = _make_engine_stub()
    engine2.device_config = None
    engine2.usb_device = None
    engine2.device_configurations = [cfg_b, cfg_a]
    engine2.general_settings = SimpleNamespace(preferred_device=None)
    _run_configure(engine2, devices_by_pid)
    assert engine2.device_config is cfg_b


def test_configure_virtual_sinks_tolerates_missing_general_settings():
    """A bare engine stub with no general_settings attribute at all (several
    existing tests build engines this way) must behave like "no preference",
    not raise."""
    dev_a = _make_mock_usb_device(0x1038, 0xAAAA, bus=1, port_numbers=(1,))
    cfg_a = _make_device_config(0x1038, [0xAAAA], "GameBuds")

    engine = MagicMock()
    engine._device_lock = threading.RLock()
    engine._usb_write_lock = threading.Lock()
    engine.device_config = None
    engine.usb_device = None
    engine.device_configurations = [cfg_a]
    del engine.general_settings  # MagicMock still answers getattr(..., None) via our own getattr default

    _run_configure(engine, {(0x1038, 0xAAAA): [dev_a]})

    assert engine.device_config is cfg_a


# ── 4. GeneralSettings.preferred_device ──────────────────────────────────────

def test_general_settings_preferred_device_defaults_to_none():
    from arctis_sound_manager.settings import GeneralSettings

    gs = GeneralSettings()
    assert gs.preferred_device is None


def test_general_settings_preferred_device_round_trips(tmp_path):
    from arctis_sound_manager.settings import GeneralSettings

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(preferred_device="1038:2202@1-3")
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.preferred_device == "1038:2202@1-3"


def test_general_settings_preferred_device_reset_to_none_round_trips(tmp_path):
    """Clearing the preference (back to 'today's behaviour') must also survive
    a restart, not just setting it."""
    from arctis_sound_manager.settings import GeneralSettings

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        gs = GeneralSettings(preferred_device="1038:2202@1-3")
        gs.write_to_file()
        gs.preferred_device = None
        gs.write_to_file()

        gs2 = GeneralSettings.read_from_file()
        assert gs2.preferred_device is None


def test_validate_config_setting_value_preferred_device_domain():
    from arctis_sound_manager.settings import GeneralSettings, validate_config_setting_value

    config = next(c for c in GeneralSettings.settings_config if c.name == "preferred_device")
    assert validate_config_setting_value(config, None) is True
    assert validate_config_setting_value(config, "1038:2202@1-3") is True
    assert validate_config_setting_value(config, 42) is False
    assert validate_config_setting_value(config, ["1038:2202"]) is False


# ── 5. D-Bus SetSetting applies a new preference live ────────────────────────

_set_setting = None  # resolved lazily below to avoid importing dbus_next at collection time


def _get_set_setting():
    global _set_setting
    if _set_setting is None:
        from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService
        _set_setting = ArctisManagerDbusSettingsService.set_setting.__dict__["__DBUS_METHOD"].fn
    return _set_setting


class _ImmediateThread:
    """Runs the target synchronously instead of spawning a real OS thread, so
    the test can assert on it deterministically."""

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _make_dbus_service(tmp_path):
    from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService
    from arctis_sound_manager.settings import GeneralSettings

    svc = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    svc.core_engine = MagicMock()
    svc.core_engine.oled_manager = None
    svc.logger = logging.getLogger("test_preferred_device_dbus")
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        svc.core_engine.general_settings = GeneralSettings()
    return svc


def test_set_setting_preferred_device_persists_and_triggers_reconfigure(tmp_path):
    set_setting = _get_set_setting()
    svc = _make_dbus_service(tmp_path)

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread", _ImmediateThread):
        ok = set_setting(svc, "preferred_device", json.dumps("1038:2202@1-3"))

    assert ok is True
    assert svc.core_engine.general_settings.preferred_device == "1038:2202@1-3"
    svc.core_engine.configure_virtual_sinks.assert_called_once()


def test_set_setting_preferred_device_rejects_non_string(tmp_path):
    set_setting = _get_set_setting()
    svc = _make_dbus_service(tmp_path)

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread", _ImmediateThread):
        ok = set_setting(svc, "preferred_device", json.dumps(1234))

    assert ok is False
    assert svc.core_engine.general_settings.preferred_device is None
    svc.core_engine.configure_virtual_sinks.assert_not_called()


def test_set_setting_preferred_device_accepts_none_to_clear_it(tmp_path):
    set_setting = _get_set_setting()
    svc = _make_dbus_service(tmp_path)
    svc.core_engine.general_settings.preferred_device = "1038:2202@1-3"

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread", _ImmediateThread):
        ok = set_setting(svc, "preferred_device", json.dumps(None))

    assert ok is True
    assert svc.core_engine.general_settings.preferred_device is None
    svc.core_engine.configure_virtual_sinks.assert_called_once()
