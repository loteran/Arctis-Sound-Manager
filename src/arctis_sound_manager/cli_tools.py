# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import json
from typing import Any, cast

import usb

from arctis_sound_manager.core import TypedDevice


def endpoint_type(bmAttributes):
    etype = bmAttributes & 0x3
    return {
        usb.util.ENDPOINT_TYPE_CTRL: "Control",
        usb.util.ENDPOINT_TYPE_ISO: "Isochronous",
        usb.util.ENDPOINT_TYPE_BULK: "Bulk",
        usb.util.ENDPOINT_TYPE_INTR: "Interrupt",
    }.get(etype, "Unknown")

def endpoint_direction(bEndpointAddress):
    return "IN" if usb.util.endpoint_direction(bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"


def arctis_usb_info(vendor_id: int = 0x1038, bInterfaceClass: int = 0x03):
    # find_all=True, and it matters more than it looks. Without it, find()
    # returns a *single* Device, and iterating a Device yields its
    # configurations — so this listed one SteelSeries device and then walked
    # its configs, which is why the loop below had to special-case
    # Configuration at all.
    #
    # The cost was not cosmetic. This output is the "USB HID devices" section
    # of every bug report, and it is what we read to learn which model someone
    # has. On a desk with a SteelSeries keyboard or mouse next to the headset,
    # whichever one libusb happened to return first was the only one reported
    # and the headset was simply absent — see issue #197, where a Nova Pro
    # Wired never appeared because an Apex Pro came back first.
    devices = list(usb.core.find(find_all=True, idVendor=vendor_id) or [])

    if not devices:
        raise ValueError(f"No devices found with vendor ID {vendor_id:04x}")

    for element in devices:
        device: TypedDevice
        # Kept for callers that hand in a Configuration, and because a
        # defensive cast costs nothing here.
        if isinstance(element, usb.core.Configuration):
            device = cast(TypedDevice, element.device)
        else:
            device = cast(TypedDevice, element)

        if not hasattr(device, 'langids') or not device.langids:
            device._langids = (1033,) # Fixed value for English (United States)

        try:
            manufacturer = device.manufacturer
            product = device.product
        except (usb.core.USBError, ValueError):
            # Reading the string descriptors needs access to the device node,
            # and that is all this failure proves. It says nothing about
            # whether the udev rules are installed: a PID that no rule matches
            # lands here with a perfectly valid rules file on disk, and the old
            # wording ("udev rules missing — run asm-setup") sent people
            # chasing a permission problem they did not have (#218). The bug
            # report's "USB device access" section answers the question this
            # line used to guess at, per device.
            manufacturer = "(no permission)"
            product = "(name unreadable — see 'USB device access' below)"

        print(f'{manufacturer} {product} ({device.idVendor:04x}:{device.idProduct:04x})')
        for config in device:
            print(f'\tConfiguration: {config.bConfigurationValue}')
            for interface in config:
                if interface.bInterfaceClass != bInterfaceClass:
                    continue
                print(f'\t\tHID interface (num : alt): {interface.bInterfaceNumber} : {interface.bAlternateSetting}')
                for endpoint in interface:
                    print(
                        f'\t\t\tEndpoint: {endpoint.bEndpointAddress:02x} '
                        f'Dir={endpoint_direction(endpoint.bEndpointAddress)} '
                        f'Type={endpoint_type(endpoint.bmAttributes)} '
                        f'MaxPacketSize={endpoint.wMaxPacketSize} '
                    )


# ── asm-cli tools read-hardware-eq (#146) ───────────────────────────────────
#
# Reads the parametric EQ curve back from the headset so a user reporting
# "the sliders don't do anything audible" can tell ASM apart from the
# firmware: this talks to the already-running asm-daemon over D-Bus rather
# than opening a second USB handle, because the daemon holds the command
# interface claimed exclusively — a standalone open here would just fail.

def read_hardware_eq_via_dbus(timeout: float = 5.0) -> dict[str, Any]:
    """Call the daemon's ReadHardwareEq D-Bus method, return the decoded dict.

    Never raises: connection/timeout/protocol failures come back as
    {'ok': False, 'error': ...} like every other failure mode
    CoreEngine.read_hardware_eq() itself reports, so the caller has one shape
    to handle regardless of where things went wrong.
    """
    from dbus_next.aio.message_bus import MessageBus
    from dbus_next.constants import MessageType
    from dbus_next.message import Message

    from arctis_sound_manager.constants import (DBUS_BUS_NAME,
                                                 DBUS_SETTINGS_INTERFACE_NAME,
                                                 DBUS_SETTINGS_OBJECT_PATH)

    async def _call() -> dict[str, Any]:
        bus = None
        try:
            bus = await asyncio.wait_for(MessageBus().connect(), timeout=timeout)
            reply = await asyncio.wait_for(bus.call(Message(
                destination=DBUS_BUS_NAME,
                path=DBUS_SETTINGS_OBJECT_PATH,
                interface=DBUS_SETTINGS_INTERFACE_NAME,
                member='ReadHardwareEq',
                message_type=MessageType.METHOD_CALL,
            )), timeout=timeout)
            if reply is None:
                return {'ok': False, 'error': 'dbus_no_reply'}
            if reply.message_type == MessageType.ERROR:
                return {'ok': False, 'error': f'dbus_error: {reply.body}'}
            return cast(dict[str, Any], json.loads(reply.body[0]))
        except asyncio.TimeoutError:
            return {'ok': False, 'error': 'dbus_timeout (is asm-daemon running?)'}
        except Exception as e:
            return {'ok': False, 'error': f'dbus_error: {e!r}'}
        finally:
            if bus is not None:
                bus.disconnect()

    return asyncio.run(_call())


def print_hardware_eq_readback(result: dict[str, Any]) -> int:
    """Pretty-print a ReadHardwareEq result. Returns a process exit code:
    0 when a curve was decoded, 1 otherwise (unsupported / no device / no
    reply / decode error) — a caller scripting this can tell success from
    "something to paste into the issue" without parsing the text."""
    if not result.get('ok'):
        error = result.get('error', 'unknown_error')
        if error == 'unsupported':
            print("This headset's profile does not declare an EQ read-back "
                  "command.")
            print('Either it has no on-device parametric EQ ASM can drive, or '
                  'nobody has confirmed the read-back opcodes against its own '
                  'spec yet.')
        elif error == 'no_device':
            print('No headset detected by asm-daemon. Is it connected and is '
                  'asm-daemon running?')
        else:
            print(f'Could not read the EQ back from the headset: {error}')
        return 1

    conn = result.get('connection_type', 0)
    print(f'Queried connection_type=0x{conn:02x} (0x00 = wireless — the slot '
          f'the Custom EQ sliders write to)')
    print()

    bands = result.get('bands')
    if bands is None:
        print(f"Bands: NO REPLY ({result.get('bands_error', 'unknown')})")
        print('  The headset never answered the get_eq_preset_data (0x32) '
              'query. If the Custom EQ write also goes unacknowledged, this')
        print('  points at a transport/USB problem rather than the headset '
              'ignoring a curve it actually received.')
    else:
        print(f"{'Band':>4}  {'Freq (Hz)':>9}  {'Gain (dB)':>9}  {'Q':>6}  Filter type")
        for i, b in enumerate(bands, start=1):
            print(f"{i:>4}  {b['frequency']:>9}  {b['gain_db']:>+9.1f}  "
                  f"{b['q']:>6.2f}  {b['filter_type']}")

    print()
    name = result.get('name')
    if name is None:
        print(f"Preset name: NO REPLY ({result.get('name_error', 'unknown')})")
    else:
        print(f"Preset name: {name!r}  preset_type={result.get('preset_type')} "
              f"(0=builtin, 1=custom)")

    # The raw replies matter as much as the table above. Where band 1 begins
    # is inferred from a "missing byte" note in SteelSeries' own spec, never
    # from an observed frame: if that is off by one, every figure printed
    # above is wrong and still looks like a plausible curve. Print the bytes
    # so the decoding can be checked — and so it can be fixed from a report
    # rather than from another guess.
    for label, key in (('Bands', 'bands_raw'), ('Name', 'name_raw')):
        raw = result.get(key)
        if raw:
            print()
            print(f'{label} raw reply (please include this when reporting):')
            print(f'  {raw}')

    return 0 if bands is not None else 1

