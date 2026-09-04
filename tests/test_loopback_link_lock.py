# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the cross-process link lock (issue #230).

Two processes rewriting the same PipeWire links at once SIGSEGVs
pipewire-filter-chain 1.6.x, so ``ensure_loopback_link`` / ``ensure_capture_link``
run under an flock. The lock has to be reentrant on two axes at once — the
daemon calls these passes from several threads, and the helpers compose — so
these tests pin both the mutual exclusion and the reentrancy.
"""

import os
import threading
import time

import pytest

from arctis_sound_manager import pw_utils


@pytest.fixture
def lock_file(tmp_path, monkeypatch):
    """Point the lock at a temp file so tests never touch the real config dir."""
    path = tmp_path / "loopback.lock"
    monkeypatch.setattr(pw_utils, "_LOOPBACK_LOCK_FILE", path)
    monkeypatch.setattr(pw_utils, "_loopback_lock_local", threading.local())
    monkeypatch.setattr(pw_utils, "_loopback_lock_thread", threading.RLock())
    return path


def test_lock_file_is_created(lock_file):
    with pw_utils._loopback_link_lock():
        assert lock_file.exists()


def test_reentrant_within_same_thread(lock_file):
    """A composed call (ensure_physical_output_links → ensure_loopback_link)
    must not deadlock on the lock its own caller already holds."""
    with pw_utils._loopback_link_lock():
        with pw_utils._loopback_link_lock():
            assert pw_utils._loopback_lock_local.depth == 2
        assert pw_utils._loopback_lock_local.depth == 1
    assert pw_utils._loopback_lock_local.depth == 0


def test_threads_are_serialized(lock_file):
    """Two daemon threads must not be inside the critical section together."""
    inside = []
    overlapped = []

    def worker():
        with pw_utils._loopback_link_lock():
            inside.append(1)
            if len(inside) > 1:
                overlapped.append(1)
            time.sleep(0.02)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not overlapped


def test_lock_released_on_exception(lock_file):
    """A failing pass must not leave the lock held for the whole session."""
    with pytest.raises(RuntimeError):
        with pw_utils._loopback_link_lock():
            raise RuntimeError("boom")

    assert pw_utils._loopback_lock_local.depth == 0
    # Still acquirable: the flock was released, not leaked.
    with pw_utils._loopback_link_lock():
        pass


def test_proceeds_unlocked_when_path_unwritable(tmp_path, monkeypatch):
    """An unwritable config dir degrades to running unlocked, never to no audio."""
    unwritable = tmp_path / "nope"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    monkeypatch.setattr(pw_utils, "_LOOPBACK_LOCK_FILE", unwritable / "loopback.lock")
    monkeypatch.setattr(pw_utils, "_loopback_lock_local", threading.local())
    monkeypatch.setattr(pw_utils, "_loopback_lock_thread", threading.RLock())

    ran = False
    try:
        with pw_utils._loopback_link_lock():
            ran = True
    finally:
        unwritable.chmod(0o700)

    assert ran


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_lock_excludes_another_process(lock_file):
    """The daemon and the GUI are different processes — the whole point of
    using flock rather than a threading lock."""
    import fcntl

    with pw_utils._loopback_link_lock():
        handle = open(lock_file, "a+")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            handle.close()


def test_ensure_loopback_link_runs_under_the_lock(lock_file, monkeypatch):
    """The decorator is actually applied — a regression here is invisible
    until a user hits the SIGSEGV."""
    seen = {}

    def fake_dump():
        seen["depth"] = getattr(pw_utils._loopback_lock_local, "depth", 0)
        return []

    monkeypatch.setattr(pw_utils, "_pw_dump", fake_dump)
    pw_utils.ensure_loopback_link("nope_playback", "nope_target")

    assert seen["depth"] == 1
