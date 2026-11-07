#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Decode a USBPcap capture of SteelSeries GG and map each setting change to the
exact HID command bytes, using the timestamped action log from
capture-gg-windows.ps1.

Usage:
    python3 parse_steelseries_capture.py capture.pcapng actions.txt
    python3 parse_steelseries_capture.py capture.pcapng --device 5 --report-id 06

Requires `tshark` (Arch/CachyOS: `sudo pacman -S wireshark-cli`).

Method (differential):
  • Each action line is timestamped when the user pressed Enter, AFTER making the
    change, so the command for action i is in the window (t[i-1], t[i]].
  • The BASELINE = idle window teaches us the constant background poll commands
    (e.g. 06 b0 status reads); those, plus any command seen in most windows, are
    treated as noise and filtered out.
  • Settings are taken to several known values; the decoder groups actions by
    setting name (text before " = ") and shows, per opcode, how the parameter
    byte changes between values — that is the update_sequence for the YAML.
  • USBPcap records the whole USB tree, so the SteelSeries device is picked as
    the address that received the most commands (override with --device), and
    the report id as the leading byte most of them share (override with
    --report-id).
  • What the device sends back in each window (status frames that differ from
    the baseline) is listed too: that is where a read-back offset is checked.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


def run_tshark(pcap: str) -> list[tuple[float, str, int, str]]:
    """(time, device address, direction 0 = host->device / 1 = device->host, payload)"""
    if not shutil.which("tshark"):
        sys.exit("tshark not found — install it (Arch/CachyOS: sudo pacman -S wireshark-cli).")
    fields = ["frame.time_epoch", "usb.device_address", "usb.endpoint_address.direction",
              "usb.capdata", "usbhid.data", "usb.data_fragment"]
    cmd = ["tshark", "-r", pcap,
           "-Y", "usb.capdata or usbhid.data or usb.data_fragment",
           "-T", "fields"] + sum((["-e", f] for f in fields), [])
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"tshark failed:\n{out.stderr}")
    rows: list[tuple[float, str, int, str]] = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or not parts[0]:
            continue
        try:
            ts = float(parts[0])
            direction = int(parts[2])
        except ValueError:
            continue
        payload = next((p for p in parts[3:] if p), "").replace(":", "").replace(" ", "").lower()
        if payload:
            rows.append((ts, parts[1], direction, payload))
    return rows


