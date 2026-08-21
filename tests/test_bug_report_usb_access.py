# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_bug_report_usb_access.py, the USB section that survives a permission bug.

Discussion #190: a user reported "I'm asked for the permission every time" and
"my headset still reads as not connected" after a clean reinstall. Every USB
section in the bug report went through pyusb, which is precisely what a
permission problem breaks, so the report was blank exactly where the answer
was, and the diagnosis had to be guessed at across several round trips.

This section reads sysfs and the device node directly, so it fills in whether
or not ASM can open the device, and it distinguishes the two causes that look
identical from the app: rules that never matched the device, and rules that
matched it too late.
"""
from __future__ import annotations

import os
from pathlib import Path

from arctis_sound_manager.bug_reporter import _usb_access

VID_STEELSERIES = "1038"


def _make_device(sys_root: Path, name: str, *, vid: str, pid: str,
                 product: str, busnum: str = "1", devnum: str = "7") -> Path:
    dev = sys_root / name
    dev.mkdir(parents=True)
    (dev / "idVendor").write_text(vid)
    (dev / "idProduct").write_text(pid)
    (dev / "product").write_text(product)
    (dev / "busnum").write_text(busnum)
    (dev / "devnum").write_text(devnum)
    return dev


def test_reports_the_headset_even_when_it_cannot_be_opened(tmp_path):
    """The device is listed from sysfs, with its PID and no pyusb involved."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-3", vid=VID_STEELSERIES, pid="12ad",
                 product="SteelSeries Arctis Nova 7")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "1038:12ad" in out
    assert "SteelSeries Arctis Nova 7" in out


def test_non_steelseries_devices_are_left_out(tmp_path):
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-1", vid="8087", pid="0032", product="Intel hub")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "Intel hub" not in out
    assert "No SteelSeries device" in out


def test_says_so_when_nothing_is_plugged_in(tmp_path):
    """An empty list is a finding, not an empty section: with no device
    enumerated there is nothing for any rule to match, and the problem is
    upstream of ASM."""
    out = _usb_access([], sys_root=tmp_path / "sys", dev_root=tmp_path / "dev")

    assert "No SteelSeries device" in out


def test_flags_a_rules_file_udev_will_ignore(tmp_path):
    """A rules file installed 0600 is silently ignored by udev, a bug this
    project has already shipped once, from a `pkexec cp` that kept root's
    umask, and it looks exactly like having no rules at all."""
    rules = tmp_path / "91-steelseries-arctis.rules"
    rules.write_text('SUBSYSTEM=="usb", ATTRS{idVendor}=="1038", TAG+="uaccess"\n')
    os.chmod(rules, 0o600)

    out = _usb_access([str(rules)], sys_root=tmp_path / "sys",
                      dev_root=tmp_path / "dev")

    assert "not world-readable" in out, out

    os.chmod(rules, 0o644)
    out = _usb_access([str(rules)], sys_root=tmp_path / "sys",
                      dev_root=tmp_path / "dev")
    assert "not world-readable" not in out


def test_reports_a_missing_rules_file_instead_of_raising(tmp_path):
    out = _usb_access([str(tmp_path / "nope.rules")], sys_root=tmp_path / "sys",
                      dev_root=tmp_path / "dev")

    assert "cannot stat" in out


def test_node_permissions_and_write_access_are_reported(tmp_path):
    """The node's mode and whether this process can write to it: the question
    the permissions popup is really asking."""
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    _make_device(sys_root, "1-3", vid=VID_STEELSERIES, pid="12ad",
                 product="Arctis Nova 7", busnum="3", devnum="12")
    node = dev_root / "003" / "012"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    os.chmod(node, 0o644)

    out = _usb_access([], sys_root=sys_root, dev_root=dev_root)

    assert str(node) in out
    assert "mode 0o644" in out
    # Writable as its owner: the run must say so rather than stay silent.
    assert "writable by this process: yes" in out


def test_a_device_with_no_node_does_not_abort_the_section(tmp_path):
    """The node can be absent (device gone between the two reads). The section
    must still carry the device it found."""
    sys_root = tmp_path / "sys"
    _make_device(sys_root, "1-3", vid=VID_STEELSERIES, pid="12ad",
                 product="Arctis Nova 7")

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "absent")

    assert "1038:12ad" in out
    assert "cannot stat" in out


def test_unreadable_bus_numbers_do_not_raise(tmp_path):
    sys_root = tmp_path / "sys"
    dev = _make_device(sys_root, "1-3", vid=VID_STEELSERIES, pid="12ad",
                       product="Arctis Nova 7")
    (dev / "busnum").unlink()

    out = _usb_access([], sys_root=sys_root, dev_root=tmp_path / "dev")

    assert "cannot locate the device node" in out
