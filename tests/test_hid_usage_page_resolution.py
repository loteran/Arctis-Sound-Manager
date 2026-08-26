# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Correcting a profile that names an interface which cannot be the vendor one.

SteelSeries' specifications address an interface the way hidapi does on
Windows — by HID usage page, written as `(sync-interface 0xff00 0x0001 …)`.
They never carry a bInterfaceNumber, so every `command_interface_index` in
this repository was typed by hand, and some were wrong: the Arctis Pro GameDAC
was given the Arctis 7 dongle's interface 5 on a device exposing 0, 1 and 2
(#213).

1.4.10 read the page and let it arbitrate: any single vendor-defined page
anywhere was enough to move a device off the interface its profile named. It
moved headsets that worked. An Arctis 7+ lost its battery, its status and its
automatic switching (#216), and a set of GameBuds lost battery and online
status (#217) - both of them products whose hardware answers 0xffc0 where
SteelSeries publish 0xff00, so their profile's page matched nothing at all.

What holds now: a device moves only on proof that the interface it names
cannot be the control channel - that interface is absent, or present and
declaring a page that is not vendor-defined. A page that cannot be read is not
proof of anything.
"""
from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace

import pytest


class _Interface:
    def __init__(self, number: int, cls: int = 3) -> None:
        self.bInterfaceNumber = number
        self.bInterfaceClass = cls


class _Device:
    """A device whose interfaces answer with the usage pages given.

    A page of None is an interface that exists but whose descriptor cannot be
    read; an interface absent from the mapping is one the device does not have.
    """

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
def _read_pages(monkeypatch, request):
    """Serve each interface's page from the fake device rather than sysfs.

    The two tests that exercise the sysfs read itself opt out with
    `@pytest.mark.real_sysfs`.
    """
    from arctis_sound_manager.core import CoreEngine

    if request.node.get_closest_marker("real_sysfs"):
        return
    monkeypatch.setattr(
        CoreEngine, "_hid_usage_page",
        lambda self, num, **kw: self.usb_device._pages.get(num))


# --- what must never happen again (#216, #217) ------------------------------

def test_a_page_the_specification_got_wrong_does_not_move_a_working_headset():
    """The Arctis 7+ and the GameBuds: interface 3 is the control channel and
    says so, while the published page for the product is 0xff00. A profile
    page that matches nothing is evidence about the specification, not about
    the interface, and 1.4.10 treated it as the latter."""
    eng = _engine(_Device({3: 0xFFC0, 5: 0xFFC0}), declared=3, wanted=0xFF00)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 3


def test_a_single_vendor_interface_elsewhere_is_not_a_reason_to_move():
    """The rule that broke production: one vendor page anywhere used to be
    enough. The declared interface is vendor-defined, so it stays."""
    eng = _engine(_Device({0: 0x000C, 3: 0xFFC0}), declared=3, wanted=0xFF00)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 3


def test_an_unreadable_declared_interface_is_never_moved_off():
    """Not knowing what the declared interface carries is the one reason not
    to touch it. 1.4.10 read an unreadable descriptor as "not the vendor
    interface" and moved the device."""
    eng = _engine(_Device({3: None, 4: 0xFFC0}), declared=3, wanted=0xFF00)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 3


# --- what it is still for (#213) --------------------------------------------

def test_an_interface_the_device_does_not_have_is_corrected():
    """The GameDAC's case: the profile named interface 5 on a device exposing
    0, 1 and 2, so every command failed with ENOENT twice a second."""
    eng = _engine(_Device({0: 0x000C, 1: 0x000C, 2: 0xFF00}), declared=5)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 2


def test_the_consumer_control_interface_is_corrected():
    """0x0C is the media-keys collection: an interface declaring it cannot be
    the control channel, which is proof enough to move."""
    eng = _engine(_Device({3: 0x000C, 4: 0xFFC0}), declared=3)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_the_profile_page_settles_a_device_with_two_vendor_interfaces():
    eng = _engine(_Device({0: 0x000C, 1: 0xFF00, 2: 0xFFC1}),
                  declared=0, wanted=0xFFC1)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 2


def test_two_vendor_interfaces_and_no_way_to_choose_leaves_the_profile_alone():
    """Guessing between them is how a device ends up addressed on its dial."""
    eng = _engine(_Device({0: 0x000C, 1: 0xFF00, 2: 0xFFC1}),
                  declared=0, wanted=0xFFAA)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 0


def test_non_hid_interfaces_are_not_considered():
    eng = _engine(_Device({0: 0xFF00, 4: 0xFFC0}, cls={0: 1}), declared=9)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4


def test_an_unreadable_configuration_is_survivable():
    class _Broken:
        def get_active_configuration(self):
            raise OSError("gone")

    eng = _engine(_Broken(), declared=3)

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 3


# --- the interface that is addressed is the interface that is claimed --------

def test_the_resolved_interface_is_one_asm_claims():
    """The regression underneath both reports: 1.4.10 moved the address
    commands were written to and left the claim on the profile's number. An
    interface ASM writes to but never claimed is one usbhid still holds, and
    every transfer to it fails with EBUSY - silently, because that log line is
    throttled. The headset then has no battery and no status while the daemon
    looks healthy (#216, #217)."""
    eng = _engine(_Device({3: 0x000C, 4: 0xFFC0}), declared=3)
    eng.device_config.listen_interface_indexes = [3]
    eng.device_config.dial_interface_index = 3
    eng.device_config.dial_interface_candidates = []

    eng.resolve_command_interface()

    assert eng._command_interface_number() == 4
    assert 4 in eng._all_used_interfaces(eng.device_config)
    # And the profile's own interface is still claimed, so claim, release and
    # re-attach keep covering the same set.
    assert 3 in eng._all_used_interfaces(eng.device_config)


def test_a_profile_that_was_not_moved_claims_exactly_what_it_always_did():
    eng = _engine(_Device({3: 0xFFC0}), declared=3)
    eng.device_config.listen_interface_indexes = [3]
    eng.device_config.dial_interface_index = 3
    eng.device_config.dial_interface_candidates = []

    eng.resolve_command_interface()

    assert eng._all_used_interfaces(eng.device_config) == [3]


# --- the descriptor itself ---------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (b"\x06\xc0\xff\x09\x01", 0xFFC0),   # long form, little-endian
    (b"\x06\x00\xff\x09\x01", 0xFF00),
    (b"\x05\x0c\x09\x01", 0x0C),         # short form: the consumer page
    (b"\x09\x01", None),                 # opens with something else
    (b"", None),
])
def test_the_usage_page_is_read_off_the_descriptor(raw, expected):
    from arctis_sound_manager.core import CoreEngine

    assert CoreEngine._usage_page_of_descriptor(raw) == expected


def test_reading_a_page_never_touches_the_device():
    """1.4.10 detached usbhid from every HID interface of the device and
    re-attached it, on the discovery path, before anything was claimed, and
    swallowed re-attach failures. The kernel already publishes the descriptor
    it parsed; nothing here has any business driving the device."""
    from arctis_sound_manager.core import CoreEngine

    source = inspect.getsource(CoreEngine._hid_usage_page)

    assert "detach_kernel_driver" not in source
    assert "ctrl_transfer" not in source


@pytest.mark.real_sysfs
def test_the_page_comes_from_sysfs(tmp_path):
    from arctis_sound_manager.core import CoreEngine

    hid = tmp_path / "1-6:1.3" / "0003:1038:220E.000A"
    hid.mkdir(parents=True)
    (hid / "report_descriptor").write_bytes(b"\x06\xc0\xff\x09\x01")

    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = logging.getLogger("test")
    eng.usb_device = SimpleNamespace(bus=1, port_numbers=(6,))

    assert eng._hid_usage_page(3, sys_root=tmp_path) == 0xFFC0
    assert eng._hid_usage_page(4, sys_root=tmp_path) is None


def test_every_shipped_profile_declaring_a_page_declares_a_vendor_one():
    """A page below 0xff00 in a profile would be a transcription slip: the
    field exists to name the vendor collection."""
    from arctis_sound_manager.config import load_device_configurations

    pages = [p for c in load_device_configurations()
             if (p := getattr(c, "hid_usage_page", None)) is not None]

    assert pages, "the field should be populated from the specifications"
    assert all(p >= 0xFF00 for p in pages), [hex(p) for p in pages]


def test_the_profiles_that_regressed_declare_the_page_their_hardware_answers():
    """0xff00 is what SteelSeries publish for these products; 0xffc0 is what
    the interface answers, as both profiles had recorded in a comment since
    they were written (#216, #217)."""
    from arctis_sound_manager.config import load_device_configurations

    seen = 0
    for config in load_device_configurations():
        page = getattr(config, "hid_usage_page", None)
        if page is None:
            # arctis_7_plus_220c declares no page at all, which is its own
            # answer: nothing was transcribed for it.
            continue
        if "Arctis 7+" in config.name or "GameBuds" in config.name:
            seen += 1
            assert page == 0xFFC0, config.name
    assert seen == 2, "both profiles should be loaded"
