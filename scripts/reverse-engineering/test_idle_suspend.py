#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find out what keeps an Arctis headset awake when nothing is playing (#180).

Symptom this exists for: the headset never reaches its inactivity timeout and
never powers itself off, because something in the audio graph is feeding it
around the clock. Stopping ASM lets it suspend, so the chain ASM builds is what
holds it.

Two of the three pieces are already confirmed on real hardware: marking the
processing chains `node.passive` lets them suspend, and it does not break the
sound. What is left is the three channel loopbacks (`Arctis_Game_sink_out` and
friends), which stay `running` with nothing playing and keep pushing their
chains.

This script tries the two properties that would stop that, one at a time, and
records what each one does. It edits ASM's installed loopback code, so it needs
root, keeps a backup, and puts everything back with `--revert`. Updating ASM
also replaces the file, which undoes it just as well.

Usage:

    sudo python3 test_idle_suspend.py --variant passive     # try this first
    sudo python3 test_idle_suspend.py --variant pause-on-idle
    sudo python3 test_idle_suspend.py --variant both
    sudo python3 test_idle_suspend.py --revert              # back to normal

After each run: leave the machine quiet with nothing playing, wait for the
report it prints, and check whether the headset finally powers off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ANCHOR = 'f" node.linger=true"'
PROPS = {
    "passive": 'f" node.passive=true"',
    "pause-on-idle": 'f" node.pause-on-idle=true"',
}
SETTLE_SECONDS = 45


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    # setdefault, not timeout=60: callers that need longer (pw-top, asm-cli
    # diagnose) pass their own, and a hard-coded keyword here made those calls
    # raise "got multiple values for keyword argument 'timeout'" — reported
    # with the fix by @Michsior14 on #180.
    kw.setdefault("timeout", 60)
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def loopback_manager_path() -> Path:
    """Locate the installed module, without importing it as root."""
    out = run([sys.executable, "-c",
               "import arctis_sound_manager.loopback_manager as m; print(m.__file__)"])
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("Could not find ASM's loopback_manager. Is ASM installed for "
                 "this Python?\n" + (out.stderr or "").strip())
    path = Path(out.stdout.strip())
    if not path.is_file():
        sys.exit(f"{path} does not exist.")
    return path


def backup_of(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".asm-idle-test.bak")


def restore(path: Path) -> bool:
    bak = backup_of(path)
    if not bak.is_file():
        return False
    shutil.copy2(bak, path)
    bak.unlink()
    for pyc in path.parent.glob("__pycache__/loopback_manager*.pyc"):
        pyc.unlink(missing_ok=True)
    return True


def apply_variant(path: Path, variant: str) -> None:
    restore(path)  # always start from the untouched file
    text = path.read_text(encoding="utf-8")
    if ANCHOR not in text:
        sys.exit(f"Could not find the line to patch in {path}.\nThis script is "
                 "older than your ASM version; please say so on issue #180.")
    wanted = ["passive", "pause-on-idle"] if variant == "both" else [variant]
    indent = re.search(rf"^(\s*){re.escape(ANCHOR)}", text, re.M).group(1)
    addition = "".join(f"\n{indent}{PROPS[k]}" for k in wanted)
    shutil.copy2(path, backup_of(path))
    path.write_text(text.replace(ANCHOR, ANCHOR + addition, 1), encoding="utf-8")
    for pyc in path.parent.glob("__pycache__/loopback_manager*.pyc"):
        pyc.unlink(missing_ok=True)


