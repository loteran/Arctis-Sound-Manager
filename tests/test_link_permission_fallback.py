# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #203: degrade a channel's EQ node only when its link is refused.

The channel EQ nodes are Audio/Sink/Internal so ASM's plumbing stays out of
every output picker. On a session that starts our clients restricted — SteamOS
through Distrobox — PipeWire refuses the cross-client link from pw-loopback
into an Internal node while allowing the identical link into an Audio/Sink one,
so the channel carries no audio at all.

Measured on a normal Arch/KDE session, the Internal link works
(Arctis_Media_sink_out:output_FL |-> effect_input.sonar-media-eq:playback_FL),
so flipping every install to Audio/Sink would put three plumbing nodes in
everyone's picker to fix a case most users do not have. Hence: keep Internal,
and degrade one channel, after repeated refusals, remembering the decision.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import arctis_sound_manager.sonar_to_pipewire as stp


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_untouched_channels_stay_out_of_the_pickers(home):
    for channel in ("game", "chat", "media"):
        assert stp._media_class_for(channel) == "Audio/Sink/Internal"


def test_the_output_channel_is_always_pickable(home):
    """A routing pin has to be able to name it."""
    assert stp._media_class_for("output") == "Audio/Sink"


def test_a_degraded_channel_becomes_pickable_and_stays_so(home):
    assert stp.mark_link_permission_fallback("media") is True
    assert stp._media_class_for("media") == "Audio/Sink"
    # Untouched channels are unaffected: the decision is per channel.
    assert stp._media_class_for("game") == "Audio/Sink/Internal"


def test_marking_twice_reports_no_change(home):
    """The watchdog uses this to avoid regenerating and restarting the
    filter-chain on every subsequent tick."""
    assert stp.mark_link_permission_fallback("game") is True
    assert stp.mark_link_permission_fallback("game") is False


def test_the_decision_survives_a_restart(home):
    stp.mark_link_permission_fallback("chat")
    stored = json.loads((home / ".config" / "arctis_manager"
                         / "link_permission_fallback.json").read_text())
    assert stored == ["chat"]


def test_a_corrupt_marker_never_breaks_conf_generation(home):
    path = home / ".config" / "arctis_manager" / "link_permission_fallback.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json")

    assert stp.link_permission_fallback_channels() == set()
    assert stp._media_class_for("game") == "Audio/Sink/Internal"


def test_the_fallback_needs_more_than_one_bad_tick():
    """Visible to the user, so it must answer a settled state. The counter is
    deliberately longer than the orphan grace, because the permission repair
    retries on its own schedule."""
    import inspect

    from arctis_sound_manager.core import CoreEngine

    src = inspect.getsource(CoreEngine._loopback_watchdog)
    assert "_PERM_FALLBACK_TICKS: int = 6" in src
    assert "_ORPHAN_GRACE_TICKS: int = 3" in src
