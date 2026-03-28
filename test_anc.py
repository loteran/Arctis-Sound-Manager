#!/usr/bin/env python3
"""Test different ANC command sequences on the Arctis Nova Pro Wireless."""
import sys
import time
import usb.core
import usb.util

VID = 0x1038
PIDS = [0x12e0, 0x12e5]
INTERFACE = 4
PAD_LEN = 64

def find_device():
    for pid in PIDS:
        dev = usb.core.find(idVendor=VID, idProduct=pid)
        if dev:
            return dev, pid
    return None, None

def send(dev, ep_out, cmd):
    padded = cmd + [0x00] * (PAD_LEN - len(cmd))
    hex_str = " ".join(f"{b:02x}" for b in cmd)
    print(f"  Sending: {hex_str}")
    try:
        dev.write(ep_out, padded)
        return True
    except Exception as e:
        print(f"  Error: {e}")
        return False

def main():
    dev, pid = find_device()
    if not dev:
        print("Device not found")
        sys.exit(1)
    print(f"Found {VID:#06x}:{pid:#06x}")

    # Detach kernel drivers
    cfg = dev.get_active_configuration()
    for intf in cfg:
        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                dev.detach_kernel_driver(intf.bInterfaceNumber)
        except Exception:
            pass

    # Find OUT endpoint on interface 4
    intf = cfg[(INTERFACE, 0)]
    ep_out = None
    for ep in intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
            ep_out = ep.bEndpointAddress
            break
    if not ep_out:
        print("No OUT endpoint found")
        sys.exit(1)

    print(f"OUT endpoint: {ep_out:#04x}\n")

    # Test different command formats for ANC OFF (value=0)
    tests = [
        ("0x06 0x89 0x00 (original YAML)", [0x06, 0x89, 0x00]),
        ("0x06 0xbd 0x00 (event opcode)",  [0x06, 0xbd, 0x00]),
        ("0x07 0xbd 0x00 (raw event)",     [0x07, 0xbd, 0x00]),
        ("0x06 0x83 0x00 (nearby opcode)", [0x06, 0x83, 0x00]),
    ]

    for desc, cmd in tests:
        print(f"\nTest: {desc}")
        send(dev, ep_out, cmd)
        ans = input("  Did ANC turn OFF? (y/n/q): ").strip().lower()
        if ans == 'y':
            print(f"\n>>> WORKING COMMAND: {[hex(b) for b in cmd]}")
            # Also test ON
            on_cmd = cmd[:-1] + [0x02]
            print(f"\nTesting ANC ON with same format:")
            send(dev, ep_out, on_cmd)
            input("  Press Enter to continue...")
            break
        if ans == 'q':
            break

    # Cleanup
    try:
        usb.util.release_interface(dev, INTERFACE)
        for intf in cfg:
            try:
                dev.attach_kernel_driver(intf.bInterfaceNumber)
            except Exception:
                pass
    except Exception:
        pass

if __name__ == "__main__":
    main()
