#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find out why ASM reads nothing from a headset that is plainly working.

Symptom this exists for: the battery never appears — not in the tray, not on
the DAC screen — while audio plays perfectly and nothing at all is logged. The
daemon's GetStatus answers with exactly this and nothing else:

    {"headset": {"headset_power_status": {"value": "online", "type": "label"}}}

That payload is a *fallback* ASM injects when it has parsed nothing. It is not
a reading. So the real question is where, between the device and the parser,
the status is being lost — and the daemon cannot tell you, because a read that
returns nothing is indistinguishable from an idle device.

This walks the same path the daemon walks, one step at a time, and says what
happened at each. The steps are ordered so that the first failure *is* the
answer:

  1. Which SteelSeries devices are on the bus, and does a profile claim them.
  2. Can we open the device at all, or are the udev rules not applied here.
  3. Who owns the HID interface right now, and can we take it.
  4. Does the interface expose the endpoints the profile expects.
  5. LISTEN — does the device send anything on its own when you turn a dial.
  6. ASK — does it answer the profile's status request.
  7. Does what came back match a response_mapping, and does it parse.

Run it with the daemon stopped, so the two are not fighting over the device:

    systemctl --user stop arctis-manager
    python3 status_probe.py
    systemctl --user start arctis-manager

Reads and writes only status frames — no setting is changed, and the device is
handed back to the kernel at the end.
"""
from __future__ import annotations

import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing — install python-pyusb (or pip install --user pyusb)")

try:
    from arctis_sound_manager.config import load_device_configurations
except ImportError:
    sys.exit("Run this on a machine with Arctis Sound Manager installed.")

VENDOR = 0x1038
LISTEN_SECONDS = 12
READ_TIMEOUT_MS = 1000


# ── output helpers ────────────────────────────────────────────────────────────

def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def ok(msg: str) -> None:
    print(f"    OK    {msg}")


def bad(msg: str) -> None:
    print(f"    FAIL  {msg}")


def info(msg: str) -> None:
    print(f"          {msg}")


def hexs(data) -> str:
    return " ".join(f"{b:02x}" for b in data)


# ── the steps ─────────────────────────────────────────────────────────────────

def find_devices(configs) -> list:
    """Every SteelSeries device present, paired with the profile claiming it."""
    found = []
    for device in usb.core.find(find_all=True, idVendor=VENDOR) or []:
        config = next((c for c in configs if device.idProduct in c.product_ids), None)
        found.append((device, config))
    return found


def check_access(device) -> bool:
    """Whether this process may talk to the device at all.

    Reading a string descriptor is the cheapest request that actually goes to
    the hardware, so it fails exactly where a missing udev rule would.
    """
    try:
        if not getattr(device, "langids", None):
            device._langids = (1033,)
        info(f"product string: {device.product!r}")
        return True
    except usb.core.USBError as exc:
        if getattr(exc, "errno", None) == 13:
            bad("permission denied — the udev rules are not applied to this device")
            info("asm-cli udev write-rules, then unplug and replug it")
        else:
            bad(f"cannot read from the device: {exc}")
        return False
    except ValueError as exc:
        bad(f"cannot read the device descriptors: {exc}")
        return False


def take_interface(device, interface: int) -> bool:
    """Detach usbhid and claim the interface, as the daemon does on startup."""
    try:
        active = device.is_kernel_driver_active(interface)
    except usb.core.USBError as exc:
        bad(f"cannot ask who owns interface {interface}: {exc}")
        return False

    info(f"kernel driver on interface {interface}: {'yes (usbhid)' if active else 'no'}")
    if active:
        try:
            device.detach_kernel_driver(interface)
            ok("detached the kernel driver")
        except usb.core.USBError as exc:
            bad(f"could not detach it: {exc}")
            return False

    try:
        usb.util.claim_interface(device, interface)
    except usb.core.USBError as exc:
        # EBUSY here is the interesting one: something else on this machine is
        # holding the headset, and that is the whole answer.
        bad(f"could not claim interface {interface}: {exc}")
        if getattr(exc, "errno", None) == 16:
            info("another program is holding this headset — headsetcontrol, "
                 "ggoled, a VM with USB passthrough, a second ASM daemon")
        return False
    ok(f"claimed interface {interface}")
    return True


def endpoints_of(device, interface_num: int, alt: int = 0):
    """(in, out) endpoint addresses and their max packet sizes, or (None, None)."""
    for config in device:
        for interface in config:
            if interface.bInterfaceNumber != interface_num:
                continue
            if alt is not None and interface.bAlternateSetting != alt:
                continue
            ep_in = ep_out = None
            for endpoint in interface:
                direction = usb.util.endpoint_direction(endpoint.bEndpointAddress)
                if direction == usb.util.ENDPOINT_IN and ep_in is None:
                    ep_in = (endpoint.bEndpointAddress, endpoint.wMaxPacketSize)
                elif direction == usb.util.ENDPOINT_OUT and ep_out is None:
                    ep_out = (endpoint.bEndpointAddress, endpoint.wMaxPacketSize)
            return ep_in, ep_out
    return None, None


def listen(device, ep_in: int, size: int, seconds: int,
           prompt: str | None = None) -> list[list[int]]:
    """Read whatever the device sends.

    This is the step that separates "the headset says nothing" from "the
    headset says things we do not understand", and those need entirely
    different fixes. Turning a dial is the cheapest way to make a healthy
    device speak — hence the prompt, which only step 5 uses. Step 6 is waiting
    on a reply to a request, and asking for dial turns there would just make
    the reply harder to spot.
    """
    if prompt:
        print(f"    {prompt}")
    frames: list[list[int]] = []
    deadline = time.monotonic() + seconds
    timeouts = 0

    while time.monotonic() < deadline:
        try:
            data = list(device.read(ep_in, size, READ_TIMEOUT_MS))
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 110:
                timeouts += 1
                continue
            bad(f"read failed: {exc}")
            break
        if data:
            frames.append(data)
            print(f"          <- {hexs(data[:16])}")

    info(f"{len(frames)} frame(s) received, {timeouts} idle second(s)")
    return frames


def build_request(config) -> list[int]:
    """The status request, padded exactly as core.send_command pads it."""
    request = config.status.request
    command = [(request >> 8) & 0xFF, request & 0xFF] if request > 0xFF else [request]

    padding = config.command_padding
    if padding and len(command) < padding.length:
        command = command + [padding.filler] * (padding.length - len(command))
    return command


def ask_for_status(device, config, ep_out) -> bool:
    """Send the profile's status request the way the daemon sends it."""
    command = build_request(config)
    info(f"request: {hexs(command[:8])}{' ...' if len(command) > 8 else ''}")

    try:
        if ep_out:
            device.write(ep_out, command)
        else:
            report_type = 0x03 if str(getattr(config, "command_transport", "")).endswith("FEATURE") else 0x02
            report_id = getattr(config, "command_report_id", None) or 0
            device.ctrl_transfer(
                usb.util.build_request_type(
                    direction=usb.util.CTRL_OUT,
                    type=usb.util.CTRL_TYPE_CLASS,
                    recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
                ),
                0x09, (report_type << 8) | (report_id & 0xFF),
                config.command_interface_index[0], command,
            )
        ok("the request was accepted by the device")
        return True
    except usb.core.USBError as exc:
        bad(f"the device refused the status request: {exc}")
        if getattr(exc, "errno", None) == 110:
            info("timed out — the command channel is not working on this unit")
        return False


