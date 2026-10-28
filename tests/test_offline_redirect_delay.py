# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Right after a re-init the headset status can read "offline" for a moment
with the headset still there. ASM redirected on that at once, moving the
default sink to the user's disconnect device for nothing; on one system that
default-sink change hung PipeWire for 40 minutes (#349). The redirect now
waits until the headset has stayed offline for a few seconds.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from arctis_sound_manager import core
from arctis_sound_manager.core import CoreEngine


class _Timers:
    """Stand-in for threading.Timer: records callbacks, runs them on demand."""

    def __init__(self):
        self.pending: list = []

    def __call__(self, delay, fn):
        timer = MagicMock()
        timer.fn, timer.delay = fn, delay
        timer.cancel.side_effect = lambda: self.pending.remove(timer)
        self.pending.append(timer)
        return timer

    def fire_all(self):
        for timer in list(self.pending):
            self.pending.remove(timer)
            timer.fn()


def _engine(online: list[bool]):
    engine = MagicMock()
    engine._stopping = False
    engine._offline_redirect_timer = None
    engine.device_config.online_status.status_variable = "headset_power_status"
    engine.is_device_online.side_effect = lambda: online[0]
    engine._schedule_offline_redirect.side_effect = (
        lambda: CoreEngine._schedule_offline_redirect(engine))
    engine._cancel_offline_redirect.side_effect = (
        lambda: CoreEngine._cancel_offline_redirect(engine))
    return engine


def test_a_short_offline_blip_does_not_redirect(monkeypatch):
    timers = _Timers()
    monkeypatch.setattr(core.threading, "Timer", timers)
    online = [False]
    engine = _engine(online)

    CoreEngine.on_device_status_changed(engine, "headset_power_status", 0)
    online[0] = True
    CoreEngine.on_device_status_changed(engine, "headset_power_status", 1)
    timers.fire_all()

    engine.redirect_audio_on_disconnect.assert_not_called()
    engine.redirect_to_media_sink.assert_called_once()


def test_a_headset_that_stays_offline_is_redirected_once(monkeypatch):
    timers = _Timers()
    monkeypatch.setattr(core.threading, "Timer", timers)
    engine = _engine([False])

    CoreEngine.on_device_status_changed(engine, "headset_power_status", 0)
    CoreEngine.on_device_status_changed(engine, "headset_power_status", 0)
    assert len(timers.pending) == 1
    assert timers.pending[0].delay == core._OFFLINE_REDIRECT_DELAY_S
    timers.fire_all()

    engine.redirect_audio_on_disconnect.assert_called_once()
