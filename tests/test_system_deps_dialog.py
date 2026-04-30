"""Tests for the pure-Python helpers in system_deps_dialog.

The Qt widget itself needs a live QApplication + event loop to test
properly — that belongs in the manual GUI smoke-test (Phase 4 of
~/Bureau/ASM_PLAN_DEPS_CHECK.md). What we DO test here:

* the skip-marker file logic — written on close + version-aware reset
* `should_show_dialog()` gating — combination of skip marker + checker

These are the two places where a regression would silently re-spam the
dialog or, worse, silently hide it for users who genuinely have a
broken install.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

# Importing the dialog module pulls PySide6 — skip the whole file when
# the test environment doesn't have it (CI containers without GUI deps).
pyside6 = pytest.importorskip("PySide6")

from arctis_sound_manager.gui import system_deps_dialog as sdd
from arctis_sound_manager.system_deps_checker import (
    CheckResult, DepCheck, Severity,
)


def _make_check(*, ok: bool, severity: Severity = Severity.BLOCKING) -> CheckResult:
    return CheckResult(
        check=DepCheck(
            name="x", severity=severity, feature="f",
            detect=lambda: ok, install_commands={"fedora": ["dnf", "install", "x"]},
        ),
        ok=ok,
    )


def test_skip_marker_writes_current_version(tmp_path, monkeypatch):
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / ".skip_deps_check")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    sdd._write_skip_marker()
    assert (tmp_path / ".skip_deps_check").read_text() == "1.0.86"


def test_skip_marker_creates_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "config" / ".skip_deps_check"
    monkeypatch.setattr(sdd, "_SKIP_MARKER", nested)
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    sdd._write_skip_marker()
    assert nested.exists()


def test_skip_marker_matches_version_true(tmp_path, monkeypatch):
    marker = tmp_path / ".skip_deps_check"
    marker.write_text("1.0.86\n")
    monkeypatch.setattr(sdd, "_SKIP_MARKER", marker)
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    assert sdd._skip_marker_matches_version() is True


def test_skip_marker_resets_on_upgrade(tmp_path, monkeypatch):
    """The marker must NOT match after the user upgrades — that's the whole
    point of versioning the skip; otherwise users miss new dep requirements
    introduced in subsequent releases."""
    marker = tmp_path / ".skip_deps_check"
    marker.write_text("1.0.85\n")
    monkeypatch.setattr(sdd, "_SKIP_MARKER", marker)
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    assert sdd._skip_marker_matches_version() is False


def test_skip_marker_missing_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / "nope")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    assert sdd._skip_marker_matches_version() is False


def test_should_show_skips_when_marker_matches(tmp_path, monkeypatch):
    marker = tmp_path / ".skip_deps_check"
    marker.write_text("1.0.86")
    monkeypatch.setattr(sdd, "_SKIP_MARKER", marker)
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")

    # Even with a failing BLOCKING check, the marker takes priority.
    monkeypatch.setattr(sdd, "run_all_checks",
                        lambda: [_make_check(ok=False, severity=Severity.BLOCKING)])
    assert sdd.should_show_dialog() is False


def test_should_show_when_blocking_dep_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / "nope")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    monkeypatch.setattr(sdd, "run_all_checks",
                        lambda: [_make_check(ok=False, severity=Severity.BLOCKING)])
    assert sdd.should_show_dialog() is True


def test_should_show_when_degraded_dep_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / "nope")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    monkeypatch.setattr(sdd, "run_all_checks",
                        lambda: [_make_check(ok=False, severity=Severity.DEGRADED)])
    assert sdd.should_show_dialog() is True


def test_should_NOT_show_when_only_optional_missing(tmp_path, monkeypatch):
    """OPTIONAL deps (gh CLI) must never trigger the dialog — bug-report
    auto-submit has a graceful manual fallback and we must not nag users
    who don't file tickets."""
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / "nope")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    monkeypatch.setattr(sdd, "run_all_checks",
                        lambda: [_make_check(ok=False, severity=Severity.OPTIONAL)])
    assert sdd.should_show_dialog() is False


def test_should_NOT_show_when_all_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(sdd, "_SKIP_MARKER", tmp_path / "nope")
    monkeypatch.setattr(sdd, "project_version", lambda: "1.0.86")
    monkeypatch.setattr(sdd, "run_all_checks",
                        lambda: [_make_check(ok=True, severity=Severity.BLOCKING)])
    assert sdd.should_show_dialog() is False
