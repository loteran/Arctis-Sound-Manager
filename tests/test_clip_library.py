# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for what the library holds, where exports land, and deletion.

All three are things the user reported after living with the first version:
an exported clip came back as a second card in the grid (and exporting *that*
produced ``_share_share``), a trim had to be redone every time a clip was
reopened, and there was no way to remove anything from the page at all.
"""
from __future__ import annotations

import json

import pytest

from arctis_sound_manager import clip_library


def _clip(directory, name: str = "clip_2026-07-30_23-15-02_Pal.mkv"):
    path = directory / name
    path.write_bytes(b"video")
    return path


# ── what counts as a clip ─────────────────────────────────────────────────────

def test_recordings_are_listed(tmp_path):
    clip = _clip(tmp_path)
    assert clip_library.list_clips(tmp_path) == [clip]


def test_an_export_beside_a_recording_is_not_a_second_clip(tmp_path):
    """The reported bug: exporting produced a new card in the library."""
    clip = _clip(tmp_path)
    _clip(tmp_path, "clip_2026-07-30_23-15-02_Pal_share.mkv")
    assert clip_library.list_clips(tmp_path) == [clip]


def test_a_twice_exported_clip_is_not_a_third(tmp_path):
    clip = _clip(tmp_path)
    _clip(tmp_path, "clip_2026-07-30_23-15-02_Pal_share_share.mkv")
    _clip(tmp_path, "clip_2026-07-30_23-15-02_Pal_share-2.mkv")
    assert clip_library.list_clips(tmp_path) == [clip]


def test_the_share_folder_is_not_walked(tmp_path):
    """Today's exports live in a subfolder; the listing must stay flat."""
    clip = _clip(tmp_path)
    shared = tmp_path / clip_library.SHARE_DIR_NAME
    shared.mkdir()
    _clip(shared, "clip_2026-07-30_23-15-02_Pal_share.mkv")
    assert clip_library.list_clips(tmp_path) == [clip]


def test_a_clip_named_by_the_user_is_still_a_clip(tmp_path):
    """Renaming is offered on the page, so names are not ours to assume."""
    clip = _clip(tmp_path, "best round ever.mkv")
    assert clip_library.list_clips(tmp_path) == [clip]


def test_newest_first(tmp_path):
    import os
    old = _clip(tmp_path, "clip_2026-07-30_10-00-00_A.mkv")
    new = _clip(tmp_path, "clip_2026-07-30_23-00-00_B.mkv")
    os.utime(old, (1, 1))
    assert clip_library.list_clips(tmp_path) == [new, old]


def test_a_missing_library_is_empty_not_an_error(tmp_path):
    assert clip_library.list_clips(tmp_path / "not-there") == []


# ── where an export goes ──────────────────────────────────────────────────────

def test_export_is_named_after_the_clip_not_the_previous_export(tmp_path):
    clip = _clip(tmp_path)
    dest = clip_library.export_destination(clip, ".mp4", directory=tmp_path)
    assert dest.name == "clip_2026-07-30_23-15-02_Pal_share.mp4"


def test_a_second_export_does_not_overwrite_the_first(tmp_path):
    clip = _clip(tmp_path)
    first = clip_library.export_destination(clip, ".mp4", directory=tmp_path)
    first.write_bytes(b"exported")
    second = clip_library.export_destination(clip, ".mp4", directory=tmp_path)
    assert second != first
    assert not second.exists()


def test_exports_default_to_the_share_folder():
    assert clip_library.export_destination(
        clip_library.clip_dir() / "clip.mkv").parent == clip_library.share_dir()


# ── remembered trim ───────────────────────────────────────────────────────────

def test_a_trim_survives_reopening(tmp_path):
    clip = _clip(tmp_path)
    clip_library.write_trim(clip, 12.5, 22.5)
    assert clip_library.read_trim(clip) == (12.5, 22.5)


def test_no_trim_yet_reads_as_none(tmp_path):
    """The editor then opens on its default tail, which is always usable."""
    assert clip_library.read_trim(_clip(tmp_path)) is None


@pytest.mark.parametrize("payload", [
    "{not json",
    '{"start_s": 10}',
    '{"start_s": 20, "end_s": 10}',      # reversed
    '{"start_s": -5, "end_s": 10}',      # before the clip starts
    '{"start_s": "a", "end_s": "b"}',
])
def test_a_damaged_trim_is_ignored_rather_than_obeyed(tmp_path, payload):
    clip = _clip(tmp_path)
    clip_library.trim_sidecar(clip).write_text(payload)
    assert clip_library.read_trim(clip) is None


def test_an_empty_span_is_not_remembered(tmp_path):
    """Saving start == end would reopen the clip on nothing to export."""
    clip = _clip(tmp_path)
    assert clip_library.write_trim(clip, 5.0, 5.0) is False
    assert clip_library.read_trim(clip) is None


