# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talk to the headset through /dev/hidraw* instead of claiming it with libusb.

ASM has always detached the kernel's HID driver from the interfaces it uses
and claimed them through libusb, then moved every byte itself: interrupt reads
sized from wMaxPacketSize, SET_REPORT / GET_REPORT control transfers. With
hidraw the kernel keeps the interface. Its HID driver sizes the reads from
the report descriptor, keeps one pending at all times, and the driver it
belongs to (usbhid, or hid-steelseries-arctis since Linux 7.3) stays bound,
so the system keeps seeing the headset's battery while ASM runs.

The calls mirror the libusb operations core.py and oled_manager.py make:

- write_output(): an output report, what SET_REPORT(Output) or a write to the
  interrupt OUT endpoint carried. The kernel picks the endpoint or the
  control pipe.
- send_feature() / get_feature(): SET_REPORT / GET_REPORT(Feature), through
  HIDIOCSFEATURE / HIDIOCGFEATURE.
- read(): one input report, or USBError(errno=ETIMEDOUT) after the timeout,
  the same exception the libusb read raises, so the listen loop's error
  handling applies unchanged.

The one convention to bridge is the report number. hidraw always wants it as
the first byte, 0 for a device without numbered reports, and strips that 0
before sending. ASM's frames carry it only on families with numbered reports
(the libusb SET_REPORT data already starts with it there). Which kind an
interface is comes from its own report descriptor, read from sysfs, rather
than from the profile.

Errors are raised as usb.core.USBError with the OS errno, so the existing
ENODEV / EIO / EBUSY handling sees what it always saw.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import select
from pathlib import Path
from typing import Iterable

import usb.core

logger = logging.getLogger(__name__)

_SYS_USB = Path('/sys/bus/usb/devices')

#: Written by the daemon at each acquisition (CoreEngine._record_usb_transport),
#: read by the bug report: which transport was asked for, which one is active.
USB_TRANSPORT_STATE_FILE = Path.home() / '.config' / 'arctis_manager' / 'usb_transport_state.json'

# linux/hidraw.h: HIDIOCSFEATURE(len) = _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, len)
#                 HIDIOCGFEATURE(len) = _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x07, len)
_IOC_READ_WRITE = 3


def _hidioc(nr: int, length: int) -> int:
    return (_IOC_READ_WRITE << 30) | (length << 16) | (ord('H') << 8) | nr


def HIDIOCSFEATURE(length: int) -> int:  # noqa: N802 — the kernel's name
    return _hidioc(0x06, length)


def HIDIOCGFEATURE(length: int) -> int:  # noqa: N802
    return _hidioc(0x07, length)


