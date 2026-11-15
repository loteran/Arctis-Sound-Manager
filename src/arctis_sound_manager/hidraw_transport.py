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
import time
from pathlib import Path
from typing import Callable, Iterable

import usb.core

logger = logging.getLogger(__name__)

_SYS_USB = Path('/sys/bus/usb/devices')
# How long reopen() waits for a replaced node to appear and for udev to give
# it the rules' MODE (the kernel creates it root 0600).
_REOPEN_WAIT_S = 2.0

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

    def __init__(self, nodes: dict[int, Path],
                 locate: Callable[[int], Path | None] | None = None):
        self.nodes = dict(nodes)
        self._fds: dict[int, int] = {}
        self._numbered: dict[int, bool] = {}
        # Finds an interface's current node again; without it a replaced
        # node cannot be reopened (#383).
        self._locate = locate
        self._reopens = 0
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

    def is_stale(self, iface: int) -> bool:
        """True when the node held open is no longer the interface's node.

        Anything that re-binds the HID driver (fwupd's SteelSeries plugin
        polls the receiver every 20 s, #383) makes the kernel delete the
        hidraw node and create a new one. The fd still open on the old one
        then only ever returns EIO / ENODEV, though the device is fine.
        """
        fd, node = self._fds.get(iface), self.nodes.get(iface)
        if fd is None or node is None:
            return False
        try:
            held = os.fstat(fd)
        except OSError:
            return True
        try:
            current = os.stat(node)
        except OSError:
            return True
        return held.st_nlink == 0 or (held.st_ino, held.st_rdev) != (current.st_ino, current.st_rdev)

    def reopen(self, iface: int) -> bool:
        """Open the interface's new node onto the same fd number.

        dup2() keeps the number, so a thread that already fetched it never
        lands on a closed or reused descriptor. False when the node was not
        replaced (a genuine EIO, left to the caller's recovery) or the new
        one cannot be found or opened in time.
        """
        if self._locate is None or not self.is_stale(iface):
            return False
        deadline = time.monotonic() + _REOPEN_WAIT_S
        while True:
            node = self._locate(iface)
            if node is not None:
                try:
                    new_fd = os.open(node, os.O_RDWR | os.O_CLOEXEC)
                except OSError as e:
                    if e.errno not in (errno.EACCES, errno.EPERM, errno.ENOENT):
                        logger.warning("hidraw: cannot reopen %s: %s", node, e)
                        return False
                else:
                    try:
                        fd = self._fds.get(iface)
                        if fd is None:  # closed meanwhile
                            return False
                        os.dup2(new_fd, fd, inheritable=False)
                    finally:
                        os.close(new_fd)
                    old = self.nodes.get(iface)
                    self.nodes[iface] = node
                    self._numbered[iface] = self._read_numbered(node)
                    self._reopens += 1
                    if self._reopens == 1 or self._reopens % 50 == 0:
                        logger.info(
                            "hidraw: interface %d node replaced (%s → %s, another program "
                            "re-bound the HID driver) — reopened in place (×%d)",
                            iface, old, node, self._reopens)
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _call(self, iface: int, op: Callable[[int], object]):
        """Run *op* on the interface's fd; after a replaced node, reopen and
        retry once (#383). OSError comes out as USBError, as from libusb."""
        try:
            return op(self._fd(iface))
        except usb.core.USBError:
            raise
        except OSError as e:
            if e.errno in (errno.EIO, errno.ENODEV) and self.reopen(iface):
                try:
                    return op(self._fd(iface))
                except OSError as e2:
                    raise usb.core.USBError(str(e2), errno=e2.errno) from e2
            raise usb.core.USBError(str(e), errno=e.errno) from e

    def _framed(self, iface: int, data: list[int] | bytes) -> bytes:
        """ASM's frame with the report number hidraw expects in front."""
        data = bytes(b & 0xFF for b in data)
        return data if self.uses_report_ids(iface) else b'\x00' + data

    def write_output(self, iface: int, data: list[int] | bytes) -> int:
        return self._call(iface, lambda fd: os.write(fd, self._framed(iface, data)))

    def send_feature(self, iface: int, data: list[int] | bytes) -> None:
        def op(fd: int) -> None:
            buf = bytearray(self._framed(iface, data))
            fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
        self._call(iface, op)

    def get_feature(self, iface: int, report_id: int, size: int) -> list[int]:
        """A feature report as GET_REPORT returns it: report id first when
        the interface numbers its reports, `size` bytes at most."""
        def op(fd: int) -> bytes:
            buf = bytearray(max(size, 1))
            buf[0] = report_id & 0xFF
            n = fcntl.ioctl(fd, HIDIOCGFEATURE(len(buf)), buf, True)
            return bytes(buf[:n]) if isinstance(n, int) and n > 0 else bytes(buf)
        data = self._call(iface, op)
        return list(data if self.uses_report_ids(iface) else data[1:])

    def read(self, iface: int, size: int, timeout_ms: int) -> list[int]:
        def op(fd: int) -> bytes | None:
            ready, _, _ = select.select([fd], [], [], timeout_ms / 1000)
            if not ready:
                return None
            # A whole report, whatever the endpoint size: a short buffer
            # truncates it (the very thing libusb reported as EOVERFLOW).
            return os.read(fd, max(size, 4096))
        data = self._call(iface, op)
        if data is None:
            raise usb.core.USBError("Operation timed out", errno=errno.ETIMEDOUT)
        return list(data)

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
