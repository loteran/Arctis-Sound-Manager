# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression guard for issue #123.

The daemon runs libusb device I/O and PipeWire/systemctl spawns in the same
asyncio thread pool. CPython's subprocess only takes the posix_spawn (vfork)
path when the executable is an *absolute* path AND close_fds is False;
otherwise it fork()s, replaying libusb's pthread_atfork handlers and COW-copying
the whole VM while a sibling thread is inside libusb poll() — a heap-corruption
vector that produced random SIGSEGVs in the daemon. These tests fail if any of
the daemon's hot-path spawns regress back to the fork()+exec path.
"""
from __future__ import annotations

import os
import subprocess

import pytest

if not hasattr(os, "posix_spawn"):
    pytest.skip("posix_spawn unavailable on this platform", allow_module_level=True)

from arctis_sound_manager import pw_utils, service_control


class _SpawnSpy:
    """Count posix_spawn vs fork_exec calls made by subprocess."""

    def __enter__(self):
        import _posixsubprocess

        self.hits = {"posix_spawn": 0, "fork_exec": 0}
        self._mod = _posixsubprocess
        self._o_spawn = os.posix_spawn
        self._o_fork = _posixsubprocess.fork_exec

        def spy_spawn(*a, **k):
            self.hits["posix_spawn"] += 1
            return self._o_spawn(*a, **k)

        def spy_fork(*a, **k):
            self.hits["fork_exec"] += 1
            return self._o_fork(*a, **k)

        os.posix_spawn = spy_spawn
        _posixsubprocess.fork_exec = spy_fork
        return self

    def __exit__(self, *exc):
        os.posix_spawn = self._o_spawn
        self._mod.fork_exec = self._o_fork


def test_abs_exe_resolves_to_absolute_path():
    assert os.path.isabs(pw_utils._abs_exe("true"))
    assert os.path.isabs(service_control._abs_exe("true"))


def test_pw_run_uses_posix_spawn_not_fork():
    with _SpawnSpy() as spy:
        pw_utils._pw_run(["true"], capture_output=True, timeout=3)
    assert spy.hits == {"posix_spawn": 1, "fork_exec": 0}


def test_pw_run_defaults_close_fds_false():
    # Sanity: a bare-name + default close_fds call WOULD fork, proving the
    # helper's absolute-path + close_fds=False is what buys posix_spawn.
    with _SpawnSpy() as spy:
        subprocess.run(["true"], capture_output=True, timeout=3)
    assert spy.hits["fork_exec"] == 1
    assert spy.hits["posix_spawn"] == 0


def test_service_control_run_uses_posix_spawn_not_fork():
    with _SpawnSpy() as spy:
        service_control._run(["true"], 5, True)
    assert spy.hits == {"posix_spawn": 1, "fork_exec": 0}
