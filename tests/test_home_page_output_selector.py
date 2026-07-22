# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the Output card's device selector (issue #139).

Game / Chat / Media each had a device selector; the Output card did not, so its
destination could only be changed from the Settings page. Its list also differs
from the other three: it *is* the channel's destination rather than a "send this
channel elsewhere" override, so there is no "headset by default" entry — and the
headset itself is offered, since routing Output at it is a supported setup (a
second path to the headset with a flat EQ and no spatial processing).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from arctis_sound_manager.gui import home_page as hp  # noqa: E402


class _Sink:
    def __init__(self, name, proplist=None):
        self.name = name
        self.proplist = proplist or {}


def _page():
    """A HomePage stand-in carrying only what _refresh_device_combos touches."""
    page = hp.HomePage.__new__(hp.HomePage)
    page._available_sinks = ()
    page._ext_device_nick = None
    page._game_card = MagicMock()
    page._chat_card = MagicMock()
    page._media_card = MagicMock()
    page._ext_card = MagicMock()
    return page


_SINKS = [
    _Sink("alsa_output.pci-hdmi", {"node.nick": "Q95A", "node.description": "Q95A"}),
    _Sink("alsa_output.usb-SteelSeries_Arctis-00",
          {"node.nick": "Arctis Nova Pro Wireless",
           "node.description": "Arctis Nova Pro Wireless",
           "device.vendor.id": "0x1038"}),
    _Sink("Arctis_Game", {}),  # ASM's own virtual sink — never offered
]


def test_output_card_gets_a_device_selector(monkeypatch):
    page = _page()
    monkeypatch.setattr(hp, "_load_channel_outputs", lambda: {})

    page._refresh_device_combos(_SINKS)

    page._ext_card.set_device_options.assert_called_once()
    offered = dict(page._ext_card.set_device_options.call_args.args[0])
    assert "Arctis Nova Pro Wireless" in offered, "the headset must be selectable (#139)"
    assert "Q95A" in offered
    assert "Arctis_Game" not in offered, "ASM's own sinks must never be offered"
    assert "" not in offered, "the Output card has no 'headset by default' entry"


def test_other_cards_still_exclude_the_headset(monkeypatch):
    """The three channel cards keep the old list: headset-by-default entry,
    and no headset among the explicit devices (it is what "" already means)."""
    page = _page()
    monkeypatch.setattr(hp, "_load_channel_outputs", lambda: {})

    page._refresh_device_combos(_SINKS)

    offered = dict(page._game_card.set_device_options.call_args.args[0])
    assert "" in offered
    assert "alsa_output.usb-SteelSeries_Arctis-00" not in offered


def test_output_selection_is_saved_as_the_setting(monkeypatch):
    """Changing it writes external_output_device through the daemon — the same
    single source of truth the Settings page and the daemon both read."""
    page = _page()
    saved = []

    class _DbusWrapper:
        @staticmethod
        def change_setting(name, value):
            saved.append((name, value))

    import sys
    monkeypatch.setitem(
        sys.modules, "arctis_sound_manager.gui.dbus_wrapper",
        type("m", (), {"DbusWrapper": _DbusWrapper}),
    )

    page._on_external_output_changed("Arctis Nova Pro Wireless")

    assert saved == [("external_output_device", "Arctis Nova Pro Wireless")]
    assert page._ext_device_nick == "Arctis Nova Pro Wireless"


def test_settings_page_change_is_reflected_in_the_channels_tab(monkeypatch):
    """Changing the Output device elsewhere must update the card.

    The refresh short-circuits when nothing changed, but its cache key used to
    hold only the *device list*. Picking another Output device from the Settings
    page leaves that list identical, so the card kept showing the previous
    selection until a device was plugged or unplugged.
    """
    page = _page()
    monkeypatch.setattr(hp, "_load_channel_outputs", lambda: {})

    page._refresh_device_combos(_SINKS)
    assert page._ext_card.set_device_options.call_args.args[1] == ""

    # Settings page changes the device — delivered to the page as a settings
    # update, with the very same sink list still present.
    page.update_settings({"general": {"external_output_device": "Q95A"}})
    page._refresh_device_combos(_SINKS)

    assert page._ext_card.set_device_options.call_args.args[1] == "Q95A", (
        "the Output card must follow a change made from the Settings page"
    )


def test_channel_override_change_also_refreshes(monkeypatch):
    """Same fix for the three channel cards: their saved override is part of
    the cache key, so an override changed elsewhere is picked up too."""
    page = _page()
    outputs: dict = {}
    monkeypatch.setattr(hp, "_load_channel_outputs", lambda: dict(outputs))

    page._refresh_device_combos(_SINKS)
    assert page._game_card.set_device_options.call_args.args[1] == ""

    outputs["game"] = "alsa_output.pci-hdmi"
    page._refresh_device_combos(_SINKS)

    assert page._game_card.set_device_options.call_args.args[1] == "alsa_output.pci-hdmi"
