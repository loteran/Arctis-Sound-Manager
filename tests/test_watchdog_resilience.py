# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the watchdog's link-enforcement resilience.

Three defects found by a state-drift audit, all in the same area:

* the four link passes share one pw-dump taken at the top of the tick, and
  resolve port ids from it with no re-check — when ports move underneath, the
  whole tick is lost (observed live: 37 `no matchable ports` / `pw-link failed`
  lines in an hour);
* the "last hop" passes (spatial EQ, physical output, micro capture) had no
  escalation at all, unlike loopback→EQ which has a grace period and recreates;
* app→sink routing overrides were only replayed after a *Chat* recreate, so a
  headset replug silently dropped every pin on Game and Media.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest


def _engine():
    """A CoreEngine with only the attributes these paths touch.

    __new__ skips the real __init__, which would open USB and audio.
    """
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = logging.getLogger("test_watchdog_resilience")
    engine._device_lock = threading.RLock()
    engine._device_session_id = 0
    return engine


# ── Stale-snapshot retry ────────────────────────────────────────────────────


def test_hop_retried_once_with_a_fresh_dump_when_the_shared_one_is_stale():
    """A pass that fails on the shared snapshot is retried on a fresh one."""
    engine = _engine()
    seen_data = []

    def _pass(data):
        seen_data.append(data)
        # Fails on the stale shared snapshot, succeeds on the fresh dump.
        return data == ["fresh"]

    fails: dict[str, int] = {}
    with patch("arctis_sound_manager.pw_utils._pw_dump", return_value=["fresh"]):
        asyncio.run(engine._enforce_link_hop(
            "test hop", _pass, (), ["stale"], fails, 6,
        ))

    assert seen_data == [["stale"], ["fresh"]]
    assert fails == {}, "a hop that recovered must not carry a failure count"


def test_successful_hop_never_pays_for_a_second_dump():
    """The extra pw-dump is only paid when the first attempt failed."""
    engine = _engine()
    fails: dict[str, int] = {}

    with patch("arctis_sound_manager.pw_utils._pw_dump") as mock_dump:
        asyncio.run(engine._enforce_link_hop(
            "test hop", lambda data: True, (), ["shared"], fails, 6,
        ))

    mock_dump.assert_not_called()


def test_empty_dict_result_is_success_not_failure():
    """`{}` means "nothing to enforce" (headset off, no external sink).

    Counting it as a failure would escalate forever on a powered-off headset.
    """
    engine = _engine()
    fails: dict[str, int] = {}

    with patch("arctis_sound_manager.pw_utils._pw_dump") as mock_dump:
        asyncio.run(engine._enforce_link_hop(
            "test hop", lambda data: {}, (), ["shared"], fails, 6,
        ))

    mock_dump.assert_not_called()
    assert fails == {}


def test_partial_dict_failure_counts_as_failure():
    """One unlinked hop in the dict is a failure, even if others succeeded."""
    engine = _engine()
    fails: dict[str, int] = {}

    with patch("arctis_sound_manager.pw_utils._pw_dump", return_value=["fresh"]):
        asyncio.run(engine._enforce_link_hop(
            "test hop", lambda data: {"chat": True, "hesuvi": False},
            (), ["shared"], fails, 6,
        ))

    assert fails == {"test hop": 1}


# ── Escalation ──────────────────────────────────────────────────────────────


def test_escalates_only_after_the_configured_number_of_failed_ticks():
    """These hops used to log and retry forever with no ceiling."""
    engine = _engine()
    fails: dict[str, int] = {}
    healthy = MagicMock()

    with patch("arctis_sound_manager.pw_utils._pw_dump", return_value=["fresh"]), \
            patch("arctis_sound_manager.sonar_to_pipewire.ensure_filter_chain_healthy", healthy):
        for tick in range(1, 4):
            asyncio.run(engine._enforce_link_hop(
                "test hop", lambda data: False, (), ["shared"], fails, 4,
            ))
            assert healthy.call_count == 0, f"escalated too early (tick {tick})"
            assert fails == {"test hop": tick}

        asyncio.run(engine._enforce_link_hop(
            "test hop", lambda data: False, (), ["shared"], fails, 4,
        ))

    assert healthy.call_count == 1
    assert fails == {}, "the counter must reset so escalation cannot loop"


