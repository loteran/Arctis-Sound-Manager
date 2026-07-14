# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for video_router — override loading/saving."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager.power_status import HeadsetPower
from arctis_sound_manager.pw_utils import app_override_key
from arctis_sound_manager.scripts import video_router
from arctis_sound_manager.scripts.video_router import (
    load_overrides,
    save_overrides,
    _auto_route,
    _is_flapping,
    _move_times,
    _confirm_manual_move,
    _lookup_override,
    _pending_moves,
    _pa_placed,
    _native_placed,
    _process_tick,
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


# ── _process_tick: override sovereignty + online/offline repatriation ────────
#
# Fixes a bug where the router forcibly repatriated every stream off the
# Arctis virtual sinks whenever the default sink wasn't Arctis, ignoring
# saved overrides entirely (routing_overrides.json was never even loaded on
# that path). Repatriation must instead be keyed on the headset's actual
# power state (R2), a saved override must always be enforced (R1), and an
# unreachable/unknown power state must never trigger a move (R3 fail-safe).

class _FakeSink:
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name


class _FakeSinkInput:
    def __init__(self, index: int, sink: int, proplist: dict):
        self.index = index
        self.sink = sink
        self.proplist = proplist


class _FakeServerInfo:
    def __init__(self, default_sink_name: str):
        self.default_sink_name = default_sink_name


class _FakePulse:
    """Minimal stand-in for pulsectl.Pulse covering what _process_tick uses."""

    def __init__(self, sinks: list, sink_inputs: list, default_sink_name: str):
        self._sinks = sinks
        self._sink_inputs = sink_inputs
        self._default_sink_name = default_sink_name
        self.moves: list[tuple[int, int]] = []

    def sink_list(self):
        return self._sinks

    def sink_input_list(self):
        return self._sink_inputs

    def server_info(self):
        return _FakeServerInfo(self._default_sink_name)

    def sink_input_move(self, si_index: int, target_index: int):
        self.moves.append((si_index, target_index))
        for si in self._sink_inputs:
            if si.index == si_index:
                si.sink = target_index


@pytest.fixture(autouse=True)
def _reset_router_globals():
    """Isolate module-level tracking state across tests in this file."""
    _pa_placed.clear()
    _native_placed.clear()
    _move_times.clear()
    _pending_moves.clear()
    video_router._power_cache = (0.0, HeadsetPower.UNKNOWN)
    with patch.object(video_router, "_last_native_check", 0.0), \
         patch("arctis_sound_manager.scripts.video_router.get_native_streams", return_value=[]), \
         patch("arctis_sound_manager.scripts.video_router._load_channel_outputs", return_value={}):
        yield
    _pa_placed.clear()
    _native_placed.clear()
    _move_times.clear()
    _pending_moves.clear()


def test_tick_online_default_hdmi_override_enforced_not_repatriated():
    """R1: a saved override to Arctis_Chat is enforced even though HDMI is
    the default sink and the headset is online — the app must end up on
    Arctis_Chat, never on the default sink."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    chat = _FakeSink(1, "Arctis_Chat")
    si = _FakeSinkInput(10, sink=hdmi.index, proplist={"application.name": "Discord"})
    pulse = _FakePulse([hdmi, chat], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.ON), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides",
               return_value={"Discord": "Arctis_Chat"}), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides"):
        _process_tick(pulse)

    assert pulse.moves == [(si.index, chat.index)]
    assert si.sink == chat.index


def test_tick_online_default_hdmi_no_override_not_moved():
    """No override, Arctis not default, headset online: the router must
    neither auto-route nor force-move the app anywhere."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    chat = _FakeSink(1, "Arctis_Chat")
    si = _FakeSinkInput(10, sink=hdmi.index, proplist={"application.name": "SomeRandomApp"})
    pulse = _FakePulse([hdmi, chat], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.ON), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides", return_value={}), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides") as mock_save:
        _process_tick(pulse)

    assert pulse.moves == []
    assert si.sink == hdmi.index
    mock_save.assert_not_called()


def test_tick_offline_repatriates_without_writing_override():
    """R2/R5: headset off — an app parked on an Arctis virtual sink is
    pulled onto the default sink, but this is transient: no override is
    written for it."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    chat = _FakeSink(1, "Arctis_Chat")
    si = _FakeSinkInput(10, sink=chat.index, proplist={"application.name": "Discord"})
    pulse = _FakePulse([hdmi, chat], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.OFF), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides") as mock_load, \
         patch("arctis_sound_manager.scripts.video_router.save_overrides") as mock_save:
        _process_tick(pulse)

    assert pulse.moves == [(si.index, hdmi.index)]
    assert si.sink == hdmi.index
    # The OFF path returns before ever touching routing_overrides.json.
    mock_load.assert_not_called()
    mock_save.assert_not_called()


def test_tick_offline_repatriates_media_channel_too():
    """R4: Arctis_Media must be repatriated exactly like Game/Chat — it used
    to be missing from the virtual-sink set, making repatriation asymmetric."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    media = _FakeSink(1, "Arctis_Media")
    si = _FakeSinkInput(10, sink=media.index, proplist={"application.name": "Firefox"})
    pulse = _FakePulse([hdmi, media], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.OFF), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides"), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides"):
        _process_tick(pulse)

    assert pulse.moves == [(si.index, hdmi.index)]


