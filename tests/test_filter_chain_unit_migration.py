# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""PKG-3: ASM's own copy of filter-chain.service must not be frozen for ever.

_ensure_filter_chain_service() copies the unit into ~/.config/systemd/user once;
on every later run the list-unit-files check found that copy and returned
without ever comparing it to the packaged one. That is the "local copy wins,
never migrated" shape that cost months on device profiles — dormant only
because the file has never been revised. The mechanism it lacked already
existed next door, as the generated confs' "# ASM-CONF-VERSION" header.
"""
from __future__ import annotations

from pathlib import Path

from arctis_sound_manager.scripts import setup as s


def test_a_stale_unmarked_copy_is_refreshed_and_backed_up(tmp_path, monkeypatch):
    dest = tmp_path / "filter-chain.service"
    dest.write_text("[Unit]\nDescription=an old copy ASM wrote years ago\n")
    monkeypatch.setattr(s, "_run_systemctl", lambda *a, **kw: None)

    s._migrate_home_filter_chain_unit(dest)

    assert s._UNIT_VERSION_MARKER in dest.read_text()
    assert "an old copy" in (tmp_path / "filter-chain.service.bak").read_text(), (
        "the previous copy must stay recoverable"
    )


def test_a_current_copy_is_left_alone(tmp_path, monkeypatch):
    dest = tmp_path / "filter-chain.service"
    s._write_filter_chain_unit(dest)
    before = dest.read_text()
    monkeypatch.setattr(s, "_run_systemctl",
                        lambda *a, **kw: pytest_fail("must not reload for a no-op"))

    s._migrate_home_filter_chain_unit(dest)

    assert dest.read_text() == before
    assert not (tmp_path / "filter-chain.service.bak").exists()


def test_a_hand_edited_copy_of_the_current_version_is_respected(tmp_path, monkeypatch):
    """Same version marker, different content: someone changed it on purpose."""
    dest = tmp_path / "filter-chain.service"
    edited = f"{s._UNIT_VERSION_MARKER}\n[Service]\nExecStart=/my/own/pipewire\n"
    dest.write_text(edited)
    monkeypatch.setattr(s, "_run_systemctl", lambda *a, **kw: None)

    s._migrate_home_filter_chain_unit(dest)

    assert dest.read_text() == edited


def test_a_missing_file_is_not_created_here(tmp_path):
    """Migration only refreshes what exists; installing is the other path."""
    dest = tmp_path / "filter-chain.service"
    s._migrate_home_filter_chain_unit(dest)
    assert not dest.exists()


def pytest_fail(msg):
    raise AssertionError(msg)
