# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""RAPPORT-CHAOS-ASM SD-3 — the pyudev backend was picked once at start() and
never checked again. If the MonitorObserver thread died (a udevd restart in a
sandboxed session, a container namespace change, netlink never working in a
Distrobox setup), replug/power-cycle events silently stopped being noticed:
the polling fallback already existed in this file, but nothing ever switched
to it, and nothing logged the silence.

This file pins down the watchdog added to close that gap: it must notice a
dead observer thread, log the failure at a level a bug report will contain,
switch to the existing polling loop exactly once, and let that fallback keep
delivering events afterwards.

Scope, honestly: `Thread.is_alive()` only catches a crashed/exited observer
thread. A netlink socket that silently stops delivering events while the
thread is still alive and blocked on it is a different failure mode with no
cheap signal from the thread object, and is NOT covered by the watchdog or by
these tests.
"""

import logging
import sys
import types
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import usb_devices_monitor as mod


def _make_monitor(*, observer, backend='pyudev') -> mod.USBDevicesMonitor:
    """A USBDevicesMonitor with only the state the watchdog/poll paths touch,
    bypassing __init__ so no real pyudev.Context()/netlink socket is opened."""
    obj = object.__new__(mod.USBDevicesMonitor)
    obj.logger = logging.getLogger('test-usb-devices-monitor')
    obj._stopping = False
    obj._on_connect_callbacks = []
    obj._on_disconnect_callbacks = []
    obj._backend = backend
    obj.context = None
    obj.monitor = None
    obj._poll_thread = None
    obj._observer = observer
    obj._watchdog_thread = None
    obj._known_devices = set()
    return obj


class _FakeObserver:
    """Stand-in for pyudev.MonitorObserver: only the Thread surface the
    watchdog reads (is_alive()) matters here."""

    def __init__(self, alive: bool):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive

    def stop(self):
        self._alive = False


# ── the watchdog notices a dead observer ────────────────────────────────────

def test_dead_observer_triggers_polling_fallback(monkeypatch):
    obj = _make_monitor(observer=_FakeObserver(alive=False))
    monkeypatch.setattr(mod.time, 'sleep', lambda _s: None)
    started = []
    monkeypatch.setattr(obj, '_start_polling', lambda: started.append(True))

    obj._watchdog_loop()

    assert obj._backend == 'polling'
    assert obj._observer is None
    assert started == [True]


def test_dead_observer_is_logged_at_a_level_a_bug_report_will_contain(monkeypatch, caplog):
    """A repair path that only logs at debug is a dead path for bug reports —
    nobody ships debug logs. This must be visible at least at WARNING."""
    obj = _make_monitor(observer=_FakeObserver(alive=False))
    monkeypatch.setattr(mod.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(obj, '_start_polling', lambda: None)

    with caplog.at_level(logging.WARNING, logger='test-usb-devices-monitor'):
        obj._watchdog_loop()

    records = [r for r in caplog.records if r.name == 'test-usb-devices-monitor']
    assert records, "the dead-observer transition must be logged"
    assert all(r.levelno >= logging.WARNING for r in records)
    assert any('polling' in r.message.lower() for r in records)


def test_watchdog_returns_after_switching_so_it_cannot_start_polling_twice(monkeypatch):
    """If the watchdog kept looping after the switch it could call
    _start_polling() again on a later tick and spin up a second poll thread —
    two sources of the same events. One trigger must mean one switch."""
    obj = _make_monitor(observer=_FakeObserver(alive=False))
    monkeypatch.setattr(mod.time, 'sleep', lambda _s: None)
    calls = []
    monkeypatch.setattr(obj, '_start_polling', lambda: calls.append(True))

    obj._watchdog_loop()  # must return on its own, not run forever

    assert len(calls) == 1


# ── the watchdog leaves a healthy observer alone ────────────────────────────

def test_alive_observer_is_not_touched(monkeypatch):
    obj = _make_monitor(observer=_FakeObserver(alive=True))
    started = []
    monkeypatch.setattr(obj, '_start_polling', lambda: started.append(True))

    def _sleep_then_stop(_seconds):
        # let the loop run exactly one liveness check, then end the test.
        obj._stopping = True

    monkeypatch.setattr(mod.time, 'sleep', _sleep_then_stop)

    obj._watchdog_loop()

    assert obj._backend == 'pyudev'
    assert obj._observer is not None
    assert started == []


def test_watchdog_exits_immediately_once_stopping_is_set(monkeypatch):
    obj = _make_monitor(observer=_FakeObserver(alive=True))
    obj._stopping = True
    sleeps = []
    monkeypatch.setattr(mod.time, 'sleep', lambda s: sleeps.append(s))

    obj._watchdog_loop()

    assert sleeps == []


# ── end-to-end: dead observer -> polling fallback actually delivers events ──

def test_after_fallback_the_polling_loop_delivers_events_exactly_once(monkeypatch):
    """The real regression: an observer that dies must be noticed AND the
    polling fallback must take over and keep working, without the dead
    pyudev side contributing a duplicate event for the same device."""
    obj = _make_monitor(observer=_FakeObserver(alive=False))
    monkeypatch.setattr(mod.time, 'sleep', lambda _s: None)

    connected = []
    obj._on_connect_callbacks.append(
        lambda vid, pid, name='': connected.append((vid, pid)))

    calls = {'n': 0}

    def fake_find(find_all, idVendor):
        calls['n'] += 1
        if calls['n'] == 1:
            return []  # seed snapshot: nothing plugged in yet
        obj._stopping = True  # stop the poll loop after this one diff
        return [types.SimpleNamespace(idVendor=0x1038, idProduct=0x12ad, product='')]

    fake_usb_core = types.SimpleNamespace(find=fake_find)
    monkeypatch.setitem(sys.modules, 'usb.core', fake_usb_core)
    monkeypatch.setitem(sys.modules, 'usb', types.SimpleNamespace(core=fake_usb_core))

    # Watchdog check: observer is dead -> switches backend and starts the
    # real polling thread (using the fakes above, and the no-op sleep so the
    # thread does not actually block for _POLL_INTERVAL_SECONDS).
    obj._watchdog_loop()

    assert obj._backend == 'polling'
    assert obj._poll_thread is not None
    obj._poll_thread.join(timeout=5)
    assert not obj._poll_thread.is_alive(), "poll loop did not finish in time"

    assert connected == [(0x1038, 0x12ad)]
