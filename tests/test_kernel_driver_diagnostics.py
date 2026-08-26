# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""INT-1 — naming which kernel driver holds a USB HID interface.

docs/HARDWARE-QUESTIONS.md, INT-1: an in-kernel `hid-steelseries` driver is
headed for Linux 7.3. Once it exists, `usb_device.is_kernel_driver_active()`
(libusb; a bool) can no longer tell a bug report "usbhid/hid-generic got
there first" apart from "hid-steelseries got there first" — a real
distinction, since the two call for different remediation. This file locks
down the sysfs-reading diagnostic added instead of that libusb call.

The key structural fact these tests pin down: a USB interface that carries a
HID collection is *always* transported by usbhid.ko at the USB-interface
level — that is the generic class driver, and its "driver" symlink says
"usbhid" whether the actual HID-bus driver ends up being hid-generic, some
other vendor driver, or (from 7.3) hid-steelseries. The decision that
matters is one layer *inside* the interface's own sysfs directory, on a
child device the "hid" bus creates once usbhid binds. Verified against this
session's real machine (kernel 7.2.0, `usbhid` bound, `hid-generic` on the
mouse) before writing this file — see the interface_dir/hid_child fixtures
below, which mirror exactly that shape.

"No-op on a current kernel" here means: the code never assumes
hid-steelseries exists, degrades to "unknown"/None instead of raising when
sysfs does not have the shape it expects (a race, a non-HID interface, a
kernel too old to have interface authorization or anything else these paths
might one day also read), and — the part that matters most — none of this
changes what kernel_detach()/the EIO-recovery path actually *do*, only what
they log. That is checked in TestBehaviourUnchanged below.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import usb.core

from arctis_sound_manager.bug_reporter import (_usb_access,
                                                find_interface_sysfs_dir,
                                                interface_driver_name,
                                                kernel_driver_for_interface)
from arctis_sound_manager.core import CoreEngine

VID_STEELSERIES = "1038"


# ── fixture builders ──────────────────────────────────────────────────────

def _make_interface_dir(
    sys_root: Path, device_name: str, config: int, interface: int,
    *, hid_driver: str | None = None, own_driver: str | None = None,
    hid_instance: str = "0005",
    interface_class: str | None = None,
    usage_page: int | None = None,
) -> Path:
    """Build "<device_name>:<config>.<interface>" the way sysfs really lays
    it out: the interface's own 'driver' symlink (usbhid, in real life,
    for any HID-class interface — but callers may pass whatever they like
    to test the fallback path), and optionally a nested HID-bus child
    directory ("0003:1038:<pid>.<instance>") with its own driver symlink,
    the way usbhid creates one once it actually binds.
    """
    iface_dir = sys_root / f"{device_name}:{config}.{interface}"
    iface_dir.mkdir(parents=True)
    (iface_dir / "bInterfaceNumber").write_text(f"{interface:02d}")
    if interface_class is not None:
        (iface_dir / "bInterfaceClass").write_text(interface_class)
    if own_driver is not None:
        (iface_dir / "driver").symlink_to(_driver_target(sys_root, own_driver))
    if hid_driver is not None:
        hid_child = iface_dir / f"0003:1038:12E0.{hid_instance}"
        hid_child.mkdir()
        (hid_child / "driver").symlink_to(_driver_target(sys_root, hid_driver))
        if usage_page is not None:
            (hid_child / "report_descriptor").write_bytes(
                bytes([0x06, usage_page & 0xFF, usage_page >> 8, 0x09, 0x01]))
    return iface_dir


def _driver_target(sys_root: Path, driver_name: str) -> Path:
    # The real symlink target looks like
    # ".../bus/usb/drivers/usbhid" or ".../bus/hid/drivers/hid-generic" — the
    # code only ever reads the *basename*, so a synthetic absolute path is
    # enough and keeps the fixtures self-contained under tmp_path.
    target = sys_root.parent / "drivers" / driver_name
    target.mkdir(parents=True, exist_ok=True)
    return target


# ── kernel_driver_for_interface ───────────────────────────────────────────

