#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find out whether the Arctis GameBuds report a battery level at all (#202).

`devices/gamebuds.yaml` says at the top why the tray never shows a percentage
for this family: there is no known `status.request` for it, so ASM has never
had anything to ask. That is not the same claim as "the buds never say
anything" — it just means nobody has listened yet. This script listens, and
if that finds nothing, it can also ask.

Two phases. The first is always safe; the second writes to the device and
only runs if you pass a flag for it.

  1. PASSIVE LISTENING (default). Reads whatever the buds push on their own
     while you go through a short scripted sequence — case in, case out, mic
     mute, idle — with each captured frame labelled by the action that was
     happening. Nothing is sent to the device in this phase. If a byte in a
     frame stays the same while one action is happening and changes to a
     different, still-steady value once you switch to another action, that
     is a candidate: a state byte, not noise. Whether that state turns out to
     be battery, connection, or something else still needs a human to look at
     the numbers — this only narrows down which byte to look at.

  2. ACTIVE PROBING (--send-status-opcodes, opt-in). Sends the status-request
     opcodes that other Arctis families use (0xb0, 0x01b0, 0x06b0, 0x0020,
     0x0612, 0x0618, 0x41aa — collected from every `status.request` in
     devices/*.yaml) and records which ones, if any, get a reply. THIS WRITES
     TO THE DEVICE. It is not known to be harmless on this family — it has
     simply never been tried. An opcode answering only proves a reply exists;
     it says nothing about what any byte in that reply means. Do not carry a
     response_mapping over from another family just because the same opcode
     answered — that mistake already shipped once, as nova_7_discrete_battery
     misreading a WoW-upgrade dongle's battery byte with the wrong headset's
     scale.

The daemon problem: the ASM daemon holds this same USB interface whenever it
is running, and a second process fighting it for that interface is exactly
the failure discussion #203's boot-race analysis is about. This script checks
whether the daemon is running before it ever touches the device, tells you
the one command to stop it, offers to do that and restart it for you when
finished, and refuses to run rather than contest the interface if you decline
or if it cannot tell (which can happen inside a Distrobox container — the
host's service manager is not reachable from there).

Works from a checkout, ASM does not need to be installed:

    cd scripts/reverse-engineering
    python3 gamebuds_battery_probe.py

Needs only the standard library and pyusb (already an ASM dependency —
`pip install --user pyusb`, or your distro's python-pyusb package, e.g.
`python3 -m pip install --user pyusb` inside a Distrobox container).

Usage:
    python3 gamebuds_battery_probe.py                       # passive capture only (safe)
    python3 gamebuds_battery_probe.py --send-status-opcodes  # also try opcodes from other families (writes to the device)
    python3 gamebuds_battery_probe.py --seconds 25           # longer listening window per step
    python3 gamebuds_battery_probe.py --assume-yes           # answer prompts yes automatically (still prints every warning)

Everything printed is also saved to a timestamped file in the current
directory, and the run ends with a short summary meant to be pasted straight
into the issue.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing — install python-pyusb (distro package) or "
             "pip install --user pyusb")

VENDOR = 0x1038
PRODUCTS = {
    0x230A: "Arctis GameBuds (PS5/PC)",
    0x2317: "Arctis GameBuds X (Xbox)",
}
INTERFACE = 3
ALT_SETTING = 0
COMMAND_PADDING_LENGTH = 64
COMMAND_PADDING_FILLER = 0x00
READ_TIMEOUT_MS = 1000
SEND_TIMEOUT_MS = 1000
SERVICE_NAME = "arctis-manager"

# Status-request opcodes already known to work on *other* Arctis families —
# every distinct `status.request` value under src/arctis_sound_manager/devices/
# except 0x0000 (the Nova 3's "wired, no polling needed" placeholder, which is
# not a real request). Trying one of these against the GameBuds only tells you
# whether *something* answers it; it never tells you what the answer means.
KNOWN_STATUS_OPCODES: tuple[int, ...] = (
    0x00B0, 0x01B0, 0x06B0, 0x0020, 0x0612, 0x0618, 0x41AA,
)

# The scripted passive-listening sequence: (label, prompt, default seconds).
# Ordered to produce the most physically distinct states with the fewest
# steps — every "into the case" is followed immediately by its own "out of
# the case" so a byte that only reflects being-in-the-case shows up as a
# steady value across exactly those two neighbouring steps.
ACTIONS: tuple[tuple[str, str, int], ...] = (
    ("baseline",
     "Wear both buds, connected to this PC, and do not touch anything.", 20),
    ("right_to_case",
     "Take the RIGHT bud out and place it in the open case.", 15),
    ("right_from_case",
     "Take the RIGHT bud back out of the case and put it back in your ear.", 15),
    ("left_to_case",
     "Take the LEFT bud out and place it in the open case.", 15),
    ("left_from_case",
     "Take the LEFT bud back out of the case and put it back in your ear.", 15),
    ("mic_mute",
     "If the buds have a mute button/gesture, mute the microphone now "
     "(otherwise just wait).", 15),
    ("both_to_case_open",
     "Put BOTH buds in the case. Leave the lid open.", 15),
    ("case_closed",
     "Close the case lid.", 15),
    ("both_from_case",
     "Open the lid, take both buds out, and put them back on.", 15),
    ("idle_end",
     "Wear both buds again and do not touch anything — final idle read.", 20),
)


# ── frames and the pure logic over them ────────────────────────────────────

@dataclass(frozen=True)
class CapturedFrame:
    """One frame read from the device, tagged with when and during what."""
    elapsed: float
    label: str
    data: tuple[int, ...]

    @property
    def kind(self) -> tuple[int, int]:
        """Group key for "the same kind of frame": length and first byte.

        Different SteelSee status/push frames are usually distinguished this
        way (see `starts_with` in the YAML profiles) — mixing frame kinds
        together before diffing would compare unrelated byte layouts.
        """
        return (len(self.data), self.data[0] if self.data else -1)


def hexs(data) -> str:
    return " ".join(f"{b:02x}" for b in data)


def format_frame(frame: CapturedFrame) -> str:
    return f"+{frame.elapsed:6.1f}s  [{frame.label:<18}] {hexs(frame.data)}"


def group_by_kind(frames: list[CapturedFrame]) -> dict[tuple[int, int], list[CapturedFrame]]:
    groups: dict[tuple[int, int], list[CapturedFrame]] = {}
    for f in frames:
        groups.setdefault(f.kind, []).append(f)
    return groups


@dataclass
class ByteReport:
    """What one byte offset did across the whole capture, for one frame kind."""
    offset: int
    values_by_label: dict[str, set[int]]

    @property
    def noisy(self) -> bool:
        """Changed even within a single action — a counter/sequence byte,
        not something that reads back a stable state."""
        return any(len(v) > 1 for v in self.values_by_label.values())

    @property
    def varies_across_labels(self) -> bool:
        """Settled (stable-within-action) value differs between two actions."""
        settled = {next(iter(v)) for v in self.values_by_label.values() if len(v) == 1}
        return len(settled) > 1

    @property
    def is_candidate(self) -> bool:
        """Stable during each action, but not the same value across all of
        them — the pattern a connection/case/battery state byte produces."""
        return self.varies_across_labels and not self.noisy


def analyze_bytes(frames: list[CapturedFrame]) -> list[ByteReport]:
    """Per-offset behaviour across one frame *kind*. Pass only same-kind frames."""
    if not frames:
        return []
    length = len(frames[0].data)
    reports = []
    for offset in range(length):
        values_by_label: dict[str, set[int]] = {}
        for f in frames:
            values_by_label.setdefault(f.label, set()).add(f.data[offset])
        reports.append(ByteReport(offset, values_by_label))
    return reports


def verdict(frames: list[CapturedFrame]) -> str:
    """'no_frames', 'nothing_varies', or 'candidates_found' — drives the summary."""
    if not frames:
        return "no_frames"
    for kind_frames in group_by_kind(frames).values():
        if any(r.is_candidate for r in analyze_bytes(kind_frames)):
            return "candidates_found"
    return "nothing_varies"


def summarize_passive(frames: list[CapturedFrame]) -> list[str]:
    v = verdict(frames)
    lines: list[str] = []
    if v == "no_frames":
        lines += [
            "No frames were received on the listen interface during any of the "
            "actions.",
            "Either the GameBuds push nothing on their own on this interface, "
            "or something",
            "else on this machine is reading it first (check the daemon is "
            "really stopped).",
            "Active probing (--send-status-opcodes) is the next thing to try.",
        ]
    elif v == "nothing_varies":
        lines += [
            f"{len(frames)} frame(s) were received, but no byte in them changed "
            "between",
            "any of the actions performed. Two explanations fit that:",
            "  - these are heartbeat/ack frames that carry no state at all, or",
            "  - the battery level itself did not move enough during this "
            "session to",
            "    show up — a battery byte can be the only byte that is steady "
            "within",
            "    each action, and a single short capture would show it as "
            "'constant'",
            "    if the charge barely changed.",
            "If you can, repeat this capture once shortly after a full charge "
            "and once",
            "when the buds are low — that comparison is what a battery byte "
            "would show.",
        ]
    else:
        lines.append(
            "Found byte(s) that stayed steady during each action but changed "
            "between")
        lines.append("actions — candidates for a state (case in/out, "
                      "connection, possibly battery):")
        for (length, first_byte), kind_frames in group_by_kind(frames).items():
            candidates = [r for r in analyze_bytes(kind_frames) if r.is_candidate]
            if not candidates:
                continue
            lines.append(
                f"\n  frame kind: length={length}, first byte=0x{first_byte:02x} "
                f"({len(kind_frames)} frame(s) seen)")
            for r in candidates:
                per_label = ", ".join(
                    f"{label}=0x{next(iter(vals)):02x}"
                    for label, vals in r.values_by_label.items()
                )
                lines.append(f"    byte[{r.offset}]: {per_label}")
        lines.append(
            "\nThese are candidates, not a decoded meaning — check them "
            "against a second")
        lines.append(
            "capture at a different battery level before writing a "
            "response_mapping entry.")
    return lines


def summarize_active(frames: list[CapturedFrame], opcodes_tried: tuple[int, ...]) -> list[str]:
    lines = ["Active probe results:"]
    answered = {f.label for f in frames}
    any_answer = False
    for opcode in opcodes_tried:
        label = f"opcode_0x{opcode:04x}"
        if label in answered:
            n = sum(1 for f in frames if f.label == label)
            lines.append(f"  0x{opcode:04x}: ANSWERED ({n} frame(s))")
            any_answer = True
        else:
            lines.append(f"  0x{opcode:04x}: no reply")
    if any_answer:
        lines += [
            "",
            "An opcode answering is a real finding — that becomes "
            "status.request in the YAML.",
            "Do NOT copy a response_mapping from another family onto it: only "
            "the fact that",
            "a reply exists is confirmed here, not what any byte in it means.",
        ]
    return lines


def build_status_request(opcode: int) -> list[int]:
    """The status request, padded exactly the way gamebuds.yaml's
    command_padding (length 64, position end, filler 0x00) pads any command."""
    command = [(opcode >> 8) & 0xFF, opcode & 0xFF] if opcode > 0xFF else [opcode]
    if len(command) < COMMAND_PADDING_LENGTH:
        command = command + [COMMAND_PADDING_FILLER] * (COMMAND_PADDING_LENGTH - len(command))
    return command


# ── daemon guard ────────────────────────────────────────────────────────────

class DaemonGuardRefused(Exception):
    """Raised when it is not safe to claim the interface — the caller must
    not touch the device after this."""


def have_systemd() -> bool:
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").is_dir()


def have_dinit() -> bool:
    return shutil.which("dinitctl") is not None


def _subprocess_run(argv: list[str]):
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def daemon_state(run=_subprocess_run, use_systemd: bool | None = None,
                  use_dinit: bool | None = None) -> str:
    """'running', 'stopped', or 'unknown' (can't tell from here — e.g. inside
    a container without the host's service manager)."""
    use_systemd = have_systemd() if use_systemd is None else use_systemd
    use_dinit = have_dinit() if use_dinit is None else use_dinit
    if use_systemd:
        try:
            r = run(["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"])
            return "running" if (r.stdout or "").strip() == "active" else "stopped"
        except Exception:
            return "unknown"
    if use_dinit:
        try:
            r = run(["dinitctl", "--user", "status", SERVICE_NAME])
            return "running" if "STARTED" in (r.stdout or "") else "stopped"
        except Exception:
            return "unknown"
    return "unknown"


def daemon_control(action: str, run=_subprocess_run, use_systemd: bool | None = None,
                    use_dinit: bool | None = None) -> bool:
    """Ask the daemon to start/stop. True only means the command was issued
    without raising — callers re-check with daemon_state()."""
    use_systemd = have_systemd() if use_systemd is None else use_systemd
    use_dinit = have_dinit() if use_dinit is None else use_dinit
    try:
        if use_systemd:
            run(["systemctl", "--user", action, f"{SERVICE_NAME}.service"])
            return True
        if use_dinit:
            run(["dinitctl", "--user", action, SERVICE_NAME])
            return True
    except Exception:
        pass
    return False


def daemon_stop_command() -> str:
    if have_systemd():
        return f"systemctl --user stop {SERVICE_NAME}"
    if have_dinit():
        return f"dinitctl --user stop {SERVICE_NAME}"
    return f"stop whatever runs the ASM daemon on this system ({SERVICE_NAME})"


def daemon_start_command() -> str:
    if have_systemd():
        return f"systemctl --user start {SERVICE_NAME}"
    if have_dinit():
        return f"dinitctl --user start {SERVICE_NAME}"
    return f"restart the ASM daemon ({SERVICE_NAME})"


def ensure_daemon_not_running(ask=input, run=_subprocess_run,
                               use_systemd: bool | None = None,
                               use_dinit: bool | None = None,
                               sleep=time.sleep) -> bool:
    """Make it safe to claim the interface, or raise DaemonGuardRefused.

    Returns True if this call stopped the daemon (the caller must restart it
    when done), False if there was nothing to stop.
    """
    state = daemon_state(run, use_systemd, use_dinit)

    if state == "stopped":
        return False

    if state == "unknown":
        print(f"Could not tell whether the ASM daemon ({SERVICE_NAME}) is "
              "running from here.")
        print("(This happens inside a Distrobox/container — the host's "
              "service manager isn't reachable from in here.)")
        print(f"If it IS running on the host, stop it first:  "
              f"{daemon_stop_command()}")
        answer = ask("Type 'y' once you're sure it is stopped, anything else "
                      "cancels: ").strip().lower()
        if answer != "y":
            raise DaemonGuardRefused("could not confirm the daemon is stopped")
        return False

    # state == "running"
    print(f"The ASM daemon ({SERVICE_NAME}) is running and holds the "
          "GameBuds' USB interface.")
    print("A second process cannot claim it at the same time — that is "
          "exactly the failure")
    print("discussion #203's boot-race analysis describes — so this script "
          "will not fight it")
    print("for the interface.")
    print(f"\nStop it yourself first if you'd rather:  {daemon_stop_command()}")
    answer = ask("Or let this script stop it now and restart it automatically "
                  "when finished? [y/N] ").strip().lower()
    if answer != "y":
        raise DaemonGuardRefused("the daemon is running and was not stopped")

    if not daemon_control("stop", run, use_systemd, use_dinit):
        raise DaemonGuardRefused("could not stop the daemon automatically")
    sleep(1)
    if daemon_state(run, use_systemd, use_dinit) == "running":
        raise DaemonGuardRefused("asked the daemon to stop, but it is still running")
    return True


# ── USB plumbing ─────────────────────────────────────────────────────────────

def find_device():
    """(device, name, product_id) for the first GameBuds found, or (None, None, None).

    Read-only: usb.core.find() only enumerates the bus, it does not open or
    claim anything. Safe to call regardless of what else is using the device.
    """
    for pid, name in PRODUCTS.items():
        dev = usb.core.find(idVendor=VENDOR, idProduct=pid)
        if dev is not None:
            return dev, name, pid
    return None, None, None


def take_interface(device) -> bool:
    """Detach usbhid and claim interface 3, as the daemon does on startup."""
    try:
        if device.is_kernel_driver_active(INTERFACE):
            device.detach_kernel_driver(INTERFACE)
    except usb.core.USBError as exc:
        print(f"Cannot detach the kernel driver from interface {INTERFACE}: {exc}")
        if getattr(exc, "errno", None) == 13:
            print("That's a permissions error — the udev rules ASM installs "
                  "are what grant this.")
        return False

    try:
        usb.util.claim_interface(device, INTERFACE)
    except usb.core.USBError as exc:
        print(f"Cannot claim interface {INTERFACE}: {exc}")
        if getattr(exc, "errno", None) == 16:
            print("Something else is holding the buds — another ASM daemon, "
                  "headsetcontrol, a VM with USB passthrough...")
        return False
    return True


def give_interface_back(device) -> None:
    try:
        usb.util.release_interface(device, INTERFACE)
    except usb.core.USBError:
        pass
    try:
        device.attach_kernel_driver(INTERFACE)
    except (usb.core.USBError, NotImplementedError):
        pass


def find_in_endpoint(device):
    """(address, max packet size) of interface 3's IN endpoint, or None."""
    for config in device:
        for interface in config:
            if interface.bInterfaceNumber != INTERFACE or interface.bAlternateSetting != ALT_SETTING:
                continue
            for endpoint in interface:
                if usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    return endpoint.bEndpointAddress, endpoint.wMaxPacketSize
    return None


def listen_for(device, ep_in: int, packet_size: int, seconds: float, label: str,
                start_time: float, on_frame=lambda f: None) -> list[CapturedFrame]:
    frames: list[CapturedFrame] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            data = tuple(device.read(ep_in, packet_size, READ_TIMEOUT_MS))
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 110:  # timeout, not a fault
                continue
            print(f"  read failed: {exc}")
            break
        if data:
            frame = CapturedFrame(time.monotonic() - start_time, label, data)
            frames.append(frame)
            on_frame(frame)
    return frames


def send_status_request(device, opcode: int, log) -> bool:
    """Send one status-request opcode the way gamebuds.yaml sends any command
    (ctrl_output: HID SET_REPORT, wIndex=3, OUTPUT report type, no report id)."""
    command = build_status_request(opcode)
    try:
        device.ctrl_transfer(
            usb.util.build_request_type(
                direction=usb.util.CTRL_OUT,
                type=usb.util.CTRL_TYPE_CLASS,
                recipient=usb.util.CTRL_RECIPIENT_INTERFACE,
            ),
            0x09, (0x02 << 8) | 0x00, INTERFACE, command,
            timeout=SEND_TIMEOUT_MS,
        )
        return True
    except usb.core.USBError as exc:
        log(f"    the buds refused opcode 0x{opcode:04x}: {exc}")
        return False


def run_passive_capture(device, ep_in: int, packet_size: int, seconds_override,
                         log) -> list[CapturedFrame]:
    frames: list[CapturedFrame] = []
    start = time.monotonic()
    for label, prompt, default_seconds in ACTIONS:
        duration = seconds_override or default_seconds
        log(f"\n>>> {prompt}")
        log(f"    (listening for {duration}s — go ahead now)")
        step_frames = listen_for(
            device, ep_in, packet_size, duration, label, start,
            on_frame=lambda f: log(f"    {format_frame(f)}"))
        log(f"    {len(step_frames)} frame(s) captured during this step")
        frames.extend(step_frames)
    return frames


def run_active_probe(device, ep_in: int, packet_size: int, log) -> list[CapturedFrame]:
    frames: list[CapturedFrame] = []
    start = time.monotonic()
    for opcode in KNOWN_STATUS_OPCODES:
        label = f"opcode_0x{opcode:04x}"
        log(f"\n>>> sending 0x{opcode:04x} ...")
        if not send_status_request(device, opcode, log):
            continue
        step_frames = listen_for(
            device, ep_in, packet_size, 2, label, start,
            on_frame=lambda f: log(f"    {format_frame(f)}"))
        log(f"    ANSWERED ({len(step_frames)} frame(s))" if step_frames else "    no reply")
        frames.extend(step_frames)
        time.sleep(0.3)
    return frames


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Find out whether the Arctis GameBuds report a battery "
                     "level, and where (#202).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--send-status-opcodes", action="store_true",
        help="ALSO send status-request opcodes used by other Arctis families "
             "and record which ones get a reply. This WRITES to the device. "
             "Opt-in; off by default.")
    ap.add_argument(
        "--seconds", type=int, default=None,
        help="override the listening time for every scripted step "
             "(default varies per step, 15-20s)")
    ap.add_argument(
        "--assume-yes", action="store_true",
        help="answer every safety prompt with yes, for scripted/unattended "
             "runs — every warning is still printed first")
    return ap


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    log_lines: list[str] = []

    def log(msg: str = "") -> None:
        print(msg)
        log_lines.append(msg)

    ask = (lambda _prompt: "y") if args.assume_yes else input

    log(__doc__.split("Usage:")[0].strip())
    log("=" * 72)

    stopped_daemon = False
    passive_frames: list[CapturedFrame] = []
    active_frames: list[CapturedFrame] = []
    device = None

    try:
        try:
            stopped_daemon = ensure_daemon_not_running(ask=ask)
        except DaemonGuardRefused as exc:
            log(f"\nRefusing to run: {exc}")
            log("Re-run once the daemon is stopped, or answer 'y' to let "
                "this script do it.")
            return 1

        device, name, pid = find_device()
        if device is None:
            known = ", ".join(f"1038:{pid:04x}" for pid in PRODUCTS)
            log(f"\nNo Arctis GameBuds found on the USB bus (looked for {known}).")
            log("Is it plugged in / connected through its 2.4 GHz dongle?")
            return 1
        log(f"\nFound {name} (1038:{pid:04x})")

        if not take_interface(device):
            log("\nCould not take the HID interface — see the message above.")
            return 1

        try:
            endpoint = find_in_endpoint(device)
            if endpoint is None:
                log(f"\nInterface {INTERFACE} exposes no IN endpoint — "
                    "nothing can be read from it.")
                return 1
            ep_in, packet_size = endpoint
            log(f"Listening on endpoint 0x{ep_in:02x}, max packet {packet_size}\n")

            log("=" * 72)
            log("PHASE 1 — passive listening (nothing is written to the device)")
            log("=" * 72)
            log("Follow each prompt when it appears. Every frame the buds "
                "send on their own")
            log("is recorded with a timestamp and the action that was "
                "happening.")
            passive_frames = run_passive_capture(device, ep_in, packet_size,
                                                  args.seconds, log)

            log("\n" + "=" * 72)
            log("PHASE 1 RESULT")
            log("=" * 72)
            for line in summarize_passive(passive_frames):
                log(line)

            if args.send_status_opcodes:
                log("\n" + "=" * 72)
                log("PHASE 2 — active probing (opt-in, WRITES to the device)")
                log("=" * 72)
                log(f"About to send {len(KNOWN_STATUS_OPCODES)} "
                    "status-request opcodes used by other Arctis families")
                log("to these GameBuds, one at a time, and record whether "
                    "each gets a reply:")
                log("  " + ", ".join(f"0x{o:04x}" for o in KNOWN_STATUS_OPCODES))
                log("\nThis is a real write to the headset's firmware. These "
                    "opcodes are read-only")
                log("status requests on the families that use them and "
                    "SteelSeries' own software")
                log("sends them routinely, but the GameBuds have never been "
                    "probed this way")
                log("before, so this is not guaranteed harmless. If "
                    "something does answer, that")
                log("only proves a reply exists — it does NOT say what any "
                    "byte in it means; this")
                log("repo has already shipped a wrong battery reading once "
                    "from assuming a")
                log("reply decodes the same way on two different headsets "
                    "(nova_7_discrete_battery).")
                answer = ask("\nProceed with the active probe? [y/N] ").strip().lower()
                if answer == "y":
                    active_frames = run_active_probe(device, ep_in, packet_size, log)
                    log("\n" + "=" * 72)
                    log("PHASE 2 RESULT")
                    log("=" * 72)
                    for line in summarize_active(active_frames, KNOWN_STATUS_OPCODES):
                        log(line)
                else:
                    log("Skipped.")
        finally:
            log("\nHanding the interface back to the kernel.")
            give_interface_back(device)

        report_path = (Path.cwd() /
                        f"gamebuds-battery-probe-{datetime.now():%Y%m%d-%H%M%S}.txt")
        try:
            report_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            print(f"\nFull capture saved to: {report_path}")
        except OSError as exc:
            print(f"\nCould not save the report ({exc}) — copy the terminal "
                  "output instead.")

        print("\n" + "=" * 72)
        print("SUMMARY TO PASTE INTO THE ISSUE")
        print("=" * 72)
        for line in summarize_passive(passive_frames):
            print(line)
        if active_frames or args.send_status_opcodes:
            print()
            for line in summarize_active(active_frames, KNOWN_STATUS_OPCODES):
                print(line)
        print(f"\nFull log: {report_path.name}  (please attach this file too)")
        return 0
    finally:
        if stopped_daemon:
            print(f"\nRestarting the ASM daemon ({daemon_start_command()})...")
            daemon_control("start")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
