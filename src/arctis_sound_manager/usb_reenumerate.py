# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""USB re-enumeration helpers for issue #238 (ChatMix dead after system resume).

Pure, mockable primitives — no CoreEngine state touched here. Two escalation
paths exist because they need different privileges:

- reset_via_usbfs(): USBDEVFS_RESET via libusb's Device.reset(). Needs only an
  rw fd on the usbfs node, already granted by the MODE="0666" udev rule
  (udev_rules.py) — safe to call automatically from the unprivileged daemon.
- toggle_authorized(): writes /sys/.../authorized, which is root:root and NOT
  reachable by udev MODE=/OWNER=/GROUP= (those apply to /dev nodes, not sysfs
  attributes). Root-only, and deliberately never called from the automatic
  resume path — see cli_tools.py's `asm-cli usb reenumerate`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def sysfs_device_dir(
    bus: int | None, port_numbers: tuple[int, ...] | list[int] | None,
    sys_root: Path = Path('/sys/bus/usb/devices'),
) -> Path | None:
    """The sysfs directory for a USB device, e.g. /sys/bus/usb/devices/1-6.2.

    Same "<bus>-<port>.<port>..." convention core.py's _hid_usage_page already
    builds inline. None if bus or port_numbers is missing/empty — there is
    nothing to point at.
    """
    if not bus or not port_numbers:
        return None
    return sys_root / (f"{bus}-" + ".".join(str(p) for p in port_numbers))


def usbfs_holders(bus: int | None, address: int | None,
                  proc_root: Path = Path('/proc'),
                  exclude_pid: int | None = None) -> list[tuple[int, str]]:
    """Other processes holding /dev/bus/usb/BBB/DDD open, as (pid, name).

    A device another program has open (a leftover script driving the same
    headset, #374) answers EBUSY to every claim, which looks exactly like a
    wedged device. A USB reset cannot clear another process's claim, so the
    caller should name the holder instead of resetting. Processes this user
    cannot inspect are skipped: the result may be incomplete, never wrong.
    """
    if not isinstance(bus, int) or not isinstance(address, int) or not bus or not address:
        return []
    node = f'/dev/bus/usb/{int(bus):03d}/{int(address):03d}'
    me = os.getpid() if exclude_pid is None else exclude_pid
    holders: list[tuple[int, str]] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            fds = list((entry / 'fd').iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target == node:
                try:
                    name = (entry / 'comm').read_text().strip()
                except OSError:
                    name = '?'
                holders.append((int(entry.name), name))
                break
    return holders


def reset_via_usbfs(usb_device: Any, logger: Any) -> bool:
    """Force a USBDEVFS_RESET on *usb_device*. Returns True on success.

    On success the libusb handle is invalid: the caller MUST
    usb.util.dispose_resources(usb_device) and re-detect the device before
    using it again — reset() re-enumerates the port, which the kernel may
    assign a new address for.
    """
    import usb.core

    try:
        usb_device.reset()
        return True
    except usb.core.USBError as e:
        logger.warning("usb_reenumerate: reset failed: %r — not escalating", e)
        return False


def can_toggle_authorized(device_dir: Path) -> bool:
    """Whether this process can write device_dir/authorized (root usually required)."""
    return os.access(device_dir / 'authorized', os.W_OK)


def toggle_authorized(device_dir: Path, settle_s: float, logger: Any) -> bool:
    """Unbind/rebind a USB device by toggling its sysfs 'authorized' attribute.

    Root-only in practice (see module docstring). Not called from any
    automatic path — exposed for `asm-cli usb reenumerate` only.
    """
    authorized_path = device_dir / 'authorized'
    try:
        authorized_path.write_text('0')
        time.sleep(settle_s)
        authorized_path.write_text('1')
        return True
    except (PermissionError, OSError) as e:
        logger.warning(
            "usb_reenumerate: authorized is not writable (root required); see docs (%r)", e)
        return False


def sysfs_has_device(vendor_id: int, product_ids: list[int] | tuple[int, ...],
                     sys_root: Path = Path('/sys/bus/usb/devices')) -> bool:
    """True if the kernel lists a device with this vendor and one of these
    product ids, whatever libusb currently believes."""
    wanted = {f'{p:04x}' for p in product_ids}
    vendor = f'{vendor_id:04x}'
    try:
        entries = list(sys_root.iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            if ((entry / 'idVendor').read_text().strip() == vendor
                    and (entry / 'idProduct').read_text().strip() in wanted):
                return True
        except OSError:
            continue
    return False


def refresh_libusb_context(logger: Any) -> bool:
    """Make the next usb.core.find() open a fresh libusb context.

    With hotplug support, libusb never rescans the bus: libusb_get_device_list
    returns the list its hotplug events keep up to date (libusb 1.0.30,
    core.c). When the "device added" event never reaches this process — a
    dongle re-enumerating on resume, seen from a distrobox container (#325) —
    the device stays invisible until the process restarts. A new context
    enumerates the bus from scratch.

    pyusb caches one backend (usb.backend.libusb1._lib_object); dropping it
    makes get_backend() build a new one, with its own libusb_init. Devices
    already opened keep a reference to the old backend, which is only
    released with them.
    """
    try:
        import usb.backend.libusb1 as libusb1
    except ImportError:
        return False
    if getattr(libusb1, '_lib_object', None) is None:
        return False
    libusb1._lib_object = None
    logger.info("The kernel lists the headset but libusb does not: "
                "opening a fresh libusb context to look again (#325)")
    return True
