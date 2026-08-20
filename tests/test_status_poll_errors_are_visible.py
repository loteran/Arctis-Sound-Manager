# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A dead command channel must leave a trace in the journal (#198).

The status poll runs every two seconds for the life of the daemon, and it used
to drop errno 16, 19 and 110 on the floor. A headset whose command channel
never answers therefore produced *no log line at all*, indefinitely: the status
stayed empty, no battery ever appeared, and nothing said why. That is how #198
took three rounds of questions before the cause was visible.

The quiet was not unreasonable — EBUSY and ENODEV are ordinary during a hotplug
or a resume, and a warning every two seconds buries a journal instead of
informing it. So the rule is: once when a streak starts, once a minute while it
lasts, once when it clears.
"""
from __future__ import annotations

import logging

import pytest
import usb.core

from arctis_sound_manager.core import CoreEngine


class _Engine:
    """Calls the real methods; they only touch the streak counter and logger."""

    _STATUS_POLL_LOG_EVERY = CoreEngine._STATUS_POLL_LOG_EVERY
    _note_status_poll_error = CoreEngine._note_status_poll_error
    _note_status_poll_ok = CoreEngine._note_status_poll_ok

    def __init__(self) -> None:
        self.logger = logging.getLogger("test-status-poll")


def _timeout() -> usb.core.USBError:
    return usb.core.USBError("Operation timed out", errno=110)


def test_the_first_timeout_is_reported(caplog) -> None:
    """The one that used to be silent. Timing out is the whole failure, not a
    detail of it."""
    engine = _Engine()
    with caplog.at_level(logging.WARNING):
        engine._note_status_poll_error(_timeout())

    assert "timed out" in caplog.text.lower()
    assert "no battery" in caplog.text.lower(), "the consequence should be named"


def test_a_long_streak_does_not_spam(caplog) -> None:
    """Every two seconds forever is not reporting, it is burying."""
    engine = _Engine()
    with caplog.at_level(logging.WARNING):
        for _ in range(_Engine._STATUS_POLL_LOG_EVERY):
            engine._note_status_poll_error(_timeout())

    # One for the start of the streak, one when the counter comes round.
    assert len(caplog.records) == 2


def test_recovery_is_announced_once(caplog) -> None:
    """A journal that says a device stopped answering must also say when it
    started again, or the reader is left with the wrong conclusion."""
    engine = _Engine()
    engine._note_status_poll_error(_timeout())

    with caplog.at_level(logging.INFO):
        engine._note_status_poll_ok()
        engine._note_status_poll_ok()

    assert len([r for r in caplog.records if "recovered" in r.message]) == 1


def test_a_busy_interface_says_so(caplog) -> None:
    """EBUSY has a different cause and a different fix — something else is
    holding the interface — so it must not read like a dead device."""
    engine = _Engine()
    with caplog.at_level(logging.WARNING):
        engine._note_status_poll_error(usb.core.USBError("busy", errno=16))

    assert "holding the interface" in caplog.text
