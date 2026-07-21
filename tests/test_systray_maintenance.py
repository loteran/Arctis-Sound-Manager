# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the tray "Maintenance" submenu (restart audio engine / restart
ASM / regenerate audio configs) — added so a user can recover from a stuck
filter-chain or a stale PipeWire config without a terminal (there is no
discoverable systemctl/dinitctl unit name for the ASM daemon).

Everything here must go through service_control exclusively (never a raw
systemctl/dinitctl call, and never pipewire itself — that would kill every
audio client on the system, not just ASM's).

The handlers are exercised directly against a minimal stand-in object rather
than a fully constructed QSystrayApp (which spins up a QSystemTrayIcon, a
D-Bus polling thread, etc.) — same style as test_systray_battery.py, which
tests QSystrayApp._extract_battery_percent the same way.
"""
from __future__ import annotations

import logging
import os
import types
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject  # noqa: E402

from arctis_sound_manager.gui import systray_app as sysapp  # noqa: E402
from arctis_sound_manager.i18n import I18n  # noqa: E402


class _FakeTray(QObject):
    """Minimal QSystrayApp stand-in carrying only what the maintenance
    handlers touch — a real QSystrayApp spins up a QSystemTrayIcon and a
    D-Bus polling thread, which these tests have no need for.

    It must be a real QObject: the outcome slot is connected as a bound method
    so that Qt runs it on the UI thread (see _run_maintenance_action), and that
    only holds for a QObject receiver."""


def _fake_self(monkeypatch):
    """Build a _FakeTray with the handler methods under test bound to it (via
    the real QSystrayApp implementations), plus a synchronous
    _ServiceActionWorker: .start() runs the body in-thread and then emits
    ``finished`` exactly as QThread would once run() returns, so the worker
    lifecycle (report on `done`, release on `finished`) is exercised without a
    real thread or a Qt event loop."""
    def _sync_start(worker):
        worker.run()
        # QThread would flip isFinished() before emitting `finished`; the
        # cleanup slot sweeps on that, so the stub has to model it too.
        worker._test_finished = True
        worker.finished.emit()

    monkeypatch.setattr(sysapp._ServiceActionWorker, "start", _sync_start)
    monkeypatch.setattr(
        sysapp._ServiceActionWorker, "isFinished",
        lambda worker: getattr(worker, "_test_finished", False),
    )
    obj = _FakeTray()
    obj.tray_icon = mock.MagicMock()
    obj.logger = logging.getLogger("test_systray_maintenance")
    obj._maintenance_workers = []
    for _name in (
        "_run_maintenance_action",
        "_on_maintenance_done",
        "_on_maintenance_worker_finished",
    ):
        setattr(obj, _name, types.MethodType(getattr(sysapp.QSystrayApp, _name), obj))
    return obj


def _message_bodies(fake_self) -> list[str]:
    return [c.args[1] for c in fake_self.tray_icon.showMessage.call_args_list]


# ── Restart audio engine (filter-chain only) ────────────────────────────────

def test_restart_audio_engine_restarts_only_filter_chain(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: calls.append((a, kw)) or True)

    sysapp.QSystrayApp._on_restart_audio_engine(fake_self)

    assert calls == [(("filter-chain",), {"timeout": 20})]
    # The worker must be cleaned up from the tracking list once done.
    assert fake_self._maintenance_workers == []
    assert I18n.translate('ui', 'restart_audio_engine_done') in _message_bodies(fake_self)


def test_restart_audio_engine_reports_failure(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: False)

    sysapp.QSystrayApp._on_restart_audio_engine(fake_self)

    assert I18n.translate('ui', 'restart_audio_engine_failed') in _message_bodies(fake_self)


# ── Restart ASM (daemon + filter-chain [+ video-router if active]) ─────────

def test_restart_asm_restarts_manager_and_filter_chain(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "is_active", lambda svc: False)
    calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: calls.append((a, kw)) or True)

    sysapp.QSystrayApp._on_restart_asm(fake_self)

    assert calls == [(("arctis-manager", "filter-chain"), {"timeout": 20})]


def test_restart_asm_includes_video_router_only_when_active(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "is_active", lambda svc: svc == "arctis-video-router")
    calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: calls.append((a, kw)) or True)

    sysapp.QSystrayApp._on_restart_asm(fake_self)

    assert calls == [
        (("arctis-manager", "filter-chain", "arctis-video-router"), {"timeout": 20})
    ]


def test_restart_asm_never_touches_pipewire_or_wireplumber(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "is_active", lambda svc: True)
    calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: calls.append(a) or True)

    sysapp.QSystrayApp._on_restart_asm(fake_self)

    for args in calls:
        assert "pipewire" not in args
        assert "wireplumber" not in args


# ── Regenerate audio configs (force regen + filter-chain restart) ──────────

def test_regenerate_audio_configs_calls_check_and_fix_then_restarts_filter_chain(monkeypatch):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)

    check_calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
        lambda: (check_calls.append(1) or (True, False)),
    )
    restart_calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: restart_calls.append((a, kw)) or True)

    sysapp.QSystrayApp._on_regenerate_audio_configs(fake_self)

    assert check_calls == [1]
    assert restart_calls == [(("filter-chain",), {"timeout": 20})]


def test_regenerate_audio_configs_restarts_even_when_nothing_was_stale(monkeypatch):
    """The button always restarts filter-chain — that's the whole point of a
    manual "apply now" action, even if check_and_fix_stale_configs() found
    nothing to fix."""
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
        lambda: (False, False),
    )
    restart_calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)

    sysapp.QSystrayApp._on_regenerate_audio_configs(fake_self)

    assert restart_calls == [("filter-chain",)]


def test_regenerate_audio_configs_never_restarts_pipewire_even_on_migration_flag(monkeypatch):
    """Even when check_and_fix_stale_configs() signals needs_pw_restart=True
    (the legacy static-HeSuVi duplicate-node migration path), the tray action
    must not restart pipewire itself — only filter-chain. A full pipewire
    restart stays reserved for the normal GUI/daemon startup path."""
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.check_and_fix_stale_configs",
        lambda: (True, True),
    )
    restart_calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)

    sysapp.QSystrayApp._on_regenerate_audio_configs(fake_self)

    assert restart_calls == [("filter-chain",)]
    for args in restart_calls:
        assert "pipewire" not in args


# ── No service manager available (systemctl/dinitctl missing) ──────────────

@pytest.mark.parametrize("handler", [
    sysapp.QSystrayApp._on_restart_audio_engine,
    sysapp.QSystrayApp._on_restart_asm,
    sysapp.QSystrayApp._on_regenerate_audio_configs,
])
def test_no_service_manager_never_calls_restart_and_notifies(monkeypatch, handler):
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: False)
    monkeypatch.setattr(sysapp.sc, "is_active", lambda svc: True)
    restart_calls = []
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)

    handler(fake_self)

    assert restart_calls == [], "must never call sc.restart when no service manager is available"
    assert I18n.translate('ui', 'service_manager_unavailable') in _message_bodies(fake_self)


# ── i18n coverage ────────────────────────────────────────────────────────────

def test_all_maintenance_i18n_keys_exist_in_en_ini():
    """Every key used by the maintenance submenu must be present in en.ini —
    the source of truth for Crowdin — so the tray never falls back to
    showing a raw i18n key as the label."""
    keys = [
        'maintenance_menu',
        'restart_audio_engine', 'restart_audio_engine_progress',
        'restart_audio_engine_done', 'restart_audio_engine_failed',
        'restart_asm', 'restart_asm_progress', 'restart_asm_done', 'restart_asm_failed',
        'regenerate_audio_configs', 'regenerate_audio_configs_progress',
        'regenerate_audio_configs_done', 'regenerate_audio_configs_failed',
        'service_manager_unavailable',
    ]
    for key in keys:
        value = I18n.translate('ui', key)
        assert value != key, f"missing en.ini [ui] key: {key}"


# ── Threading invariants ─────────────────────────────────────────────────────
#
# These two lock down *how* the worker is wired, not what it does. Both
# mistakes they guard against are silent at runtime until they crash the tray
# (issue #126 is a tray crash), and neither is visible in the behavioural tests
# above, which run the worker synchronously.

class _FakeSignal:
    """Records what a signal was connected to, and replays it on emit()."""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class _RecordingWorker:
    """Stand-in for _ServiceActionWorker that captures its connections instead
    of touching Qt, so the wiring itself can be asserted on."""

    last = None

    def __init__(self, func, done_key, failed_key, parent=None):
        self.func, self.done_key, self.failed_key = func, done_key, failed_key
        self.done = _FakeSignal()
        self.finished = _FakeSignal()
        self.deleted = False
        self._finished = False
        _RecordingWorker.last = self

    def start(self):
        pass

    def isFinished(self):
        return self._finished

    def deleteLater(self):
        self.deleted = True


def test_done_is_connected_to_a_bound_method_not_a_closure(monkeypatch):
    """`done` must be connected to a bound method of the tray object.

    Qt treats a context-less functor (a local function or a lambda) as a
    DirectConnection, which would run the slot in the worker thread and call
    QSystemTrayIcon.showMessage() off the UI thread. Only a bound method of a
    UI-thread QObject gets the AutoConnection→QueuedConnection promotion.
    """
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp, "_ServiceActionWorker", _RecordingWorker)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: True)

    sysapp.QSystrayApp._on_restart_audio_engine(fake_self)

    worker = _RecordingWorker.last
    assert len(worker.done.slots) == 1
    slot = worker.done.slots[0]
    assert getattr(slot, '__self__', None) is fake_self, (
        "done must be connected to a bound method of the tray object, not a "
        "closure — a context-less functor runs the slot in the worker thread"
    )
    # The outcome keys must reach the slot through the signal, since a bound
    # method cannot capture them.
    assert (worker.done_key, worker.failed_key) == (
        'restart_audio_engine_done', 'restart_audio_engine_failed',
    )


def test_worker_reference_is_released_on_finished_not_on_done(monkeypatch):
    """The worker must stay referenced until `finished`.

    `done` is emitted from inside run(), so dropping the last reference there
    can destroy a QThread that is still running ("QThread: Destroyed while
    thread is still running").
    """
    fake_self = _fake_self(monkeypatch)
    monkeypatch.setattr(sysapp, "_ServiceActionWorker", _RecordingWorker)
    monkeypatch.setattr(sysapp.sc, "manager_available", lambda: True)
    monkeypatch.setattr(sysapp.sc, "restart", lambda *a, **kw: True)

    sysapp.QSystrayApp._on_restart_audio_engine(fake_self)
    worker = _RecordingWorker.last

    worker.done.emit(True, worker.done_key, worker.failed_key)
    assert fake_self._maintenance_workers == [worker], (
        "the worker must still be referenced after `done` — run() has not "
        "returned yet at that point"
    )

    # QThread emits `finished` only once run() has returned, at which point
    # isFinished() is true.
    worker._finished = True
    worker.finished.emit()
    assert fake_self._maintenance_workers == []
    assert worker.deleted, "a finished worker must be handed to deleteLater()"
