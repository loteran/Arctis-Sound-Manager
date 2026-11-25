# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""A write that changes a saved output says so, with who made it.

Users report their output choices gone after an update, and nothing in the
journal says what cleared them. Every write of general_settings.yaml that
changes one of those keys now logs old -> new and the code path.
"""
from __future__ import annotations

import logging

from arctis_sound_manager import settings as settings_mod
from arctis_sound_manager.settings import GeneralSettings


def test_changed_output_is_logged_with_caller(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings_mod, "SETTINGS_FOLDER", tmp_path)
    gs = GeneralSettings()
    gs.external_output_device = "HDMI 0"
    gs.write_to_file()

    gs.external_output_device = None
    with caplog.at_level(logging.INFO, logger=settings_mod.__name__):
        gs.write_to_file()
    assert "Output setting external_output_device: 'HDMI 0' -> None" in caplog.text
    assert "test_changed_output_is_logged_with_caller" in caplog.text


def test_unchanged_outputs_log_nothing(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings_mod, "SETTINGS_FOLDER", tmp_path)
    gs = GeneralSettings()
    gs.redirect_audio_on_disconnect_device = "HDMI 0"
    gs.write_to_file()
    gs.systray_show_battery = not gs.systray_show_battery
    with caplog.at_level(logging.INFO, logger=settings_mod.__name__):
        gs.write_to_file()
    assert "Output setting" not in caplog.text
