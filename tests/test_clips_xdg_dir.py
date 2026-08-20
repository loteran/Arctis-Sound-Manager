# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #192 — clips must land in the user's real video folder.

"Videos" is the English name of a folder every desktop localises: ~/Vidéos,
~/Videók, ~/ビデオ. Writing to a hard-coded ~/Videos created a second video
folder beside the real one on any non-English system — clips saved somewhere
the user's file manager never points at.
"""

from pathlib import Path

import pytest

from arctis_sound_manager import clip_library


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """An isolated HOME, so no test can see the developer's real clips."""
    # HOME is set in the environment as well as patched on Path: the config
    # file writes "$HOME/Vidéos" and os.path.expandvars reads the env var, so
    # patching only Path.home() would resolve against the suite's shared home.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(clip_library, "_LEGACY_CLIP_DIR", tmp_path / "Videos" / "ASM Clips")
    monkeypatch.delenv("XDG_VIDEOS_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    (tmp_path / ".config").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_user_dirs(home: Path, value: str) -> None:
    (home / ".config" / "user-dirs.dirs").write_text(
        "# This file is written by xdg-user-dirs-update\n"
        'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n'
        f"XDG_VIDEOS_DIR={value}\n"
    )


# ── resolving the video folder ────────────────────────────────────────────────

def test_follows_the_localised_video_folder(home: Path) -> None:
    _write_user_dirs(home, '"$HOME/Vidéos"')
    assert clip_library.clip_dir() == home / "Vidéos" / "ASM Clips"


def test_env_var_wins_over_the_config_file(home: Path, monkeypatch) -> None:
    _write_user_dirs(home, '"$HOME/Vidéos"')
    monkeypatch.setenv("XDG_VIDEOS_DIR", str(home / "elsewhere"))
    assert clip_library.clip_dir() == home / "elsewhere" / "ASM Clips"


def test_falls_back_to_videos_without_config(home: Path) -> None:
    """No user-dirs.dirs at all — minimal install, container, fresh account."""
    assert clip_library.clip_dir() == home / "Videos" / "ASM Clips"


def test_unreadable_config_is_not_fatal(home: Path) -> None:
    config = home / ".config" / "user-dirs.dirs"
    config.mkdir()  # a directory where a file is expected → OSError on read
    assert clip_library.clip_dir() == home / "Videos" / "ASM Clips"


def test_ignores_other_xdg_entries(home: Path) -> None:
    (home / ".config" / "user-dirs.dirs").write_text(
        'XDG_VIDEOS_DIR_EXTRA="$HOME/nope"\n'
        'XDG_MUSIC_DIR="$HOME/Musique"\n'
    )
    assert clip_library.clip_dir() == home / "Videos" / "ASM Clips"


# ── not moving anybody's existing clips ───────────────────────────────────────

def test_legacy_folder_with_clips_stays_the_active_one(home: Path) -> None:
    """Someone upgrading has clips in ~/Videos/ASM Clips, and a file manager,
    a bookmark or a habit pointing there. Silently relocating them is worse
    than an unlocalised folder name."""
    _write_user_dirs(home, '"$HOME/Vidéos"')
    legacy = home / "Videos" / "ASM Clips"
    legacy.mkdir(parents=True)
    (legacy / "clip_2026-08-17_18-23-14.mkv").write_bytes(b"")
    assert clip_library.clip_dir() == legacy


def test_empty_legacy_folder_hands_over(home: Path) -> None:
    """ASM creates the folder on start-up even when nothing is recorded, so an
    empty one proves nothing and must not pin the user to the wrong place."""
    _write_user_dirs(home, '"$HOME/Vidéos"')
    (home / "Videos" / "ASM Clips").mkdir(parents=True)
    assert clip_library.clip_dir() == home / "Vidéos" / "ASM Clips"


def test_legacy_with_only_sidecars_hands_over(home: Path) -> None:
    """Trim/mix JSON without their recording is leftovers, not a library."""
    _write_user_dirs(home, '"$HOME/Vidéos"')
    legacy = home / "Videos" / "ASM Clips"
    legacy.mkdir(parents=True)
    (legacy / "clip_2026-08-17.trim.json").write_text("{}")
    assert clip_library.clip_dir() == home / "Vidéos" / "ASM Clips"


def test_share_dir_follows_clip_dir(home: Path) -> None:
    _write_user_dirs(home, '"$HOME/Vidéos"')
    assert clip_library.share_dir() == clip_library.clip_dir() / clip_library.SHARE_DIR_NAME


# ── the writers must go through it ────────────────────────────────────────────

def test_no_module_hard_codes_the_english_folder() -> None:
    """The three modules that used to each carry their own copy of the path."""
    import inspect

    from arctis_sound_manager import clip_capture
    from arctis_sound_manager.gui import clips_page

    for module in (clip_capture, clips_page):
        src = inspect.getsource(module)
        assert '"Videos"' not in src, f"{module.__name__} still hard-codes ~/Videos"
