# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the clip library's card grid.

The library used to be a list of filenames that differ only by a timestamp,
which is unreadable the moment there is more than a handful of clips: the only
way to tell which one held the moment was to open each in turn. It is now a
grid of preview cards, and what is worth pinning down is that it stays one —
a single wrong view property silently turns it back into a list — and that the
two lines of text under each card say something a person can act on.

The page itself is not built here: constructing it binds a global shortcut
through the desktop portal, which is a real request to the compositor of
whoever runs the suite. The grid widget and the label functions are the parts
with logic in them.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListView, QListWidgetItem

from arctis_sound_manager.gui.clips_page import (CARD_SIZE, THUMB_SIZE,
                                                 ClipGrid, _placeholder_icon,
                                                 clip_caption, clip_title)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ── card text ─────────────────────────────────────────────────────────────────

def test_title_is_the_game_the_clip_was_taken_from():
    """clip_capture writes the window title into the name with its spaces
    replaced; the card puts them back."""
    assert clip_title("/c/clip_2026-07-30_23-15-02_Counter_Strike_2.mkv") == \
        "Counter Strike 2"


def test_title_falls_back_when_no_game_was_detected():
    """Nothing was in the foreground, or the compositor would not say what."""
    assert clip_title("/c/clip_2026-07-30_23-15-02.mkv") == "Clip"


def test_title_of_a_renamed_clip_is_not_mangled():
    """A file the user renamed by hand still has to show as something."""
    assert clip_title("/c/best round ever.mkv") == "best round ever"


def test_caption_carries_the_time_and_the_size():
    """Both are how a clip is picked out of a run of near-identical ones."""
    import time
    when = time.mktime((2026, 7, 30, 23, 15, 2, 0, 0, -1))
    caption = clip_caption(when, 38 * 1024 * 1024)
    assert "23:15" in caption
    assert "38 MB" in caption


def test_caption_of_a_partial_file_does_not_crash():
    assert "0 MB" in clip_caption(0.0, 0)


# ── the grid stays a grid ─────────────────────────────────────────────────────

def test_library_is_a_grid_of_cards_not_a_list(app):
    grid = ClipGrid()
    assert grid.viewMode() == QListView.ViewMode.IconMode
    assert grid.isWrapping()


def test_cards_are_large_enough_to_preview(app):
    """A 16:9 thumbnail with room for two lines of text under it — an icon-sized
    picture would be no more informative than the old list."""
    grid = ClipGrid()
    assert grid.iconSize() == THUMB_SIZE
    assert THUMB_SIZE.width() >= 200
    assert CARD_SIZE.height() > THUMB_SIZE.height()


def test_cards_reflow_with_the_window(app):
    grid = ClipGrid()
    assert grid.resizeMode() == QListView.ResizeMode.Adjust


def test_cards_cannot_be_dragged_out_of_order(app):
    """Icon views let items be rearranged by default, which would make every
    drag-to-share gesture a chance to scramble the library instead."""
    grid = ClipGrid()
    assert grid.movement() == QListView.Movement.Static


def test_dragging_a_card_carries_the_file_not_its_label(app, tmp_path):
    """Dropping onto Discord or an upload box is the fast path to sharing, and
    it only works if the drag is a text/uri-list."""
    clip = tmp_path / "clip_2026-07-30_23-15-02_Half_Life.mkv"
    clip.write_bytes(b"video")

    grid = ClipGrid()
    item = QListWidgetItem("Half Life")
    item.setData(Qt.ItemDataRole.UserRole, str(clip))
    grid.addItem(item)

    data = grid.mimeData([item])
    assert data.hasUrls()
    assert data.urls()[0].toLocalFile() == str(clip)
    assert str(clip) in data.text()


def test_dragging_a_clip_that_vanished_offers_nothing(app, tmp_path):
    grid = ClipGrid()
    item = QListWidgetItem("gone")
    item.setData(Qt.ItemDataRole.UserRole, str(tmp_path / "gone.mkv"))
    grid.addItem(item)
    assert not grid.mimeData([item]).hasUrls()


# ── placeholder ───────────────────────────────────────────────────────────────

def test_placeholder_fills_the_card_while_the_frame_is_extracted(app):
    """Cards must not pop into existence at a different size once ffmpeg
    answers, so the placeholder is the full thumbnail size."""
    icon = _placeholder_icon()
    assert not icon.isNull()
    assert icon.pixmap(THUMB_SIZE).size() == THUMB_SIZE