def load_actions(path: str) -> list[tuple[float, str]]:
    actions: list[tuple[float, str]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            try:
                stamp, label = line.split("  ", 1)
                stamp = stamp.strip()
                if stamp.endswith("Z"):   # capture-gg-windows.ps1 writes UTC
                    dt = datetime.strptime(stamp[:-1], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                else:                     # older logs: the capturing PC's local time
                    dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f").astimezone()
                actions.append((dt.timestamp(), label.strip()))
            except ValueError:
                continue
    return actions


def fmt(payload: str, maxbytes: int = 20) -> str:
    b = [payload[i:i + 2] for i in range(0, len(payload), 2)]
    return " ".join(b[:maxbytes]) + (" …" if len(b) > maxbytes else "")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcap")
    ap.add_argument("actions", nargs="?", help="actions.txt from the capture script")
    ap.add_argument("--device", help="USB device address to decode (default: the busiest device that also answers)")
    ap.add_argument("--report-id", help="HID report id prefix (default: the most common leading byte, if most "
                         "commands share it; '' = no prefix, opcode at byte 0)")
    args = ap.parse_args()

    allrows = run_tshark(args.pcap)
    vendors = subprocess.run(
        ["tshark", "-r", args.pcap, "-Y", "usb.idVendor", "-T", "fields",
         "-e", "usb.device_address", "-e", "usb.idVendor", "-e", "usb.idProduct"],
        capture_output=True, text=True).stdout.split("\n")
    known = {l.split("\t")[0]: l.split("\t")[1:] for l in vendors if l.count("\t") == 2}
    if known and not any(v[0].lower() == "0x1038" for v in known.values()):
        # USBPcap records one USB tree; a capture of the wrong one holds only
        # the other devices (#337: an RGB controller instead of an Arctis Pro
        # Wireless). A post-filtered file may have lost the descriptors, so
        # this only warns when some descriptors are there and none is ours.
        print("WARNING: no SteelSeries device (1038:xxxx) in this capture's descriptors: "
              + ", ".join(f"{a}={v[0][2:]}:{v[1][2:]}" for a, v in sorted(known.items()))
              + "\n  The wrong USBPcap tree was probably recorded; ask for a new capture.\n")
    per_dev = Counter(d for _, d, dirn, _ in allrows if dirn == 0)
    if not per_dev:
        sys.exit("No host->device data in this capture — wrong USB tree picked?")
    print("Commands received per device address:",
          ", ".join(f"{d}: {c}" for d, c in per_dev.most_common()))
    # The busiest device is not always the headset: on a Nova Elite capture
    # (#337) an RGB controller on the same tree sent 20x more, and never
    # answered. A SteelSeries device answers its queries, so prefer the
    # busiest one that sent anything back.
    answering = {d for _, d, dirn, _ in allrows if dirn == 1}
    ranked = [d for d, _ in per_dev.most_common() if d in answering] or \
             [d for d, _ in per_dev.most_common()]
    dev = args.device or ranked[0]
    if not args.device and dev != per_dev.most_common(1)[0][0]:
        print(f"Picked device {dev}: the busiest one that answers "
              f"(override with --device)")
    out_rows = [(t, p) for t, d, dirn, p in allrows if d == dev and dirn == 0]
    # Most headsets prefix every command with a report id (06, 00…), so the
    # opcode is the second byte. Some (Arctis Pro GameDAC, #337) send report
    # 0 with no prefix: the first byte IS the opcode and no leading byte
    # dominates. Then keep every command and read the opcode at byte 0.
    lead, lead_n = Counter(p[:2] for _, p in out_rows).most_common(1)[0]
    if args.report_id is not None:
        rid = args.report_id.lower()
    else:
        rid = lead if lead_n * 2 > len(out_rows) else ""
    op_at = 2 if rid else 0
    rows = [(t, p) for t, p in out_rows if p.startswith(rid)]
    in_rows = [(t, p) for t, d, dirn, p in allrows if d == dev and dirn == 1]
    prefix = f"starting with 0x{rid}" if rid else "(no report id prefix: opcode = byte 0)"
    print(f"Device {dev}: {len(rows)} host->device commands {prefix}, "
          f"{len(in_rows)} frames back\n")

    if not args.actions:
        # No log: just list unique commands grouped by opcode.
        groups: dict[str, list[str]] = defaultdict(list)
        for _, p in rows:
            op = p[op_at:op_at + 2] if len(p) >= op_at + 2 else "??"
            if p not in groups[op]:
                groups[op].append(p)
        for op in sorted(groups):
            print(f"opcode 0x{op}:")
            for p in groups[op]:
                print(f"    {fmt(p)}")
        return

    actions = load_actions(args.actions)
    if not actions:
        sys.exit("Could not read any timestamped actions from the log.")
    # A log in the capturing PC's local time is off by whole (or half) hours
    # from the capture's UTC clock: shift it to where the commands are.
    start, end = actions[0][0] - 5.0, actions[-1][0]
    def inside(shift: float) -> int:
        return sum(1 for t, _ in rows if start + shift < t <= end + shift)
    shift = max((h * 1800.0 for h in range(-28, 29)), key=lambda s: (inside(s), -abs(s)))
    if shift and inside(shift) > inside(0.0):
        print(f"Action log shifted by {shift / 3600:+.1f} h to match the capture's clock\n")
        actions = [(t + shift, label) for t, label in actions]

    # Window (t[i-1], t[i]] per action; first window starts a bit before t[0].
    windows: list[tuple[str, list[str]]] = []
    prev = actions[0][0] - 5.0
    replies: list[list[str]] = []
    for ts, label in actions:
        cmds = [p for ct, p in rows if prev < ct <= ts]
        windows.append((label, cmds))
        replies.append([p for ct, p in in_rows if prev < ct <= ts])
        prev = ts

    # Noise = commands in the BASELINE window, or present in most windows.
    baseline = {p for label, cmds in windows if label.lower().startswith("baseline") for p in cmds}
    freq = Counter(p for _, cmds in windows for p in set(cmds))
    n = len(windows)
    noise = baseline | {p for p, c in freq.items() if c >= max(2, n // 2)}

    print("== Background/noise commands (filtered out) ==")
    for p in sorted(noise):
        print(f"    {fmt(p)}   (seen in {freq[p]}/{n} windows)")

    # Per-action signal commands.
    print("\n== Setting → command(s) ==")
    by_setting: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    in_baseline = {p for (label, _), back in zip(windows, replies)
                   if label.lower().startswith("baseline") for p in back}
    in_freq = Counter(p for back in replies for p in set(back))
    in_noise = in_baseline | {p for p, c in in_freq.items() if c >= max(2, n // 2)}
    for (label, cmds), back in zip(windows, replies):
        if label.lower().startswith("baseline"):
            continue
        sig = list(dict.fromkeys(p for p in cmds if p not in noise))
        print(f"\n• {label}")
        for p in sig:
            print(f"    {fmt(p)}")
        for p in list(dict.fromkeys(p for p in back if p not in in_noise))[:6]:
            print(f"    <- {fmt(p, 32)}")
        setting = label.split("=")[0].strip()
        by_setting[setting].append((label, sig))

    # Differential view: per setting, the opcode whose param byte changes.
    print("\n\n== Differential summary (opcode = stable byte, param = changing byte) ==")
    for setting, items in by_setting.items():
        opcodes = defaultdict(list)   # opcode -> [(value_label, payload)]
        for label, sig in items:
            value = label.split("=", 1)[1].strip() if "=" in label else label
            for p in sig:
                opcodes[p[op_at:op_at + 2]].append((value, p))
        # The opcode that appears across the setting's values is the candidate.
        best = sorted(opcodes.items(), key=lambda kv: -len({v for v, _ in kv[1]}))
        print(f"\n{setting}:")
        if not best:
            print("    (no distinct command captured — recheck this one)")
        for op, vals in best[:2]:
            print(f"    opcode 0x{op}:")
            for value, p in vals:
                print(f"        {value:<22} -> {fmt(p)}")


if __name__ == "__main__":
    main()
