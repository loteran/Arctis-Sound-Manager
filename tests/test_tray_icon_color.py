# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for resolve_tray_icon_color — the systray icon color setting (#130).

systray_icon_color is 0 (auto, follow the desktop theme), 1 (white) or
2 (black). Auto detects the color scheme via QApplication.styleHints(),
with a robust fallback to white when no QApplication instance exists yet
or the colorScheme() API is unavailable.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from arctis_sound_manager.gui.ui_utils import resolve_tray_icon_color


def test_white_choice_returns_white():
    assert resolve_tray_icon_color(1) == '#ffffff'


def test_black_choice_returns_black():
    assert resolve_tray_icon_color(2) == '#000000'


def test_auto_dark_scheme_returns_white():
    fake_app = MagicMock()
    fake_app.styleHints.return_value.colorScheme.return_value = Qt.ColorScheme.Dark
    with patch('arctis_sound_manager.gui.ui_utils.QApplication.instance', return_value=fake_app):
        assert resolve_tray_icon_color(0) == '#ffffff'


def test_auto_light_scheme_returns_black():
    fake_app = MagicMock()
    fake_app.styleHints.return_value.colorScheme.return_value = Qt.ColorScheme.Light
    with patch('arctis_sound_manager.gui.ui_utils.QApplication.instance', return_value=fake_app):
        assert resolve_tray_icon_color(0) == '#000000'


def test_auto_without_qapplication_instance_falls_back_to_white():
    with patch('arctis_sound_manager.gui.ui_utils.QApplication.instance', return_value=None):
        assert resolve_tray_icon_color(0) == '#ffffff'


def test_auto_with_broken_style_hints_falls_back_to_white():
    fake_app = MagicMock()
    fake_app.styleHints.side_effect = Exception("no styleHints on this PySide6 build")
    with patch('arctis_sound_manager.gui.ui_utils.QApplication.instance', return_value=fake_app):
        assert resolve_tray_icon_color(0) == '#ffffff'


def test_unknown_choice_falls_back_to_auto_path():
    # Any value other than 1/2 is treated like 0 (auto).
    with patch('arctis_sound_manager.gui.ui_utils.QApplication.instance', return_value=None):
        assert resolve_tray_icon_color(99) == '#ffffff'
