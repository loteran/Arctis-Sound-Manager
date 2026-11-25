# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The transparency level stays reachable where the profile hides it.

#419 moved the level to the Headset page, built from the profile's own
settings. The Nova Pro Wireless and the Omni keep transparent_level
`hidden` (it follows the base's status), so that panel was empty and the
level could no longer be set. The ANC widget's own slider stands in there.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _widget():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui.anc_widget import QAncWidget
    return QAncWidget()


def _status(mode: str) -> dict:
    return {"headset": {"noise_cancelling": {"value": mode},
                        "transparent_noise_cancelling_level": {"value": 50}}}


def test_slider_shown_when_profile_hides_the_level():
    w = _widget()
    w.set_level_row_fallback(True)
    w.update_status(_status("transparent"))
    assert not w._level_row.isHidden()


def test_slider_hidden_when_page_shows_the_level():
    w = _widget()
    w.set_level_row_fallback(False)
    w.update_status(_status("transparent"))
    assert w._level_row.isHidden()


def test_slider_hidden_outside_transparency():
    w = _widget()
    w.set_level_row_fallback(True)
    w.update_status(_status("on"))
    assert w._level_row.isHidden()
