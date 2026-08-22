# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The watchdog must not recreate a loopback whose links were refused (#181).

On a system that starts ASM's clients with restricted PipeWire permissions
(SteamOS), every link the watchdog tries is denied. The watchdog read "could
not link" as "this loopback is broken" and recreated it — which fixes a missing
node and makes a refusal strictly worse: the replacement client comes up just
as restricted, and the recreation drops the channels that *were* linked.

That is the loop behind the report: sound for thirty seconds, then one ear,
then the other, then silence, over and over. Recreating three channels every
five seconds is what the reporter was hearing, and it is caused by ASM.

These tests drive the real ``_loopback_watchdog`` coroutine — the decision
under test lives inside it, so a test that re-implemented the condition would
prove nothing about the code that ships.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

from arctis_sound_manager import core as core_mod
from arctis_sound_manager import pw_utils
from arctis_sound_manager.core import CoreEngine

# Mirrors of two values that live inside _loopback_watchdog as locals: its
# sleep interval, and the number of consecutive failing ticks before a recreate.
_WATCHDOG_TICK_S = 5.0
_ORPHAN_GRACE = 3


class _FakeLoopbackManager:
    def __init__(self) -> None:
        self.recreated: list[str] = []
        self._specs = {
            "game": SimpleNamespace(
                playback_name="Arctis_Game_sink_out",
                target="effect_input.sonar-game-eq",
            ),
        }

    def specs(self):
        return self._specs

    def is_running(self, _channel):
        return True

    def restart_dead(self, skip_channels=None):
        return []

    def recreate(self, spec):
        self.recreated.append(spec.playback_name)


async def _async_true(*_a, **_k):
    """The three "last hop" passes are awaited; they are not what is tested."""
    return True


def _fake_core() -> SimpleNamespace:
    """The attributes ``_loopback_watchdog`` actually touches, and no more."""
    return SimpleNamespace(
        logger=logging.getLogger("test_watchdog_permission_refusal"),
        loopback_manager=_FakeLoopbackManager(),
        _stopping=False,
        _device_session_id=1,
        _volume_restore_pending={},
        _VOLUME_RESTORE_TICKS=3,
        _queue_volume_restore=lambda *a, **k: None,
        _process_volume_restore=lambda: None,
        _enforce_link_hop=_async_true,
    )


async def _drive_until_stopped(fake_self, max_ticks: int) -> None:
    """Run the real coroutine, letting its own sleep drive the clock.

    ``_WATCHDOG_INTERVAL`` is a local inside the coroutine, so the cadence
    cannot be patched from outside; waiting it out would mean 5 s per tick and
    ~20 s to clear the orphan grace. Standing in for ``asyncio.sleep`` yields
    control instead of waiting, counts the ticks, and asks the loop to stop —
    the same way ``CoreEngine.stop()`` does. Nothing about the decision under
    test is bypassed: every branch runs exactly as it ships.
    """
    real_sleep = asyncio.sleep
    ticks = {"n": 0}

    async def counting_sleep(delay, *args, **kwargs):
        if delay == _WATCHDOG_TICK_S:
            ticks["n"] += 1
            if ticks["n"] > max_ticks:
                fake_self._stopping = True
        await real_sleep(0)

    with patch.object(asyncio, "sleep", counting_sleep):
        await CoreEngine._loopback_watchdog(fake_self)

    assert ticks["n"] > _ORPHAN_GRACE, (
        f"only {ticks['n']} ticks ran; the recreate path needs more than "
        f"{_ORPHAN_GRACE} consecutive failures, so this test proved nothing"
    )


def _drive(link_result: bool, outcome_fill: dict, max_ticks: int = 8):
    """Run the watchdog with ensure_loopback_link returning a fixed verdict."""
    fake_self = _fake_core()
    seen: list[dict] = []

    def fake_link(playback_name, target_name, data=None, outcome=None):
        seen.append({"playback": playback_name})
        if outcome is not None:
            outcome.update(outcome_fill)
        return link_result

    # Imported inside the coroutine, so pw_utils is where they must be patched;
    # patching them on `core` would silently do nothing. No raising=False here
    # either — if a name moves, this test must break loudly rather than pass by
    # patching something that is not called.
    with patch.object(pw_utils, "ensure_loopback_link", fake_link), \
         patch.object(pw_utils, "pw_dump_or_none", lambda *a, **k: []), \
         patch.object(pw_utils, "pw_node_exists", lambda *a, **k: True), \
         patch.object(core_mod.device_state, "is_device_set", lambda: True):
        asyncio.run(_drive_until_stopped(fake_self, max_ticks))

    assert seen, "the link pass never ran — the harness is not exercising it"
    return fake_self


def test_a_refused_link_never_triggers_a_recreate():
    """The channel is left alone: recreating it would drop what still plays."""
    fake_self = _drive(
        link_result=False,
        outcome_fill={"created": 0, "total": 2, "denied": 2},
    )
    assert fake_self.loopback_manager.recreated == [], (
        "a loopback whose links PipeWire refused was recreated — this is the "
        "five-second loop that cost the reporter his audio"
    )


def test_an_unlinkable_loopback_is_still_recreated():
    """The fix must not disarm the watchdog for the case it exists for: a
    loopback that cannot link because its node is missing still gets rebuilt."""
    fake_self = _drive(
        link_result=False,
        outcome_fill={"created": 0, "total": 2, "denied": 0},
    )
    assert fake_self.loopback_manager.recreated, (
        "an unlinkable loopback with no refusal was never recreated — the "
        "watchdog would no longer recover a genuinely broken channel"
    )


def test_a_healthy_link_leaves_the_loopback_alone():
    fake_self = _drive(
        link_result=True,
        outcome_fill={"created": 2, "total": 2, "denied": 0},
    )
    assert fake_self.loopback_manager.recreated == []
