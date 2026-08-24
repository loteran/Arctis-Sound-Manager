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


def test_the_button_lives_with_the_other_page_actions(page):
    """It sits in the profile bar beside "Save current settings", not among the
    channel cards: it is something you do to the page, not one of the channels
    it acts on. The bar rebuilds itself, so it owns the widget."""
    assert page.profile_bar._aux_btn is not None


def test_the_button_says_what_pressing_it_will_do(page, monkeypatch):
    """One control, two states."""
    from arctis_sound_manager.gui import profile_bar as pb

    monkeypatch.setattr(pb.ProfileBar, "_aux_label", staticmethod(lambda: "ADD"))
    page.profile_bar.refresh_aux_label()
    assert page.profile_bar._aux_btn.text() == "ADD"

    monkeypatch.setattr(pb.ProfileBar, "_aux_label", staticmethod(lambda: "HIDE"))
    page.profile_bar.refresh_aux_label()

    assert page.profile_bar._aux_btn.text() == "HIDE"


def test_the_card_starts_hidden_on_a_default_install(page):
    assert page._aux_card.isHidden()


# ── parity with the other channels ───────────────────────────────────────────


def test_it_has_an_output_device_picker_like_the_others(page):
    """The first thing missing when this shipped: every other channel lets you
    choose where it plays, and a channel you cannot point anywhere is a slider
    attached to nothing."""
    assert page._aux_card._device_combo is not None


def test_it_gets_a_routing_button_only_while_it_exists(page):
    """The small squares next to each running application — G, C, M, O — gain
    an A. Not while the channel is off: pressing it would move the stream to a
    sink that does not exist and lose the audio."""
    from arctis_sound_manager.gui.home_page import _AppTag

    page._apply_aux_visibility(False)
    assert "A" not in [label for label, _c, _cb in _AppTag._cards_registry]

    page._apply_aux_visibility(True)
    assert "A" in [label for label, _c, _cb in _AppTag._cards_registry]


def test_the_routing_button_keeps_output_last(page):
    """O is the way out of the Arctis channels; it reads as the last step."""
    from arctis_sound_manager.gui.home_page import _AppTag

    page._apply_aux_visibility(True)

    assert [l for l, _c, _cb in _AppTag._cards_registry][-1] == "O"


def test_it_has_an_equalizer_tab_with_the_game_presets():
    """Its own EQ is the request. Same tag as Game and Media, so the Sonar
    catalogue applies to it — a music channel with no presets would be an
    equaliser you have to build by hand."""
    from arctis_sound_manager.gui.sonar_page import _CHANNEL_TAG

    assert _CHANNEL_TAG["aux"] == "[Game]"


def test_stream_guard_can_hold_it_back():
    """Selectable like the others, so a screen share can exclude it."""
    from arctis_sound_manager.stream_guard import CHANNEL_SINKS

    assert CHANNEL_SINKS["aux"] == ("Arctis_Aux", "effect_input.sonar-aux-eq")


def test_hiding_the_channel_takes_the_A_button_with_it(page):
    """Each tag reads the registry once, when it is built, so changing the
    registry leaves the buttons already on screen untouched. Hiding Aux left an
    "A" under every application, pointing at a channel that was gone."""
    from arctis_sound_manager.gui.home_page import _AppTag

    page._apply_aux_visibility(True)
    assert "A" in [label for label, _c, _cb in _AppTag._cards_registry]
    # A card that has already drawn a row remembers it and skips the rebuild.
    page._game_card._app_sig = (("Some Game", 1, 2),)

    page._apply_aux_visibility(False)

    assert "A" not in [label for label, _c, _cb in _AppTag._cards_registry]
    assert page._game_card._app_sig is None, "the row must be rebuilt, not kept"


def test_the_daemon_acts_on_the_toggle_instead_of_waiting_for_a_restart():
    """Without this the setting was written and nothing acted on it: the mixer
    showed the channel while no Arctis_Aux sink existed, so the "A" button had
    nowhere to move a stream to and silently did nothing.

    Read from the source rather than driven through D-Bus — the handler runs
    inside the daemon's event loop, which a unit test has no way to stand up."""
    import inspect
    from arctis_sound_manager import dbus_service

    src = inspect.getsource(dbus_service)
    assert "setting == 'aux_enabled'" in src
    marker = src.index("setting == 'aux_enabled'")
    assert "configure_virtual_sinks" in src[marker:marker + 700], (
        "the toggle must reconfigure the sinks, like preferred_device does")


def test_a_stream_on_aux_belongs_to_its_card(page):
    """It was landing in "other applications" at the bottom: that list is
    everything no card represents, and Arctis_Aux was not on the list of sinks
    a card stands for."""
    from types import SimpleNamespace
    from arctis_sound_manager.gui.home_page import SINK_AUX

    sinks = [SimpleNamespace(index=7, name=SINK_AUX, description="Aux")]

    assert 7 in page._channel_sink_indices(sinks, None)


def test_picking_a_device_for_aux_relinks_it(monkeypatch):
    """The output side used to be a hand-written pair of (game, media), so
    choosing a device for Aux saved the setting and relinked nothing — the same
    inertness the code comments describe for Media before #169."""
    from arctis_sound_manager import sonar_to_pipewire as sp

    # The suite runs on a throwaway HOME where the channel is off, which is the
    # right default and the wrong fixture for this.
    monkeypatch.setattr(sp, "_aux_enabled", lambda: True)
    outputs = {c: sp._hesuvi_output_node(c) for c in sp.spatial_channels()}

    assert outputs["aux"] == "effect_output.virtual-surround-7.1-hesuvi-aux"
    assert len(set(outputs.values())) == len(outputs), "each channel needs its own"
