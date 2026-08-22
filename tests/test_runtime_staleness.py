# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for runtime_staleness — detecting a package upgraded under a live process."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager import runtime_staleness as rs


def test_an_upgrade_under_a_running_process_is_reported(monkeypatch):
    """The whole point: files changed on disk, this process still runs the old code."""
    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.12")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")

    assert rs.upgraded_under_us() == "1.2.14"


def test_no_banner_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.14")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")

    assert rs.upgraded_under_us() is None


def test_a_source_checkout_never_asks_for_a_restart(monkeypatch):
    """project_version() answers "dev" outside an installed package.

    Comparing "dev" against anything would nag developers on every launch.
    """
    monkeypatch.setattr(rs, "RUNNING_VERSION", "dev")
    monkeypatch.setattr(rs, "installed_version", lambda: "1.2.14")
    assert rs.upgraded_under_us() is None

    monkeypatch.setattr(rs, "RUNNING_VERSION", "1.2.14")
    monkeypatch.setattr(rs, "installed_version", lambda: "dev")
    assert rs.upgraded_under_us() is None


def test_installed_version_is_re_read_not_remembered(monkeypatch):
    """A cached sys.path scan would hide the upgrade for the life of the process."""
    calls = []
    monkeypatch.setattr(rs.importlib, "invalidate_caches", lambda: calls.append("invalidated"))
    monkeypatch.setattr(rs, "project_version", lambda: "1.2.14")

    assert rs.installed_version() == "1.2.14"
    assert calls == ["invalidated"]


def test_services_are_try_restarted_not_started(monkeypatch):
    """Someone who stopped ASM on purpose must not get it back from an upgrade.

    restart_user_services() delegates to service_control, so "try-restart" is
    implemented here as "restart only what is already active" rather than the
    literal systemctl verb — service_control.restart() only exposes plain
    restart, so the stopped-stays-stopped behaviour has to be enforced by
    filtering on is_active() before calling it.
    """
    active = {"arctis-manager", "arctis-video-router"}  # arctis-stream-guard is stopped
    restarted = []

    monkeypatch.setattr(rs.sc, "manager_available", lambda: True)
    monkeypatch.setattr(rs.sc, "is_active", lambda name: name in active)
    monkeypatch.setattr(rs.sc, "restart",
                        lambda *names, **kw: restarted.extend(names) or True)

    rs.restart_user_services()

    assert set(restarted) == active
    assert "arctis-stream-guard" not in restarted


def test_dinit_box_restarts_the_daemon_through_service_control(monkeypatch):
    """The bug this test guards against: on a dinit box (no systemctl on PATH,
    dinitctl present) the old code did `shutil.which("systemctl")`, got None,
    and returned without doing anything or logging anything — "Restart Now"
    relaunched the GUI while the daemon kept running the old code forever.

    service_control already has a dinit backend (dinitctl per service); this
    only has to prove restart_user_services() actually reaches it instead of
    hand-rolling systemctl.
    """
    monkeypatch.setattr(rs.sc, "detect_init", lambda: "dinit")
    monkeypatch.setattr(rs.sc, "manager_available", lambda: True)
    monkeypatch.setattr(rs.sc, "is_active", lambda name: True)

    calls = []

    def fake_run(cmd, timeout, capture):
        calls.append(list(cmd))
        return True

    monkeypatch.setattr(rs.sc, "_run", fake_run)

    rs.restart_user_services()

    # One dinitctl restart per logical service, resolved via _SERVICE_MAP.
    assert calls == [
        ["dinitctl", "restart", "arctis-manager"],
        ["dinitctl", "restart", "arctis-video-router"],
        ["dinitctl", "restart", "arctis-stream-guard"],
    ]


def test_restarting_services_survives_no_init_manager_and_logs(monkeypatch, caplog):
    """Containers, or a box with neither systemctl nor dinitctl: no reason to
    raise, but — unlike the old silent `return` — this must log, the same way
    service_control itself logs when it is asked to do something it can't.
    """
    monkeypatch.setattr(rs.sc, "manager_available", lambda: False)

    def explode(*a, **kw):
        raise AssertionError("service_control must not be asked to act without a manager")

    monkeypatch.setattr(rs.sc, "is_active", explode)
    monkeypatch.setattr(rs.sc, "restart", explode)

    with caplog.at_level("WARNING"):
        rs.restart_user_services()  # must not raise

    assert any("restart" in r.message.lower() for r in caplog.records)


def test_a_failing_service_restart_is_not_fatal(monkeypatch):
    """The GUI restart that follows matters more than the services succeeding."""
    monkeypatch.setattr(rs.sc, "manager_available", lambda: True)
    monkeypatch.setattr(rs.sc, "is_active", lambda name: True)
    monkeypatch.setattr(rs.sc, "restart", lambda *names, **kw: False)

    rs.restart_user_services()  # must not raise