class TestKernelDriverForInterface:
    def test_reads_the_hid_bus_child_not_the_interfaces_own_driver(self, tmp_path):
        """This is the whole point: today's kernel *always* shows "usbhid" at
        the interface's own level for any HID-class interface, regardless of
        whether hid-generic or a vendor driver is really in charge. The child
        is where the real answer lives."""
        sys_root = tmp_path / "sys"
        iface = _make_interface_dir(
            sys_root, "1-6", 1, 3, own_driver="usbhid", hid_driver="hid-generic")

        assert kernel_driver_for_interface(iface) == "hid-generic"

    def test_forward_compatible_with_a_future_vendor_driver(self, tmp_path):
        """Nothing about this code names hid-steelseries specifically — it
        reads whatever driver bound. Simulated here because no kernel on
        this machine (or anywhere installable today, 2026-08-22) has
        hid-steelseries; this is the "once 7.3 ships" half of the contract,
        proven without needing that kernel."""
        sys_root = tmp_path / "sys"
        iface = _make_interface_dir(
            sys_root, "1-6", 1, 3, own_driver="usbhid", hid_driver="hid-steelseries")

        assert kernel_driver_for_interface(iface) == "hid-steelseries"

    def test_falls_back_to_the_interfaces_own_driver_when_no_hid_child_exists(self, tmp_path):
        """A non-HID interface (e.g. the Nova-family audio-class interfaces)
        never gets a hid-bus child at all — the interface's own driver is
        the whole answer there, and must not come back as unknown."""
        sys_root = tmp_path / "sys"
        iface = _make_interface_dir(sys_root, "1-6", 1, 0, own_driver="snd-usb-audio")

        assert kernel_driver_for_interface(iface) == "snd-usb-audio"

    def test_unclaimed_interface_reports_none_not_a_guess(self, tmp_path):
        """Boot-race window: udev has created the interface directory but
        nothing has bound to it yet. Must say "nothing", not fabricate."""
        sys_root = tmp_path / "sys"
        iface = sys_root / "1-6:1.3"
        iface.mkdir(parents=True)
        (iface / "bInterfaceNumber").write_text("03")

        assert kernel_driver_for_interface(iface) is None

    def test_missing_directory_does_not_raise(self, tmp_path):
        assert kernel_driver_for_interface(tmp_path / "sys" / "nope:1.3") is None


# ── find_interface_sysfs_dir ──────────────────────────────────────────────

class TestFindInterfaceSysfsDir:
    def test_finds_the_right_interface_by_number(self, tmp_path):
        sys_root = tmp_path / "sys"
        _make_interface_dir(sys_root, "1-6", 1, 0, own_driver="snd-usb-audio")
        wanted = _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                                      hid_driver="hid-generic")

        found = find_interface_sysfs_dir(sys_root, "1-6", 3)
        assert found == wanted

    def test_does_not_confuse_interface_3_with_interface_13(self, tmp_path):
        """Config numbers are wildcarded (":*.<n>"), which would be a trap if
        the glob matched on a bare numeric suffix instead of the full
        ".<n>" component — "1-6:1.13" must never satisfy interface=3."""
        sys_root = tmp_path / "sys"
        _make_interface_dir(sys_root, "1-6", 1, 13, own_driver="usbhid",
                            hid_driver="hid-generic")
        three = _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                                     hid_driver="hid-steelseries")

        found = find_interface_sysfs_dir(sys_root, "1-6", 3)
        assert found == three

    def test_returns_none_for_an_interface_that_does_not_exist(self, tmp_path):
        sys_root = tmp_path / "sys"
        sys_root.mkdir()
        assert find_interface_sysfs_dir(sys_root, "1-6", 4) is None

    def test_returns_none_when_sys_root_itself_is_absent(self, tmp_path):
        assert find_interface_sysfs_dir(tmp_path / "nope", "1-6", 3) is None


# ── interface_driver_name (the two above, composed) ───────────────────────

def test_interface_driver_name_end_to_end(tmp_path):
    sys_root = tmp_path / "sys"
    _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                        hid_driver="hid-generic")

    assert interface_driver_name(sys_root, "1-6", 3) == "hid-generic"
    assert interface_driver_name(sys_root, "1-6", 4) is None


# ── _usb_access: the field a bug report actually ships ────────────────────

