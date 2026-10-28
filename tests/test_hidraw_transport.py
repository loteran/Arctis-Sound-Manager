# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The hidraw transport (Settings → Headset access): ASM leaves the HID
interfaces to the kernel and talks through /dev/hidraw* instead of detaching
the driver and claiming them with libusb. Falls back to libusb when the nodes
cannot be opened. The bug report says which one was chosen and which one is
in use.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import usb.core

from arctis_sound_manager import core, hidraw_transport
from arctis_sound_manager.config import CommandTransport
from arctis_sound_manager.core import CoreEngine
from arctis_sound_manager.hidraw_transport import (HidrawTransport, descriptor_uses_report_ids,
                                                   find_hidraw_nodes)

# A vendor page with Report ID 6 (0x85 0x06), and one without any.
NUMBERED = bytes([0x06, 0xC0, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x06, 0x95, 0x3F, 0xC0])
UNNUMBERED = bytes([0x06, 0xC0, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x95, 0x40, 0x75, 0x08, 0xC0])
# 0x85 as the *data* of another item (Usage Page 0xFF85) is not a Report ID.
DATA_LOOKS_LIKE_ID = bytes([0x06, 0x85, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0xC0])


def test_report_id_detection_walks_items():
    assert descriptor_uses_report_ids(NUMBERED)
    assert not descriptor_uses_report_ids(UNNUMBERED)
    assert not descriptor_uses_report_ids(DATA_LOOKS_LIKE_ID)


def test_nodes_are_found_per_interface(tmp_path):
    for iface, node in ((3, "hidraw2"), (4, "hidraw3")):
        (tmp_path / f"1-6:1.{iface}" / f"0003:1038:12E0.000{iface}" / "hidraw" / node).mkdir(parents=True)

    nodes = find_hidraw_nodes(1, [6], [3, 4, 5, -1], sys_root=tmp_path)

    assert nodes == {3: Path("/dev/hidraw2"), 4: Path("/dev/hidraw3")}
    assert find_hidraw_nodes(None, [6], [3], sys_root=tmp_path) == {}


def _transport(monkeypatch, numbered: bool) -> tuple[HidrawTransport, list[bytes]]:
    written: list[bytes] = []
    monkeypatch.setattr(hidraw_transport.os, "open", lambda path, flags: 99)
    monkeypatch.setattr(hidraw_transport.os, "write", lambda fd, data: written.append(data) or len(data))
    monkeypatch.setattr(hidraw_transport.os, "close", lambda fd: None)
    monkeypatch.setattr(HidrawTransport, "_read_numbered", staticmethod(lambda node: numbered))
    return HidrawTransport({4: Path("/dev/hidraw3")}), written


def test_unnumbered_reports_get_hidraws_zero_in_front(monkeypatch):
    transport, written = _transport(monkeypatch, numbered=False)
    transport.write_output(4, [0xA5, 0x00, 0x01])
    assert written == [bytes([0x00, 0xA5, 0x00, 0x01])]


def test_numbered_reports_go_out_as_asm_built_them(monkeypatch):
    transport, written = _transport(monkeypatch, numbered=True)
    transport.write_output(4, [0x06, 0x27, 0x02])
    assert written == [bytes([0x06, 0x27, 0x02])]


def test_a_read_with_nothing_pending_times_out_like_libusb():
    r, w = os.pipe()
    transport = HidrawTransport.__new__(HidrawTransport)
    transport.nodes, transport._fds, transport._numbered = {}, {4: r}, {4: True}
    try:
        with pytest.raises(usb.core.USBError) as exc:
            transport.read(4, 64, 10)
        assert exc.value.errno == errno.ETIMEDOUT
        os.write(w, bytes([0x06, 0xB0, 0x01]))
        assert transport.read(4, 64, 1000) == [0x06, 0xB0, 0x01]
    finally:
        os.close(r)
        os.close(w)


def _engine(transport_choice: int):
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine.general_settings = MagicMock(usb_transport=transport_choice)
    engine._hidraw = None
    engine._hidraw_failure = None
    engine.usb_transport_active = "libusb"
    engine._command_iface_override = None
    engine._record_usb_transport = MagicMock()
    config = MagicMock()
    config.name = "Nova Pro Wireless"
    config.command_interface_index = [4, 0]
    config.oled = None
    engine.device_config = config
    engine._all_used_interfaces = lambda cfg: [4]
    usb_device = MagicMock(bus=1, port_numbers=[6], idVendor=0x1038, idProduct=0x12E0)
    usb_device.is_kernel_driver_active.return_value = True
    return engine, config, usb_device


