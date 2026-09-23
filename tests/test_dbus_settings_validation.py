# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the D-Bus SetSetting validation boundary.

CHA-2, CHA-8 and CHA-12 (see RAPPORT-CHAOS-ASM.md) all trace back to the same
`SetSetting` code path (dbus_service.py) accepting a value the setting's own
declared domain (SettingType, min/max, values_mapping, or the HRIR catalogue)
would have refused:

- CHA-2: `SetSetting pipewire_quantum "8192"` (out of range) and
  `SetSetting pipewire_quantum "true"` (bool sailing through an int check,
  since `isinstance(True, int)` is `True`) both used to reach
  `apply_force_quantum`, forcing PipeWire's global quantum system-wide.
- CHA-8: every SELECT setting defaults to `None`, which used to skip type
  validation entirely — a list or dict reached `general_settings.yaml`.
- CHA-12: `hrir_id` was only checked with `isinstance(value, str)`, so a
  directory-traversal id reached `package_hrir_path()` unfiltered.
"""

import json
import logging
from unittest.mock import MagicMock, patch

from arctis_sound_manager.dbus_service import ArctisManagerDbusSettingsService
from arctis_sound_manager.settings import GeneralSettings

# dbus_next's @method wrapper calls the function but discards its return
# value, so reach the original callable through the stored metadata — same
# pattern used in tests/test_external_output_sink.py.
_set_setting = ArctisManagerDbusSettingsService.set_setting.__dict__["__DBUS_METHOD"].fn


def _make_service(tmp_path):
    svc = ArctisManagerDbusSettingsService.__new__(ArctisManagerDbusSettingsService)
    svc.core_engine = MagicMock()
    svc.core_engine.oled_manager = None
    svc.logger = logging.getLogger("test_dbus_settings_validation")
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        svc.core_engine.general_settings = GeneralSettings()
    return svc


# ── CHA-2: pipewire_quantum must stay inside its declared BUTTON_GROUP domain ──

def test_set_setting_rejects_out_of_domain_pipewire_quantum(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.pw_utils.apply_force_quantum") as apply_quantum:
        ok = _set_setting(svc, "pipewire_quantum", json.dumps(8192))

    assert ok is False
    assert svc.core_engine.general_settings.pipewire_quantum == 0
    apply_quantum.assert_not_called()


def test_set_setting_rejects_bool_as_int_for_pipewire_quantum(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.pw_utils.apply_force_quantum") as apply_quantum:
        # json.loads("true") -> Python True; isinstance(True, int) is True,
        # which is exactly what let this through before the fix.
        ok = _set_setting(svc, "pipewire_quantum", json.dumps(True))

    assert ok is False
    assert svc.core_engine.general_settings.pipewire_quantum == 0
    apply_quantum.assert_not_called()


def test_set_setting_accepts_declared_pipewire_quantum_value(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.pw_utils.apply_force_quantum") as apply_quantum:
        ok = _set_setting(svc, "pipewire_quantum", json.dumps(2048))

    assert ok is True
    assert svc.core_engine.general_settings.pipewire_quantum == 2048
    apply_quantum.assert_called_once_with(2048)


# ── CHA-8: SELECT settings (default_value=None) must still validate type ──

def test_set_setting_rejects_list_for_select_setting(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "external_output_device", json.dumps([1, 2, {"a": None}]))

    assert ok is False
    assert svc.core_engine.general_settings.external_output_device is None


def test_set_setting_rejects_dict_for_select_setting(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "redirect_audio_on_disconnect_device", json.dumps({"x": 1}))

    assert ok is False
    assert svc.core_engine.general_settings.redirect_audio_on_disconnect_device is None


def test_set_setting_accepts_string_for_select_setting(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "external_output_device", json.dumps("alsa_output.pci-0000_00_1f.3"))

    assert ok is True
    assert svc.core_engine.general_settings.external_output_device == "alsa_output.pci-0000_00_1f.3"


def test_set_setting_accepts_none_for_select_setting(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "external_output_device", json.dumps(None))

    assert ok is True
    assert svc.core_engine.general_settings.external_output_device is None


# ── CHA-12: hrir_id must be checked against the bundled catalogue ──

def test_set_setting_rejects_directory_traversal_hrir_id(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.sonar_to_pipewire.apply_hrir_choice") as apply_hrir:
        ok = _set_setting(svc, "hrir_id", json.dumps("../../../../../../tmp/x/sine"))

    assert ok is False
    # Falls back to whatever hrir_id already was (the bundled default) — the
    # traversing id must never reach general_settings.yaml or apply_hrir_choice.
    assert svc.core_engine.general_settings.hrir_id == "atmos"
    apply_hrir.assert_not_called()


def test_set_setting_accepts_catalogue_hrir_id(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.sonar_to_pipewire.apply_hrir_choice") as apply_hrir:
        ok = _set_setting(svc, "hrir_id", json.dumps("ssc_hu"))

    assert ok is True
    assert svc.core_engine.general_settings.hrir_id == "ssc_hu"
    apply_hrir.assert_called_once_with("ssc_hu")


# ── aux_enabled has no ConfigSetting entry either (its own GUI button, not a
# generic widget) — it must be special-cased like hrir_id/weather keys above
# it, or it falls into the generic `general_settings_keys` branch, finds no
# ConfigSetting, and always returns False without writing or reconfiguring. ──

class _ImmediateThread:
    """Runs the target synchronously instead of spawning a real OS thread, so
    the test can assert on it deterministically (same helper as
    tests/test_preferred_device.py)."""

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_set_setting_aux_enabled_persists_and_reconfigures(tmp_path, caplog):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread", _ImmediateThread), \
         caplog.at_level(logging.ERROR):
        ok = _set_setting(svc, "aux_enabled", json.dumps(True))

    assert ok is True
    assert svc.core_engine.general_settings.aux_enabled is True
    svc.core_engine.configure_virtual_sinks.assert_called_once()
    assert "Unknown general setting configuration" not in caplog.text


def test_set_setting_aux_enabled_rejects_non_bool(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path), \
         patch("arctis_sound_manager.dbus_service.threading.Thread", _ImmediateThread):
        ok = _set_setting(svc, "aux_enabled", json.dumps("yes"))

    assert ok is False
    assert svc.core_engine.general_settings.aux_enabled is False
    svc.core_engine.configure_virtual_sinks.assert_not_called()


# ── chatmix_channels has no ConfigSetting entry either (#249/#269, its own
# per-card checkbox, not a generic widget) — special-cased the same way.
# Unlike aux_enabled, nothing needs reconfiguring: the next
# manage_mix_change() tick reads it fresh via PulseAudioManager.set_mix. ──

def test_set_setting_chatmix_channels_persists(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "chatmix_channels", json.dumps(["game", "media", "aux"]))

    assert ok is True
    assert svc.core_engine.general_settings.chatmix_channels == ["game", "media", "aux"]


def test_set_setting_chatmix_channels_rejects_chat(tmp_path):
    """Chat is the dial's fixed other side — never configurable."""
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "chatmix_channels", json.dumps(["media", "chat"]))

    assert ok is False
    assert svc.core_engine.general_settings.chatmix_channels == ["game"]


