#!/usr/bin/env python3
"""Capture raw USB HID events from Arctis Nova Pro Wireless.

Run: sudo python3 capture_events.py
Then press buttons on the headset (ANC toggle, wheel, etc.).
Press Ctrl+C to stop.
"""
import usb.core
import usb.util
import sys
import time

VID = 0x1038
PIDS = [0x12e0, 0x12e5]
INTERFACE = 4

def main():
    dev = None
    for pid in PIDS:
        dev = usb.core.find(idVendor=VID, idProduct=pid)
        if dev:
            print(f"Found device: {VID:#06x}:{pid:#06x}")
            break
    if not dev:
        print("Arctis Nova Pro Wireless not found")
        sys.exit(1)

    # Detach kernel driver from all interfaces
    cfg = dev.get_active_configuration()
    for intf in cfg:
        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                dev.detach_kernel_driver(intf.bInterfaceNumber)
                print(f"Detached kernel driver from interface {intf.bInterfaceNumber}")
        except Exception:
            pass

    # Find IN endpoint on interface 4
    intf = cfg[(INTERFACE, 0)]
    ep_in = None
    for ep in intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
            ep_in = ep
            break

    if not ep_in:
        print("No IN endpoint found on interface 4")
        sys.exit(1)

    print(f"Listening on endpoint {ep_in.bEndpointAddress:#04x} "
          f"(max packet: {ep_in.wMaxPacketSize})")
    print("Press buttons on the headset. Ctrl+C to stop.\n")
    print(f"{'Time':>8}  {'Raw hex bytes'}")
    print("-" * 70)

    t0 = time.monotonic()
    last_data = None
    try:
        while True:
            try:
                data = dev.read(ep_in.bEndpointAddress, ep_in.wMaxPacketSize, timeout=300)
                raw = list(data)
                # Skip repeated identical packets (status polling noise)
                if raw == last_data:
                    continue
                last_data = raw

                elapsed = time.monotonic() - t0
                hex_str = " ".join(f"{b:02x}" for b in raw[:16])
                prefix = f"{elapsed:8.2f}s"

                # Highlight 0x07 events (headset-initiated)
                if raw[0] == 0x07:
                    print(f"{prefix}  ** EVENT ** {hex_str}")
                else:
                    print(f"{prefix}  {hex_str}")

            except usb.core.USBError as e:
                if e.errno == 110:  # timeout
                    continue
                raise
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            usb.util.release_interface(dev, INTERFACE)
            dev.attach_kernel_driver(INTERFACE)
        except Exception:
            pass

if __name__ == "__main__":
    main()
