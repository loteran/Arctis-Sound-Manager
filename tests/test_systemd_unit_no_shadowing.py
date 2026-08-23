# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #206: ASM rewrote a stale copy of its own unit on every session.

A unit of the same name under ~/.config/systemd/user REPLACES the packaged one
rather than extending it. The template hardcoded in this module had drifted
from systemd/arctis-manager.service — it lost `filter-chain.service` from
After= and Wants= — so every session start quietly reinstated a copy that let
the daemon start before the filter-chain existed. That is the node the mic
capture has to link into, so it made #206's link race materially more likely.

The AUR .install already deletes this path on removal, calling it "stale user
level systemd unit copies left over from older installs" — while the code put
it back on the next login.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from arctis_sound_manager import systemd as asm_systemd

_PACKAGED = (
    "[Unit]\n"
    "Description=Arctis Sound Manager\n"
    "After=pipewire.service pipewire-pulse.service filter-chain.service\n"
    "Wants=pipewire.service filter-chain.service\n"
    "[Service]\nExecStart=/usr/bin/asm-daemon\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home_unit = tmp_path / "home" / "arctis-manager.service"
    packaged = tmp_path / "pkg" / "arctis-manager.service"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(_PACKAGED)

    monkeypatch.setattr(asm_systemd, "HOME_SYSTEMD_SERVICE_FOLDER", home_unit.parent)
    monkeypatch.setattr(asm_systemd, "_PACKAGED_UNIT_DIRS", (packaged.parent,))
    monkeypatch.setattr(asm_systemd.shutil, "which", lambda name: "/usr/bin/asm-daemon")
    monkeypatch.setattr("arctis_sound_manager.init_system.detect_init", lambda: "systemd")
    monkeypatch.setattr(asm_systemd.sc, "daemon_reload", lambda: True)
    monkeypatch.setattr(asm_systemd.sc, "enable", lambda *a, **kw: True)
    for var in ("container", "DISTROBOX_ENTER_PATH", "CONTAINER_ID", "FLATPAK_ID", "SNAP"):
        monkeypatch.delenv(var, raising=False)
    return home_unit


def test_no_user_copy_is_written_when_the_package_ships_one(env):
    asm_systemd.ensure_systemd_unit()
    assert not env.exists(), "the packaged unit must be left to apply"


def test_a_stale_copy_we_wrote_is_cleared_away(env):
    """The exact file the reporter found: our template, minus filter-chain."""
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(
        "[Unit]\nDescription=Arctis Sound Manager\n"
        "After=pipewire.service pipewire-pulse.service\n"
        "Wants=pipewire.service\n"
        "[Service]\nExecStart=/usr/bin/asm-daemon\n"
    )

    asm_systemd.ensure_systemd_unit()

    assert not env.exists()


def test_someone_elses_edit_is_left_alone(env):
    """Deleting a deliberate customisation would be worse than the drift."""
    env.parent.mkdir(parents=True, exist_ok=True)
    hand_written = "[Unit]\nDescription=My own thing\n[Service]\nExecStart=/opt/mine\n"
    env.write_text(hand_written)

    asm_systemd.ensure_systemd_unit()

    assert env.read_text() == hand_written


def test_without_a_packaged_unit_the_copy_is_still_written(env, monkeypatch):
    """A pip/source install has no packaged unit; that is what this is for."""
    monkeypatch.setattr(asm_systemd, "_PACKAGED_UNIT_DIRS", (Path("/nonexistent"),))

    asm_systemd.ensure_systemd_unit()

    assert env.exists()
    assert "ExecStart=/usr/bin/asm-daemon" in env.read_text()


def test_the_written_template_matches_the_packaged_one():
    """The drift that caused #206: the two must not disagree about the unit's
    dependencies. Checked against the file the packages install."""
    repo_unit = (Path(__file__).resolve().parents[1]
                 / "systemd" / "arctis-manager.service").read_text()

    import inspect
    template = inspect.getsource(asm_systemd.write_systemd_service)

    for line in ("After=", "Wants="):
        packaged_value = next(l.strip() for l in repo_unit.splitlines()
                              if l.startswith(line))
        assert packaged_value in template, (
            f"systemd.py's template disagrees with the packaged unit on {line} "
            f"— expected {packaged_value!r}"
        )
