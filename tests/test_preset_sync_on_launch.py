# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""New presets must be picked up when ASM is launched, not once a day.

Sonar preset packs are published whenever SteelSeries ships one. With the 24 h
cache alone, a check that happened to run shortly *before* a batch landed left
the user without it for a full day — and closing and reopening ASM changed
nothing, since the cache decides, not the launch. Observed exactly that: the
automatic check ran at 12:56, five presets were published at 13:33, and the app
would not have seen them until the next day.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from arctis_sound_manager import preset_sync as ps  # noqa: E402


def _cache(monkeypatch, tmp_path, age_hours: float | None):
    f = tmp_path / ".preset_sync_cache"
    if age_hours is not None:
        when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        f.write_text(json.dumps({"last_check": when.isoformat()}))
    monkeypatch.setattr(ps, "_CACHE_FILE", f)
    return f


def test_launch_check_ignores_the_daily_cache(monkeypatch, tmp_path):
    """A check two hours old must not stop a launch-time check."""
    _cache(monkeypatch, tmp_path, age_hours=2)
    assert ps.PresetSyncWorker(force=True)._should_check() is True


def test_periodic_check_still_honours_the_daily_cache(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path, age_hours=2)
    assert ps.PresetSyncWorker()._should_check() is False


def test_launch_check_is_rate_limited_against_bursts(monkeypatch, tmp_path):
    """Relaunching three times in a row (or a crash loop) must not re-fetch."""
    _cache(monkeypatch, tmp_path, age_hours=1 / 60)  # one minute ago
    assert ps.PresetSyncWorker(force=True)._should_check() is False


def test_launch_check_runs_again_after_the_burst_window(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path, age_hours=10 / 60)  # ten minutes ago
    assert ps.PresetSyncWorker(force=True)._should_check() is True


def test_first_ever_run_always_checks(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path, age_hours=None)  # no cache file
    assert ps.PresetSyncWorker(force=True)._should_check() is True
    assert ps.PresetSyncWorker()._should_check() is True


def test_unreadable_cache_does_not_block_the_check(monkeypatch, tmp_path):
    f = _cache(monkeypatch, tmp_path, age_hours=1)
    f.write_text("{ not json")
    assert ps.PresetSyncWorker(force=True)._should_check() is True


def test_gui_starts_the_worker_in_forced_mode():
    """Guard rail: the launch path must keep asking for a forced check."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager" / "scripts" / "gui.py"
    assert "PresetSyncWorker(force=True)" in src.read_text()
