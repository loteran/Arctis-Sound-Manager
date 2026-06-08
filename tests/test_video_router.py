# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for video_router — override loading/saving."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.scripts.video_router import (
    load_overrides,
    save_overrides,
    _auto_route,
)


# ── _auto_route (issue #64: browsers, incl. LibreWolf, must go to Media) ──────

def test_auto_route_librewolf_to_media():
    assert _auto_route("LibreWolf", {}) == "Arctis_Media"


def test_auto_route_firefox_to_media():
    assert _auto_route("Firefox", {}) == "Arctis_Media"


def test_auto_route_game_binary_to_game():
    assert _auto_route("RocketLeague.exe",
                       {"application.process.binary": "wine64-preloader"}) == "Arctis_Game"


def test_auto_route_chat_app_to_chat():
    assert _auto_route("Discord", {}) == "Arctis_Chat"


def test_auto_route_unknown_returns_none():
    assert _auto_route("SomeRandomApp", {}) is None


def test_load_overrides_missing_file():
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", Path("/nonexistent/path.json")):
        assert load_overrides() == {}


def test_load_overrides_valid_json(tmp_path):
    f = tmp_path / "overrides.json"
    f.write_text(json.dumps({"firefox": "Arctis_Game"}))
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        result = load_overrides()
    assert result == {"firefox": "Arctis_Game"}


def test_load_overrides_invalid_json(tmp_path):
    f = tmp_path / "overrides.json"
    f.write_text("not valid json{{{")
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        result = load_overrides()
    assert result == {}


def test_save_overrides_atomic(tmp_path):
    f = tmp_path / "overrides.json"
    overrides = {"mpv": "Arctis_Chat", "vlc": "Arctis_Game"}
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        save_overrides(overrides)
    assert json.loads(f.read_text()) == overrides
    # tmp file should be cleaned up (replaced)
    assert not (tmp_path / "overrides.tmp").exists()


def test_save_then_load_roundtrip(tmp_path):
    f = tmp_path / "overrides.json"
    data = {"app1": "sink1", "app2": "sink2"}
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        save_overrides(data)
        loaded = load_overrides()
    assert loaded == data
