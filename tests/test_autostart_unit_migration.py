# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Carrying "launch at login" across the v1.3.0 tray unit rename (#191).

The rename itself was right: named after the desktop entry, the portal can read
an app id off the cgroup and the clip shortcut binds. What was missing is that
nobody carried the *enablement* over.

For anyone whose autostart pointed at the packaged arctis-gui.service, the
upgrade deleted the file the symlink pointed to and never enabled the
replacement. The tray stopped appearing at login on a machine where nothing
else looked wrong — reported on two by TheJurassicSnark.

The rule these tests pin down: restore an autostart that existed, never grant
one that did not.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".config" / "systemd" / "user").mkdir(parents=True)
    return tmp_path


def _units(home) -> Path:
    return home / ".config" / "systemd" / "user"


@pytest.fixture(autouse=True)
def no_packaged_unit(home, monkeypatch):
    """No distribution unit unless a test provides one."""
    from arctis_sound_manager import autostart
    monkeypatch.setattr(autostart, "_PACKAGED_UNIT_DIR", home / "packaged")


def test_an_enabled_legacy_unit_is_moved_to_the_new_one(home):
    from arctis_sound_manager import autostart

    (_units(home) / "arctis-gui.service").write_text("[Unit]\n")

    with patch.object(autostart.sc, "is_enabled", lambda s: s == "arctis-gui.service"), \
         patch.object(autostart.sc, "enable", MagicMock(return_value=True)) as enable, \
         patch.object(autostart.sc, "disable", MagicMock(return_value=True)) as disable, \
         patch.object(autostart.sc, "daemon_reload", MagicMock(return_value=True)), \
         patch("shutil.which", return_value="/usr/bin/asm-gui"):
        changed = autostart.migrate_legacy_gui_autostart()

    assert changed is True
    enable.assert_called_once_with("arctis-gui")      # maps to app-ArctisManager
    disable.assert_called_once_with("arctis-gui.service")
    assert not (_units(home) / "arctis-gui.service").exists(), "the old unit is removed"


def test_a_dangling_want_is_enough_evidence(home):
    """The packaged-unit case, which is the reported one.

    The upgrade deleted /usr/lib/systemd/user/arctis-gui.service, so the want
    left in the user's config points at nothing and `is-enabled` can answer
    anything. The leftover symlink is what says the user had an autostart.
    """
    from arctis_sound_manager import autostart

    wants = _units(home) / "graphical-session.target.wants"
    wants.mkdir(parents=True)
    (wants / "arctis-gui.service").symlink_to("/usr/lib/systemd/user/arctis-gui.service")

    with patch.object(autostart.sc, "is_enabled", lambda s: False), \
         patch.object(autostart.sc, "enable", MagicMock(return_value=True)) as enable, \
         patch.object(autostart.sc, "disable", MagicMock(return_value=True)), \
         patch.object(autostart.sc, "daemon_reload", MagicMock(return_value=True)), \
         patch("shutil.which", return_value="/usr/bin/asm-gui"):
        changed = autostart.migrate_legacy_gui_autostart()

    assert changed is True
    enable.assert_called_once_with("arctis-gui")
    assert not (wants / "arctis-gui.service").is_symlink(), "the dangling want is cleared"


def test_someone_who_never_wanted_an_autostart_is_left_alone(home):
    """The line this must not cross: it restores, it does not grant."""
    from arctis_sound_manager import autostart

    with patch.object(autostart.sc, "is_enabled", lambda s: False), \
         patch.object(autostart.sc, "enable", MagicMock()) as enable, \
         patch.object(autostart.sc, "disable", MagicMock()) as disable:
        changed = autostart.migrate_legacy_gui_autostart()

    assert changed is False
    enable.assert_not_called()
    disable.assert_not_called()


def test_the_old_unit_survives_a_failed_migration(home):
    """Better the autostart they had than none at all.

    If the replacement cannot be put in place — no packaged unit, no asm-gui on
    PATH — the old one must not be removed, or the user is left with nothing.
    """
    from arctis_sound_manager import autostart

    legacy = _units(home) / "arctis-gui.service"
    legacy.write_text("[Unit]\n")

    with patch.object(autostart.sc, "is_enabled", lambda s: s == "arctis-gui.service"), \
         patch.object(autostart.sc, "enable", MagicMock()) as enable, \
         patch.object(autostart.sc, "disable", MagicMock()) as disable, \
         patch.object(autostart.sc, "daemon_reload", MagicMock()), \
         patch("shutil.which", return_value=None), \
         patch.object(autostart, "_PACKAGED_UNIT_DIR", home / "no-such-packaged-dir"):
        changed = autostart.migrate_legacy_gui_autostart()

    assert changed is False
    enable.assert_not_called()
    disable.assert_not_called()
    assert legacy.exists(), "the working autostart must not be taken away"


def test_the_daemon_runs_the_migration_at_startup():
    """It cannot live in the GUI: the GUI not starting is the symptom."""
    from pathlib import Path as P

    import arctis_sound_manager.scripts.daemon as daemon_mod

    src = P(daemon_mod.__file__).read_text()
    assert "migrate_legacy_gui_autostart" in src, (
        "the daemon must run the migration — the GUI is what fails to start")
