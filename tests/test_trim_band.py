# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the clip editor's trim band.

The behaviour worth pinning down is the opening selection. A clip is saved by
pressing a shortcut *after* the thing worth keeping has happened, so the buffer
ends on it — and the editor is only useful if it opens already framed on that
tail, ready for Export. Getting the default wrong in either direction is a
silent papercut: too long and every clip needs trimming by hand, too short and
the moment itself is cut off.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.trim_band import (DEFAULT_TAIL_S, EDGE_PAD,
                                                default_range, s_to_x,
                                                tick_step_s, x_to_s)


# ── opening selection ──────────────────────────────────────────────────────────

def test_opens_on_the_last_ten_seconds_of_a_thirty_second_clip():
    assert default_range(30.0) == (20.0, 30.0)


def test_selection_always_ends_at_the_end_of_the_clip():
    """The moment is at the end — the tail is never trimmed off."""
    for duration in (12.0, 30.0, 90.0, 300.0):
        assert default_range(duration)[1] == duration


def test_short_clip_is_selected_whole_rather_than_left_empty():
    """A 6 s clip has no 10 s tail to fall back on; selecting nothing would
    disable Export on a perfectly good clip."""
    assert default_range(6.0) == (0.0, 6.0)
    assert default_range(DEFAULT_TAIL_S) == (0.0, DEFAULT_TAIL_S)


def test_zero_length_clip_does_not_produce_a_negative_range():
    assert default_range(0.0) == (0.0, 0.0)


# ── seconds ↔ pixels ───────────────────────────────────────────────────────────

def test_ends_of_the_clip_land_on_the_ends_of_the_band():
    assert s_to_x(0.0, 30.0, 400) == pytest.approx(EDGE_PAD)
    assert s_to_x(30.0, 30.0, 400) == pytest.approx(400 - EDGE_PAD)


def test_position_maps_back_to_the_same_second():
    for seconds in (0.0, 7.5, 20.0, 30.0):
        x = s_to_x(seconds, 30.0, 640)
        assert x_to_s(x, 30.0, 640) == pytest.approx(seconds, abs=0.01)


def test_clicks_past_either_edge_clamp_into_the_clip():
    assert x_to_s(-40, 30.0, 400) == 0.0
    assert x_to_s(9999, 30.0, 400) == pytest.approx(30.0)


def test_no_division_by_zero_before_the_length_is_known():
    assert s_to_x(5.0, 0.0, 400) == EDGE_PAD
    assert x_to_s(200, 0.0, 400) == 0.0


# ── ruler ──────────────────────────────────────────────────────────────────────

def test_ticks_thin_out_when_there_is_no_room_for_labels():
    """Narrow band, same clip: fewer, coarser marks instead of overlapping text."""
    assert tick_step_s(30.0, 300) > tick_step_s(30.0, 1400)


def test_tick_labels_never_collide():
    for width in (240, 480, 900, 1600):
        for duration in (10.0, 30.0, 120.0, 600.0):
            step = tick_step_s(duration, width)
            spacing = s_to_x(step, duration, width) - s_to_x(0.0, duration, width)
            assert spacing >= 50, (duration, width, step)


# ── widget behaviour ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _band(duration=30.0):
    from arctis_sound_manager.gui.trim_band import TrimBand
    band = TrimBand(duration)
    band.resize(600, band.HEIGHT)
    return band


def test_band_opens_on_the_tail(_app):
    band = _band(30.0)
    assert (band.start_s, band.end_s) == (20.0, 30.0)


def test_markers_cannot_cross(_app):
    """An out point before the in point is a clip of negative length, which the
    export refuses with an error the user cannot act on."""
    band = _band(30.0)
    band.set_range(25.0, 5.0)
    assert band.end_s > band.start_s
    assert band.span_s >= band.MIN_SPAN_S


def test_range_is_clamped_to_the_clip(_app):
    band = _band(30.0)
    band.set_range(-10.0, 999.0)
    assert (band.start_s, band.end_s) == (0.0, 30.0)


def test_quick_lengths_measure_back_from_the_end(_app):
    band = _band(30.0)
    band.select_last(5.0)
    assert (band.start_s, band.end_s) == (25.0, 30.0)
    band.select_all()
    assert (band.start_s, band.end_s) == (0.0, 30.0)


def test_a_longer_length_than_the_clip_selects_all_of_it(_app):
    band = _band(8.0)
    band.select_last(30.0)
    assert (band.start_s, band.end_s) == (0.0, 8.0)


def test_learning_the_real_duration_reframes_the_tail(_app):
    """ffprobe and the player disagree on clips written straight out of the
    buffer; the band has to follow whichever length is actually used."""
    band = _band(0.0)
    seen: list[tuple[float, float]] = []
    band.rangeChanged.connect(lambda a, b: seen.append((a, b)))
    band.set_duration(30.0)
    assert (band.start_s, band.end_s) == (20.0, 30.0)
    assert seen[-1] == (20.0, 30.0)


def test_playhead_stays_inside_the_clip(_app):
    band = _band(30.0)
    band.set_position(120.0)
    assert band._position == 30.0
    band.set_position(-5.0)
    assert band._position == 0.0
