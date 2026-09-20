# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hiding the Output card is a display preference only (#262).

Unlike Aux, Output isn't a channel the daemon creates or tears down — it's
always the physical/external routing card. Someone who never uses it just
wants the room back on the Channels page.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.settings import GeneralSettings


def test_it_is_visible_on_a_fresh_install():
    assert GeneralSettings().output_channel_visible is True


@pytest.fixture
def page():
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui.home_page import HomePage
    widget = HomePage()
    yield widget
    widget.deleteLater()


def test_hiding_it_only_touches_the_output_card(page):
    page._apply_output_visibility(False)

    assert page._ext_card.isHidden()
    assert not page._game_card.isHidden()
    assert not page._chat_card.isHidden()
    assert not page._media_card.isHidden()


def test_showing_it_again_brings_it_back(page):
    page._apply_output_visibility(False)
    page._apply_output_visibility(True)

    assert not page._ext_card.isHidden()


def test_the_choice_is_persisted_and_the_daemon_is_not_told():
    """No D-Bus call, no daemon-side effect — it's a GUI-only setting."""
    import inspect
    from arctis_sound_manager.gui import home_page

    src = inspect.getsource(home_page.HomePage._set_output_channel_visible)
    assert "output_channel_visible" in src
    assert "DbusWrapper" not in src


def test_it_does_not_remove_the_routing_button(page):
    """The card can be tucked away without losing the ability to route an
    application to it — Output always has a sink behind it, unlike Aux."""
    from arctis_sound_manager.gui.home_page import _AppTag

    page._apply_output_visibility(False)

    assert "O" in [label for label, _c, _cb in _AppTag._cards_registry]
