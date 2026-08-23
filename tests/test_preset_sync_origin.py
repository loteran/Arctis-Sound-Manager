# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A synced SteelSeries preset is not one of the user's own.

The Sonar page treats everything in ``sonar_presets/`` as the user's work: it
sorts those entries first, paints them in the accent colour, and offers a rename
and a delete on them. preset_sync wrote its downloads to that same folder, so
presets published by SteelSeries showed up looking hand-made — and offered to
delete a file that comes straight back on the next sync.

They now land in ``sonar_presets_synced/`` and are read with the same standing
as the ones bundled in the package.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager import preset_sync
from arctis_sound_manager.gui import sonar_page


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """The three places a preset can come from, all redirected."""
    bundled = tmp_path / "bundled"
    synced = tmp_path / "synced"
    mine = tmp_path / "mine"
    for d in (bundled, synced, mine):
        d.mkdir()
    monkeypatch.setattr(preset_sync, "_BUNDLED_DIR", bundled)
    monkeypatch.setattr(preset_sync, "_SYNCED_DIR", synced)
    monkeypatch.setattr(preset_sync, "_PRESETS_DIR", mine)
    monkeypatch.setattr(sonar_page, "_RAW_DIR", bundled)
    monkeypatch.setattr(sonar_page, "_SYNCED_DIR", synced)
    monkeypatch.setattr(sonar_page, "_PRESETS_DIR", mine)
    return bundled, synced, mine


def _write(d, name):
    (d / name).write_text(json.dumps({"formFactor": "headphones"}))


def test_a_downloaded_preset_lands_outside_the_user_folder(dirs, monkeypatch):
    _bundled, synced, mine = dirs
    monkeypatch.setattr(preset_sync.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(b'{"ok": 1}'))

    assert preset_sync.PresetSyncWorker()._download("Halo_ Campaign Evolved [Game].json")

    assert (synced / "Halo_ Campaign Evolved [Game].json").is_file()
    assert not list(mine.iterdir()), "the user's folder must stay untouched"


def test_presets_already_downloaded_are_moved_out(dirs):
    """An install that synced before this change still has them in the wrong
    place; the manifest is what tells them apart from the user's own."""
    _bundled, synced, mine = dirs
    _write(mine, "Heave Ho 2 [Game].json")      # came from a past sync
    _write(mine, "My Own Curve [Game].json")    # genuinely the user's

    preset_sync.PresetSyncWorker._migrate_synced(["Heave Ho 2 [Game].json"])

    assert (synced / "Heave Ho 2 [Game].json").is_file()
    assert (mine / "My Own Curve [Game].json").is_file(), "must not touch the user's"
    assert not (mine / "Heave Ho 2 [Game].json").exists()


def test_migration_of_an_already_migrated_preset_does_not_duplicate(dirs):
    _bundled, synced, mine = dirs
    _write(mine, "Heave Ho 2 [Game].json")
    _write(synced, "Heave Ho 2 [Game].json")

    preset_sync.PresetSyncWorker._migrate_synced(["Heave Ho 2 [Game].json"])

    assert (synced / "Heave Ho 2 [Game].json").is_file()
    assert not (mine / "Heave Ho 2 [Game].json").exists()


def test_the_page_lists_synced_presets(dirs):
    _bundled, synced, _mine = dirs
    _write(synced, "Moonlight Peaks [Game].json")

    assert "Moonlight Peaks" in sonar_page._list_presets("game")


def test_a_synced_preset_is_not_shown_as_the_users_own(dirs):
    """The visible symptom. `_custom_names` is what drives the accent colour,
    the top placement and the delete entry — a synced preset must be in none of
    them."""
    _bundled, synced, mine = dirs
    _write(synced, "Moonlight Peaks [Game].json")
    _write(mine, "My Own Curve [Game].json")

    presets = sonar_page._list_presets("game")
    custom = {n for n, p in presets.items() if p.parent == sonar_page._PRESETS_DIR}

    assert "My Own Curve" in custom
    assert "Moonlight Peaks" not in custom


def test_the_user_wins_over_a_synced_preset_of_the_same_name(dirs):
    """Precedence is bundled, then the user's, then downloaded.

    A preset someone made under a name that later gets published must keep
    working: a sync adds choices, it does not overwrite them.
    """
    bundled, synced, mine = dirs
    _write(synced, "Duel [Game].json")
    _write(mine, "Duel [Game].json")

    assert sonar_page._list_presets("game")["Duel"].parent == mine

    _write(bundled, "Duel [Game].json")
    assert sonar_page._list_presets("game")["Duel"].parent == bundled


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── three origins, three colours ─────────────────────────────────────────────


