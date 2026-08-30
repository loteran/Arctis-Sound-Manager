#!/usr/bin/env python3
"""Watch the Arctis 7's ChatMix dial as you turn it, and print what it reports.

Why this exists. SteelSeries' own specification for the Arctis 7 dongle declares
the dial as a *read*, not as something the device announces:

    (struct game_chat_status
        (outgoing (field report_id uint8 (constant 0x06))
                  (field command   uint8 (constant 0x24)))
        (incoming (field report_id uint8 (constant 0x06))
                  (field command   uint8 (constant 0x24))
                  (field game uint8)
                  (field chat uint8)))

So the position is readable — but only if something asks, and nothing arrives
on its own when the dial turns. ASM asked once, at startup, and never again,
which is why the mix it showed was frozen (#220). Before trusting the fix, it
is worth seeing on real hardware which bytes actually move, over what range,
and whether they move together.

    systemctl --user stop arctis-manager     # it holds the interface
    python3 arctis7_chatmix_probe.py
    # sweep the dial slowly from one end to the other, then Ctrl-C
    systemctl --user start arctis-manager

Paste the whole output into the issue. Read-only: it sends one query per pass
and reads the answer. Nothing is written to the device, no setting changes.

Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

VENDOR = 0x1038
#: Every product ASM drives with the arctis_7 profile.
PRODUCTS = {
    0x1260: "Arctis 7",
    0x12ad: "Arctis 7 2019",
    0x1252: "Arctis Pro 2019",
}
SERVICE_NAME = "arctis-manager"
QUERY = [0x06, 0x24]
PAYLOAD_LENGTH = 31          # (chunk HIDIO 31) in the spec, and ASM's padding
PROFILE_INTERFACE = 5        # what arctis_7.yaml declares
POLL_PERIOD_S = 0.25


def main() -> int:
    try:
        import usb.core
        import usb.util  # noqa: F401 — imported for its side effects on some builds
    except ImportError:
        print("pyusb is missing. Install it, or run this from an ASM checkout.")
        return 1

    if not _daemon_is_out_of_the_way():
        return 1

    dev = product = None
    for pid, name in PRODUCTS.items():
        dev = usb.core.find(idVendor=VENDOR, idProduct=pid)
        if dev is not None:
            product = f"{name} ({VENDOR:#06x}:{pid:#06x})"
            break
    if dev is None:
        print("No Arctis 7 dongle found. Is it plugged in?")
        return 1
    print(f"Found {product}")

    try:
        cfg = dev.get_active_configuration()
    except usb.core.USBError as exc:
        print(f"Cannot read the configuration: {exc}")
        print("Permissions? The udev rules ship with ASM; try `asm-setup`.")
        return 1

    # The profile's interface first, then any other HID interface with an IN
    # endpoint — the USB layout is in no SteelSeries specification, and a
    # profile shared by several products can name an interface only some of
    # them have (#213).
    interfaces = sorted(
        (i for i in cfg if any(ep.bEndpointAddress & 0x80 for ep in i)),
        key=lambda i: (i.bInterfaceNumber != PROFILE_INTERFACE, i.bInterfaceNumber),
    )
    for intf in interfaces:
        endpoint = next(ep for ep in intf if ep.bEndpointAddress & 0x80)
        if _watch(dev, intf.bInterfaceNumber, endpoint):
            return 0
        print(f"interface {intf.bInterfaceNumber}: no usable answer, trying the next one\n")

    print("No interface answered the dial query.")
    print("Is the ASM daemon still running? `systemctl --user stop arctis-manager`.")
    return 1


def _daemon_is_out_of_the_way() -> bool:
    """Refuse to run while the ASM daemon holds the same interface.

    Two processes cannot claim it at once, and the loser gets EIO in a loop
    rather than an error that says so. Deliberately a short, standalone check:
    this file is meant to be downloaded on its own. The fuller version — the
    one that offers to stop and restart the daemon for you, and that knows
    about containers where the service manager is out of reach — lives in
    gamebuds_battery_probe.py in this same folder.
    """
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        stop = f"systemctl --user stop {SERVICE_NAME}"
        start = f"systemctl --user start {SERVICE_NAME}"
        query = ["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"]
        # Exact match on purpose: "inactive" contains "active".
        is_running = lambda out: out.strip() == "active"  # noqa: E731
    elif shutil.which("dinitctl"):
        stop = f"dinitctl --user stop {SERVICE_NAME}"
        start = f"dinitctl --user start {SERVICE_NAME}"
        query = ["dinitctl", "--user", "status", SERVICE_NAME]
        is_running = lambda out: "STARTED" in out  # noqa: E731
    else:
        print(f"Could not tell whether the ASM daemon ({SERVICE_NAME}) is running.")
        print("Stop it before running this, and start it again afterwards.\n")
        return True

    try:
        out = subprocess.run(query, capture_output=True, text=True, timeout=15).stdout or ""
    except Exception:  # noqa: BLE001 — an unreadable service manager is not a reason to stop
        return True
    if not is_running(out):
        return True

    print("The ASM daemon is running and holds this dongle's USB interface.")
    print("Two processes cannot claim it at once, so this script will not fight it.\n")
    print(f"  {stop}")
    print(f"  python3 {Path(__file__).name}")
    print(f"  {start}")
    return False


def _watch(dev, number: int, endpoint) -> bool:
    """Poll one interface until Ctrl-C. False if it never answers."""
    import usb.core

    detached = False
    try:
        if dev.is_kernel_driver_active(number):
            dev.detach_kernel_driver(number)
            detached = True
    except usb.core.USBError:
        pass

    answered = False
    previous: tuple[int, ...] | None = None
    print(f"interface {number}, endpoint {endpoint.bEndpointAddress:#04x} — "
          f"sweep the dial slowly, then Ctrl-C")
    try:
        while True:
            try:
                dev.ctrl_transfer(
                    0x21, 0x09, 0x0200, number,
                    QUERY + [0] * (PAYLOAD_LENGTH - len(QUERY)))
                data = bytes(dev.read(endpoint.bEndpointAddress,
                                      endpoint.wMaxPacketSize, timeout=1000))
            except usb.core.USBError:
                if not answered:
                    break
                # A dropped read is not worth stopping a sweep over, but it
                # must not turn the loop into a spin either.
                time.sleep(POLL_PERIOD_S)
                continue
            if not data:
                continue
            answered = True
            # Print only what changed: a sweep is then a handful of lines
            # rather than hundreds of identical ones.
            head = tuple(data[:8])
            if head != previous:
                previous = head
                print("  " + " ".join(f"{b:02x}" for b in head)
                      + f"    (bytes 2/3 = {data[2]:3d} / {data[3]:3d})")
            time.sleep(POLL_PERIOD_S)
    except KeyboardInterrupt:
        print("\nStopped. Paste everything above into the issue —")
        print("including where the dial sat at each end of the sweep.")
    finally:
        if detached:
            try:
                dev.attach_kernel_driver(number)
            except Exception:  # noqa: BLE001
                pass
    return answered


if __name__ == "__main__":
    sys.exit(main())
