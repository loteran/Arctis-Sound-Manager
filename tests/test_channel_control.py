# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #193 — adjusting a channel from a key binding.

The ask was Sonar's keybind system: a dial on a macropad sends F20, one
channel moves by a fixed step. ASM cannot claim F20 itself on Wayland, so it
exposes the action as a command and lets the desktop bind it — which is the
one mechanism every desktop and window manager already has.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import channel_control


class _Sink:
    def __init__(self, node_name: str, volume: float = 0.5, mute: int = 0):
        self.proplist = {"node.name": node_name}
        self.volume = volume
        self.mute = mute


class _Pulse:
    """Enough of pulsectl.Pulse for this module, recording what it was told."""

    def __init__(self, sinks):
        self._sinks = sinks
        self.muted: list[tuple[str, bool]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sink_list(self):
        return self._sinks

    def volume_get_all_chans(self, sink):
        return sink.volume

    def volume_set_all_chans(self, sink, value):
        sink.volume = value

    def mute(self, sink, state):
        sink.mute = int(state)
        self.muted.append((sink.proplist["node.name"], state))


@pytest.fixture
def pulse(monkeypatch):
    sinks = [_Sink("Arctis_Game", 0.50), _Sink("Arctis_Chat", 0.80),
             _Sink("Arctis_Media", 0.20)]
    obj = _Pulse(sinks)
    module = types.ModuleType("pulsectl")
    module.Pulse = lambda *a, **kw: obj
    monkeypatch.setitem(sys.modules, "pulsectl", module)
    monkeypatch.setattr(channel_control, "save_channel_volume", MagicMock())
    return obj


def _volume(pulse, name: str) -> int:
    return round(next(s for s in pulse.sink_list()
                      if s.proplist["node.name"] == name).volume * 100)


# ── the actions ───────────────────────────────────────────────────────────────

def test_step_up(pulse) -> None:
    channel_control.apply("game", "+5")
    assert _volume(pulse, "Arctis_Game") == 55


def test_step_down(pulse) -> None:
    channel_control.apply("chat", "-30")
    assert _volume(pulse, "Arctis_Chat") == 50


def test_absolute(pulse) -> None:
    channel_control.apply("media", "42")
    assert _volume(pulse, "Arctis_Media") == 42


def test_step_clamps_at_the_top(pulse) -> None:
    """A dial spun past the end must stop at 100, not wrap or overshoot."""
    channel_control.apply("chat", "+90")
    assert _volume(pulse, "Arctis_Chat") == 100


def test_step_clamps_at_the_bottom(pulse) -> None:
    channel_control.apply("media", "-90")
    assert _volume(pulse, "Arctis_Media") == 0


@pytest.mark.parametrize("action,expected", [("mute", True), ("unmute", False)])
def test_mute_and_unmute(pulse, action, expected) -> None:
    channel_control.apply("game", action)
    assert pulse.muted == [("Arctis_Game", expected)]


def test_toggle_flips_whatever_is_there(pulse) -> None:
    channel_control.apply("game", "toggle")
    assert pulse.muted == [("Arctis_Game", True)]
    channel_control.apply("game", "toggle")
    assert pulse.muted[-1] == ("Arctis_Game", False)


def test_the_new_level_is_persisted(pulse, monkeypatch) -> None:
    """The daemon restores this file when it rebuilds a channel, so without it
    a key-bound change is lost at the next loopback recreation."""
    saved = MagicMock()
    monkeypatch.setattr(channel_control, "save_channel_volume", saved)
    channel_control.apply("game", "+10")
    saved.assert_called_once_with("Arctis_Game", 60)


# ── chaining, which is what was actually asked for ────────────────────────────

def test_several_channels_in_one_call(pulse) -> None:
    channel_control.apply_all(["game", "+10", "chat", "-10"])
    assert _volume(pulse, "Arctis_Game") == 60
    assert _volume(pulse, "Arctis_Chat") == 70


def test_one_bad_pair_does_not_abandon_the_rest(pulse) -> None:
    """A bound key doing half its job beats one that stops at the first
    channel that happens to be missing."""
    out = channel_control.apply_all(["nope", "+10", "chat", "-10"])
    assert _volume(pulse, "Arctis_Chat") == 70
    assert "unknown channel" in out[0]


def test_odd_number_of_arguments_is_refused(pulse) -> None:
    with pytest.raises(channel_control.ChannelError):
        channel_control.apply_all(["game"])


def test_no_arguments_is_refused(pulse) -> None:
    with pytest.raises(channel_control.ChannelError):
        channel_control.apply_all([])


# ── saying what is wrong ──────────────────────────────────────────────────────

def test_unknown_channel_lists_the_real_ones(pulse) -> None:
    with pytest.raises(channel_control.ChannelError) as exc:
        channel_control.apply("headset", "+5")
    for name in ("game", "chat", "media"):
        assert name in str(exc.value)


def test_unknown_action_says_what_is_accepted(pulse) -> None:
    with pytest.raises(channel_control.ChannelError) as exc:
        channel_control.apply("game", "louder")
    assert "+N" in str(exc.value) and "toggle" in str(exc.value)


def test_absent_channel_is_reported_not_silently_ignored(monkeypatch) -> None:
    module = types.ModuleType("pulsectl")
    module.Pulse = lambda *a, **kw: _Pulse([])
    monkeypatch.setitem(sys.modules, "pulsectl", module)
    with pytest.raises(RuntimeError, match="not there right now"):
        channel_control.apply("game", "+5")


# ── the read-out ──────────────────────────────────────────────────────────────

def test_show_lists_every_channel(pulse) -> None:
    lines = channel_control.show()
    assert len(lines) == 3
    assert any(line.startswith("game") and "50%" in line for line in lines)


def test_show_marks_a_muted_channel(pulse) -> None:
    channel_control.apply("game", "mute")
    assert any("muted" in line for line in channel_control.show())
