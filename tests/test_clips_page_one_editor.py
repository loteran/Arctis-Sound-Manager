# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Double-clicking a clip quickly must open one editor, not one per click.

Seen in use: two or three fast double-clicks on a card put two or three
editors on screen, stacked. The editor is not instant to build — it probes
the file with ffprobe first — and every double-click that arrives in that gap
is still queued when `exec()` starts its nested event loop, which then hands
each one to `_on_open_clip` again while the first editor is up.

The page is not built here (its constructor binds a global shortcut through
the desktop portal); `_on_open_clip` is called unbound on a stand-in, with an
editor whose `exec()` re-enters the handler the way the queued clicks do.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import arctis_sound_manager.gui.clip_editor as clip_editor
from arctis_sound_manager.gui.clips_page import ClipsPage


def _page():
    page = MagicMock()
    page._editor = None
    page._editor_open = False
    return page


def _item(path="/c/clip.mkv"):
    item = MagicMock()
    item.data.return_value = path
    return item


def test_a_click_that_lands_while_the_editor_is_up_does_not_open_another(monkeypatch):
    page = _page()
    built = []

    class _Editor:
        def __init__(self, path, parent):
            built.append(path)
            self.raised = 0

        def exec(self):
            # The queued second double-click, delivered inside exec()'s loop.
            ClipsPage._on_open_clip(page, _item())

        def raise_(self):
            self.raised += 1

        def activateWindow(self):
            pass

    monkeypatch.setattr(clip_editor, "ClipEditor", _Editor)

    ClipsPage._on_open_clip(page, _item())

    assert len(built) == 1
    assert page._editor is None and page._editor_open is False


def test_the_refused_click_brings_the_open_editor_forward(monkeypatch):
    page = _page()
    editors = []

    class _Editor:
        def __init__(self, path, parent):
            self.raised = 0
            editors.append(self)

        def exec(self):
            ClipsPage._on_open_clip(page, _item())

        def raise_(self):
            self.raised += 1

        def activateWindow(self):
            pass

    monkeypatch.setattr(clip_editor, "ClipEditor", _Editor)

    ClipsPage._on_open_clip(page, _item())

    assert editors[0].raised == 1


def test_the_guard_is_released_when_the_editor_fails_to_build(monkeypatch):
    """A broken file must not lock the page out of opening the next clip."""
    page = _page()

    def _boom(path, parent):
        raise RuntimeError("ffprobe not found")

    monkeypatch.setattr(clip_editor, "ClipEditor", _boom)
    monkeypatch.setattr("arctis_sound_manager.gui.clips_page.QDesktopServices.openUrl",
                        lambda url: True)

    ClipsPage._on_open_clip(page, _item())

    assert page._editor_open is False


def test_a_start_in_flight_is_not_started_again():
    """start() waits for the portal in a nested GLib loop; the game poll fired
    inside it, saw no capture, and opened another picker — three at once."""
    page = MagicMock()
    page._starting = False
    calls = []

    def inner():
        calls.append(1)
        ClipsPage._start_capture(page)      # the poll firing mid-start

    page._start_capture_inner = inner
    ClipsPage._start_capture(page)
    assert calls == [1]
    assert page._starting is False