def test_hidraw_choice_opens_the_nodes_and_detaches_nothing(monkeypatch):
    engine, config, usb_device = _engine(1)
    monkeypatch.setattr(core, "find_hidraw_nodes", lambda bus, ports, ifaces: {4: Path("/dev/hidraw3")})
    monkeypatch.setattr(core, "HidrawTransport",
                        type("FakeTransport", (HidrawTransport,), {"__init__": lambda self, nodes: setattr(self, "nodes", nodes),
                                                                    "describe": lambda self: ""}))

    assert CoreEngine.kernel_detach(engine, usb_device, config)

    assert engine.usb_transport_active == "hidraw"
    usb_device.detach_kernel_driver.assert_not_called()
    engine._record_usb_transport.assert_called_once_with("hidraw", None)


def test_hidraw_falls_back_to_libusb_without_a_command_node(monkeypatch):
    engine, config, usb_device = _engine(1)
    monkeypatch.setattr(core, "_HIDRAW_NODE_WAIT_S", 0.0)
    monkeypatch.setattr(core, "find_hidraw_nodes", lambda bus, ports, ifaces: {})
    monkeypatch.setattr(core.usb.util, "claim_interface", lambda dev, iface: None)

    assert CoreEngine.kernel_detach(engine, usb_device, config)

    assert engine.usb_transport_active == "libusb"
    usb_device.detach_kernel_driver.assert_called_once_with(4)
    active, reason = engine._record_usb_transport.call_args.args
    assert active == "libusb" and "no hidraw node" in reason


def test_commands_go_through_hidraw_when_it_is_active():
    engine, config, usb_device = _engine(1)
    engine._hidraw = MagicMock(spec=HidrawTransport)
    engine.usb_device = usb_device
    engine._usb_write_lock = threading.Lock()
    engine._last_usb_write_monotonic = 0.0
    engine._untagged_requests_pending = []
    config.command_transport = CommandTransport.CTRL_OUTPUT
    config.command_padding = MagicMock(length=4, filler=0x00)
    config.time_between_commands_ms = 0
    config.status = None
    config.command_report_id = 0x06

    CoreEngine.send_command(engine, [0x06, 0x27, 0x02], 0)

    engine._hidraw.write_output.assert_called_once_with(4, [0x06, 0x27, 0x02, 0x00])
    usb_device.ctrl_transfer.assert_not_called()


def test_the_bug_report_follows_the_choice(tmp_path, monkeypatch):
    from arctis_sound_manager import bug_reporter

    dev = tmp_path / "1-6"
    dev.mkdir()
    (dev / "idVendor").write_text("1038\n")
    (dev / "idProduct").write_text("12e0\n")
    (dev / "busnum").write_text("1\n")
    (dev / "devnum").write_text("18\n")
    intf = tmp_path / "1-6:1.4"
    (intf / "0003:1038:12E0.0004" / "hidraw" / "hidraw3").mkdir(parents=True)
    (intf / "bInterfaceClass").write_text("03\n")
    (intf / "bInterfaceNumber").write_text("04\n")
    state = tmp_path / "usb_transport_state.json"
    monkeypatch.setattr(hidraw_transport, "USB_TRANSPORT_STATE_FILE", state)

    settings = MagicMock(usb_transport=1)
    monkeypatch.setattr("arctis_sound_manager.settings.GeneralSettings.read_from_file",
                        staticmethod(lambda: settings))
    text = bug_reporter._usb_access_info(sys_usb=tmp_path)
    assert "chosen in Settings: hidraw" in text
    assert "/dev/hidraw3" in text and "usbfs" not in text

    settings.usb_transport = 0
    text = bug_reporter._usb_access_info(sys_usb=tmp_path)
    assert "chosen in Settings: libusb" in text
    assert "/dev/bus/usb/001/018" in text and "hidraw3" not in text


def test_udev_rules_cover_the_hidraw_nodes():
    from arctis_sound_manager.udev_rules import render_rules, render_uaccess_rules

    devices = [(0x1038, [0x12E0], "Nova Pro Wireless")]
    assert 'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1038", ATTRS{idProduct}=="12e0", MODE="0666"' in render_rules(devices)
    assert 'SUBSYSTEM=="hidraw"' in render_uaccess_rules(devices)
