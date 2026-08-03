# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every screencast session ASM opens has to be closed on the bus.

Reported from use as three record symbols stacked in the corner of the screen,
overlapping. They were three live portal sessions. The portal keeps a session
until its client calls Close or drops off the bus, and the GUI does neither
when a capture stops — it goes on running — so `self.portal = None` released
the Python reference and left the session exactly where it was. The compositor
draws one recording indicator per live session, so a Stop/Start cycle, a
pipeline restart, or a capture that failed to start each added one more.

The object is built with `__new__` and given a recording stand-in for the bus:
`close()` touches only `bus`, `session`, `_Gio` and `_closed_sub`, and going
through `__init__` would mean a live D-Bus connection and PyGObject just to
observe one method call. Only the two calls verified against Gio are stood in
for — `call_sync` and `signal_unsubscribe`.
"""
from __future__ import annotations

import pytest

from arctis_sound_manager.clip_capture import ClipCapture, ScreenCastPortal

PORTAL = "org.freedesktop.portal.Desktop"
SESSION_IFACE = "org.freedesktop.portal.Session"


class _RecordingBus:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple] = []
        self.unsubscribed: list[int] = []
        self._fail = fail

    def call_sync(self, dest, path, iface, method, *rest):
        self.calls.append((dest, path, iface, method))
        if self._fail:
            raise RuntimeError("the portal is not answering")

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)


class _Flags:
    class DBusCallFlags:
        NONE = 0


def _portal(bus: _RecordingBus, session: str | None = "/session/1",
            sub: int | None = 7) -> ScreenCastPortal:
    p = object.__new__(ScreenCastPortal)
    p.bus = bus
    p.session = session
    p.closed = False
    p._closed_sub = sub
    p._Gio = _Flags
    return p


class _FakePortal:
    def __init__(self):
        self.closed_calls = 0

    def close(self):
        self.closed_calls += 1


# ── closing ───────────────────────────────────────────────────────────────────


def test_close_calls_the_portal_and_not_just_the_garbage_collector():
    bus = _RecordingBus()
    portal = _portal(bus)

    portal.close()

    assert bus.calls == [(PORTAL, "/session/1", SESSION_IFACE, "Close")]
    assert portal.session is None


def test_close_drops_the_signal_subscription():
    """The Closed subscription outlives the session it was made for, and its
    callback holds the portal object alive with it."""
    bus = _RecordingBus()
    portal = _portal(bus, sub=7)

    portal.close()

    assert bus.unsubscribed == [7]
    assert portal._closed_sub is None


def test_close_on_a_portal_that_never_opened_does_nothing():
    bus = _RecordingBus()
    portal = _portal(bus, session=None)

    portal.close()

    assert bus.calls == []


def test_close_twice_only_closes_once():
    bus = _RecordingBus()
    portal = _portal(bus)

    portal.close()
    portal.close()

    assert len(bus.calls) == 1


def test_a_portal_that_refuses_to_close_does_not_raise():
    """This runs on the way out of a capture. A session that cannot be closed
    is not a reason to fail the stop the user asked for."""
    portal = _portal(_RecordingBus(fail=True))

    portal.close()  # must not raise

    assert portal.session is None


# ── the call sites ────────────────────────────────────────────────────────────


def _capture_shell(portal) -> ClipCapture:
    """A ClipCapture with only what stop()/restart() reach for."""
    cap = object.__new__(ClipCapture)
    cap.pipeline = None
    cap.portal = portal
    return cap


def test_stop_closes_the_session():
    portal = _FakePortal()
    cap = _capture_shell(portal)

    cap.stop()

    assert portal.closed_calls == 1
    assert cap.portal is None


def test_stop_without_a_capture_is_harmless():
    cap = _capture_shell(None)

    cap.stop()  # must not raise

    assert cap.portal is None


def test_restart_closes_the_old_session_before_opening_the_next(monkeypatch):
    """restart() deliberately keeps the user out of the picker by reopening
    from the saved token — but the session it is replacing still has to go, or
    the indicator it draws stays on screen for a pipeline that no longer
    exists."""
    class _Clearable:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

    portal = _FakePortal()
    cap = _capture_shell(portal)
    cap._Gst = None            # pipeline is None, so Gst is never reached
    cap.buffer = _Clearable()
    cap.caps = _Clearable()
    cap._pts_offset = _Clearable()

    order: list[str] = []
    monkeypatch.setattr(portal, "close",
                        lambda: order.append("closed"))
    monkeypatch.setattr(ClipCapture, "start",
                        lambda self: order.append("started"))

    cap.restart()

    assert order == ["closed", "started"], order


def test_starting_twice_does_not_strand_the_first_session(monkeypatch):
    """Overwriting self.portal would leave a session on the bus with nothing
    left holding a handle able to close it."""
    first = _FakePortal()
    cap = _capture_shell(first)

    # Fail the second open immediately: everything after it in start() needs a
    # live pipeline, and the question here is only what happened to `first`.
    monkeypatch.setattr(
        "arctis_sound_manager.clip_capture.ScreenCastPortal",
        lambda: (_ for _ in ()).throw(RuntimeError("no portal")))
    cap._Gst = None

    with pytest.raises(RuntimeError):
        cap.start()

    assert first.closed_calls == 1


def test_a_failed_open_closes_the_half_made_session(monkeypatch):
    """CreateSession may have succeeded before the picker was cancelled. That
    half-open session is as visible to the compositor as a working one."""
    made = _FakePortal()

    def _open(window=False):
        raise RuntimeError("cancelled")

    made.open = _open
    monkeypatch.setattr(
        "arctis_sound_manager.clip_capture.ScreenCastPortal", lambda: made)

    cap = _capture_shell(None)
    cap._Gst = None
    cap.window = False

    with pytest.raises(RuntimeError):
        cap.start()

    assert made.closed_calls == 1
    assert cap.portal is None
