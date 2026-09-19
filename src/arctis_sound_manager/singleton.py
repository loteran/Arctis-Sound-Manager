# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""One PID-file singleton, shared by the daemons that need one.

Both `asm-stream-guard` and `asm-router` had grown their own copy of the same
sixteen lines, and both copies had the same defect — so the second one was
still failing on a live machine after the first was fixed. That is the whole
argument for this module: the check is subtle enough to get wrong twice, and
a lesson learned in one copy does not reach the other.

**What the check has to answer.** Not "is this number in use" but "is another
one of *me* running". Those come apart the moment a PID file survives a
reboot, which is exactly when it matters: `release` only runs on a clean exit,
so a killed session leaves the file behind, and the next boot reissues low
numbers within seconds.

`os.kill(pid, 0)` answers the first question. It is also worse than it looks,
because it succeeds for *thread* ids too — the failure seen in the wild was a
router deferring to pid 1346, which was a `gmain` thread inside
gnome-keyring-daemon. Thread ids vastly outnumber process ids, so the odds of
a leftover number being "alive" are far higher than a naive reading suggests.

Reading `/proc/<pid>/cmdline` answers the second. /proc is Linux-only and
these daemons are PipeWire-only, so there is no portability being traded.

**Ambiguity resolves towards starting.** An unreadable /proc entry counts as
"not ours". A second guard or router running briefly is harmless — they
converge on the same graph — while refusing to start is the failure that
hides: the unit exits 0, systemd reports success, and nothing looks wrong
until audio goes somewhere it should not.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

# Overridden by tests. Kept as a module-level string for the same reason
# loopback_manager keeps one: it makes /proc injectable without a fixture
# having to patch open().
_PROC_ROOT = "/proc"


def _read_cmdline(pid: int) -> bytes:
    """Raw ``/proc/<pid>/cmdline``, or empty bytes if it cannot be read.

    A vanished process, a zombie (whose cmdline reads empty) and another
    user's process are all ordinary outcomes here, not errors — every one of
    them means "not ours", which is what the caller does with empty bytes.
    """
    try:
        with open(os.path.join(_PROC_ROOT, str(pid), "cmdline"), "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def is_running(pid: int, exe_name: str) -> bool:
    """True when *pid* belongs to a live process whose argv names *exe_name*."""
    return exe_name.encode() in _read_cmdline(pid)


def acquire(pid_file: Path, exe_name: str,
            log: logging.Logger | None = None) -> bool:
    """Claim *pid_file* for this process. False means another instance has it.

    *exe_name* is matched against the recorded process's command line, so it
    has to be the name that actually appears there — the console script
    (``asm-router``), not the module.
    """
    log = log or logging.getLogger(__name__)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            old_pid = None      # unreadable or garbage — take over
        if old_pid is not None and old_pid != os.getpid() \
                and is_running(old_pid, exe_name):
            log.warning("Another %s instance (PID %d) is already running — exiting.",
                        exe_name, old_pid)
            return False
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    return True


def release(pid_file: Path) -> None:
    """Drop the claim. Never raises — this runs on the way out."""
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass
