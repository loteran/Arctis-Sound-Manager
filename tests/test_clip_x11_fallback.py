# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recording on desktops whose portal has no ScreenCast (#214).

Clips built one pipeline, and its first element was a pipewiresrc fed by an
``org.freedesktop.portal.ScreenCast`` session. Desktops whose portal backend
does not implement that interface — xdg-desktop-portal-gtk, so XFCE, MATE,
Cinnamon and plain window managers — could not record at all.

The rest of the pipeline never knew where its frames came from, so the source
is swapped and everything downstream is untouched. What these tests pin is the
decision: it is asked of the portal, never inferred from the session type,
because X11 under GNOME or KDE has a portal that works and must keep it.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager import clip_capture as cc


# ── the decision ─────────────────────────────────────────────────────────────


def test_a_desktop_with_no_portal_falls_back(monkeypatch):
    """xdg-desktop-portal-gtk answers nothing for ScreenCast."""
    monkeypatch.setattr(cc, "screencast_portal_available", lambda: False)

    assert cc.screencast_portal_available() is False


def test_the_probe_survives_a_desktop_with_no_gobject(monkeypatch):
    """No GI means no portal path at all, and must not raise on the way to
    saying so — this runs while the user is trying to start a recording."""
    import builtins

    real_import = builtins.__import__

    def _no_gi(name, *a, **k):
        if name == "gi":
            raise ImportError("no gi here")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_gi)

    assert cc.screencast_portal_available() is False


# ── which monitor ────────────────────────────────────────────────────────────


_XRANDR = """Monitors: 2
 0: +*eDP-1 1920/344x1080/193+0+0  eDP-1
 1: +HDMI-1 2560/597x1440/336+1920+0  HDMI-1
"""


def _fake_xrandr(monkeypatch, out=_XRANDR, pointer=None):
    from types import SimpleNamespace
    monkeypatch.setattr(cc.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(cc.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=out))
    monkeypatch.setattr(cc, "_x11_pointer", lambda: pointer)


def test_it_records_the_monitor_the_pointer_is_on(monkeypatch):
    """The portal asks which output to record and remembers the answer. With no
    portal there is nobody to ask, so it takes the screen being looked at."""
    _fake_xrandr(monkeypatch, pointer=(2500, 700))

    assert cc.x11_capture_region() == (1920, 0, 1920 + 2560 - 1, 1439)


def test_it_falls_back_to_the_primary_monitor(monkeypatch):
    """No pointer position — xdotool absent, or a bare X session."""
    _fake_xrandr(monkeypatch, pointer=None)

    assert cc.x11_capture_region() == (0, 0, 1919, 1079)


def test_the_whole_screen_is_better_than_no_recording(monkeypatch):
    """Unreadable geometry leaves ximagesrc to take the whole X screen: right
    on a single monitor, and still a recording on several."""
    _fake_xrandr(monkeypatch, out="Monitors: 0\n")

    assert cc.x11_capture_region() is None


def test_no_xrandr_is_survivable(monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)

    assert cc.x11_capture_region() is None


# ── what the fallback records ────────────────────────────────────────────────


def test_the_region_is_inclusive_of_its_last_pixel(monkeypatch):
    """ximagesrc's endx/endy are inclusive: passing the width would record one
    column of the next monitor."""
    _fake_xrandr(monkeypatch, pointer=(10, 10))
    x0, y0, x1, y1 = cc.x11_capture_region()

    assert (x1 - x0 + 1, y1 - y0 + 1) == (1920, 1080)