def test_recovery_resets_the_failure_counter():
    """A hop that comes back healthy starts from zero again."""
    engine = _engine()
    fails = {"test hop": 3}

    with patch("arctis_sound_manager.pw_utils._pw_dump", return_value=["fresh"]):
        asyncio.run(engine._enforce_link_hop(
            "test hop", lambda data: True, (), ["shared"], fails, 6,
        ))

    assert fails == {}


def test_a_raising_pass_never_breaks_the_watchdog():
    """Enforcement is best effort: an exception must not kill the tick."""
    engine = _engine()
    fails: dict[str, int] = {}

    def _boom(data):
        raise RuntimeError("pw-dump exploded")

    asyncio.run(engine._enforce_link_hop("test hop", _boom, (), None, fails, 6))


# ── Routing overrides / device session ──────────────────────────────────────


def _engine_for_setup_loopbacks():
    engine = _engine()
    engine.loopback_manager = MagicMock()
    engine._link_loopbacks = MagicMock()
    engine._queue_volume_restore = MagicMock()
    engine._read_eq_mode_is_sonar = MagicMock(return_value=True)
    return engine


def test_setup_loopbacks_reapplies_routing_overrides():
    """The headset attach/reconnect path destroys and recreates all three
    sinks, so every app pinned to them falls back to the default output. Only
    a Chat recreate in the watchdog used to replay those pins."""
    engine = _engine_for_setup_loopbacks()
    reapply = MagicMock()

    with patch("arctis_sound_manager.core.device_state") as ds, \
            patch("arctis_sound_manager.core.make_specs", return_value=[]), \
            patch("arctis_sound_manager.pw_utils.reapply_routing_overrides", reapply):
        ds.is_device_set.return_value = True
        ds.get_physical_out_game.return_value = "alsa_output.game"
        ds.get_physical_out_chat.return_value = "alsa_output.chat"
        ds.get_device_name.return_value = "Test Headset"
        engine.setup_loopbacks()

    reapply.assert_called_once()


def test_setup_loopbacks_bumps_the_device_session_id():
    """A rebuilt loopback set is a new session: the watchdog keys its anti-flap
    history by channel *name*, which outlives the processes it describes."""
    engine = _engine_for_setup_loopbacks()

    with patch("arctis_sound_manager.core.device_state") as ds, \
            patch("arctis_sound_manager.core.make_specs", return_value=[]), \
            patch("arctis_sound_manager.pw_utils.reapply_routing_overrides"):
        ds.is_device_set.return_value = True
        ds.get_physical_out_game.return_value = "alsa_output.game"
        ds.get_physical_out_chat.return_value = "alsa_output.chat"
        ds.get_device_name.return_value = "Test Headset"

        before = engine._device_session_id
        engine.setup_loopbacks()
        engine.setup_loopbacks()

    assert engine._device_session_id == before + 2


def test_setup_loopbacks_survives_a_failing_reapply():
    """Replaying pins is best effort — it must not break loopback setup."""
    engine = _engine_for_setup_loopbacks()

    with patch("arctis_sound_manager.core.device_state") as ds, \
            patch("arctis_sound_manager.core.make_specs", return_value=[]), \
            patch("arctis_sound_manager.pw_utils.reapply_routing_overrides",
                  side_effect=RuntimeError("pulse is away")):
        ds.is_device_set.return_value = True
        ds.get_physical_out_game.return_value = "alsa_output.game"
        ds.get_physical_out_chat.return_value = "alsa_output.chat"
        ds.get_device_name.return_value = "Test Headset"
        engine.setup_loopbacks()  # must not raise

    engine._link_loopbacks.assert_called_once()
