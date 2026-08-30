# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_udev_container_boundary.py — the write-side counterpart of #<container
udev boundary> gets fixed here.

The bug: udev_checker.py already learned to *read* the host's udev rules
through distrobox-host-exec (container.py's docstring tells the whole story),
but `asm-cli udev write-rules` kept writing into the CONTAINER's own /etc,
where udev never looks, and reported success anyway. The "install rules"
dialog then reopened on the very next launch, with no way for the user to
escape it from the GUI.

These tests pin the fix's three required behaviours:
  1. Outside a container, nothing changes — this is the entire install path
     for every normal user.
  2. Inside a container with a way out (host_exec() returns a prefix), the
     write actually crosses into the host — the prefix shows up in the argv
     that gets run, and the container's own local file is never touched.
  3. Inside a container with NO way out (host_exec() is None, or the host
     call itself fails), nothing is written locally, the call reports
     failure, and the user is handed the exact command to run by hand. The
     old silent-local-write fallback is exactly the bug being fixed here —
     it must never come back.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from arctis_sound_manager import container
from arctis_sound_manager.scripts import cli


class _Recorder:
    """Fake subprocess.run: records every argv, always answers *returncode*."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode)


@pytest.fixture(autouse=True)
def _isolated_stage_dir(tmp_path, monkeypatch):
    """Never let a test touch the real ~/.cache/arctis-sound-manager."""
    monkeypatch.setattr(cli, "_HOST_STAGE_DIR", tmp_path / "cache")


def test_not_in_container_writes_locally_unchanged(tmp_path, monkeypatch):
    """Outside a container: write_udev_rules must behave exactly as before."""
    monkeypatch.setattr(container, "running_in_container", lambda: False)

    rules_path = tmp_path / "91-steelseries-arctis.rules"
    rc = cli.write_udev_rules(rules_path, create_directories=True, force_write=True)

    assert rc == 0
    assert rules_path.exists()
    assert "SUBSYSTEM" in rules_path.read_text()


def test_in_container_with_host_exec_crosses_to_host(tmp_path, monkeypatch):
    """Inside a container with a way out: the host prefix must appear in the
    invoked argv, and the container's local rules_path must stay untouched."""
    monkeypatch.setattr(container, "running_in_container", lambda: True)
    monkeypatch.setattr(container, "host_exec", lambda: ["distrobox-host-exec"])

    recorder = _Recorder(returncode=0)
    monkeypatch.setattr(cli.subprocess, "run", recorder)

    rules_path = tmp_path / "91-steelseries-arctis.rules"
    rc = cli.write_udev_rules(rules_path, create_directories=True, force_write=True)

    assert rc == 0
    # Nothing was ever written to the container's own filesystem.
    assert not rules_path.exists()
    # The host prefix must actually have been used to run something.
    assert recorder.calls, "expected at least one host-crossing subprocess call"
    assert any(call[:1] == ["distrobox-host-exec"] for call in recorder.calls)


def test_in_container_with_reload_crosses_to_host(tmp_path, monkeypatch):
    """and_reload=True must also cross into the host, not run udevadm locally."""
    monkeypatch.setattr(container, "running_in_container", lambda: True)
    monkeypatch.setattr(container, "host_exec", lambda: ["distrobox-host-exec"])

    recorder = _Recorder(returncode=0)
    monkeypatch.setattr(cli.subprocess, "run", recorder)

    rules_path = tmp_path / "91-steelseries-arctis.rules"
    rc = cli.write_udev_rules(rules_path, create_directories=True, force_write=True, and_reload=True)

    assert rc == 0
    assert not rules_path.exists()
    assert any(call[:1] == ["distrobox-host-exec"] for call in recorder.calls)


def test_in_container_with_no_host_exec_fails_loud(tmp_path, monkeypatch, capsys):
    """host_exec() is None (no distrobox-host-exec, or an unreachable host):
    nothing local, a non-zero return, and a copy-pasteable command printed."""
    monkeypatch.setattr(container, "running_in_container", lambda: True)
    monkeypatch.setattr(container, "host_exec", lambda: None)

    rules_path = tmp_path / "91-steelseries-arctis.rules"
    rc = cli.write_udev_rules(rules_path, create_directories=True, force_write=True)

    assert rc != 0
    assert not rules_path.exists()

    out = capsys.readouterr().out
    assert "dump-rules" in out
    assert "udevadm control --reload-rules" in out
    assert str(rules_path) in out


def test_in_container_host_call_fails_no_silent_local_fallback(tmp_path, monkeypatch, capsys):
    """The host call runs but fails (non-zero exit on both elevators tried):
    still no local write, still a non-zero return, still the manual command."""
    monkeypatch.setattr(container, "running_in_container", lambda: True)
    monkeypatch.setattr(container, "host_exec", lambda: ["distrobox-host-exec"])

    recorder = _Recorder(returncode=1)
    monkeypatch.setattr(cli.subprocess, "run", recorder)

    rules_path = tmp_path / "91-steelseries-arctis.rules"
    rc = cli.write_udev_rules(rules_path, create_directories=True, force_write=True)

    assert rc != 0
    assert not rules_path.exists()
    # Both elevators (sudo, then pkexec) must have been tried through the host.
    assert any(call[:2] == ["distrobox-host-exec", "sudo"] for call in recorder.calls)
    assert any(call[:2] == ["distrobox-host-exec", "pkexec"] for call in recorder.calls)

    out = capsys.readouterr().out
    assert "dump-rules" in out
    assert "udevadm control --reload-rules" in out


def test_reload_udev_rules_in_container_with_no_host_exec_fails_loud(monkeypatch, capsys):
    """reload_udev_rules has the same boundary problem and the same fallback."""
    monkeypatch.setattr(container, "running_in_container", lambda: True)
    monkeypatch.setattr(container, "host_exec", lambda: None)

    rc = cli.reload_udev_rules()

    assert rc != 0
    out = capsys.readouterr().out
    assert "udevadm control --reload-rules" in out