def _make_device(sys_root: Path, name: str, *, vid: str = VID_STEELSERIES,
                 pid: str = "12e0", product: str = "SteelSeries Arctis Nova Pro Wireless",
                 busnum: str = "1", devnum: str = "5") -> Path:
    dev = sys_root / name
    dev.mkdir(parents=True)
    (dev / "idVendor").write_text(vid)
    (dev / "idProduct").write_text(pid)
    (dev / "product").write_text(product)
    (dev / "busnum").write_text(busnum)
    (dev / "devnum").write_text(devnum)
    return dev


def test_usb_access_names_the_driver_on_every_interface(tmp_path):
    """The field a filed GitHub issue actually contains (bug_reporter._usb_access
    feeds bug_reporter.generate_report()'s 'usb_access' key). Mirrors the real
    Nova Pro Wireless layout on this machine: interfaces 0-2 audio class,
    3 the vendor command/OLED interface (hid-generic today), 4 the
    consumer-control/media-key interface ASM must never claim."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-6")
    _make_interface_dir(sys_root, "1-6", 1, 0, own_driver="snd-usb-audio")
    _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                        hid_driver="hid-generic")
    _make_interface_dir(sys_root, "1-6", 1, 4, own_driver="usbhid",
                        hid_driver="hid-generic", hid_instance="0006")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "interfaces:" in out
    assert "1-6:1.0" in out and "driver=snd-usb-audio" in out
    assert "1-6:1.3" in out and "driver=hid-generic" in out
    # Two interfaces both report hid-generic; the section must show both
    # entries, not collapse or drop one.
    assert out.count("driver=hid-generic") == 2


def test_usb_access_reports_a_future_vendor_driver_by_name(tmp_path):
    """The concrete scenario INT-1 exists for: once hid-steelseries binds,
    a bug report must say so by name, not "a kernel driver is active"."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-6")
    _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                        hid_driver="hid-steelseries")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "driver=hid-steelseries" in out


