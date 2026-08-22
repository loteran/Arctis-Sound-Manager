# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""EXT-1: the EQ mode toggle must always release the UI.

_ToggleWorker.run() emitted `done` only on its success path and on two early
returns, while everything between _apply_yaml() and _restore_streams() ran
unguarded. The button is disabled before the worker starts and re-enabled only
by _on_toggle_done, so one exception in that stretch froze the button on
"restarting_audio" for ever — and, if it landed after the filter-chain restart,
left every playing stream parked on nodes that no longer existed: silent audio
loss on the user's own click.

The near-identical _ApplyWorker in sonar_page.py has always wrapped its whole
body and emitted done(False) on exception. This worker was the outlier, and
nothing covered it.
"""
from __future__ import annotations

from arctis_sound_manager.gui import equalizer_page as ep


def _worker_reporting_to(collected):
    worker = ep._ToggleWorker("sonar", "custom")
    worker.done.connect(lambda ok, mode: collected.append((ok, mode)))
    return worker


def test_an_exception_mid_toggle_still_reports_done(monkeypatch):
    """The failure the user sees is a frozen button; this is what unfreezes
    it."""
    collected: list[tuple[bool, str]] = []
    monkeypatch.setattr(ep, "_apply_yaml",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    _worker_reporting_to(collected).run()

    assert collected == [(False, "custom")], (
        "run() must emit done(False, old_mode) rather than let the exception "
        "escape and leave the toggle disabled for ever"
    )


def test_an_exception_after_the_restart_still_reports_done(monkeypatch, tmp_path):
    """The worst case: the filter-chain has already been restarted, so the
    streams are on torn-down nodes. Reporting done is what lets the user act."""
    collected: list[tuple[bool, str]] = []
    monkeypatch.setattr(ep, "_apply_yaml", lambda *a, **kw: True)
    monkeypatch.setattr(ep, "ensure_sonar_eq_configs", lambda *a, **kw: True)
    monkeypatch.setattr(ep, "STATE_FILE", tmp_path / ".eq_mode")
    monkeypatch.setattr(ep._ToggleWorker, "_snapshot_streams",
                        staticmethod(lambda log: ([], [])))
    monkeypatch.setattr(ep.sc, "restart", lambda *a, **kw: True)
    monkeypatch.setattr(ep._ToggleWorker, "msleep", lambda self, ms: None)
    monkeypatch.setattr(
        ep.DbusWrapper, "recreate_loopbacks_game_media_sync",
        staticmethod(lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bus gone"))),
    )

    _worker_reporting_to(collected).run()

    assert collected == [(False, "custom")]


def test_pw_metadata_absence_does_not_abort_the_toggle(monkeypatch):
    """These four calls had neither a which() guard nor a try, unlike every
    other pw-metadata call site in the code base."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    ran: list = []
    monkeypatch.setattr(ep.subprocess, "run", lambda *a, **kw: ran.append(a))

    import logging
    ep._ToggleWorker._pw_metadata("default.audio.sink", "{}", logging.getLogger("t"))

    assert ran == [], "no pw-metadata process may be spawned when the binary is absent"
