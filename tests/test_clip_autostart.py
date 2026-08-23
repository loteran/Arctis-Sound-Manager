# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Following the game: arming the buffer when one starts, letting it go when
one ends.

A rolling buffer is only worth having if it is already running when something
happens, and what people forget is arming it — "I closed the game and the
capture kept going" and "it never started" are the same gap seen from either
end. Stopping matters for its own reason: a capture with no game behind it
holds a screen's worth of frames in memory and keeps an encoder busy for a
recording nobody will ask for.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import clips_page


@pytest.fixture
def page(tmp_path, monkeypatch):
    """A Clips page whose capture is a stand-in, so nothing touches the screen."""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(clips_page, "clip_dir", lambda: tmp_path)
    monkeypatch.setattr(clips_page.ClipsPage, "_queue_thumbnail",
                        lambda self, clip: None)
    # The page polls once through the event loop at startup; nothing has been
    # stubbed at that point, so keep it out of the way until the test asks.
    monkeypatch.setattr(clips_page.ClipsPage, "_poll_game", lambda self: None)

    widget = clips_page.ClipsPage()
    monkeypatch.undo()
    monkeypatch.setattr(clips_page, "clip_dir", lambda: tmp_path)

    started: list[bool] = []
    stopped: list[bool] = []
    monkeypatch.setattr(type(widget), "_on_toggle",
                        lambda self: (started.append(True),
                                      setattr(self, "_capture", object()))[0])
    monkeypatch.setattr(type(widget), "_stop_capture",
                        lambda self: (stopped.append(True),
                                      setattr(self, "_capture", None))[0])
    widget.started, widget.stopped = started, stopped
    yield widget
    widget.deleteLater()


def _game(monkeypatch, name):
    monkeypatch.setattr("arctis_sound_manager.clip_capture.detect_game",
                        lambda: name)


def test_a_game_starting_arms_the_buffer(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)

    page._poll_game()

    assert page.started == [True]


def test_the_buffer_is_not_armed_twice(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)

    page._poll_game()
    page._poll_game()

    assert page.started == [True]


def test_the_game_going_quiet_does_not_stop_it_immediately(page, monkeypatch):
    """A loading screen or a cutscene is silence, and tearing the pipeline down
    there throws the buffer away and costs a portal prompt to rebuild."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()

    assert page.stopped == []


def test_a_game_that_stays_gone_lets_the_capture_go(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()
    # Far enough past the grace that the next poll is decisive.
    page._game_gone_since -= clips_page._GAME_GONE_GRACE_S + 1
    page._poll_game()

    assert page.stopped == [True]


def test_a_capture_the_user_started_is_never_stopped_for_them(page, monkeypatch):
    """Auto-stop only takes back what auto-start gave. Someone who pressed
    Start is recording deliberately and gets to decide when it ends."""
    page._autostart.setChecked(True)
    page._capture = object()          # as if Start had been pressed
    page._auto_started = False

    _game(monkeypatch, None)
    for _ in range(3):
        page._poll_game()

    assert page.stopped == []
    # The countdown never even starts: there is nothing here to take back.
    assert page._game_gone_since is None


def test_switching_it_off_stops_following_the_game(page, monkeypatch):
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(False)

    page._poll_game()

    assert page.started == []


def test_a_game_returning_during_the_grace_keeps_the_buffer(page, monkeypatch):
    """Coming back from a loading screen must reset the countdown, not carry
    half of it into the next silence."""
    _game(monkeypatch, "GenshinImpact")
    page._autostart.setChecked(True)
    page._poll_game()

    _game(monkeypatch, None)
    page._poll_game()
    _game(monkeypatch, "GenshinImpact")
    page._poll_game()

    assert page._game_gone_since is None
    assert page.stopped == []


def test_it_is_off_until_the_user_says_otherwise():
    """Starting ASM must never start a capture.

    Following the game is worth having, but not before anyone has asked for it:
    a session that opens with the screen already being recorded — because a
    game happened to be running when the tray came up — is a surprise, and the
    surprise costs more than the clip it might have caught. The checkbox on the
    Clips page turns it on for whoever wants the buffer armed without thinking
    about it."""
    from arctis_sound_manager.settings import GeneralSettings

    assert GeneralSettings().clips_autostart is False
