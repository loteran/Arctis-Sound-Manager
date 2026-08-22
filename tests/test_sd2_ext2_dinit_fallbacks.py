# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for RAPPORT-CHAOS-ASM.md findings SD-2 and EXT-2.

SD-2: service_control.nrestarts() returns None on non-systemd by design, so
sonar_to_pipewire.ensure_filter_chain_healthy()'s NRestarts check was
unreachable on dinit — a crash-looping filter-chain that happened to be
momentarily active at the check-1 sample never armed safe mode there. Fixed
with an init-agnostic fallback (_dinit_crash_loop_settled()) that watches the
service live instead of querying a counter dinit does not expose.

EXT-2: scripts/setup.py's _setup_dinit_services() made ~12 raw
subprocess.run(["dinitctl", ...]) calls guarded only against
subprocess.TimeoutExpired, never FileNotFoundError, bypassing
service_control._run(). On a dinit box missing dinitctl from PATH, setup died
with an uncaught traceback partway through. Fixed by routing every call
through the new service_control.run_raw(), which catches
FileNotFoundError/OSError/TimeoutExpired and returns None instead of raising.

Notes on this machine (see the task report for the full transcript):
dinitctl is genuinely absent here (confirmed via `which`, `pacman -Q`, a full
filesystem search, and no AUR/container fallback) and the suite's own
conftest.py blocks any real "dinitctl" Popen invocation outright (it is in
_AUDIO_TOOLS) regardless of whether the binary exists. That guard is correct
suite behaviour and not something this file works around; instead these tests
mock service_control's own boundary (is_active/detect_init/run_raw) and, for
the exception-catching path itself, exercise a real subprocess call against a
binary name that is *not* an audio tool so the real FileNotFoundError path is
genuinely observed rather than assumed.
"""

from unittest.mock import patch

import pytest


# ── EXT-2: service_control.run_raw() ──────────────────────────────────────


def test_run_raw_returns_none_for_missing_binary():
    """run_raw() catches a real FileNotFoundError and returns None instead of
    raising. Deliberately not mocked: a mocked subprocess can never observe
    the tool actually refusing to exist (the #181 pattern) — this binary name
    is not in conftest's _AUDIO_TOOLS guard list, so the call really reaches
    Python's subprocess machinery and really fails to find the executable."""
    from arctis_sound_manager import service_control as sc

    result = sc.run_raw(["definitely-not-a-real-binary-asm-test-xyz", "status"])

    assert result is None


def test_run_raw_pins_argv_shape(monkeypatch):
    """run_raw() must hand dinitctl the same argv shape asm-setup always sent
    (`dinitctl <verb> <service-name>`, no injected flags) — pinned here since
    EXT-2's fix moved every call through this function. Confirmed against the
    real dinitctl 0.22.1 source (doc/manpages/dinitctl.8.m4's SYNOPSIS: `dinitctl
    [options] status service-name`, `... enable [--from from-service]
    to-service`, etc.) — dinitctl itself is not installed on this machine (see
    the task report), so this is a mocked-transport test; the real-binary
    proof for the exception path is test_run_raw_returns_none_for_missing_binary
    above, and the manpage citation is the source for the argv grammar."""
    from arctis_sound_manager import service_control as sc

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return type("R", (), {"returncode": 0, "stdout": "State: STARTED\n", "stderr": ""})()

    monkeypatch.setattr(sc, "_abs_exe", lambda name: name)  # isolate argv construction
    with patch("subprocess.run", side_effect=fake_run):
        result = sc.run_raw(["dinitctl", "status", "pipewire-filter-chain"])

    assert captured["cmd"] == ["dinitctl", "status", "pipewire-filter-chain"]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert result is not None and result.returncode == 0


def test_run_raw_returns_none_on_timeout(monkeypatch):
    """run_raw() catches subprocess.TimeoutExpired too, not only FileNotFoundError."""
    import subprocess as sp
    from arctis_sound_manager import service_control as sc

    monkeypatch.setattr(sc, "_abs_exe", lambda name: name)
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="dinitctl", timeout=10)):
        result = sc.run_raw(["dinitctl", "status", "pipewire-filter-chain"], timeout=10)

    assert result is None


# ── EXT-2: scripts/setup.py._setup_dinit_services() survives a missing dinitctl ──


def test_setup_dinit_services_survives_missing_dinitctl(tmp_path, monkeypatch, capsys):
    """The regression itself: with every dinitctl/pgrep call unavailable (as it
    would be if dinitctl were missing from PATH — run_raw() returns None for
    exactly that reason), _setup_dinit_services() must run to completion
    instead of raising, and must say so instead of going silent."""
    import arctis_sound_manager.scripts.setup as setup_mod

    fake_dinit_dir = tmp_path / "dinit.d"
    monkeypatch.setattr(setup_mod, "HOME_DINIT_SERVICE_FOLDER", fake_dinit_dir)
    monkeypatch.setattr(setup_mod, "filter_chain_conf_path",
                        lambda: str(tmp_path / "filter-chain.conf"))
    monkeypatch.setattr(setup_mod, "_ensure_dinit_boot_target", lambda: None)
    monkeypatch.setattr(setup_mod, "write_xdg_autostart", lambda: None)
    monkeypatch.setattr(setup_mod, "_has_xdg_autostart_consumer", lambda: True)
    monkeypatch.setattr(setup_mod, "write_xprofile_fallback", lambda: True)

    # Simulate "dinitctl (and pgrep) not on PATH": every raw command asm-setup
    # would have issued now returns None instead of an actual CompletedProcess.
    monkeypatch.setattr(setup_mod.sc, "run_raw", lambda *a, **k: None)

    setup_mod._setup_dinit_services()  # must not raise

    out = capsys.readouterr().out
    assert "unavailable" in out  # a clear message, not a swallowed failure

    # The part that doesn't need dinitctl at all (writing the service files)
    # still happened.
    assert (fake_dinit_dir / "arctis-manager").exists()
    assert (fake_dinit_dir / "pipewire-filter-chain").exists()


