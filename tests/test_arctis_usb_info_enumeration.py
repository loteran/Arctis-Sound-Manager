# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every SteelSeries device must be listed, not just the first one.

`arctis_usb_info` called `usb.core.find(idVendor=...)` without `find_all=True`,
which returns a *single* Device. Iterating a Device yields its configurations,
so the function listed one device and then walked its configs — which is why it
had to special-case Configuration at all.

That output is the "USB HID devices" section of every bug report, and it is
what we read to learn which model a reporter has. On a desk with a SteelSeries
keyboard or mouse beside the headset, whichever device libusb returned first
was the only one reported. In issue #197 a Nova Pro Wired never appeared at
all, because an Apex Pro Gen 3 came back first — so the one section that would
have named the hardware described a keyboard instead.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager import cli_tools


class _Endpoint:
    bEndpointAddress = 0x84
    bmAttributes = 0x03
    wMaxPacketSize = 64


class _Interface:
    bInterfaceClass = 0x03
    bInterfaceNumber = 4
    bAlternateSetting = 0

    def __iter__(self):
        return iter([_Endpoint()])


class _Config:
    bConfigurationValue = 1

    def __iter__(self):
        return iter([_Interface()])


class _Device:
    def __init__(self, pid: int, product: str) -> None:
        self.idVendor = 0x1038
        self.idProduct = pid
        self.product = product
        self.manufacturer = "SteelSeries"
        self.langids = (1033,)

    def __iter__(self):
        return iter([_Config()])


_KEYBOARD = (0x1640, "Apex Pro Gen 3")
_DAC = (0x12cb, "Arctis Nova Pro Wired")


def test_a_second_steelseries_device_is_not_swallowed(monkeypatch, capsys):
    """The #197 case: a keyboard enumerated first must not hide the headset."""
    monkeypatch.setattr(
        cli_tools.usb.core, "find",
        lambda *a, **k: iter([_Device(*_KEYBOARD), _Device(*_DAC)]))

    cli_tools.arctis_usb_info()

    out = capsys.readouterr().out
    assert "1038:1640" in out
    assert "1038:12cb" in out, "the headset is missing from the listing"


def test_find_is_asked_for_all_of_them(monkeypatch):
    """The whole bug was one missing keyword, so pin the call itself: without
    find_all, pyusb hands back one Device and iterating it yields configs."""
    seen: dict = {}

    def _find(*args, **kwargs):
        seen.update(kwargs)
        return iter([_Device(*_DAC)])

    monkeypatch.setattr(cli_tools.usb.core, "find", _find)
    cli_tools.arctis_usb_info()

    assert seen.get("find_all") is True


def test_no_devices_still_raises(monkeypatch):
    """find_all returns a generator, which is truthy even when it yields
    nothing — so the emptiness check has to survive the change."""
    monkeypatch.setattr(cli_tools.usb.core, "find", lambda *a, **k: iter([]))

    with pytest.raises(ValueError):
        cli_tools.arctis_usb_info()
