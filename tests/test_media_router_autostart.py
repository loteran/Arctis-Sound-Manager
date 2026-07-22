# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The media router must survive an ASM restart.

Quitting ASM from the tray stops arctis-manager, arctis-video-router and
filter-chain together (deliberate: the system should behave as if ASM were not
installed). Relaunching brought back the daemon and the filter-chain but never
the router, so it stayed dead for the rest of the session while ASM looked
healthy — and with it went the feature that watches for manual stream moves and
remembers them as per-app routing overrides.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from arctis_sound_manager.scripts.daemon import _ensure_media_router_running


def _sc(monkeypatch, *, available=True, active=False, enabled=True):
    from arctis_sound_manager import service_control as sc
    monkeypatch.setattr(sc, "manager_available", lambda: available)
    monkeypatch.setattr(sc, "is_active", lambda svc: active)
    monkeypatch.setattr(sc, "is_enabled", lambda svc: enabled)
    started = []
    monkeypatch.setattr(sc, "start", lambda *a, **kw: started.append(a) or True)
    return started


def test_enabled_but_stopped_router_is_started(monkeypatch):
    started = _sc(monkeypatch, active=False, enabled=True)
    _ensure_media_router_running(logging.getLogger("t"))
    assert started == [("arctis-video-router",)]


def test_running_router_is_left_alone(monkeypatch):
    started = _sc(monkeypatch, active=True, enabled=True)
    _ensure_media_router_running(logging.getLogger("t"))
    assert started == []


def test_disabled_router_is_never_resurrected(monkeypatch):
    """A user who turned the router off must not get it back on every start."""
    started = _sc(monkeypatch, active=False, enabled=False)
    _ensure_media_router_running(logging.getLogger("t"))
    assert started == []


def test_no_service_manager_is_a_no_op(monkeypatch):
    started = _sc(monkeypatch, available=False, active=False, enabled=True)
    _ensure_media_router_running(logging.getLogger("t"))
    assert started == []


def test_failure_never_breaks_daemon_startup(monkeypatch):
    from arctis_sound_manager import service_control as sc
    monkeypatch.setattr(sc, "manager_available", lambda: True)
    monkeypatch.setattr(sc, "is_active", MagicMock(side_effect=RuntimeError("boom")))
    _ensure_media_router_running(logging.getLogger("t"))  # must not raise
