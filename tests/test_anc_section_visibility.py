# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_anc_section_visibility.py — hide noise cancelling on headsets without it.

The Settings page showed the "Noise Cancelling" section to everyone, whatever
their headset. On a model that has no ANC — the Arctis Nova 7P, for one — the
controls were there and simply could not do anything, which reads as a broken
app rather than as an absent feature (issue #146, reported by @camperotactico).

The device profile is the authority: a headset without noise cancelling does
not declare the setting at all.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest.mock import MagicMock

from arctis_sound_manager.gui.headset_page import HeadsetPage


def _page_stub(visible: bool = False) -> MagicMock:
    """A stand-in exposing just what _update_anc_visibility touches."""
    page = MagicMock()
    page._anc_section = MagicMock()
    page._anc_section.isVisible.return_value = visible
    page._update_anc_visibility = lambda s: HeadsetPage._update_anc_visibility(page, s)
    return page


def test_hidden_when_headset_has_no_noise_cancelling():
    page = _page_stub()
    page._update_anc_visibility({
        "settings_config": {"mic_volume": {}, "volume_limiter": {}},
    })
    page._anc_section.setVisible.assert_called_once_with(False)


def test_shown_when_headset_declares_noise_cancelling():
    page = _page_stub()
    page._update_anc_visibility({
        "settings_config": {"mic_volume": {}, "noise_cancelling": {}},
    })
    page._anc_section.setVisible.assert_called_once_with(True)


def test_empty_config_leaves_the_section_alone():
    """No device yet: don't flash the section away on a transient update."""
    page = _page_stub(visible=True)
    page._update_anc_visibility({"settings_config": {}})
    page._anc_section.setVisible.assert_not_called()

    page._update_anc_visibility({})
    page._anc_section.setVisible.assert_not_called()


def test_hidden_by_default_before_any_device_is_known():
    """Built hidden, so a headset without ANC never flashes it on startup."""
    import inspect

    source = inspect.getsource(HeadsetPage.__init__)
    assert "self._anc_section.setVisible(False)" in source
