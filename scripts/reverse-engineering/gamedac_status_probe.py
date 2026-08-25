#!/usr/bin/env python3
"""Read the Arctis Pro GameDAC's audio-settings frame and print it as bytes.

Why this exists. SteelSeries' own specification describes the frame the GameDAC
answers to command 0x20 — volume, sidetone, and the two values that are the
ChatMix dial:

    hp_speaker_game     the game side
    hp_speaker_chat     the chat side

What it does not say is whether the report id travels on the wire. The struct
is exactly 64 bytes counting it, and the endpoint carries 64, so both readings
fit and they differ by one byte — which shifts every field. Writing a device
profile on the wrong one produces plausible garbage rather than an error, so it
is worth one minute of somebody's real hardware instead of a guess (#213).

    sudo systemctl --user stop arctis-manager     # it holds the interface
    python3 scripts/reverse-engineering/gamedac_status_probe.py

Turn the ChatMix dial between runs: the two bytes that move together, one up
and one down, are the pair. Paste the whole output into the issue.

Read-only. It sends one query and reads the answer; nothing is written to the
device and no setting is changed.

Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import sys

VENDOR = 0x1038
GAMEDAC = 0x1280
STATUS_COMMAND = 0x20


def main() -> int:
    try:
        import usb.core
        import usb.util
    except ImportError:
        print("pyusb is missing. Install it, or run this from an ASM checkout.")
        return 1

    dev = usb.core.find(idVendor=VENDOR, idProduct=GAMEDAC)
    if dev is None:
        print(f"No {VENDOR:#06x}:{GAMEDAC:#06x} found. Is the GameDAC plugged in?")
        return 1

    try:
        cfg = dev.get_active_configuration()
    except usb.core.USBError as exc:
        print(f"Cannot read the configuration: {exc}")
        print("Permissions? The udev rules ship with ASM; try `asm-setup`.")
        return 1

    # Every HID interface with a full-size report is a candidate: the layout is
    # in no specification, and the profile's own number turned out to be wrong
    # on this device.
    candidates = [
        i for i in cfg
        if i.bInterfaceClass == 3
        and any(ep.wMaxPacketSize >= 64 for ep in i)
    ]
    print(f"HID interfaces with 64-byte endpoints: "
          f"{[i.bInterfaceNumber for i in candidates] or 'none'}\n")

    for intf in candidates:
        num = intf.bInterfaceNumber
        detached = False
        try:
            if dev.is_kernel_driver_active(num):
                dev.detach_kernel_driver(num)
                detached = True
        except usb.core.USBError:
            pass

        try:
            # HID SET_REPORT, Output, unnumbered — the same shape ASM uses.
            dev.ctrl_transfer(0x21, 0x09, 0x0200, num, [STATUS_COMMAND] + [0] * 63)
        except usb.core.USBError as exc:
            print(f"interface {num}: write failed ({exc})")
            _reattach(dev, num, detached)
            continue

        data = None
        for ep in intf:
            if ep.bEndpointAddress & 0x80 and ep.wMaxPacketSize >= 64:
                try:
                    data = dev.read(ep.bEndpointAddress, ep.wMaxPacketSize, timeout=1500)
                except usb.core.USBError as exc:
                    print(f"interface {num}: no answer on ep "
                          f"{ep.bEndpointAddress:#04x} ({exc})")
                break

        if data:
            print(f"interface {num}: {len(data)} bytes")
            _dump(bytes(data))
            print()
        _reattach(dev, num, detached)

    print("Turn the dial and run this again. The pair of bytes that move in")
    print("opposite directions is hp_speaker_game / hp_speaker_chat.")
    return 0


def _dump(data: bytes) -> None:
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        print(f"  {off:3d}: " + " ".join(f"{b:02x}" for b in row))
    # The two readings that differ by one byte, so the answer is visible without
    # counting: whichever line names the command at its command slot is right.
    print(f"  → byte 0 = {data[0]:#04x}, byte 1 = {data[1]:#04x} "
          f"(command is {STATUS_COMMAND:#04x}: if it is at byte 0 the report id "
          f"is not on the wire, if at byte 1 it is)")


def _reattach(dev, num: int, detached: bool) -> None:
    if not detached:
        return
    try:
        dev.attach_kernel_driver(num)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