def descriptor_uses_report_ids(descriptor: bytes) -> bool:
    """True if a HID report descriptor declares any Report ID item.

    Walks the items rather than searching for the byte 0x85, which can just
    as well be the data of another item.
    """
    i = 0
    while i < len(descriptor):
        prefix = descriptor[i]
        if prefix == 0xFE:  # long item: bDataSize, bLongItemTag, data
            if i + 1 >= len(descriptor):
                break
            i += 3 + descriptor[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        if prefix & 0xFC == 0x84:  # Global item, Report ID
            return True
        i += 1 + size
    return False


def find_hidraw_nodes(bus: int | None, port_numbers: Iterable[int] | None,
                      interfaces: Iterable[int],
                      sys_root: Path = _SYS_USB) -> dict[int, Path]:
    """bInterfaceNumber → /dev/hidrawN for the interfaces asked for.

    An interface with no HID driver bound (or claimed by another program
    through libusb) has no hidraw node and is simply left out.
    """
    if not bus or not port_numbers:
        return {}
    device = f"{bus}-" + ".".join(str(p) for p in port_numbers)
    nodes: dict[int, Path] = {}
    for iface in interfaces:
        if iface < 0:
            continue
        for hid in sorted(sys_root.glob(f"{device}:*.{iface}/*/hidraw/hidraw*")):
            nodes[iface] = Path('/dev') / hid.name
            break
    return nodes


def _sysfs_hid_dir(node: Path) -> Path:
    return Path('/sys/class/hidraw') / node.name / 'device'


class HidrawTransport:
    """Open hidraw nodes for one headset, keyed by USB interface number."""

    def __init__(self, nodes: dict[int, Path]):
        self.nodes = dict(nodes)
        self._fds: dict[int, int] = {}
        self._numbered: dict[int, bool] = {}
        try:
            for iface, node in self.nodes.items():
                self._fds[iface] = os.open(node, os.O_RDWR | os.O_CLOEXEC)
                self._numbered[iface] = self._read_numbered(node)
        except OSError:
            self.close()
            raise

    @staticmethod
    def _read_numbered(node: Path) -> bool:
        try:
            return descriptor_uses_report_ids(
                (_sysfs_hid_dir(node) / 'report_descriptor').read_bytes())
        except OSError as e:
            # Without the descriptor, assume numbered: ASM's frames then go
            # out exactly as the libusb path sent them.
            logger.warning("hidraw: cannot read the report descriptor of %s (%s)", node, e)
            return True

    def has(self, iface: int) -> bool:
        return iface in self._fds

    def uses_report_ids(self, iface: int) -> bool:
        return self._numbered.get(iface, True)

    def _fd(self, iface: int) -> int:
        try:
            return self._fds[iface]
        except KeyError:
            raise usb.core.USBError(f"no hidraw node for interface {iface}",
                                    errno=errno.ENOENT) from None

    def _framed(self, iface: int, data: list[int] | bytes) -> bytes:
        """ASM's frame with the report number hidraw expects in front."""
        data = bytes(b & 0xFF for b in data)
        return data if self.uses_report_ids(iface) else b'\x00' + data

    def write_output(self, iface: int, data: list[int] | bytes) -> int:
        try:
            return os.write(self._fd(iface), self._framed(iface, data))
        except OSError as e:
            raise usb.core.USBError(str(e), errno=e.errno) from e

    def send_feature(self, iface: int, data: list[int] | bytes) -> None:
        buf = bytearray(self._framed(iface, data))
        try:
            fcntl.ioctl(self._fd(iface), HIDIOCSFEATURE(len(buf)), buf)
        except OSError as e:
            raise usb.core.USBError(str(e), errno=e.errno) from e

    def get_feature(self, iface: int, report_id: int, size: int) -> list[int]:
        """A feature report as GET_REPORT returns it: report id first when
        the interface numbers its reports, `size` bytes at most."""
        buf = bytearray(max(size, 1))
        buf[0] = report_id & 0xFF
        try:
            n = fcntl.ioctl(self._fd(iface), HIDIOCGFEATURE(len(buf)), buf, True)
        except OSError as e:
            raise usb.core.USBError(str(e), errno=e.errno) from e
        data = bytes(buf[:n]) if isinstance(n, int) and n > 0 else bytes(buf)
        return list(data if self.uses_report_ids(iface) else data[1:])

    def read(self, iface: int, size: int, timeout_ms: int) -> list[int]:
        fd = self._fd(iface)
        try:
            ready, _, _ = select.select([fd], [], [], timeout_ms / 1000)
            if not ready:
                raise usb.core.USBError("Operation timed out", errno=errno.ETIMEDOUT)
            # A whole report, whatever the endpoint size: a short buffer
            # truncates it (the very thing libusb reported as EOVERFLOW).
            return list(os.read(fd, max(size, 4096)))
        except OSError as e:
            raise usb.core.USBError(str(e), errno=e.errno) from e

    def close(self) -> None:
        for fd in self._fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def describe(self) -> str:
        """One line per interface, for the log and the bug report."""
        return ", ".join(
            f"interface {iface} → {node} ({'numbered' if self.uses_report_ids(iface) else 'unnumbered'} reports)"
            for iface, node in sorted(self.nodes.items()))