def test_sidecars_follow_the_convention_the_capture_already_uses(tmp_path):
    """clip_….tracks.json is written by the capture with with_suffix(); the
    trim and the mix have to be named the same way or deleting a clip leaves
    one behind."""
    clip = _clip(tmp_path)
    names = {p.name for p in clip_library.sidecars(clip)}
    assert names == {"clip_2026-07-30_23-15-02_Pal.trim.json",
                     "clip_2026-07-30_23-15-02_Pal.mix.json",
                     "clip_2026-07-30_23-15-02_Pal.tracks.json"}
    assert clip_library.trim_sidecar(clip) in set(clip_library.sidecars(clip))
    assert clip_library.mix_sidecar(clip) in set(clip_library.sidecars(clip))


# ── remembered channel levels ─────────────────────────────────────────────────
#
# From use: "this setting should stick". Deciding the microphone is too loud in
# a clip and the chat channel should be off is a judgement about that recording,
# and closing the editor threw it away — so reopening a clip to adjust an export
# meant making every one of those decisions again from scratch.

def test_a_mix_survives_a_round_trip(tmp_path):
    clip = _clip(tmp_path)
    assert clip_library.write_mix(clip, {"game": (0.8, False), "mic": (1.0, True)})

    assert clip_library.read_mix(clip) == {"game": (0.8, False), "mic": (1.0, True)}


def test_a_mix_is_keyed_by_channel_not_by_position(tmp_path):
    """Read back by index, a sidecar written when the clip had a different set
    of channels would apply the microphone's settings to the game."""
    clip = _clip(tmp_path)
    clip_library.write_mix(clip, {"mic": (0.2, True)})

    assert clip_library.read_mix(clip)["mic"] == (0.2, True)
    assert "game" not in clip_library.read_mix(clip)


def test_no_mix_yet_leaves_every_channel_at_its_default(tmp_path):
    assert clip_library.read_mix(_clip(tmp_path)) == {}


def test_a_corrupt_mix_is_treated_as_absent(tmp_path):
    """Defaults are always a usable answer; refusing to open the clip is not."""
    clip = _clip(tmp_path)
    clip_library.mix_sidecar(clip).write_text("{not json")

    assert clip_library.read_mix(clip) == {}


def test_a_nonsense_level_cannot_reach_the_mixer(tmp_path):
    """A volume of 40 would be a burst of noise at the first unmute."""
    clip = _clip(tmp_path)
    clip_library.mix_sidecar(clip).write_text(
        '{"tracks": {"game": {"volume": 40, "muted": false},'
        ' "chat": {"volume": "loud", "muted": false}}}')

    mix = clip_library.read_mix(clip)
    assert mix["game"][0] <= 1.5
    assert "chat" not in mix


# ── deletion ──────────────────────────────────────────────────────────────────

@pytest.fixture
def no_trash(monkeypatch):
    """No `gio` on PATH, so deletion falls through to unlink."""
    monkeypatch.setattr(clip_library.shutil, "which", lambda name: None)


def test_deleting_takes_the_sidecars_with_it(tmp_path, no_trash):
    clip = _clip(tmp_path)
    clip_library.write_trim(clip, 1.0, 2.0)
    tracks = clip.with_suffix(".tracks.json")
    tracks.write_text(json.dumps({"tracks": ["game"]}))

    assert clip_library.delete_clip(clip) is True
    assert not clip.exists()
    assert not tracks.exists()
    assert not clip_library.trim_sidecar(clip).exists()


def test_the_trash_is_preferred_over_unlinking(tmp_path, monkeypatch):
    """A recording can take a whole session to produce; a mis-click on a grid
    of near-identical cards must be recoverable."""
    clip = _clip(tmp_path)
    monkeypatch.setattr(clip_library.shutil, "which", lambda name: "/usr/bin/gio")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(clip_library.subprocess, "run", fake_run)
    assert clip_library.delete_clip(clip) is True
    assert calls and calls[0][:2] == ["/usr/bin/gio", "trash"]


def test_a_refused_trash_still_deletes(tmp_path, monkeypatch):
    """No trash on this desktop is not a reason for Delete to do nothing."""
    clip = _clip(tmp_path)
    monkeypatch.setattr(clip_library.shutil, "which", lambda name: "/usr/bin/gio")
    monkeypatch.setattr(
        clip_library.subprocess, "run",
        lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "",
                                         "stderr": "no trash"})())
    assert clip_library.delete_clip(clip) is True
    assert not clip.exists()


def test_one_failure_does_not_abandon_the_rest(tmp_path, no_trash):
    """A multi-selection has to be either done or reported, not half-done and
    silent."""
    good = _clip(tmp_path, "clip_2026-07-30_23-15-02_A.mkv")
    missing = tmp_path / "already-gone.mkv"

    gone, failed = clip_library.delete_clips([missing, good])
    assert gone == 1
    assert failed == [missing]
    assert not good.exists()
