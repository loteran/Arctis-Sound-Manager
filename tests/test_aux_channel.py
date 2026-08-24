# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The opt-in Aux channel, and making room for it (#209).

Someone using Media for video wants their music somewhere else, with its own
EQ. The mixer already holds four cards — Game, Chat, Media and Output — so Aux
makes a fifth, and five cards at the old fixed 260 px minimum need a window
around 1840 px. That is wider than a 1366 px laptop screen, and the row has no
scroll area to fall back on: Qt would push the window off the edge of the
display rather than let a card shrink.

So the channel is off unless asked for, and the cards' minimum width depends on
how many are on screen.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from arctis_sound_manager.loopback_manager import DEFAULT_SINKS, make_specs
from arctis_sound_manager.settings import GeneralSettings


# ── the channel ──────────────────────────────────────────────────────────────


def test_it_is_off_on_a_fresh_install():
    """A channel nobody asked for is one more entry in every application's
    output list, and one more filter stage running for nothing."""
    assert GeneralSettings().aux_enabled is False


def test_no_loopback_is_created_when_it_is_off():
    """Not a sink with nothing routed to it — an empty channel in the output
    picker is worse than no channel."""
    specs = make_specs(sonar=True, physical_game="alsa.g", physical_chat="alsa.c")

    assert [s.channel for s in specs] == ["game", "chat", "media"]


def test_switching_it_on_adds_exactly_one_loopback():
    specs = make_specs(sonar=True, physical_game="alsa.g", physical_chat="alsa.c",
                       aux=True)

    assert [s.channel for s in specs] == ["game", "chat", "media", "aux"]


def test_the_existing_channels_keep_their_order_and_names():
    """Anything walking DEFAULT_SINKS in order — the mixer, the router, the
    watchdog — must see what it saw before."""
    assert [s["channel"] for s in DEFAULT_SINKS[:3]] == ["game", "chat", "media"]
    assert DEFAULT_SINKS[3]["capture_name"] == "Arctis_Aux"


def test_it_routes_through_its_own_eq_stage():
    """Its own EQ is the point of the request: Media is video, Aux is music."""
    spec = make_specs(sonar=True, physical_game="alsa.g", physical_chat="alsa.c",
                      aux=True)[-1]

    assert spec.target == "effect_input.sonar-aux-eq"


def test_the_eq_stage_knows_the_channel():
    """sonar_to_pipewire has to be able to generate the conf, or the loopback
    above orphans on a target that never loads."""
    from arctis_sound_manager import sonar_to_pipewire as sp

    assert sp._CHANNEL_CHANNELS["aux"] == 8
    assert sp._CHANNEL_POSITION["aux"] == "FL FR FC LFE RL RR SL SR"
    assert sp._CHANNEL_TARGET["aux"] == sp._SURROUND


# ── making room ──────────────────────────────────────────────────────────────


@pytest.fixture
def page():
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui.home_page import HomePage
    widget = HomePage()
    yield widget
    widget.deleteLater()


def test_four_cards_keep_the_full_width(page):
    from arctis_sound_manager.gui.home_page import CARD_MIN_WIDTH

    page._apply_aux_visibility(False)

    assert page._game_card.minimumWidth() == CARD_MIN_WIDTH


def test_a_fifth_card_narrows_all_of_them(page):
    """All of them, not just the new one: a row of four wide cards and one
    narrow one is not a mixer."""
    from arctis_sound_manager.gui.home_page import CARD_MIN_WIDTH_TIGHT

    page._apply_aux_visibility(True)

    assert {c.minimumWidth() for c in page._all_cards()} == {CARD_MIN_WIDTH_TIGHT}


def test_the_row_fits_a_laptop_screen(page):
    """The number that made this necessary. Five cards, four 20 px gaps, and
    the row takes three quarters of the window."""
    from arctis_sound_manager.gui.home_page import CARD_MIN_WIDTH_TIGHT

    page._apply_aux_visibility(True)
    row = 5 * CARD_MIN_WIDTH_TIGHT + 4 * 20

    assert row / 0.75 <= 1500, "a 1366 px laptop cannot show the mixer"


def test_turning_it_off_gives_the_width_back(page):
    from arctis_sound_manager.gui.home_page import CARD_MIN_WIDTH

    page._apply_aux_visibility(True)
    page._apply_aux_visibility(False)

    assert page._game_card.minimumWidth() == CARD_MIN_WIDTH


def test_the_plus_and_the_card_are_never_both_shown(page):
    """The "+" stands where the card will be, so one replaces the other."""
    for enabled in (True, False, True):
        page._apply_aux_visibility(enabled)
        assert page._aux_card.isHidden() != page._aux_add_btn.isHidden()


def test_the_card_starts_hidden_on_a_default_install(page):
    assert page._aux_card.isHidden()
    assert not page._aux_add_btn.isHidden()
