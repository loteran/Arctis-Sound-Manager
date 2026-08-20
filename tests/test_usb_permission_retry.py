# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_usb_permission_retry.py — a transient EACCES must not nag the user.

A dongle that never leaves its port is enumerated during boot, and the daemon
starts right behind it — early enough that the access rights are sometimes not
in place yet on the freshly created device node. The first acquisition of the
session then fails with EACCES while every later one succeeds.

Before the retry, that single failure both surfaced the "USB device permissions
not applied" dialog on every boot and left the headset unmanaged until the user
clicked something, because nothing retried on its own (discussion #140).
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager.core import (CoreEngine,
                                       _USB_PERMISSION_RETRY_DELAYS,
                                       _USB_PERMISSION_WATCH_INTERVAL)


@pytest.fixture
def engine() -> MagicMock:
    eng = MagicMock()
    eng.logger = MagicMock()
    eng.permission_error = True          # kernel_detach just set it
    eng._usb_permission_attempt = 0
    # Set explicitly: an unset attribute on a MagicMock is truthy, which would
    # make the engine think the slow watch is already armed.
    eng._usb_permission_watching = False
    eng._schedule_usb_permission_retry = lambda: CoreEngine._schedule_usb_permission_retry(eng)
    return eng


def test_first_failure_schedules_a_retry_and_stays_quiet(engine):
    """The GUI must not be told about a permission error while retries remain."""
    with patch.object(threading, "Timer") as timer:
        engine._schedule_usb_permission_retry()

    assert engine.permission_error is False, "prompted the user on a transient EACCES"
    assert engine._usb_permission_attempt == 1
    timer.assert_called_once()
    delay, callback = timer.call_args[0]
    assert delay == _USB_PERMISSION_RETRY_DELAYS[0]
    assert timer.return_value.start.called
    assert timer.return_value.daemon is True


def test_retry_reacquires_the_device(engine):
    """The scheduled callback goes back through the normal acquisition path."""
    with patch.object(threading, "Timer") as timer:
        engine._schedule_usb_permission_retry()
    _, callback = timer.call_args[0]

    callback()

    engine.configure_virtual_sinks.assert_called_once()


def test_retry_callback_swallows_exceptions(engine):
    """A raising retry must not take the timer thread down with it."""
    engine.configure_virtual_sinks.side_effect = RuntimeError("boom")
    with patch.object(threading, "Timer") as timer:
        engine._schedule_usb_permission_retry()
    _, callback = timer.call_args[0]

    callback()  # must not raise

    assert engine.logger.warning.called


def test_delays_back_off_then_keep_watching(engine):
    """After the last attempt the flag is raised — but checking continues.

    This used to assert that nothing further was scheduled. That was the bug:
    the budget was only refilled by a successful acquisition or by the device
    being unplugged, so on a machine where the dongle never leaves its port a
    single slow boot left the popup up until the user physically replugged it
    (discussions #140, #190).
    """
    for expected in _USB_PERMISSION_RETRY_DELAYS:
        engine.permission_error = True
        with patch.object(threading, "Timer") as timer:
            engine._schedule_usb_permission_retry()
        assert timer.call_args[0][0] == expected
        assert engine.permission_error is False

    # Attempts exhausted: a real permission problem, let the user know — and
    # keep re-checking, so late udev rules repair it without a click.
    engine.permission_error = True
    with patch.object(threading, "Timer") as timer:
        engine._schedule_usb_permission_retry()

    assert timer.call_args[0][0] == _USB_PERMISSION_WATCH_INTERVAL
    assert engine.permission_error is True
    assert engine.logger.error.called
