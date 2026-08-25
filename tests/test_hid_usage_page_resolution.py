# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Finding the vendor interface by what it declares, not by its number (#213).

SteelSeries' specifications address an interface the way hidapi does on
Windows — by HID usage page, written as `(sync-interface 0xff00 0x0001 …)`.
They never carry a bInterfaceNumber, so every `command_interface_index` in
this repository was typed by hand, and several were wrong: the Arctis Pro
GameDAC was given the Arctis 7 dongle's interface 5 on a device exposing 0, 1
and 2, and the Nova 5 lost one of its two in a copy.

The usage page is readable on Linux too, straight from the report descriptor,
so the number can be worked out instead of guessed.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


class _Interface:
    def __init__(self, number: int, cls: int = 3) -> None:
        self.bInterfaceNumber = number
        self.bInterfaceClass = cls


class _Device:
    """A device whose interfaces answer with the usage pages given."""

    def __init__(self, pages: dict[int, int | None], cls: dict | None = None) -> None:
        self._pages = pages
        self._cls = cls or {}

    def get_active_configuration(self):
        return [_Interface(n, self._cls.get(n, 3)) for n in self._pages]


def _engine(device, declared: int, wanted: int | None = None):
    from arctis_sound_manager.core import CoreEngine

    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = logging.getLogger("test")
    eng.usb_device = device
    eng.device_config = SimpleNamespace(
        command_interface_index=[declared, 0], hid_usage_page=wanted)
    eng._command_iface_override = None
    return eng


@pytest.fixture(autouse=True)
def _read_pages(monkeypatch):
    """Serve each interface's page from the fake device rather than USB."""
    from arctis_sound_manager.core import CoreEngine

    monkeypatch.setattr(
        CoreEngine, "_hid_usage_page",
        staticmethod(lambda dev, num: dev._pages.get(num)))


def test_the_vendor_interface_wins_over_the_number_in_the_profile():
    """The GameDAC's case: the profile named an interface the device does not
    have, while the one it does have says outright that it is the vendor
    channel."""
    eng = _engine(_Device({0: 0x000C, 2: 0xFF00}), declared=5)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 2


def test_the_consumer_control_interface_is_never_chosen():
    """0x0C is the media-keys collection. Commands sent there do nothing, and
    telling it apart from the control channel is the whole reason the usage
    page exists."""
    eng = _engine(_Device({3: 0x000C, 4: 0xFFC0}), declared=9)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_the_range_holds_where_the_exact_page_does_not():
    """A Nova Pro Wireless answers 0xffc0 where its own specification says
    0xff00, so an exact match cannot be the only rule — anything from 0xff00 up
    is vendor-defined."""
    eng = _engine(_Device({3: 0x000C, 4: 0xFFC0}), declared=3, wanted=0xFF00)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_an_exact_page_settles_a_device_with_two_vendor_interfaces():
    eng = _engine(_Device({0: 0xFF00, 2: 0xFFC1}), declared=0, wanted=0xFFC1)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 2


def test_a_profile_already_naming_a_vendor_interface_is_left_alone():
    """Every headset that works today must keep working: this moves a device
    only when it would otherwise be addressed wrongly."""
    eng = _engine(_Device({0: 0xFF00, 2: 0xFFC1}), declared=2)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 2


def test_unreadable_descriptors_leave_the_profile_in_charge():
    eng = _engine(_Device({0: None, 1: None}), declared=4)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_non_hid_interfaces_are_not_considered():
    eng = _engine(_Device({0: 0xFF00, 4: 0xFFC0}, cls={0: 1}), declared=9)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_every_shipped_profile_declaring_a_page_declares_a_vendor_one():
    """A page below 0xff00 in a profile would be a transcription slip: the
    field exists to name the vendor collection."""
    from arctis_sound_manager.config import load_device_configurations

    pages = [p for c in load_device_configurations()
             if (p := getattr(c, "hid_usage_page", None)) is not None]

    assert pages, "the field should be populated from the specifications"
    assert all(p >= 0xFF00 for p in pages), [hex(p) for p in pages]
