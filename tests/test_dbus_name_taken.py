# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#387: when another process holds ASM's D-Bus name, the daemon names it and
exits instead of writing a crash report."""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

from dbus_next.constants import MessageType

from arctis_sound_manager.dbus_service import BusNameTakenError, describe_name_owner


class _FakeBus:
    def __init__(self, replies):
        self.replies = replies

    async def call(self, msg):
        value = self.replies.get(msg.member)
        if value is None:
            return SimpleNamespace(message_type=MessageType.ERROR, body=[])
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[value])


def test_owner_is_named_by_pid_and_command_line():
    bus = _FakeBus({'GetNameOwner': ':1.42', 'GetConnectionUnixProcessID': os.getpid()})
    text = asyncio.run(describe_name_owner(bus, 'x.y'))
    assert text.startswith(f'pid {os.getpid()} (') and 'python' in text


def test_unknown_owner_gives_an_empty_description():
    assert asyncio.run(describe_name_owner(_FakeBus({}), 'x.y')) == ''


def test_a_failing_bus_never_raises():
    class _Broken:
        async def call(self, msg):
            raise OSError('gone')
    assert asyncio.run(describe_name_owner(_Broken(), 'x.y')) == ''


def test_name_taken_is_not_reported_as_a_crash(monkeypatch):
    from arctis_sound_manager.scripts import daemon

    async def _taken():
        raise BusNameTakenError('taken')

    reports = []
    monkeypatch.setattr(daemon, 'main_async', _taken)
    monkeypatch.setattr(daemon, 'write_crash_report', lambda *a, **k: reports.append(a))
    monkeypatch.setattr('sys.argv', ['asm-daemon'])
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)  # main() replaces it
    try:
        daemon.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError('expected SystemExit')
    assert reports == []