def test_usb_access_survives_a_device_with_no_interfaces_enumerated(tmp_path):
    """Sysfs listing raced ahead of interface enumeration, or a device with
    an unusual config — must not raise, must not silently drop the device
    line either."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-6")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "1038:12e0" in out
    assert "interfaces:" not in out  # nothing to report — not fabricated


# ── CoreEngine._interface_kernel_driver ───────────────────────────────────

class _FakeUsbDevice:
    def __init__(self, bus, port_numbers):
        self.bus = bus
        self.port_numbers = port_numbers


class TestCoreEngineInterfaceKernelDriver:
    def _engine(self) -> MagicMock:
        stub = MagicMock()
        stub.logger = logging.getLogger("test-kernel-driver-diag")
        return stub

    def test_names_the_driver_from_a_live_looking_device(self, tmp_path):
        sys_root = tmp_path / "sys"
        _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                            hid_driver="hid-generic")
        dev = _FakeUsbDevice(bus=1, port_numbers=(6,))
        engine = self._engine()

        result = CoreEngine._interface_kernel_driver(engine, dev, 3, sys_root=sys_root)
        assert result == "hid-generic"

    def test_multi_hop_port_chain_builds_the_right_sysfs_path(self, tmp_path):
        """A device behind a hub gets a dotted port chain (e.g. "1-4.6"),
        not just a bus-port pair — this must round-trip too."""
        sys_root = tmp_path / "sys"
        _make_interface_dir(sys_root, "1-4.6", 1, 3, own_driver="usbhid",
                            hid_driver="hid-steelseries")
        dev = _FakeUsbDevice(bus=1, port_numbers=(4, 6))
        engine = self._engine()

        result = CoreEngine._interface_kernel_driver(engine, dev, 3, sys_root=sys_root)
        assert result == "hid-steelseries"

    def test_never_raises_when_the_device_has_no_port_info(self, tmp_path):
        """A mocked/incomplete usb.core.Device (as many existing tests use)
        must degrade to "unknown", never blow up a log call."""
        engine = self._engine()
        bare = MagicMock(spec=[])  # no .bus, no .port_numbers at all

        result = CoreEngine._interface_kernel_driver(
            engine, bare, 3, sys_root=tmp_path / "sys")
        assert result == "unknown"

    def test_never_raises_when_sysfs_tree_is_entirely_absent(self, tmp_path):
        """No-op-on-current-kernel, the other half: sysfs paths this code has
        never seen before (an unmounted/minimal /sys, a container) must not
        turn a diagnostic helper into a crash."""
        dev = _FakeUsbDevice(bus=1, port_numbers=(6,))
        engine = self._engine()

        result = CoreEngine._interface_kernel_driver(
            engine, dev, 3, sys_root=tmp_path / "does-not-exist")
        assert result == "unknown"


# ── behaviour is unchanged: only logging gained a driver name ─────────────

class TestBehaviourUnchanged:
    """The diagnostic must be provably a no-op on control flow: kernel_detach's
    return value, its EACCES/permission_error bookkeeping, and which
    interfaces get claimed must be identical to before this change — on
    this machine's real kernel (7.2.0, no hid-steelseries) there is nothing
    for the new code to report beyond "usbhid"/"hid-generic", and even where
    it reports something else, it must never feed back into what
    kernel_detach *does*.
    """

    def _mock_dev(self, vid: int, pid: int) -> MagicMock:
        dev = MagicMock(spec=usb.core.Device)
        dev.idVendor, dev.idProduct = vid, pid
        dev.bus, dev.address = 1, 5
        dev.port_numbers = (6,)
        dev.is_kernel_driver_active = MagicMock(
            side_effect=usb.core.USBError("denied", errno=13))
        dev.detach_kernel_driver = MagicMock()
        dev._ctx = MagicMock()
        return dev

    def test_eacces_still_sets_permission_error_and_returns_false(self, tmp_path, caplog):
        from arctis_sound_manager.config import DeviceConfiguration

        engine = MagicMock()
        engine.logger = logging.getLogger("test-behaviour-unchanged")
        engine._all_used_interfaces = MagicMock(return_value=[3])
        # Real method, bound to the mock engine, exactly like production.
        engine._interface_kernel_driver = lambda *a, **k: \
            CoreEngine._interface_kernel_driver(engine, *a, sys_root=tmp_path / "sys", **k)

        cfg = MagicMock(spec=DeviceConfiguration)
        cfg.name = "Test Headset"
        dev = self._mock_dev(0x1038, 0x12e0)

        with caplog.at_level(logging.ERROR):
            result = CoreEngine.kernel_detach(engine, dev, cfg)

        assert result is False
        assert engine.permission_error is True
        # The new context is additive to the message, not a replacement for
        # the remediation steps a user still needs.
        assert "udev rules are missing" in caplog.text
        assert "driver=unknown" in caplog.text  # no sysfs tree behind tmp_path/sys


# ── what a report must say about which interface is the control channel ──────

def test_usb_access_names_the_usage_page_of_every_hid_interface(tmp_path):
    """Both #216 and #217 came down to one question - which interface is this
    device's vendor control channel - and no report could answer it. The
    driver was listed; the class and the usage page it declares were not, so a
    profile naming interface 3 could not be checked against the hardware at
    all."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-6", pid="220e", product="SteelSeries Arctis 7+")
    _make_interface_dir(sys_root, "1-6", 1, 0, own_driver="snd-usb-audio",
                        interface_class="01")
    _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                        hid_driver="hid-generic", interface_class="03",
                        usage_page=0xFFC0)
    _make_interface_dir(sys_root, "1-6", 1, 4, own_driver="usbhid",
                        hid_driver="hid-generic", hid_instance="0006",
                        interface_class="03", usage_page=0x000C)

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "usage_page=0xffc0 (vendor)" in out
    assert "usage_page=0x000c" in out
    assert "0x000c (vendor)" not in out
    assert "class=0x03" in out and "class=0x01" in out


def test_usb_access_says_so_when_a_usage_page_cannot_be_read(tmp_path):
    """"Not readable" and "not a vendor interface" are different answers, and
    a report that blurred them is how 1.4.10's resolver came to move devices
    it had learnt nothing about."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-6")
    _make_interface_dir(sys_root, "1-6", 1, 3, own_driver="usbhid",
                        interface_class="03")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "usage_page=not readable" in out
