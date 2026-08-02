# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Two things a clip shortcut needs to be trusted: feedback, and the right name.

Reported from use: pressing the shortcut gave no sign whatsoever that anything
had happened — the shortcut is pressed *because* something else owns the screen,
so a status line in a hidden window is not feedback. And a clip taken during a
game came out labelled ``WEBRTC_VoiceEngine``, the name a browser tab's audio
stream carries, because detection ranked streams by name alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from arctis_sound_manager import clip_feedback


# ── the sound ─────────────────────────────────────────────────────────────────

def test_the_theme_sound_is_preferred(monkeypatch):
    """canberra plays the user's own theme, and the same cue other apps use for
    the same event."""
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    cmd = clip_feedback.sound_command()
    assert cmd[0].endswith("canberra-gtk-play")
    assert clip_feedback.SHUTTER_SOUND_ID in cmd


def test_a_desktop_without_canberra_still_makes_a_sound(monkeypatch, tmp_path):
    sound = tmp_path / "camera-shutter.oga"
    sound.write_bytes(b"ogg")
    monkeypatch.setattr(clip_feedback, "_SOUND_PATHS", (sound,))
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: None if name == "canberra-gtk-play"
                        else f"/usr/bin/{name}")
    cmd = clip_feedback.sound_command()
    assert cmd[0].endswith("pw-play")
    assert cmd[-1] == str(sound)


def test_no_player_at_all_is_not_an_error(monkeypatch):
    monkeypatch.setattr(clip_feedback.shutil, "which", lambda name: None)
    assert clip_feedback.sound_command() is None


def test_a_missing_sound_file_is_not_played(monkeypatch, tmp_path):
    """Handing a nonexistent path to paplay produces an error, not a sound."""
    monkeypatch.setattr(clip_feedback, "_SOUND_PATHS", (tmp_path / "gone.oga",))
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: None if name == "canberra-gtk-play"
                        else f"/usr/bin/{name}")
    assert clip_feedback.sound_command() is None


# ── the notification ──────────────────────────────────────────────────────────

def test_the_notification_names_the_app_and_the_clip(monkeypatch):
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    cmd = clip_feedback.notify_command("Clip saved", "clip_x.mkv")
    assert clip_feedback.APP_NAME in cmd
    assert cmd[-2:] == ["Clip saved", "clip_x.mkv"]


def test_clips_replace_their_own_notification(monkeypatch):
    """A session of heavy clipping should not leave a stack of pop-ups."""
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    cmd = clip_feedback.notify_command("Clip saved")
    assert any("x-canonical-private-synchronous" in part for part in cmd)


def test_no_notify_send_is_not_an_error(monkeypatch):
    monkeypatch.setattr(clip_feedback.shutil, "which", lambda name: None)
    assert clip_feedback.notify_command("Clip saved") is None


# ── announcing a save ─────────────────────────────────────────────────────────

@pytest.fixture
def spawned(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: calls.append(list(cmd)) or
                        SimpleNamespace(pid=1))
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    return calls


def test_a_saved_clip_is_both_heard_and_seen(spawned):
    clip_feedback.clip_saved(Path("/clips/clip_x.mkv"))
    assert len(spawned) == 2


def test_either_half_can_be_turned_off(spawned):
    clip_feedback.clip_saved(Path("/clips/clip_x.mkv"), notify=False)
    assert len(spawned) == 1
    assert spawned[0][0].endswith("canberra-gtk-play")


def test_a_player_that_will_not_start_does_not_fail_the_clip(monkeypatch):
    """The file is already on disk by the time this runs; a missing player is
    not a reason to report a failed clip."""
    monkeypatch.setattr(clip_feedback.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    def explode(*a, **kw):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "Popen", explode)
    clip_feedback.clip_saved(Path("/clips/clip_x.mkv"))     # must not raise


def test_the_announcement_does_not_wait_for_the_player(spawned, monkeypatch):
    """Popen, never run/communicate: a sound player between the shortcut and
    the next thing the user does would be felt."""
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: seen.setdefault("ran", True))
    clip_feedback.clip_saved(Path("/clips/clip_x.mkv"))
    assert "ran" not in seen