def test_set_setting_chatmix_channels_rejects_non_list(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "chatmix_channels", json.dumps("media"))

    assert ok is False
    assert svc.core_engine.general_settings.chatmix_channels == ["game"]


def test_set_setting_chatmix_channels_rejects_garbage_entry(tmp_path):
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "chatmix_channels", json.dumps(["media", 42]))

    assert ok is False
    assert svc.core_engine.general_settings.chatmix_channels == ["game"]


def test_set_setting_chatmix_channels_falls_back_to_game_when_emptied(tmp_path):
    """The bar/dial must always drive something (#269): persisting an empty
    selection falls back to Game rather than being written as-is."""
    svc = _make_service(tmp_path)
    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "chatmix_channels", json.dumps([]))

    assert ok is True
    assert svc.core_engine.general_settings.chatmix_channels == ["game"]


# ── #180: pm_shutdown links headset_idle_off_minutes on the same slider ─────
#
# Every device profile that declares pm_shutdown uses its own raw domain (a
# 0-6 slider on the Nova Pro Wireless, seconds*60 on a button group on the
# Arctis 7+, ...), but all of them label each value through values_mapping
# with either "never" or "<N>_minute(s)". That label, not the raw value, is
# what decides headset_idle_off_minutes — see _pm_shutdown_minutes.

def _with_pm_shutdown_device(svc, values_mapping, default_value=0):
    from arctis_sound_manager.config import ConfigSetting

    config = ConfigSetting(
        name="pm_shutdown", type="slider", default_value=default_value,
        values_mapping=values_mapping,
    )
    svc.core_engine.device_config = MagicMock(
        settings={"power_management": [config]}, name="Test Device")
    svc.core_engine.device_settings = MagicMock(settings={"pm_shutdown": default_value})
    return config


def test_pm_shutdown_slider_sets_headset_idle_off_minutes(tmp_path):
    svc = _make_service(tmp_path)
    _with_pm_shutdown_device(svc, {0: "never", 1: "1_minutes", 2: "5_minutes",
                                    3: "10_minutes"})

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "pm_shutdown", json.dumps(3))

    assert ok is True
    assert svc.core_engine.device_settings.settings["pm_shutdown"] == 3
    assert svc.core_engine.general_settings.headset_idle_off_minutes == 10


def test_pm_shutdown_never_disables_headset_idle_off_minutes(tmp_path):
    svc = _make_service(tmp_path)
    svc.core_engine.general_settings.headset_idle_off_minutes = 10  # was on
    _with_pm_shutdown_device(svc, {0: "never", 1: "1_minutes"})

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "pm_shutdown", json.dumps(0))

    assert ok is True
    assert svc.core_engine.general_settings.headset_idle_off_minutes == 0


def test_pm_shutdown_button_group_domain_also_maps_by_label(tmp_path):
    """Arctis 7+-style domain (raw value = seconds, not a small index) —
    proves the mapping goes through the label, not the raw number."""
    svc = _make_service(tmp_path)
    _with_pm_shutdown_device(svc, {0x00: "never", 0x0a: "10_minutes", 0x3c: "60_minutes"})

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "pm_shutdown", json.dumps(0x3c))

    assert ok is True
    assert svc.core_engine.general_settings.headset_idle_off_minutes == 60


def test_pm_shutdown_unparseable_label_leaves_general_setting_untouched(tmp_path):
    svc = _make_service(tmp_path)
    svc.core_engine.general_settings.headset_idle_off_minutes = 5
    _with_pm_shutdown_device(svc, {0: "never", 1: "not_a_minutes_label"})

    with patch("arctis_sound_manager.settings.SETTINGS_FOLDER", tmp_path):
        ok = _set_setting(svc, "pm_shutdown", json.dumps(1))

    assert ok is True  # the device write itself still succeeds
    assert svc.core_engine.general_settings.headset_idle_off_minutes == 5
