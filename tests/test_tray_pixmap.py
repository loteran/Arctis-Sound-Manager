# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one tray icon has to stay readable in a 24px slot (#194, #119).

Folding the battery back into the ASM icon closes the crash — one item, never
destroyed — but it also puts the number in a strip roughly a third of the
square, and a tray slot is small. #119 exists because the number was
unreadable before it; this must not walk that back.

So: the strip is actually drawn, it scales to the value (a 3-digit "100%" gets
a smaller font than "9%" rather than being clipped), and nothing spills outside
the square.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui.ui_utils import get_tray_pixmap


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _ink_rows(pixmap) -> list[int]:
    """Rows of the pixmap that have any non-transparent pixel."""
    image = pixmap.toImage()
    rows = []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 20:
                rows.append(y)
                break
    return rows


def test_no_battery_is_the_bare_logo():
    """Powered off is not a blank icon and not a missing item — it is the logo,
    exactly what the tray showed before the battery was ever added."""
    from arctis_sound_manager.gui.ui_utils import get_icon_pixmap

    assert get_tray_pixmap(None).toImage() == get_icon_pixmap().toImage()


def test_a_level_puts_ink_in_the_bottom_strip():
    logo_rows = set(_ink_rows(get_tray_pixmap(None)))
    with_pct = set(_ink_rows(get_tray_pixmap(87)))

    bottom = {y for y in with_pct if y >= 44}
    assert bottom, "the number is not in the bottom strip"
    assert bottom - logo_rows, "nothing was drawn that the logo did not already cover"


def test_three_digits_stay_inside_the_square():
    """"100%" is the width-bound case. A fixed font size that suits "9%" clips
    it, and a clipped battery reading is worse than none."""
    for value in (9, 42, 87, 100):
        pixmap = get_tray_pixmap(value)
        assert pixmap.width() == 64 and pixmap.height() == 64

        image = pixmap.toImage()
        for y in range(image.height()):
            for x in (0, image.width() - 1):
                assert image.pixelColor(x, y).alpha() <= 20, (
                    f"{value}% touches the edge at row {y}")


def test_the_number_shrinks_as_it_grows_longer():
    """Fit, not truncate: the strip is a fixed height, so a longer string has
    to be drawn smaller rather than run off the side."""
    def ink_width(value: int) -> int:
        image = get_tray_pixmap(value).toImage()
        cols = [x for x in range(image.width())
                for y in range(44, image.height())
                if image.pixelColor(x, y).alpha() > 20]
        return max(cols) - min(cols) if cols else 0

    # Wider string, but still bounded by the same square.
    assert ink_width(100) > ink_width(9)
    assert ink_width(100) <= 62