# ── which app the clip is named after ─────────────────────────────────────────

def _sink(index: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(index=index, name=name, proplist={"node.name": name})


def _stream(sink: int, app: str, pid: str = "") -> SimpleNamespace:
    props = {"application.name": app}
    if pid:
        props["application.process.id"] = pid
    return SimpleNamespace(sink=sink, proplist=props)


class _Pulse:
    def __init__(self, sinks, streams):
        self._sinks, self._streams = sinks, streams

    def sink_list(self):
        return self._sinks

    def sink_input_list(self):
        return self._streams

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


SINKS = [_sink(1, "Arctis_Game"), _sink(2, "Arctis_Chat"), _sink(3, "Arctis_Media")]


def _detect(sinks, streams):
    from arctis_sound_manager import clip_capture
    with patch("pulsectl.Pulse", return_value=_Pulse(sinks, streams)):
        return clip_capture.detect_game()


def test_the_stream_on_the_game_channel_wins(spawned=None):
    """The reported mislabel: a browser tab playing while a game ran took the
    name, because both are just names until you ask where they are routed."""
    assert _detect(SINKS, [_stream(3, "WEBRTC VoiceEngine"),
                           _stream(1, "GenshinImpact")]) == "GenshinImpact"


def test_order_does_not_decide_it():
    """Same graph, other order — a heuristic that depends on enumeration order
    is a coin flip, and it was landing wrong."""
    assert _detect(SINKS, [_stream(1, "GenshinImpact"),
                           _stream(3, "WEBRTC VoiceEngine")]) == "GenshinImpact"


def test_the_eq_input_counts_as_the_game_channel():
    """A stream sits on the filter-chain input when the Sonar EQ is on and on
    the virtual sink when it is off; the user means the same channel."""
    sinks = [_sink(1, "effect_input.sonar-game-eq"), _sink(3, "Arctis_Media")]
    assert _detect(sinks, [_stream(3, "Google Chrome"),
                           _stream(1, "Palworld")]) == "Palworld"


def test_a_browser_alone_is_still_not_a_game():
    """Nothing on the Game channel and only a browser playing — a clip labelled
    with a browser's audio-engine name is worse than an unlabelled one."""
    assert _detect(SINKS, [_stream(3, "WEBRTC VoiceEngine")]) is None


def test_nothing_playing_is_not_a_guess():
    assert _detect(SINKS, []) is None


# ── the blocklist matches display names, not binaries ─────────────────────────
#
# Reported from use: the status bar read "Recording: Google Chrome" while the
# user had picked their OBS window in the portal picker. Two faults met there.
# This is the first: the blocklist held "chrome" and the graph carries "Google
# Chrome", and the test was exact membership — so every blocked app whose
# display name is more than its binary name sailed straight through.

@pytest.mark.parametrize("name", [
    "Google Chrome", "Chromium", "OBS Studio", "Brave Browser",
    "Firefox Web Browser", "Discord", "WEBRTC_VoiceEngine", "Spotify",
    "speech-dispatcher",
])
def test_display_names_of_blocked_apps_are_blocked(name):
    assert _detect(SINKS, [_stream(3, name)]) is None, name


@pytest.mark.parametrize("name", ["Observation", "Chromatic Souls", "Vivid"])
def test_a_game_is_not_blocked_for_a_word_inside_a_longer_one(name):
    """A plain substring test would read "Observation" as OBS and "Vivid" as
    Vivaldi. Only whole words count."""
    assert _detect(SINKS, [_stream(3, name)]) == name


def test_a_blocked_app_on_the_game_channel_is_still_blocked():
    """Routing a browser to the Game channel does not make it the game — the
    channel wins over the *order* streams come in, not over the blocklist."""
    assert _detect(SINKS, [_stream(1, "Google Chrome")]) is None


def test_an_unlisted_app_is_still_taken_as_the_game():
    """The blocklist is a list of exceptions; anything not on it still counts,
    or every native game would need to be known in advance."""
    assert _detect(SINKS, [_stream(3, "Palworld")]) == "Palworld"