def test_setup_dinit_services_uses_run_raw_not_bare_subprocess(tmp_path, monkeypatch):
    """Every dinitctl/pgrep call in _setup_dinit_services() must go through
    service_control.run_raw() (the single point of passage) rather than a
    second, unguarded subprocess.run — that duplication is exactly how EXT-2
    happened. Asserts run_raw is actually invoked (not just present)."""
    import arctis_sound_manager.scripts.setup as setup_mod

    fake_dinit_dir = tmp_path / "dinit.d"
    monkeypatch.setattr(setup_mod, "HOME_DINIT_SERVICE_FOLDER", fake_dinit_dir)
    monkeypatch.setattr(setup_mod, "filter_chain_conf_path",
                        lambda: str(tmp_path / "filter-chain.conf"))
    monkeypatch.setattr(setup_mod, "_ensure_dinit_boot_target", lambda: None)
    monkeypatch.setattr(setup_mod, "write_xdg_autostart", lambda: None)
    monkeypatch.setattr(setup_mod, "_has_xdg_autostart_consumer", lambda: True)
    monkeypatch.setattr(setup_mod, "write_xprofile_fallback", lambda: True)

    calls: list[list[str]] = []

    def fake_run_raw(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(setup_mod.sc, "run_raw", fake_run_raw)
    with patch("subprocess.run") as mock_subprocess_run, patch("time.sleep"):
        setup_mod._setup_dinit_services()
        mock_subprocess_run.assert_not_called()

    dinitctl_calls = [c for c in calls if c[0] == "dinitctl"]
    assert len(dinitctl_calls) >= 8  # matches RAPPORT-CHAOS-ASM.md's "~12 raw dinitctl calls"


# ── SD-2: sonar_to_pipewire._dinit_crash_loop_settled() ───────────────────


def test_dinit_crash_loop_settled_true_when_service_stays_down():
    """Watches is_active() settle to False by the last sample -> crash-loop
    signal (dinit's own restart-limit having given up)."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    with patch("arctis_sound_manager.service_control.is_active", return_value=False), \
         patch("time.sleep"):
        result = stp._dinit_crash_loop_settled("filter-chain", window_s=3.0, interval_s=1.0)

    assert result is True


def test_dinit_crash_loop_settled_false_when_service_recovers():
    """A service that is active by the final sample is not reported as a
    crash-loop, even if is_active() flickered false earlier in the window (a
    single benign restart must not false-positive)."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    with patch("arctis_sound_manager.service_control.is_active",
               side_effect=[False, True, True]), \
         patch("time.sleep"):
        result = stp._dinit_crash_loop_settled("filter-chain", window_s=3.0, interval_s=1.0)

    assert result is False


# ── SD-2: ensure_filter_chain_healthy() on dinit ───────────────────────────


def test_ensure_filter_chain_healthy_arms_safe_mode_on_dinit_crash_loop(tmp_path, monkeypatch):
    """The finding itself: a filter-chain that looks active on the first
    is_active() sample (check 1 passes) but is actually crash-looping must
    still arm safe mode on dinit, where NRestarts is unavailable. Simulates
    dinit's own restart-limit having fired: is_active() is True on the very
    first call (the check-1 sample), then settles to False for the rest of
    the observation window."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    call_count = {"n": 0}

    def is_active_side_effect(*_a, **_k):
        call_count["n"] += 1
        return call_count["n"] == 1  # True on the check-1 sample only

    with patch("arctis_sound_manager.service_control.is_active",
               side_effect=is_active_side_effect), \
         patch("arctis_sound_manager.service_control.detect_init", return_value="dinit"), \
         patch("arctis_sound_manager.service_control.restart", return_value=True), \
         patch("time.sleep"):
        result = stp.ensure_filter_chain_healthy()

    assert result is False
    assert stp._filter_chain_safe_mode is True
    # More than the single check-1 sample was taken (the live observation ran).
    assert call_count["n"] > 1


def test_ensure_filter_chain_healthy_dinit_no_false_positive_when_healthy(tmp_path, monkeypatch):
    """A genuinely healthy dinit filter-chain (always active) must not be
    pushed into safe mode by the new fallback — no false positive from the
    SD-2 fix on the common, non-crash-looping case."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    with patch("arctis_sound_manager.service_control.is_active", return_value=True), \
         patch("arctis_sound_manager.service_control.detect_init", return_value="dinit"), \
         patch("time.sleep"):
        result = stp.ensure_filter_chain_healthy()

    assert result is True
    assert stp._filter_chain_safe_mode is False
