# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the size the clip editor opens at, and refuses to go below.

Reported from use: the dialog opened small enough that the preview was a strip,
and resizing it further ran the trim band's markers and read-out into the preset
buttons underneath. Both are size problems — one about what it opens at, one
about what it must never shrink past.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize

from arctis_sound_manager.gui.clip_editor import (MIN_SIZE, PREFERRED_SIZE,
                                                  _opening_size)


def test_it_opens_larger_than_the_old_fixed_minimum():
    """720x560 was both the minimum and, in practice, the opening size."""
    opened = _opening_size(QSize(2560, 1440))
    assert opened.width() > 720 and opened.height() > 560


def test_a_large_screen_gets_the_preferred_size_not_more():
    """Bigger is not better past this: the preview stops gaining from it."""
    assert _opening_size(QSize(3840, 2160)) == PREFERRED_SIZE


def test_it_never_opens_larger_than_the_screen():
    """A dialog wider than the display cannot be dragged back into view on
    some compositors."""
    screen = QSize(1366, 768)
    opened = _opening_size(screen)
    assert opened.width() <= screen.width()


def test_a_small_screen_still_gets_a_usable_dialog():
    """On a display smaller than the minimum, the minimum wins — a squeezed
    layout is the failure being fixed, not an acceptable fallback."""
    opened = _opening_size(QSize(1024, 600))
    assert opened.width() >= MIN_SIZE.width()
    assert opened.height() >= MIN_SIZE.height()


def test_the_minimum_fits_the_rows_that_share_a_line():
    """The trim band, five preset buttons and the span read-out are one row;
    so are the size picker and the two export buttons."""
    assert MIN_SIZE.width() >= 900
    assert MIN_SIZE.height() >= 640


def test_the_band_cannot_be_squeezed_under_its_own_markers():
    """The band paints handles, end times and a playhead; below this width they
    are drawn on top of each other."""
    from arctis_sound_manager.gui.trim_band import EDGE_PAD

    # A width the band is guaranteed by the dialog minimum, less the margins.
    assert MIN_SIZE.width() - 2 * 18 - 2 * EDGE_PAD >= 360
