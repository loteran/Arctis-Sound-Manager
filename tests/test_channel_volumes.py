# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for issue #134 — Game/Chat volume reverts to 100% on its own.

The Arctis virtual sinks are pw-loopback processes. Every time a loopback is
(re)created — socket change, EQ-mode switch, config regen, or a watchdog
restart of a dead process — the fresh sink comes up at the PipeWire default
(100%), discarding the user's level. Nothing persisted or re-applied it.

Fixed by:
  * arctis_sound_manager.channel_volumes — a small JSON store, keyed by the
    sink's stable node.name, written by the GUI on every volume change.
  * CoreEngine._queue_volume_restore / _restore_channel_volumes /
    _process_volume_restore — the daemon re-asserts each saved level after a
    (re)creation, retrying until the sink reappears, without fighting a later
    deliberate change from the system mixer.
"""

import logging
import threading
from unittest.mock import MagicMock

import arctis_sound_manager.channel_volumes as cv


# ── Persistence store ──────────────────────────────────────────────────────

def _redirect_store(monkeypatch, tmp_path):
    monkeypatch.setattr(cv, "CHANNEL_VOLUMES_FILE", tmp_path / "channel_volumes.json")


def test_load_missing_file_returns_empty(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    assert cv.load_channel_volumes() == {}


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    cv.save_channel_volume("Arctis_Game", 50)
    cv.save_channel_volume("Arctis_Chat", 30)
    assert cv.load_channel_volumes() == {"Arctis_Game": 50, "Arctis_Chat": 30}


def test_save_merges_and_overwrites(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    cv.save_channel_volume("Arctis_Game", 50)
    cv.save_channel_volume("Arctis_Game", 75)
    assert cv.load_channel_volumes() == {"Arctis_Game": 75}


def test_save_clamps_out_of_range(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    cv.save_channel_volume("Arctis_Game", 150)
    cv.save_channel_volume("Arctis_Chat", -10)
    assert cv.load_channel_volumes() == {"Arctis_Game": 100, "Arctis_Chat": 0}


def test_load_malformed_file_returns_empty(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    cv.CHANNEL_VOLUMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    cv.CHANNEL_VOLUMES_FILE.write_text("{not valid json")
    assert cv.load_channel_volumes() == {}


def test_load_ignores_non_dict_and_bad_entries(monkeypatch, tmp_path):
    _redirect_store(monkeypatch, tmp_path)
    cv.CHANNEL_VOLUMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    cv.CHANNEL_VOLUMES_FILE.write_text('{"Arctis_Game": 40, "Arctis_Chat": "loud", "x": null}')
    assert cv.load_channel_volumes() == {"Arctis_Game": 40}


# ── CoreEngine volume-restore logic ────────────────────────────────────────

def _spec(channel, capture_name):
    s = MagicMock()
    s.channel = channel
    s.capture_name = capture_name
    return s


def _make_engine(saved, specs, present):
    """Build a bare CoreEngine wired with mocks.

    saved   — dict node_name -> pct returned by load_channel_volumes
    specs   — dict channel -> spec returned by loopback_manager.specs()
    present — set of node names whose sink is currently in the graph
    """
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = logging.getLogger("test")
    engine._volume_restore_pending = {}

    engine.loopback_manager = MagicMock()
    engine.loopback_manager.specs.return_value = specs

    applied = {}

    def _set(node_name, pct):
        if node_name in present:
            applied[node_name] = pct
            return True
        return False

    engine.pa_audio_manager = MagicMock()
    engine.pa_audio_manager.set_sink_volume_by_node.side_effect = _set
    engine._applied = applied

    import arctis_sound_manager.core as core_mod
    engine._patch_saved = saved
    core_mod.load_channel_volumes = lambda: saved  # type: ignore[assignment]
    return engine


def test_restore_applies_saved_volume_when_sink_present():
    specs = {"game": _spec("game", "Arctis_Game")}
    engine = _make_engine({"Arctis_Game": 50}, specs, present={"Arctis_Game"})

    still = engine._restore_channel_volumes(["game"])

    assert still == set()
    assert engine._applied == {"Arctis_Game": 50}


def test_restore_keeps_channel_pending_when_sink_absent():
    specs = {"game": _spec("game", "Arctis_Game")}
    engine = _make_engine({"Arctis_Game": 50}, specs, present=set())

    still = engine._restore_channel_volumes(["game"])

    assert still == {"game"}
    assert engine._applied == {}


def test_restore_skips_channel_with_no_saved_volume():
    specs = {"chat": _spec("chat", "Arctis_Chat")}
    engine = _make_engine({"Arctis_Game": 50}, specs, present={"Arctis_Chat"})

    still = engine._restore_channel_volumes(["chat"])

    assert still == set()
    assert engine._applied == {}


def test_process_drops_applied_and_retries_absent():
    specs = {
        "game": _spec("game", "Arctis_Game"),
        "chat": _spec("chat", "Arctis_Chat"),
    }
    # Game sink present (will apply), Chat absent (will retry).
    engine = _make_engine(
        {"Arctis_Game": 50, "Arctis_Chat": 30}, specs, present={"Arctis_Game"}
    )
    engine._volume_restore_pending = {"game": 6, "chat": 6}

    engine._process_volume_restore()

    assert "game" not in engine._volume_restore_pending      # applied → dropped
    assert engine._volume_restore_pending["chat"] == 5       # absent → budget spent
    assert engine._applied == {"Arctis_Game": 50}


def test_process_gives_up_after_retry_budget_exhausted():
    specs = {"game": _spec("game", "Arctis_Game")}
    engine = _make_engine({"Arctis_Game": 50}, specs, present=set())
    engine._volume_restore_pending = {"game": 1}

    engine._process_volume_restore()

    assert engine._volume_restore_pending == {}              # given up
    assert engine._applied == {}


def test_queue_marks_all_channels_with_full_budget():
    engine = _make_engine({}, {}, present=set())
    engine._process_volume_restore = MagicMock()  # not under test here

    engine._queue_volume_restore(["game", "chat", "media"])

    ticks = engine._VOLUME_RESTORE_TICKS
    assert engine._volume_restore_pending == {"game": ticks, "chat": ticks, "media": ticks}
