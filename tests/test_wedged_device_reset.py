# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Nova Pro Wireless base station that timed out on every write stayed dead
through reinstalls, udev replays and daemon restarts (#272): none of that
touches the wedged USB handle. init_device() now escalates to the same USB
reset resume_from_sleep() uses (#238) once a majority of device_init fails —
a stray unsupported opcode or two must not trigger it.
"""

import ast
import inspect
import textwrap
import threading
from unittest.mock import MagicMock, patch

import pytest

from arctis_sound_manager import core as core_mod
from arctis_sound_manager.core import CoreEngine


def _make_engine(failed: int, attempted: int) -> CoreEngine:
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._device_lock = threading.RLock()
    engine._request_settings_readback = MagicMock()
    engine._send_device_init_sequence = MagicMock(return_value=(failed, attempted))
    engine.reconcile_hardware_eq_mode = MagicMock()
    engine._schedule_wedged_device_reset = MagicMock()
    return engine


@pytest.mark.parametrize("failed, attempted", [(20, 38), (38, 38)])
def test_a_majority_of_failures_escalates_to_a_reset(failed, attempted):
    engine = _make_engine(failed, attempted)

    CoreEngine.init_device(engine)

    engine._schedule_wedged_device_reset.assert_called_once()


@pytest.mark.parametrize("failed, attempted", [(0, 38), (1, 38), (19, 38), (0, 0)])
def test_a_few_failures_leave_the_connection_alone(failed, attempted):
    engine = _make_engine(failed, attempted)

    CoreEngine.init_device(engine)

    engine._schedule_wedged_device_reset.assert_not_called()


def _initial_last_reset() -> float:
    """The value CoreEngine.__init__ really starts the rate limiter from.

    Building a real CoreEngine connects to PipeWire, so read the assignment
    out of __init__ instead of mirroring it here, where it could drift.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(CoreEngine.__init__)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign)
                and ast.unparse(node.target) == "self._last_usb_reset_monotonic"):
            return eval(ast.unparse(node.value))
    raise AssertionError("_last_usb_reset_monotonic is no longer set in __init__")


def _make_scheduler() -> CoreEngine:
    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._main_event_loop = MagicMock()
    engine._main_event_loop.is_running.return_value = True
    engine._escalate_to_usb_reset = MagicMock()
    engine._last_usb_reset_monotonic = _initial_last_reset()
    return engine


def test_the_first_reset_fires_even_right_after_boot():
    """monotonic() counts from boot: a daemon started at login is seconds in."""
    engine = _make_scheduler()

    with patch.object(core_mod.time, "monotonic", return_value=12.0), \
         patch.object(core_mod.asyncio, "run_coroutine_threadsafe") as run:
        CoreEngine._schedule_wedged_device_reset(engine, "test")

    run.assert_called_once()


def test_a_second_reset_within_the_minimum_interval_is_skipped():
    engine = _make_scheduler()

    with patch.object(core_mod.time, "monotonic", return_value=500.0), \
         patch.object(core_mod.asyncio, "run_coroutine_threadsafe") as run:
        CoreEngine._schedule_wedged_device_reset(engine, "test")
    with patch.object(core_mod.time, "monotonic",
                      return_value=500.0 + core_mod._USB_RESET_MIN_INTERVAL_S - 1), \
         patch.object(core_mod.asyncio, "run_coroutine_threadsafe") as run_again:
        CoreEngine._schedule_wedged_device_reset(engine, "test")

    run.assert_called_once()
    run_again.assert_not_called()
