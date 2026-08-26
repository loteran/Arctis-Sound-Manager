# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recovering from a profile that names an interface the device does not have.

The USB layout is in no SteelSeries specification — the `sync-interface` they
publish is not the `bInterfaceNumber`, as the Nova 7 Gen 2 already showed — so a
profile shared by several products can name an interface only some of them
carry. `arctis_7.yaml` says interface 5, taken from the Arctis 7 dongle, and the
Arctis Pro GameDAC on the same profile exposes 0, 1 and 2 (#213).

What that looked like: `Error sending command: [Errno 2] Entity not found`,
twice a second, for as long as the device was plugged in, and a headset that
reported nothing at all.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


class _Endpoint:
    def __init__(self, size: int) -> None:
        self.wMaxPacketSize = size


class _Interface:
    def __init__(self, number: int, cls: int, sizes: list[int]) -> None:
        self.bInterfaceNumber = number
        self.bInterfaceClass = cls
        self._eps = [_Endpoint(s) for s in sizes]

    def __iter__(self):
        return iter(self._eps)


class _Device:
    """The GameDAC's real layout, from the report on #213."""

    def __init__(self, interfaces=None) -> None:
        self._cfg = interfaces if interfaces is not None else [
            _Interface(0, 3, [64]),
            _Interface(1, 3, [4]),    # the dial: too small to be the command channel
            _Interface(2, 3, [64]),
        ]

    def get_active_configuration(self):
        return self._cfg


def _engine(device=None, declared: int = 5):
    from arctis_sound_manager.core import CoreEngine

    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = logging.getLogger("test")
    eng.usb_device = device if device is not None else _Device()
    eng.device_config = SimpleNamespace(
        command_interface_index=[declared, 0], name="test device",
        listen_interface_indexes=[declared], dial_interface_index=declared,
        dial_interface_candidates=[])
    # Moving off the declared interface has to claim the one it moves to, so
    # the engine records what it was asked to detach instead of touching USB.
    eng.claimed = []
    eng.kernel_detach = lambda dev, cfg: eng.claimed.extend(
        eng._all_used_interfaces(cfg)) or True
    return eng


def test_it_uses_the_profile_until_something_goes_wrong():
    eng = _engine()

    assert eng._command_interface_number() == 5


def test_a_missing_interface_moves_commands_to_a_real_one():
    """The whole point: an interface that does not exist never starts existing,
    so retrying the same number is a loop rather than a recovery."""
    eng = _engine()

    eng._retarget_command_interface()

    assert eng._command_interface_number() == 2


def test_the_small_packet_interface_is_never_chosen():
    """A device's 4-byte interface is its volume or dial control. Writing
    commands there achieves nothing while looking like it worked, which is
    worse than the error it would replace."""
    eng = _engine(_Device([_Interface(1, 3, [4]), _Interface(3, 3, [64])]))

    eng._retarget_command_interface()

    assert eng._command_interface_number() == 3


def test_non_hid_interfaces_are_ignored():
    eng = _engine(_Device([_Interface(0, 1, [64]), _Interface(4, 3, [64])]))

    eng._retarget_command_interface()

    assert eng._command_interface_number() == 4


def test_it_moves_once_and_stays_put():
    """Cycling would turn a wrong profile into a quiet walk across every
    interface — harder to diagnose than the failure it replaces."""
    eng = _engine()

    eng._retarget_command_interface()
    first = eng._command_interface_number()
    eng._retarget_command_interface()

    assert eng._command_interface_number() == first


def test_nothing_to_move_to_leaves_the_profile_alone():
    """A single-interface device with the wrong number in its profile is a
    profile bug, and inventing an interface would not help anyone."""
    eng = _engine(_Device([_Interface(5, 3, [64])]))

    eng._retarget_command_interface()

    assert eng._command_interface_number() == 5


def test_it_says_so_loudly(caplog):
    """This is a profile that does not match the hardware. It recovers, but
    somebody has to be told, or the wrong profile ships forever."""
    eng = _engine()

    with caplog.at_level(logging.WARNING):
        eng._retarget_command_interface()

    assert "does not match the hardware" in caplog.text


def test_the_interface_moved_to_is_claimed():
    """The move only changes the address commands are written to. usbhid still
    holds the interface it moves to, so without a claim ENOENT is traded for a
    silent EBUSY and nothing reaches the device either way (#216, #217)."""
    eng = _engine()

    eng._retarget_command_interface()

    assert eng._command_interface_number() in eng.claimed


def test_a_claim_that_fails_does_not_take_the_command_path_down():
    """This runs from the command path: failing to claim is a reason to log,
    never a reason to bring the daemon down."""
    eng = _engine()
    eng.kernel_detach = lambda dev, cfg: (_ for _ in ()).throw(OSError("nope"))

    eng._retarget_command_interface()

    assert eng._command_interface_number() == 2


def test_an_unreadable_configuration_is_survivable():
    class _Broken(_Device):
        def get_active_configuration(self):
            raise OSError("gone")

    eng = _engine(_Broken())
    eng._retarget_command_interface()

    assert eng._command_interface_number() == 5