def as_user(argv: list[str], **kw) -> subprocess.CompletedProcess:
    """Run *argv* as the logged-in user, from a root shell.

    Everything this script asks about lives in the user's session: the daemon
    is a user unit, and PipeWire's socket is under their runtime directory.
    Root has its own empty session bus and no PipeWire at all, so running any
    of it directly gets "could not connect" and an empty result that looks
    like a finding. Reported on #180, where pw-dump had to be fixed by hand
    before the capture said anything.
    """
    user = os.environ.get("SUDO_USER")
    if not user:
        return run(argv, **kw)
    uid = run(["id", "-u", user]).stdout.strip()
    env = {**os.environ,
           "XDG_RUNTIME_DIR": f"/run/user/{uid}",
           "PULSE_RUNTIME_PATH": f"/run/user/{uid}/pulse",
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
    return run(["sudo", "-u", user, "-E", *argv], env=env, **kw)


def user_ctl(*args: str) -> subprocess.CompletedProcess:
    return as_user(["systemctl", "--user", *args])


def restart_stack() -> None:
    """Restart the daemon and clear the orphaned loopbacks.

    pw-loopback runs with node.linger=true, so the processes outlive the
    daemon by design. Without clearing them the old ones keep running with
    the old properties and the test measures nothing.
    """
    print("  restarting arctis-manager ...")
    user_ctl("restart", "arctis-manager")
    time.sleep(2)
    print("  clearing orphaned pw-loopback processes ...")
    run(["pkill", "-f", "pw-loopback"])
    time.sleep(1)
    user_ctl("restart", "arctis-manager")
    time.sleep(5)


def loopback_cmdlines() -> list[str]:
    out = run(["pgrep", "-a", "pw-loopback"])
    return [l for l in out.stdout.splitlines() if l.strip()]


def verify_applied(variant: str) -> bool:
    """Confirm the property really reached the running processes."""
    lines = loopback_cmdlines()
    if not lines:
        print("  ✗ no pw-loopback processes are running at all.")
        return False
    wanted = ["node.passive=true"] if variant == "passive" else \
             ["node.pause-on-idle=true"] if variant == "pause-on-idle" else \
             ["node.passive=true", "node.pause-on-idle=true"]
    ok = all(all(w in l for w in wanted) for l in lines)
    print(f"  {'✓' if ok else '✗'} {len(lines)} pw-loopback process(es), "
          f"property {'present on all' if ok else 'MISSING'}")
    if not ok:
        print("     " + lines[0][:200])
    return ok


def pw_dump() -> list:
    if shutil.which("pw-dump") is None:
        return []
    try:
        data = json.loads(as_user(["pw-dump"]).stdout)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def graph_report(dump: list) -> list[str]:
    """Node states, the links between them, and anything still playing.

    The states alone are not enough, which is what the first round of #180
    ran into: a chain that is suspended because nothing drives it and a chain
    that is suspended because its last link is missing look exactly the same
    in a list of states. The links tell those apart. The playing streams
    matter for the opposite reason, "nothing is playing" has to be a
    measurement rather than an assumption, since one forgotten browser tab
    holding a channel open explains the whole symptom.
    """
    if not dump:
        return ["  (pw-dump unavailable)"]

    names: dict[int, str] = {}
    nodes: list[tuple[str, str]] = []
    streams: list[str] = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        name = props.get("node.name", "?")
        names[obj.get("id")] = name
        mclass = props.get("media.class", "")
        if "Audio" not in mclass:
            continue
        state = info.get("state", "?")
        if name.startswith(("alsa_output.", "alsa_input.", "bluez_output.")) \
                or "Arctis_" in name or "sonar-" in name or "virtual-surround" in name:
            nodes.append((state, name))
        app = props.get("application.name")
        if app and mclass.startswith("Stream/Output"):
            streams.append(f"  {state:<10} {app}  (node {name})")

    out = ["== node states with nothing playing ==",
           "(suspended/idle = not holding the device; running = holding it)", ""]
    out += [f"  {s:<10} {n}" for s, n in sorted(nodes, key=lambda r: r[1])] or \
           ["  (no audio nodes found)"]

    out += ["", "== links ==",
            "(a chain with no link out is suspended because it reaches nothing,",
            " which is a different fault from one that is simply not driven)", ""]
    links = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        src = names.get(info.get("output-node-id"), "?")
        dst = names.get(info.get("input-node-id"), "?")
        if any(f in src or f in dst for f in
               ("Arctis_", "sonar-", "virtual-surround", "alsa_output.")):
            links.append(f"  {info.get('state', '?'):<10} {src}  ->  {dst}")
    out += sorted(set(links)) or ["  (no links at all, that alone is the fault)"]

    out += ["", "== application streams ==",
            "(this must be empty, or 'nothing playing' was not true)", ""]
    out += sorted(set(streams)) or ["  (none, nothing is playing)"]
    return out


def driver_report() -> list[str]:
    """Which node drives the graph, from pw-top.

    pw-top lists each driver with the nodes that follow it indented beneath.
    That is the one question the states cannot answer: something has to be
    keeping the device awake, and this names it directly instead of leaving
    it to be inferred from what happens to be running.
    """
    if shutil.which("pw-top") is None:
        return ["  (pw-top not installed)"]
    res = as_user(["pw-top", "-b", "-n", "2"], timeout=30)
    lines = [l.rstrip() for l in (res.stdout or "").splitlines() if l.strip()]
    if not lines:
        return ["  (pw-top produced nothing)"]
    # -n 2 prints two full batches; the second one is the settled reading.
    starts = [i for i, l in enumerate(lines) if l.lstrip().startswith("S ")]
    if len(starts) > 1:
        lines = lines[starts[-1]:]
    return [f"  {l}" for l in lines]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["passive", "pause-on-idle", "both"])
    ap.add_argument("--revert", action="store_true",
                    help="restore the original file and restart, then exit")
    args = ap.parse_args()

    if not args.revert and not args.variant:
        ap.error("give --variant passive | pause-on-idle | both, or --revert")
    if os.geteuid() != 0:
        sys.exit("This edits ASM's installed files, so it needs root:\n"
                 f"    sudo python3 {sys.argv[0]} "
                 + ("--revert" if args.revert else f"--variant {args.variant}"))

    path = loopback_manager_path()

    if args.revert:
        if restore(path):
            print(f"Restored {path}")
        else:
            print("Nothing to restore; the file is already untouched.")
        restart_stack()
        print("Done. ASM is back to its released behaviour.")
        return 0

    print(f"== testing node.{args.variant} on the channel loopbacks ==\n")
    print(f"Patching {path}")
    apply_variant(path, args.variant)
    restart_stack()

    if not verify_applied(args.variant):
        print("\nThe property did not reach the running processes, so the "
              "measurement below would be meaningless.\nPlease paste this "
              "output on issue #180 as it is.")

    print(f"\nNow leave the machine quiet: stop any music, video, game or call, "
          f"and\ndo not touch it for {SETTLE_SECONDS} seconds while the graph "
          f"settles.\n")
    for remaining in range(SETTLE_SECONDS, 0, -5):
        print(f"  {remaining:>3}s ...", end="\r", flush=True)
        time.sleep(5)
    print(" " * 30, end="\r")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = [f"# ASM idle test, variant={args.variant}, {stamp}", ""]
    lines += graph_report(pw_dump())
    lines += ["", "== what drives the graph (pw-top) ==",
              "(followers are indented under their driver)", ""]
    lines += driver_report()
    lines += ["", "== pw-loopback command lines ==", ""]
    lines += [f"  {l}" for l in loopback_cmdlines()] or ["  (none running)"]

    report = "\n".join(lines)
    print(report)

    home = Path(f"/home/{os.environ['SUDO_USER']}") if os.environ.get("SUDO_USER") \
        else Path.home()
    out = home / f"asm-idle-test-{args.variant}-{stamp}.txt"
    try:
        out.write_text(report + "\n", encoding="utf-8")
        if os.environ.get("SUDO_USER"):
            shutil.chown(out, os.environ["SUDO_USER"])
        print(f"\nSaved to {out}")
    except OSError as exc:
        print(f"\n(could not save a copy: {exc})")

    # ASM's own report carries the rest, settings, config files, the device
    # state, the kernel's view, so there is nothing left to collect by hand.
    bug = home / f"asm-report-{args.variant}-{stamp}.txt"
    if shutil.which("asm-cli"):
        print("\nCollecting ASM's full report ...")
        res = as_user(["asm-cli", "diagnose", "--output", str(bug)], timeout=180)
        if res.returncode == 0 and bug.exists():
            print(f"Saved to {bug}")
        else:
            print("  (asm-cli diagnose did not complete; the file above is "
                  "still worth attaching)")
            bug = None
    else:
        bug = None

    print("\n== what to do now ==")
    print("1. Leave the headset alone and see whether it finally powers off.")
    print("2. Check that sound still works normally afterwards.")
    print(f"3. Attach {out.name}"
          + (f" and {bug.name}" if bug else "")
          + " to issue #180, with the answer to 1 and 2.")
    print(f"\nTo undo:  sudo python3 {sys.argv[0]} --revert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