def test_a_community_import_lands_in_its_own_folder():
    """Imports from asm-presets used to share a folder with the user's own work,
    which made the two impossible to tell apart afterwards."""
    from arctis_sound_manager.gui import preset_import_dialog

    assert preset_import_dialog._PRESETS_DIR.name == "sonar_presets_community"


def test_the_community_colour_is_the_sites_own():
    """A preset wears the colour of the site it was shared on: #22D3B4 is the
    `--accent` custom property of loteran.github.io/asm-presets."""
    assert sonar_page.COMMUNITY_COLOR.upper() == "#22D3B4"


def test_each_origin_is_told_apart(dirs, monkeypatch, tmp_path):
    """The whole point: SteelSeries' presets, the user's and the community's
    must land in three distinct buckets, because that is what drives the colour,
    the ordering and whether a delete is offered."""
    bundled, synced, mine = dirs
    community = tmp_path / "community"
    community.mkdir()
    monkeypatch.setattr(sonar_page, "_COMMUNITY_DIR", community)

    _write(bundled, "Bundled One [Game].json")
    _write(synced, "Synced One [Game].json")
    _write(mine, "Mine One [Game].json")
    _write(community, "Shared One [Game].json")

    presets = sonar_page._list_presets("game")
    origin = {n: p.parent for n, p in presets.items()}

    assert origin["Bundled One"] == bundled
    assert origin["Synced One"] == synced
    assert origin["Mine One"] == mine
    assert origin["Shared One"] == community

    # Only the user's own and the community ones are the user's to remove.
    assert {n for n, p in presets.items() if p.parent == sonar_page._PRESETS_DIR} == {"Mine One"}
    assert {n for n, p in presets.items() if p.parent == sonar_page._COMMUNITY_DIR} == {"Shared One"}


# ── what the user is told when presets arrive ────────────────────────────────


def test_the_signal_carries_names_and_the_gg_version(dirs, monkeypatch):
    """A count alone said "something changed" without saying what. The dialog
    names the presets and the GG release they came from, so the answer to "where
    did these come from?" is on screen instead of in a log file."""
    _bundled, synced, _mine = dirs
    monkeypatch.setattr(preset_sync.PresetSyncWorker, "_should_check", lambda self: True)
    monkeypatch.setattr(preset_sync.PresetSyncWorker, "_write_cache", lambda self: None)
    monkeypatch.setattr(
        preset_sync.PresetSyncWorker, "_fetch_json",
        staticmethod(lambda url: (
            {"presets": ["Heave Ho 2 [Game].json"]} if "manifest" in url
            else {"Heave Ho 2 [Game].json": "117.0.0"})))
    monkeypatch.setattr(preset_sync.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(b'{"ok": 1}'))

    seen = []
    w = preset_sync.PresetSyncWorker()
    w.new_presets_added.connect(lambda names, versions: seen.append((names, versions)))
    w._sync()

    assert seen == [(["Heave Ho 2 [Game].json"], ["117.0.0"])]
    assert (synced / "Heave Ho 2 [Game].json").is_file()


def test_nothing_is_announced_when_nothing_arrived(dirs, monkeypatch):
    """The dialog must never appear on an ordinary launch."""
    bundled, _synced, _mine = dirs
    _write(bundled, "Heave Ho 2 [Game].json")
    monkeypatch.setattr(preset_sync.PresetSyncWorker, "_should_check", lambda self: True)
    monkeypatch.setattr(preset_sync.PresetSyncWorker, "_write_cache", lambda self: None)
    monkeypatch.setattr(
        preset_sync.PresetSyncWorker, "_fetch_json",
        staticmethod(lambda url: {"presets": ["Heave Ho 2 [Game].json"]}))

    seen = []
    w = preset_sync.PresetSyncWorker()
    w.new_presets_added.connect(lambda *a: seen.append(a))
    w._sync()

    assert seen == []


def test_the_dialog_strings_exist():
    """Every user-visible string goes through en.ini; a missing key renders as
    a raw identifier in the dialog."""
    from arctis_sound_manager.i18n import I18n

    for key in ("preset_sync_title", "preset_sync_body",
                "preset_sync_from_gg", "preset_sync_and_more"):
        assert I18n.translate("ui", key) != key, f"{key} missing from en.ini"
