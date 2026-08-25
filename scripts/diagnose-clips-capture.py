#!/usr/bin/env python3
"""Why Clips cannot record on this desktop.

Answers one question in order, stopping at the first thing that is actually
wrong: can ASM get frames off this screen at all, and by which route?

Clips takes one of two paths. Where the desktop's portal implements
``org.freedesktop.portal.ScreenCast`` it asks the portal, which is what gives
window and multi-monitor selection. Where it does not — xdg-desktop-portal-gtk,
so XFCE, MATE, Cinnamon and plain window managers — it captures X11 directly
with ximagesrc (#214). Neither path works on Wayland without a portal, and that
is a property of Wayland rather than something ASM can route around.

Read-only, and it records nothing: the pipeline test below runs for two seconds
into a null sink.

    python3 scripts/diagnose-clips-capture.py

Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

OK, BAD, INFO = "  [ok]  ", "  [!!]  ", "  [--]  "


def out(cmd: list[str], timeout: int = 5) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 62 - len(title)))


def main() -> int:
    print("ASM — Clips capture diagnostic")
    print("Paste the whole output into the issue.\n")

    # ── 1. the desktop ───────────────────────────────────────────────────────
    section("Desktop")
    session = os.environ.get("XDG_SESSION_TYPE", "<unset>")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "<unset>")
    print(f"{INFO}XDG_SESSION_TYPE = {session}")
    print(f"{INFO}XDG_CURRENT_DESKTOP = {desktop}")
    print(f"{INFO}DISPLAY = {os.environ.get('DISPLAY', '<unset>')}")
    print(f"{INFO}WAYLAND_DISPLAY = {os.environ.get('WAYLAND_DISPLAY', '<unset>')}")

    # Which backend is installed matters more than the desktop's name: the GTK
    # one is the one with no ScreenCast.
    backends = [p for p in ("xdg-desktop-portal", "xdg-desktop-portal-gtk",
                            "xdg-desktop-portal-kde", "xdg-desktop-portal-wlr",
                            "xdg-desktop-portal-hyprland", "xdg-desktop-portal-gnome")
                if shutil.which(p) or os.path.exists(f"/usr/libexec/{p}")
                or os.path.exists(f"/usr/lib/{p}")]
    print(f"{INFO}portal backends found: {', '.join(backends) or 'none'}")

    # ── 2. the portal ────────────────────────────────────────────────────────
    section("ScreenCast portal")
    portal_ok = False
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD}PyGObject unavailable ({exc}) — Clips cannot run at all.")
        print(f"{INFO}Install it: the Clips toggle in ASM offers to do this.")
        return 1
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.freedesktop.portal.ScreenCast", "version")),
            None, Gio.DBusCallFlags.NONE, 2000, None)
        portal_ok = reply is not None
        print(f"{OK}ScreenCast is available (version {reply.unpack()[0]}).")
        print(f"{INFO}ASM will use the portal, not X11 capture.")
    except Exception as exc:  # noqa: BLE001
        print(f"{INFO}No ScreenCast: {exc}")
        print(f"{INFO}Expected on XFCE/MATE/Cinnamon — ASM falls back to X11.")

    # ── 3. the X11 route ─────────────────────────────────────────────────────
    if not portal_ok:
        section("X11 capture (the fallback ASM will use)")
        if session == "wayland" and not os.environ.get("DISPLAY"):
            print(f"{BAD}Wayland with no portal and no XWayland: nothing can capture.")
            return 1
        if not os.environ.get("DISPLAY"):
            print(f"{BAD}DISPLAY is unset — there is no X server to capture.")
            return 1

        have_xrandr = bool(shutil.which("xrandr"))
        print(f"{OK if have_xrandr else INFO}xrandr: "
              f"{'present' if have_xrandr else 'absent — the whole screen is captured'}")
        if have_xrandr:
            print(out(["xrandr", "--listactivemonitors"]) or "<no output>")
        have_xdotool = bool(shutil.which("xdotool"))
        print(f"{OK if have_xdotool else INFO}xdotool: "
              f"{'present' if have_xdotool else 'absent — the primary monitor is used'}")

    # ── 4. GStreamer ─────────────────────────────────────────────────────────
    section("GStreamer elements")
    wanted = ["ximagesrc", "pipewiresrc", "videorate", "videoconvert",
              "h264parse", "matroskamux", "opusenc", "pulsesrc"]
    encoders = ["nvh264enc", "vah264enc", "x264enc"]
    if not shutil.which("gst-inspect-1.0"):
        print(f"{BAD}gst-inspect-1.0 missing — install the GStreamer tools.")
        return 1
    missing = []
    for el in wanted:
        found = subprocess.run(["gst-inspect-1.0", el],
                               capture_output=True).returncode == 0
        print(f"{OK if found else BAD}{el}")
        if not found:
            missing.append(el)
    enc = [e for e in encoders
           if subprocess.run(["gst-inspect-1.0", e], capture_output=True).returncode == 0]
    print(f"{OK if enc else BAD}encoders: {', '.join(enc) or 'NONE — Clips cannot encode'}")

    # ── 5. does it actually run ──────────────────────────────────────────────
    section("Pipeline test (2 s, records nothing)")
    if portal_ok:
        print(f"{INFO}Skipped: the portal path needs a picker, which a script "
              f"cannot answer. Use Clips itself.")
    elif "ximagesrc" in missing or not enc:
        print(f"{BAD}Skipped: something above is missing.")
    else:
        desc = ["gst-launch-1.0", "-q", "ximagesrc", "use-damage=false",
                "num-buffers=30", "!", "video/x-raw,framerate=30/1",
                "!", "videoconvert", "!", enc[0], "!", "fakesink"]
        try:
            r = subprocess.run(desc, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                print(f"{OK}X11 capture works — Clips should record here.")
            else:
                print(f"{BAD}The pipeline failed. This is the useful part:")
                print((r.stderr or r.stdout).strip()[:1200])
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}Could not run the test: {exc}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
