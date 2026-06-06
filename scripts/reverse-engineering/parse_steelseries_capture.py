#!/usr/bin/env python3
# Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""
Decode a USBPcap capture of SteelSeries GG and extract the HID control commands
it sent to the headset, correlated with the timestamped action log produced by
capture-omni-windows.ps1.

Usage:
    python3 parse_steelseries_capture.py omni.pcapng [omni-actions.txt]
    python3 parse_steelseries_capture.py omni.pcapng --pid 0x2290

Requires `tshark` (Arch/CachyOS: `sudo pacman -S wireshark-cli`).

SteelSeries Arctis DAC commands are HID reports whose first byte is the report
id 0x06. This script pulls every host->device payload, keeps the 0x06… ones,
groups them by opcode (the 2nd byte), and — if the action log is given — shows
which command(s) were sent right after each thing you changed in GG. That maps
"EQ band up" / "ANC on" / … to the exact bytes ASM must send.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def run_tshark(pcap: str) -> list[tuple[float, str]]:
    """Return [(epoch, hexpayload), …] for every host->device USB OUT payload."""
    if not shutil.which("tshark"):
        sys.exit("tshark not found — install it (Arch/CachyOS: sudo pacman -S wireshark-cli).")
    # Pull several possible data fields; USBPcap puts SET_REPORT / interrupt-OUT
    # bytes in usb.capdata, sometimes usbhid.data or usb.control.Data.
    fields = ["frame.time_epoch", "usb.capdata", "usbhid.data", "usb.control.Data"]
    cmd = ["tshark", "-r", pcap,
           "-Y", "usb.endpoint_address.direction == 0",  # 0 = OUT (host->device)
           "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"tshark failed:\n{out.stderr}")
    rows: list[tuple[float, str]] = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        # first non-empty data field wins; normalise "aa:bb" / "aabb" -> "aabb"
        payload = next((p for p in parts[1:] if p), "")
        payload = payload.replace(":", "").replace(" ", "").lower()
        if payload:
            rows.append((ts, payload))
    return rows


def load_actions(path: str) -> list[tuple[float, str]]:
    actions: list[tuple[float, str]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            # "2026-06-06 14:03:12.345  EQ: select preset 'Flat'"
            try:
                stamp, label = line.split("  ", 1)
                dt = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S.%f")
                # actions.txt is local time; convert to epoch (assume local tz)
                actions.append((dt.astimezone().timestamp(), label.strip()))
            except ValueError:
                continue
    return actions


def fmt(payload: str, maxbytes: int = 16) -> str:
    b = [payload[i:i + 2] for i in range(0, len(payload), 2)]
    shown = " ".join(b[:maxbytes])
    return shown + (" …" if len(b) > maxbytes else "")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pcap")
    ap.add_argument("actions", nargs="?", help="omni-actions.txt from the capture script")
    ap.add_argument("--report-id", default="06",
                    help="HID report id prefix to keep (default 06 = SteelSeries Arctis)")
    args = ap.parse_args()

    rows = run_tshark(args.pcap)
    rid = args.report_id.lower()
    cmds = [(ts, p) for ts, p in rows if p.startswith(rid)]

    print(f"OUT payloads: {len(rows)} total, {len(cmds)} starting with 0x{rid}\n")

    # ── Grouped by opcode (byte after the report id) ─────────────────────────
    groups: dict[str, list[str]] = {}
    for _, p in cmds:
        opcode = p[2:4] if len(p) >= 4 else "??"
        groups.setdefault(opcode, [])
        if p not in groups[opcode]:
            groups[opcode].append(p)
    print("== Unique commands grouped by opcode (06 XX …) ==")
    for opcode in sorted(groups):
        print(f"\n  opcode 0x{opcode}:")
        for p in groups[opcode]:
            print(f"    {fmt(p)}")

    # ── Correlated with the action log ───────────────────────────────────────
    if args.actions:
        actions = load_actions(args.actions)
        if not actions:
            print("\n(could not read any timestamped actions)")
            return
        print("\n\n== Commands sent after each action ==")
        for i, (ts, label) in enumerate(actions):
            nxt = actions[i + 1][0] if i + 1 < len(actions) else float("inf")
            window = [p for cts, p in cmds if ts - 0.3 <= cts < nxt]
            uniq = list(dict.fromkeys(window))
            print(f"\n• {label}")
            for p in uniq:
                print(f"    {fmt(p)}")
        print("\nTip: the byte(s) that change between two values of the same "
              "setting are the parameter; the leading 06 XX is the opcode for "
              "that setting's update_sequence in the device YAML.")


if __name__ == "__main__":
    main()
