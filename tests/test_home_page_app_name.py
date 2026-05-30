# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for HomePage._friendly_app_name — the per-channel app label helper.

Discord's audio streams report application.name "WEBRTC VoiceEngine"; the
helper must fall back to the process binary so the user sees "Discord".
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.home_page import HomePage


def test_uses_application_name_when_specific():
    assert HomePage._friendly_app_name({"application.name": "Firefox"}) == "Firefox"


def test_falls_back_to_binary_for_webrtc():
    pl = {
        "application.name": "WEBRTC VoiceEngine",
        "application.process.binary": "Discord",
    }
    assert HomePage._friendly_app_name(pl) == "Discord"


def test_capitalizes_and_strips_path_from_binary():
    pl = {"application.name": "", "application.process.binary": "/usr/bin/mpv"}
    assert HomePage._friendly_app_name(pl) == "Mpv"


def test_keeps_application_name_over_binary_when_not_generic():
    pl = {
        "application.name": "Spotify",
        "application.process.binary": "spotify",
    }
    assert HomePage._friendly_app_name(pl) == "Spotify"


def test_default_when_nothing_usable():
    assert HomePage._friendly_app_name({}) == "Audio"
