# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The PID-file singleton must not mistake a recycled number for an instance.

Found on a live machine, twice, because the check had been copy-pasted.

`arctis-stream-guard` had been dead for four days: "failed (start-limit-hit)"
over a main process that exited **0/SUCCESS** in 154 ms. Its PID file was
written at 11:20:08 and the machine booted at 11:25:12 — the file had outlived
a reboot, and its number (1345) was alive again as something else.

`arctis-video-router` was caught mid-loop on the same machine, restart counter
37 and climbing, deferring to pid 1346. That one is worse than it sounds: 1346
was not a process at all but a `gmain` *thread* inside gnome-keyring-daemon.
`os.kill` accepts thread ids, and there are far more of those than processes,
so "the number is alive" is a much weaker signal than it looks.

Neither failure looks like a failure from outside: the unit exits 0, systemd
reports success, and the first symptom is audio going somewhere it should not.

The daemons now share `arctis_sound_manager.singleton`, so these tests drive
that module and then check both call sites are wired to it.
"""
from __future__ import annotations

import os

import pytest

from arctis_sound_manager import singleton


@pytest.fixture
def proc(tmp_path, monkeypatch):
    """A fake /proc the tests can populate, laid out the way the real one is."""
    root = tmp_path / "proc"
    root.mkdir()
    monkeypatch.setattr(singleton, "_PROC_ROOT", str(root))

    def _add(pid: int, *argv: str) -> None:
        entry = root / str(pid)
        entry.mkdir()
        (entry / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    return _add


@pytest.fixture
def pid_file(tmp_path):
    return tmp_path / "guard.pid"


def test_takes_over_when_the_pid_belongs_to_something_else(pid_file, proc):
    """The reported bug: a live PID that is not this daemon."""
    pid_file.write_text("1345")
    proc(1345, "/usr/lib/systemd/systemd-journald")

    assert singleton.acquire(pid_file, "asm-stream-guard") is True
    assert pid_file.read_text() == str(os.getpid())


def test_takes_over_when_the_pid_is_a_thread_of_another_process(pid_file, proc):
    """The router's failure: pid 1346 was a gmain thread in gnome-keyring."""
    pid_file.write_text("1346")
    proc(1346, "/usr/bin/gnome-keyring-daemon", "--foreground")

    assert singleton.acquire(pid_file, "asm-router") is True
    assert pid_file.read_text() == str(os.getpid())


def test_takes_over_when_the_pid_is_gone(pid_file, proc):
    pid_file.write_text("1345")

    assert singleton.acquire(pid_file, "asm-stream-guard") is True


def test_defers_to_a_real_second_instance(pid_file, proc):
    """The check still has to do its actual job."""
    pid_file.write_text("4242")
    proc(4242, "/usr/bin/python", "/usr/bin/asm-stream-guard")

    assert singleton.acquire(pid_file, "asm-stream-guard") is False
    assert pid_file.read_text() == "4242"       # not stolen from the live one


def test_the_two_daemons_do_not_defer_to_each_other(pid_file, proc):
    """A router must not be held off by a running guard, or vice versa.

    They keep separate PID files, so this only bites if a name is matched too
    loosely — but "asm-router" is a substring of nothing here by luck rather
    than design, and the guard's name contains no daemon's name but its own.
    """
    pid_file.write_text("4242")
    proc(4242, "/usr/bin/python", "/usr/bin/asm-stream-guard")

    assert singleton.acquire(pid_file, "asm-router") is True


def test_a_garbage_pid_file_is_taken_over(pid_file, proc):
    pid_file.write_text("not a pid")

    assert singleton.acquire(pid_file, "asm-stream-guard") is True


def test_no_pid_file_at_all(pid_file, proc):
    assert singleton.acquire(pid_file, "asm-stream-guard") is True
    assert pid_file.read_text() == str(os.getpid())


def test_our_own_pid_is_not_another_instance(pid_file, proc):
    """A file left naming *this* process must not stop it starting."""
    pid_file.write_text(str(os.getpid()))
    proc(os.getpid(), "/usr/bin/asm-stream-guard")

    assert singleton.acquire(pid_file, "asm-stream-guard") is True


def test_release_removes_the_file_and_tolerates_a_missing_one(pid_file, proc):
    singleton.acquire(pid_file, "asm-stream-guard")
    singleton.release(pid_file)

    assert not pid_file.exists()
    singleton.release(pid_file)         # must not raise the second time


def test_parent_directory_is_created(tmp_path, proc):
    nested = tmp_path / "config" / "arctis_manager" / "guard.pid"

    assert singleton.acquire(nested, "asm-stream-guard") is True
    assert nested.read_text() == str(os.getpid())


# ── the daemons are actually wired to it ───────────────────────────────────
# The bug survived one fix because the second copy was never touched. These
# pin that neither daemon has a private implementation to drift again.

def test_stream_guard_uses_the_shared_singleton(monkeypatch):
    from arctis_sound_manager.scripts import stream_guard as sg

    seen: list = []
    monkeypatch.setattr(sg.singleton, "acquire",
                        lambda path, name, log=None: seen.append((path, name)) or True)

    assert sg._acquire_singleton() is True
    assert seen[0][0] == sg._PID_FILE
    assert seen[0][1] == "asm-stream-guard"


def test_video_router_uses_the_shared_singleton(monkeypatch):
    pytest.importorskip("pulsectl")
    from arctis_sound_manager.scripts import video_router as vr

    seen: list = []
    monkeypatch.setattr(vr.singleton, "acquire",
                        lambda path, name, log=None: seen.append((path, name)) or True)

    assert vr._acquire_singleton() is True
    assert seen[0][0] == vr._PID_FILE
    assert seen[0][1] == "asm-router"
