# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Distrobox generators must write the *current* tray unit name.

v1.3.0 renamed the tray unit from arctis-gui.service to app-ArctisManager.service
(autostart._GUI_SERVICE / service_control._SERVICE_MAP["arctis-gui"]) because
xdg-desktop-portal only derives an app id for a non-sandboxed process from a
cgroup shaped app-<AppID>[-<random>].service. The native install path and its
migration were covered by test_autostart.py / test_autostart_unit_migration.py,
but scripts/distrobox/*.sh hand-write their own unit files and were never
updated: bazzite.sh, silverblue.sh, steamos.sh and the shared _common.sh all
kept writing "arctis-gui.service", so on every Distrobox install (the
documented path for Bazzite, SteamOS and Silverblue) the resulting cgroup never
matches app-<AppID>[-<random>].service and the Clips global shortcut can never
bind — the portal answers "NotAllowed: An app id is required".

These tests pin the fix down at the text level: every generator must carry the
current name, and none of them may carry the old one anywhere (a unit file
name, an enable/disable list, a status-summary loop).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DISTROBOX_DIR = Path(__file__).resolve().parents[1] / "scripts" / "distrobox"

_CURRENT_GUI_UNIT = "app-ArctisManager.service"
_LEGACY_GUI_UNIT = "arctis-gui.service"

# Every generator that writes host systemd unit files or a service list for
# the tray. uninstall.sh is included because leaving it targeting the old name
# would strand the renamed unit file behind on every uninstall.
_SCRIPTS = sorted(_DISTROBOX_DIR.glob("*.sh"))


@pytest.fixture(params=_SCRIPTS, ids=lambda p: p.name)
def script_text(request) -> str:
    return request.param.read_text()


def test_every_distrobox_script_was_found():
    """Guard against a typo'd glob silently checking zero files."""
    names = {p.name for p in _SCRIPTS}
    assert {"_common.sh", "bazzite.sh", "silverblue.sh", "steamos.sh"} <= names


def test_no_distrobox_script_writes_the_legacy_unit_name(script_text):
    assert _LEGACY_GUI_UNIT not in script_text


@pytest.mark.parametrize("name", ["_common.sh", "bazzite.sh", "silverblue.sh", "steamos.sh"])
def test_generator_writes_the_current_gui_unit_file(name):
    """Each generator's write_systemd_units function names the file correctly."""
    text = (_DISTROBOX_DIR / name).read_text()
    assert f'"{_CURRENT_GUI_UNIT}" <<EOF' in text or f"/{_CURRENT_GUI_UNIT}\" <<EOF" in text


@pytest.mark.parametrize("name", ["_common.sh", "bazzite.sh", "silverblue.sh", "steamos.sh"])
def test_generator_enables_the_current_gui_unit(name):
    """The enable-services step must target the renamed unit, not the old one."""
    text = (_DISTROBOX_DIR / name).read_text()
    assert _CURRENT_GUI_UNIT in text


def test_uninstall_cleans_up_the_current_gui_unit():
    text = (_DISTROBOX_DIR / "uninstall.sh").read_text()
    assert _CURRENT_GUI_UNIT in text
    assert _LEGACY_GUI_UNIT not in text
