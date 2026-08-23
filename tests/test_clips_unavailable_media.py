# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #204: a clips folder on a disconnected drive took the window down.

The reporter had his clips on removable media. When the drive went away, the
mount point stayed — `/run/media/<user>/Videos/clips` still passes `exists()`
— and answered EIO on the first read. Two things followed from that one cause:

1. ClipsPage's constructor calls refresh_clips() -> list_clips(), the OSError
   escaped, and the whole main window failed to open: no mixer, no Sonar, no
   settings, because a disk was not there.
2. The 5 s game poll kept calling _on_toggle() every tick while a game ran,
   since the capture never came up. Each attempt asks the portal for a
   screencast and spawns an encoder — "clips started several instances over
   and over again".
"""
from __future__ import annotations

import pathlib
from unittest import mock

import pytest

from arctis_sound_manager.clip_library import list_clips
from arctis_sound_manager.gui.clips_page import ClipsPage


def test_a_clips_folder_that_answers_eio_lists_nothing(tmp_path):
    (tmp_path / "clip_a.mkv").write_bytes(b"x")

    with mock.patch.object(pathlib.Path, "iterdir",
                           side_effect=OSError(5, "Input/output error")):
        assert list_clips(tmp_path) == []


def test_one_unreadable_file_does_not_hide_the_others(tmp_path):
    """On failing media some syscalls succeed and some do not."""
    good = tmp_path / "clip_good.mkv"
    good.write_bytes(b"x")
    bad = tmp_path / "clip_bad.mkv"
    bad.write_bytes(b"x")

    real_stat = pathlib.Path.stat

    def flaky(self, *a, **kw):
        if self.name == "clip_bad.mkv":
            raise OSError(5, "Input/output error")
        return real_stat(self, *a, **kw)

    with mock.patch.object(pathlib.Path, "stat", flaky):
        assert [p.name for p in list_clips(tmp_path)] == ["clip_good.mkv"]


def test_a_missing_folder_is_still_just_empty(tmp_path):
    assert list_clips(tmp_path / "not-there") == []


def _page(capture_starts: bool):
    """A ClipsPage stub exercising only _poll_game's autostart branch."""
    page = mock.MagicMock()
    page._closing = False
    page._autostart.isChecked.return_value = True
    page._capture = None
    page._autostart_failed_for = None
    page._error = "clips folder unavailable"
    page.attempts = 0

    def _toggle():
        page.attempts += 1
        if capture_starts:
            page._capture = object()

    page._on_toggle.side_effect = _toggle
    return page


def _run_ticks(page, game: str, ticks: int):
    import arctis_sound_manager.clip_capture as cc
    original = cc.detect_game
    cc.detect_game = lambda: game
    try:
        for _ in range(ticks):
            ClipsPage._poll_game(page)
    finally:
        cc.detect_game = original


def test_a_failed_autostart_is_not_retried_every_tick():
    """The behaviour that produced "several instances over and over again"."""
    page = _page(capture_starts=False)

    _run_ticks(page, "Elden Ring", ticks=5)

    assert page.attempts == 1, (
        "each attempt asks the portal for a screencast and spawns an encoder; "
        "a cause that is not going away must not be retried on a 5 s timer"
    )


def test_a_different_game_is_tried_again():
    """The backoff is per game, not permanent: something changed."""
    page = _page(capture_starts=False)

    _run_ticks(page, "Elden Ring", ticks=3)
    _run_ticks(page, "Hades", ticks=3)

    assert page.attempts == 2


def test_a_successful_autostart_clears_the_backoff():
    page = _page(capture_starts=True)

    _run_ticks(page, "Elden Ring", ticks=2)

    assert page.attempts == 1
    assert page._autostart_failed_for is None


def test_pressing_the_button_clears_the_backoff():
    """Asking again by hand is exactly the signal that a retry is wanted."""
    page = mock.MagicMock()
    page._autostart_failed_for = "Elden Ring"

    ClipsPage._on_toggle_clicked(page)

    assert page._autostart_failed_for is None
    page._on_toggle.assert_called_once()
