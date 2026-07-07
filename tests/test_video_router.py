# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for video_router — override loading/saving."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.pw_utils import app_override_key
from arctis_sound_manager.scripts.video_router import (
    load_overrides,
    save_overrides,
    _auto_route,
    _is_flapping,
    _move_times,
    _confirm_manual_move,
    _lookup_override,
    _pending_moves,
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


# ── Anti-flap guard (issue #102) ──────────────────────────────────────────────

def test_is_flapping_under_threshold():
    _move_times.clear()
    assert _is_flapping("mpv", now=0.0) is False
    assert _is_flapping("mpv", now=1.0) is False


def test_is_flapping_at_threshold():
    _move_times.clear()
    _is_flapping("mpv", now=0.0)
    _is_flapping("mpv", now=5.0)
    assert _is_flapping("mpv", now=10.0) is True


def test_is_flapping_window_expiry():
    _move_times.clear()
    _is_flapping("mpv", now=0.0)
    _is_flapping("mpv", now=1.0)
    assert _is_flapping("mpv", now=40.0) is False


def test_is_flapping_per_app_isolation():
    _move_times.clear()
    _is_flapping("mpv", now=0.0)
    _is_flapping("mpv", now=1.0)
    assert _is_flapping("firefox", now=2.0) is False


# ── app_override_key (issue #108: Chromium apps sharing application.name) ─────

def test_app_override_key_generic_name_with_binary_is_composite():
    key1 = app_override_key("Chromium", "vesktop")
    key2 = app_override_key("Chromium", "pear-desktop")
    assert key1 == "Chromium|vesktop"
    assert key2 == "Chromium|pear-desktop"
    assert key1 != key2


def test_app_override_key_generic_name_without_binary_falls_back_to_name():
    assert app_override_key("Chromium", "") == "Chromium"


def test_app_override_key_non_generic_name_is_unaffected():
    assert app_override_key("Discord", "discord") == "Discord"
    assert app_override_key("Firefox", "firefox") == "Firefox"


# ── _lookup_override (issue #108: composite key with legacy fallback) ─────────

def test_lookup_override_prefers_composite_key():
    overrides = {"Chromium|vesktop": "Arctis_Chat", "Chromium": "Arctis_Media"}
    key = app_override_key("Chromium", "vesktop")
    assert _lookup_override(overrides, key, "Chromium") == "Arctis_Chat"


def test_lookup_override_falls_back_to_legacy_name():
    """A legacy override ('Chromium': 'Arctis_Media') written before #108
    still applies to an app with no composite entry of its own yet."""
    overrides = {"Chromium": "Arctis_Media"}
    key = app_override_key("Chromium", "pear-desktop")
    assert _lookup_override(overrides, key, "Chromium") == "Arctis_Media"


def test_lookup_override_two_chromium_apps_are_independent():
    overrides = {
        "Chromium|vesktop": "Arctis_Chat",
        "Chromium|pear-desktop": "Arctis_Media",
    }
    vesktop_key = app_override_key("Chromium", "vesktop")
    pear_key = app_override_key("Chromium", "pear-desktop")
    assert _lookup_override(overrides, vesktop_key, "Chromium") == "Arctis_Chat"
    assert _lookup_override(overrides, pear_key, "Chromium") == "Arctis_Media"


def test_lookup_override_non_generic_app_ignores_other_entries():
    overrides = {"Discord": "Arctis_Chat"}
    assert _lookup_override(overrides, "Discord", "Discord") == "Arctis_Chat"
    assert _lookup_override(overrides, "Firefox", "Firefox") is None


# ── _confirm_manual_move stability gate (issue #102 residual gap) ─────────────
# The anti-flap guard (_FLAP_THRESHOLD=3) only fires on the 3rd distinct
# detected move within the flap window, leaving the first one or two flips
# free to be saved immediately as an override. _confirm_manual_move closes
# that gap: a candidate move is only persisted once it has stayed on the
# same target for _STABILITY_DELAY seconds without being displaced again.

def test_confirm_manual_move_not_saved_immediately():
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    result = _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=0.0)
    assert result is False
    assert overrides == {}
    assert "mpv" in _pending_moves


def test_confirm_manual_move_saved_after_stability_delay(tmp_path):
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    f = tmp_path / "overrides.json"
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        assert _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=0.0) is False
        assert _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=2.5) is True
    assert overrides == {"mpv": "Arctis_Media"}
    assert "mpv" not in _pending_moves


def test_confirm_manual_move_not_confirmed_before_delay_elapses(tmp_path):
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    f = tmp_path / "overrides.json"
    with patch("arctis_sound_manager.scripts.video_router.OVERRIDES_FILE", f):
        assert _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=0.0) is False
        # Still well under the 2s stability delay.
        assert _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=1.0) is False
    assert overrides == {}


def test_confirm_manual_move_single_flip_reverted_does_not_save():
    """A flip immediately re-moved back before it stabilizes must not poison
    routing_overrides.json (issue #102)."""
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    assert _confirm_manual_move("mpv", "mpv", "Arctis_Chat", overrides, now=0.0) is False
    # The caller detects the app is back on its baseline sink and drops the
    # stale candidate (mirrors the "no drift this tick" branch in the loop).
    _pending_moves.pop("mpv", None)
    assert overrides == {}
    assert "mpv" not in _pending_moves


def test_confirm_manual_move_new_target_before_stability_replaces_pending():
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    assert _confirm_manual_move("mpv", "mpv", "Arctis_Media", overrides, now=0.0) is False
    assert _pending_moves["mpv"][0] == "Arctis_Media"
    assert _confirm_manual_move("mpv", "mpv", "Arctis_Chat", overrides, now=0.5) is False
    assert _pending_moves["mpv"][0] == "Arctis_Chat"
    assert overrides == {}


def test_confirm_manual_move_physical_arctis_ignored_immediately():
    _pending_moves.clear()
    overrides = {}
    result = _confirm_manual_move(
        "mpv", "mpv", "SteelSeries_Arctis_Nova_Pro_Wireless", overrides, now=0.0,
    )
    assert result is True
    assert overrides == {}
    assert "mpv" not in _pending_moves


def test_confirm_manual_move_flapping_keeps_existing_override():
    _pending_moves.clear()
    _move_times.clear()
    _is_flapping("firefox", now=0.0)
    _is_flapping("firefox", now=1.0)
    overrides = {"firefox": "Arctis_Game"}
    result = _confirm_manual_move("firefox", "firefox", "Arctis_Chat", overrides, now=2.0)
    assert result is True
    assert overrides == {"firefox": "Arctis_Game"}
    assert "firefox" not in _pending_moves


def test_confirm_manual_move_composite_key_independent_pending():
    """Two apps sharing the generic 'Chromium' name (issue #108) must not
    share a pending-move slot once keyed by app_override_key."""
    _pending_moves.clear()
    _move_times.clear()
    overrides = {}
    vesktop_key = app_override_key("Chromium", "vesktop")
    pear_key = app_override_key("Chromium", "pear-desktop")
    assert _confirm_manual_move(vesktop_key, "Chromium", "Arctis_Chat", overrides, now=0.0) is False
    assert _confirm_manual_move(pear_key, "Chromium", "Arctis_Media", overrides, now=0.0) is False
    assert vesktop_key in _pending_moves
    assert pear_key in _pending_moves
    assert _pending_moves[vesktop_key][0] == "Arctis_Chat"
    assert _pending_moves[pear_key][0] == "Arctis_Media"
