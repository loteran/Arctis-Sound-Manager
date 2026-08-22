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
