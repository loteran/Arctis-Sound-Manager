#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Decode a USBPcap capture of SteelSeries GG and map each setting change to the
exact HID command bytes, using the timestamped action log from
capture-omni-windows.ps1.

Usage:
    python3 parse_steelseries_capture.py omni.pcapng omni-actions.txt
    python3 parse_steelseries_capture.py omni.pcapng --pid 0x2290

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
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime


def run_tshark(pcap: str) -> list[tuple[float, str]]:
    if not shutil.which("tshark"):
        sys.exit("tshark not found — install it (Arch/CachyOS: sudo pacman -S wireshark-cli).")
    fields = ["frame.time_epoch", "usb.capdata", "usbhid.data", "usb.data_fragment"]
    cmd = ["tshark", "-r", pcap,
           "-Y", "usb.endpoint_address.direction == 0",  # host -> device
           "-T", "fields"] + sum((["-e", f] for f in fields), [])
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
        payload = next((p for p in parts[1:] if p), "").replace(":", "").replace(" ", "").lower()
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
            try:
                stamp, label = line.split("  ", 1)
                dt = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S.%f")
                actions.append((dt.astimezone().timestamp(), label.strip()))
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
    ap.add_argument("actions", nargs="?", help="omni-actions.txt from the capture script")
    ap.add_argument("--report-id", default="06", help="HID report id prefix (default 06)")
    args = ap.parse_args()

    rid = args.report_id.lower()
    rows = [(t, p) for t, p in run_tshark(args.pcap) if p.startswith(rid)]
    print(f"{len(rows)} host->device commands starting with 0x{rid}\n")

    if not args.actions:
        # No log: just list unique commands grouped by opcode.
        groups: dict[str, list[str]] = defaultdict(list)
        for _, p in rows:
            op = p[2:4] if len(p) >= 4 else "??"
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

    # Window (t[i-1], t[i]] per action; first window starts a bit before t[0].
    windows: list[tuple[str, list[str]]] = []
    prev = actions[0][0] - 5.0
    for ts, label in actions:
        cmds = [p for ct, p in rows if prev < ct <= ts]
        windows.append((label, cmds))
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
    for label, cmds in windows:
        if label.lower().startswith("baseline"):
            continue
        sig = list(dict.fromkeys(p for p in cmds if p not in noise))
        print(f"\n• {label}")
        for p in sig:
            print(f"    {fmt(p)}")
        setting = label.split("=")[0].strip()
        by_setting[setting].append((label, sig))

    # Differential view: per setting, the opcode whose param byte changes.
    print("\n\n== Differential summary (opcode = stable byte, param = changing byte) ==")
    for setting, items in by_setting.items():
        opcodes = defaultdict(list)   # opcode -> [(value_label, payload)]
        for label, sig in items:
            value = label.split("=", 1)[1].strip() if "=" in label else label
            for p in sig:
                opcodes[p[2:4]].append((value, p))
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
