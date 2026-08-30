# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

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


# ── container.py delegation + defensive fallbacks ───────────────────────────
#
# system_deps_dialog used to carry its own copy of _running_in_container,
# copy-pasted from udev_checker.py and systemd.py, and the three drifted.
# container.py is now the one shared home for this and the host-reaching
# helpers built on it (host_exec, host_distro). These tests check the
# delegation and, importantly, that a broken/missing container module falls
# back to "behave as if native" rather than taking the dialog down with it —
# the same defensive contract systemd.py already relies on.

def test_running_in_container_delegates_to_container_module(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.container.running_in_container", lambda: True)
    assert sdd._running_in_container() is True


def test_running_in_container_false_on_import_failure(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "arctis_sound_manager.container":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    assert sdd._running_in_container() is False


def test_host_exec_delegates_to_container_module(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.container.host_exec", lambda: ["distrobox-host-exec"])
    assert sdd._host_exec() == ["distrobox-host-exec"]


def test_host_exec_none_means_no_way_out(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.container.host_exec", lambda: None)
    assert sdd._host_exec() is None


def test_host_exec_falls_back_to_no_prefix_on_import_failure(monkeypatch):
    """A broken import must not be indistinguishable from 'stuck in a
    container with no way out' — that would refuse to run commands that
    used to work fine before this file knew about containers at all."""
    import builtins
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "arctis_sound_manager.container":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    assert sdd._host_exec() == []


def test_host_distro_delegates_to_container_module(monkeypatch):
    monkeypatch.setattr("arctis_sound_manager.container.host_distro", lambda: "bazzite")
    assert sdd._host_distro() == "bazzite"


def test_host_distro_none_on_import_failure(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == "arctis_sound_manager.container":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    assert sdd._host_distro() is None


# ── Immutable host → point at the maintained script, never fabricate ───────

def test_immutable_host_script_bazzite(monkeypatch):
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    assert sdd._immutable_host_script("bazzite") == "bazzite.sh"


def test_immutable_host_script_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    assert sdd._immutable_host_script("BAZZITE") == "bazzite.sh"


def test_silverblue_and_kinoite_both_report_fedora_and_share_a_script(monkeypatch):
    """The case that made naming unworkable: an ostree Fedora is "fedora" like
    any other, so only the immutability probe tells them apart."""
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    assert sdd._immutable_host_script("fedora") == "silverblue.sh"


def test_immutable_host_script_none_for_mutable_host(monkeypatch):
    """Fedora Workstation, Ubuntu, Arch, … — dnf/apt/pacman all write to a
    normal writable rootfs, so nothing here should redirect them. Note that
    "fedora" appears here AND in the immutable test above: the distribution
    name alone never decides this, the probe does."""
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: False)
    assert sdd._immutable_host_script("fedora") is None
    assert sdd._immutable_host_script("ubuntu") is None
    assert sdd._immutable_host_script("arch") is None


def test_immutable_host_script_none_when_host_is_mutable_and_unknown(monkeypatch):
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: False)
    assert sdd._immutable_host_script(None) is None


def test_unidentified_immutable_host_still_gets_a_script(monkeypatch):
    """An ostree host we could not name is still an ostree host: the generic
    script beats an install command it will reject."""
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    assert sdd._immutable_host_script(None) == "silverblue.sh"


def test_unprobeable_host_keeps_its_install_path(monkeypatch):
    """A host we cannot reach is not assumed immutable — unknown must never
    silently take away an install path that works."""
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: False)
    assert sdd._immutable_host_script("bazzite") is None