def explain(frames: list[list[int]], config) -> None:
    """Match what arrived against the profile's response_mapping.

    A frame that arrives and matches nothing is a different bug from a frame
    that never arrives, and this is where the two become visible: it means the
    profile's `starts_with` values do not describe this firmware.
    """
    mappings = config.status.response_mapping
    known = [f"{m.starts_with:04x}" for m in mappings]
    info(f"profile expects frames starting with: {', '.join(known)}")

    matched = 0
    for frame in frames:
        as_hex = "".join(f"{b:02x}" for b in frame)
        for mapping in mappings:
            prefix = f"{mapping.starts_with:02x}"
            if len(prefix) % 2:
                prefix = "0" + prefix
            if as_hex.startswith(prefix):
                matched += 1
                values = mapping.get_status_values(frame)
                ok(f"{prefix}: {values}")
                break

    if frames and not matched:
        bad("frames arrived, but none match anything the profile knows")
        info("this firmware speaks a different status format than the YAML "
             "describes — that is the bug, and it needs the raw frames above")


def main() -> int:
    print(__doc__.split("Run it with")[0].strip())
    print("=" * 72)

    configs = load_device_configurations()

    step(1, "SteelSeries devices on the bus")
    devices = find_devices(configs)
    if not devices:
        bad("no SteelSeries device found at all — is it plugged in?")
        return 1
    for device, config in devices:
        name = config.name if config else "(no ASM profile claims this one)"
        print(f"    1038:{device.idProduct:04x}  {name}")

    supported = [(d, c) for d, c in devices if c is not None and c.status is not None]
    if not supported:
        bad("none of these has a profile with a status block — nothing to probe")
        return 1
    device, config = supported[0]
    print(f"\n    probing: {config.name} (1038:{device.idProduct:04x})")

    step(2, "Can this process talk to it")
    if not check_access(device):
        return 1
    ok("the device answers control requests")

    interface = config.command_interface_index[0]
    alt = config.command_interface_index[1] if len(config.command_interface_index) > 1 else 0
    listen_interfaces = list(config.listen_interface_indexes)

    step(3, f"Taking the HID interface (command={interface}, listen={listen_interfaces})")
    if not take_interface(device, interface):
        return 1
    for extra in listen_interfaces:
        if extra != interface:
            take_interface(device, extra)

    step(4, "Endpoints the profile expects")
    ep_in, ep_out = endpoints_of(device, listen_interfaces[0], alt)
    if ep_in is None:
        bad(f"interface {listen_interfaces[0]} exposes no IN endpoint — "
            "nothing can ever be read from it")
        info("this is a profile/firmware mismatch, and it explains an empty status")
        return 1
    ok(f"IN  endpoint 0x{ep_in[0]:02x}, max packet {ep_in[1]}")
    if ep_out:
        ok(f"OUT endpoint 0x{ep_out[0]:02x}, max packet {ep_out[1]}")
    else:
        info("no OUT endpoint — commands go through the control pipe")

    step(5, "Does the device say anything on its own")
    frames = listen(device, ep_in[0], ep_in[1], LISTEN_SECONDS,
                    prompt=f"Turn the volume wheel and the ChatMix dial for the "
                           f"next {LISTEN_SECONDS} seconds...")
    if not frames:
        # Deliberately not a FAIL: this step only means something if the dials
        # were actually turned, and the script cannot know whether they were.
        info("nothing arrived during those seconds")
        info("if you did turn the dials, that is a real finding: either the "
             "headset sends nothing on this interface, or something else is "
             "consuming its frames. If you did not, step 6 is the one that "
             "matters.")

    step(6, "Does it answer the status request")
    asked = ask_for_status(device, config, ep_out[0] if ep_out else None)
    if asked:
        reply = listen(device, ep_in[0], ep_in[1], 3,
                       prompt="waiting for the reply...")
        frames += reply
        if not reply:
            bad("the request went out, and nothing came back")
            info("the device is ignoring it — this is why the battery is never "
                 "shown, and it is what a fix has to address")

    step(7, "Does what arrived mean anything to the profile")
    if frames:
        explain(frames, config)
    else:
        info("nothing was received, so there is nothing to decode")

    print("\nHanding the device back to the kernel.")
    for iface in {interface, *listen_interfaces}:
        try:
            usb.util.release_interface(device, iface)
            device.attach_kernel_driver(iface)
        except (usb.core.USBError, NotImplementedError):
            pass
    print("Done — restart ASM with:  systemctl --user start arctis-manager")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — restart ASM with: systemctl --user start arctis-manager")
        sys.exit(130)