def test_tick_offline_default_is_arctis_sink_moves_nothing():
    """Regression: when the headset is off AND the system default sink is
    ITSELF an Arctis virtual sink (e.g. the user's default is Arctis_Game),
    there is nowhere useful to repatriate to — every Arctis channel is
    equally silent. The router must leave the stream exactly where the user
    placed it (Arctis_Media) instead of bouncing it to the default channel
    (Arctis_Game)."""
    game = _FakeSink(0, "Arctis_Game")
    media = _FakeSink(1, "Arctis_Media")
    si = _FakeSinkInput(10, sink=media.index, proplist={"application.name": "Firefox"})
    pulse = _FakePulse([game, media], [si], default_sink_name=game.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.OFF), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides") as mock_load, \
         patch("arctis_sound_manager.scripts.video_router.save_overrides") as mock_save:
        _process_tick(pulse)

    assert pulse.moves == []
    assert si.sink == media.index
    mock_load.assert_not_called()
    mock_save.assert_not_called()


def test_tick_offline_default_is_physical_arctis_moves_nothing():
    """Same regression guard, but for the physical SteelSeries output as
    default sink (not just a virtual Arctis_* channel)."""
    physical = _FakeSink(0, "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo")
    chat = _FakeSink(1, "Arctis_Chat")
    si = _FakeSinkInput(10, sink=chat.index, proplist={"application.name": "Discord"})
    pulse = _FakePulse([physical, chat], [si], default_sink_name=physical.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.OFF), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides"), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides"):
        _process_tick(pulse)

    assert pulse.moves == []
    assert si.sink == chat.index


def test_tick_offline_default_hdmi_still_repatriates():
    """The existing repatriation behaviour must be unaffected when the
    default sink is a real non-Arctis output (HDMI) — this is the normal
    case the fix must not regress."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    game = _FakeSink(1, "Arctis_Game")
    si = _FakeSinkInput(10, sink=game.index, proplist={"application.name": "Steam"})
    pulse = _FakePulse([hdmi, game], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.OFF), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides"), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides"):
        _process_tick(pulse)

    assert pulse.moves == [(si.index, hdmi.index)]


def test_tick_unknown_power_status_moves_nothing():
    """R3 fail-safe: when the headset's power state can't be determined
    (daemon down / D-Bus unreachable), the router must not move anything."""
    hdmi = _FakeSink(0, "alsa_output.hdmi-stereo")
    chat = _FakeSink(1, "Arctis_Chat")
    si = _FakeSinkInput(10, sink=chat.index, proplist={"application.name": "Discord"})
    pulse = _FakePulse([hdmi, chat], [si], default_sink_name=hdmi.name)

    with patch("arctis_sound_manager.scripts.video_router.get_headset_power",
               return_value=HeadsetPower.UNKNOWN), \
         patch("arctis_sound_manager.scripts.video_router.load_overrides", return_value={}), \
         patch("arctis_sound_manager.scripts.video_router.save_overrides") as mock_save:
        _process_tick(pulse)

    assert pulse.moves == []
    assert si.sink == chat.index
    mock_save.assert_not_called()


def test_get_headset_power_caches_within_ttl():
    """get_headset_power() must not re-query D-Bus on every call within the
    cache TTL, so the router doesn't hammer the daemon every tick."""
    from unittest.mock import AsyncMock

    video_router._power_cache = (0.0, HeadsetPower.UNKNOWN)
    with patch("arctis_sound_manager.scripts.video_router._fetch_headset_power_async",
               new_callable=AsyncMock, return_value=HeadsetPower.ON) as mock_fetch:
        first = video_router.get_headset_power()
        second = video_router.get_headset_power()

    assert first == HeadsetPower.ON
    assert second == HeadsetPower.ON
    mock_fetch.assert_called_once()


def test_get_headset_power_unreachable_daemon_is_unknown():
    """Any failure querying D-Bus (timeout, daemon down, malformed reply)
    resolves to UNKNOWN, never to a guessed ON/OFF."""
    from unittest.mock import AsyncMock

    video_router._power_cache = (0.0, HeadsetPower.UNKNOWN)
    with patch("arctis_sound_manager.scripts.video_router._fetch_headset_power_async",
               new_callable=AsyncMock, side_effect=TimeoutError("no reply")):
        result = video_router.get_headset_power(force=True)

    assert result == HeadsetPower.UNKNOWN
