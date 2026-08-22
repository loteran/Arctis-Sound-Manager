# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""CHA-4: a second daemon must refuse to start before it does any damage.

CoreEngine.start() is a plain synchronous method, so the work it does —
starting the USB monitor, which drives configure_virtual_sinks() ->
setup_loopbacks() -> LoopbackManager's orphan sweep — happens while
`asyncio.create_task(core_engine.start())`'s argument is evaluated, tens of
lines before the D-Bus name guard fires. The sweep excludes only the calling
process's own handles, so a hand-started second daemon reaps the *running*
daemon's healthy loopbacks and rewrites clock.force-quantum, then dies on the
taken bus name. The lock has to be taken before CoreEngine exists at all.
"""
from __future__ import annotations

import logging

import pytest

from arctis_sound_manager.scripts import daemon


def test_second_instance_exits_instead_of_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    logger = logging.getLogger("test-daemon")

    first = daemon._single_instance_lock(logger)
    assert first is not None, "the first daemon must get the lock"

    with pytest.raises(SystemExit) as exit_info:
        daemon._single_instance_lock(logger)
    assert exit_info.value.code == 1

    # Releasing it lets the next daemon start — a restart must not need a
    # stale lock file reaped by hand.
    first.close()
    second = daemon._single_instance_lock(logger)
    assert second is not None
    second.close()


def test_the_lock_is_taken_before_the_engine_is_built():
    """Order is the whole finding: a lock taken after CoreEngine() would
    still let the second process reap the first one's loopbacks."""
    import inspect

    src = inspect.getsource(daemon.main_async)
    assert "_single_instance_lock" in src
    assert src.index("_single_instance_lock") < src.index("CoreEngine()"), (
        "the single-instance lock must be acquired before CoreEngine is "
        "constructed — that is what CHA-4 is about"
    )


def test_an_unwritable_runtime_dir_does_not_block_startup(tmp_path, monkeypatch):
    """A daemon that will not start because it could not open a lock file is
    worse than one running without the lock."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(daemon.os, "makedirs",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only")))

    assert daemon._single_instance_lock(logging.getLogger("test-daemon")) is None
