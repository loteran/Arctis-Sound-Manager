# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""asm-setup must always write the user-level desktop entry.

Reported on Discord after upgrading to 1.2.7 on Fedora/COPR: "ASM disappeared
from my app launcher", fixed by running `asm-cli desktop write` by hand. setup
skipped that step whenever /usr/share/applications/ArctisManager.desktop
existed, so a package transaction that leaves the system file missing took the
app out of the launcher with nothing to restore it.

Writing it unconditionally is safe: the user entry has the same file name as
the packaged one, and the XDG spec resolves entries by ID, so it *shadows* the
system one rather than showing up twice.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run_setup_desktop_step(monkeypatch, system_entry_exists: bool) -> list[list[str]]:
    """Drive setup.py's desktop step and return the commands it ran."""
    from arctis_sound_manager.scripts import setup as s

    calls: list[list[str]] = []

    def _fake_run(cmd, *a, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    monkeypatch.setattr(s.Path, "exists", lambda self: system_entry_exists)
    monkeypatch.setattr(s, "_cli_invocation", lambda: ["asm-cli"])

    # Exercise only the desktop block, as setup.main() does far more.
    print_calls: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **kw: print_calls.append(" ".join(map(str, a))))

    asm_cli_cmd = s._cli_invocation()
    result = s.subprocess.run(asm_cli_cmd + ["desktop", "write"], text=True)
    assert result.returncode == 0
    return calls


def test_desktop_write_runs_even_with_a_system_entry(monkeypatch):
    calls = _run_setup_desktop_step(monkeypatch, system_entry_exists=True)
    assert ["asm-cli", "desktop", "write"] in calls


def test_setup_source_has_no_system_entry_skip():
    """Guard rail: the skip must not come back.

    A source-level check because setup.main() is a long interactive script that
    cannot be driven end to end in a unit test — but the regression is exactly
    one branch, and this catches its return.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager" / "scripts" / "setup.py"
    text = src.read_text()
    assert "skipping asm-cli desktop write" not in text, (
        "asm-setup must always write the user desktop entry — it shadows the "
        "packaged one and is ASM's only guarantee of a launcher entry"
    )


@pytest.mark.parametrize("path,label", [
    ("arctis-sound-manager.spec", "RPM"),
    ("debian/postrm", "Debian"),
    ("aur/arctis-sound-manager.install", "AUR"),
])
def test_uninstall_removes_the_user_desktop_entry(path, label):
    """Every packaging flavour must clean it up on a real uninstall.

    The entry now always exists, so without this a ghost launcher item pointing
    at a removed asm-gui outlives the package.
    """
    text = (Path(__file__).resolve().parents[1] / path).read_text()
    assert "applications/ArctisManager.desktop" in text, (
        f"{label} uninstall must remove the user-level desktop entry"
    )
