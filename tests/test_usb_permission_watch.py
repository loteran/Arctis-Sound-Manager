# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Discussions #140 / #190 — the permissions popup that came back every boot.

The dongle is usually enumerated early in the boot, before udev has applied
the rules to it, so the first USB acquisition fails with EACCES even though
the rules on disk are perfectly valid. A retry was added for that. It was not
enough, for two reasons this file pins down:

1. the budget was 1+3+6 = 10 seconds, which a busy boot beats;
2. once spent, nothing ever tried again — and the budget was only refilled by
   a successful acquisition or by the device being unplugged. On the common
   setup where the dongle never leaves its port, one slow boot meant the popup
   stayed until the user physically replugged it.
"""

from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager import core as _core


@pytest.fixture
def engine():
    """A CoreEngine with only the state this behaviour touches."""
    obj = object.__new__(_core.CoreEngine)
    obj.logger = MagicMock()
    obj.permission_error = False
    obj._usb_permission_attempt = 0
    obj._usb_permission_watching = False
    return obj


def _schedule(engine) -> float:
    """Run one scheduling pass and return the delay it asked the timer for."""
    with patch.object(_core.threading, "Timer") as timer:
        _core.CoreEngine._schedule_usb_permission_retry(engine)
        if not timer.called:
            return -1.0
        return timer.call_args[0][0]


# ── the window ────────────────────────────────────────────────────────────────

def test_fast_retries_cover_more_than_ten_seconds() -> None:
    """10s was the old budget, and it was losing the race on real machines."""
    assert sum(_core._USB_PERMISSION_RETRY_DELAYS) >= 60


def test_fast_retries_do_not_prompt() -> None:
    """A boot race is not a permissions problem, and must not look like one."""
    engine = object.__new__(_core.CoreEngine)
    engine.logger = MagicMock()
    engine.permission_error = True
    engine._usb_permission_attempt = 0
    engine._usb_permission_watching = False
    for _ in _core._USB_PERMISSION_RETRY_DELAYS:
        _schedule(engine)
        assert engine.permission_error is False


def test_delays_are_used_in_order(engine) -> None:
    seen = [_schedule(engine) for _ in _core._USB_PERMISSION_RETRY_DELAYS]
    assert seen == list(_core._USB_PERMISSION_RETRY_DELAYS)


# ── never giving up ───────────────────────────────────────────────────────────

def _exhaust(engine) -> None:
    for _ in _core._USB_PERMISSION_RETRY_DELAYS:
        _schedule(engine)


def test_keeps_checking_after_the_budget_is_spent(engine) -> None:
    """The heart of it: the old code returned without scheduling anything, so
    nothing in the running system would ever look again."""
    _exhaust(engine)
    assert _schedule(engine) == _core._USB_PERMISSION_WATCH_INTERVAL


def test_watch_does_not_stop_on_its_own(engine) -> None:
    _exhaust(engine)
    for _ in range(20):
        assert _schedule(engine) == _core._USB_PERMISSION_WATCH_INTERVAL


def test_dialog_is_raised_once_the_budget_is_spent(engine) -> None:
    _exhaust(engine)
    assert engine.permission_error is False   # still only fast retries so far
    _schedule(engine)
    assert engine.permission_error is True


def test_the_error_is_logged_once_not_every_thirty_seconds(engine) -> None:
    _exhaust(engine)
    for _ in range(5):
        _schedule(engine)
    assert engine.logger.error.call_count == 1


# ── standing back down ────────────────────────────────────────────────────────

def test_watch_is_disarmed_by_a_successful_acquisition(engine) -> None:
    """Otherwise a watch armed hours ago keeps the flag up after ASM recovers."""
    _exhaust(engine)
    _schedule(engine)
    assert engine._usb_permission_watching is True

    # what the acquisition path does on success
    engine._usb_permission_attempt = 0
    engine._usb_permission_watching = False

    assert _schedule(engine) == _core._USB_PERMISSION_RETRY_DELAYS[0]
    assert engine.permission_error is False
