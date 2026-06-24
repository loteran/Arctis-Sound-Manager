# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for update_checker — detect_all_install_methods, PACKAGE_MANAGER_COMMANDS."""

import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager.update_checker import (
    PACKAGE_MANAGER_COMMANDS,
    InstallMethod,
    detect_all_install_methods,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _ok(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _fail(returncode=1, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ── FIX 3: RPM command contains --refresh ─────────────────────────────────

def test_rpm_command_contains_refresh():
    cmd = PACKAGE_MANAGER_COMMANDS[InstallMethod.RPM]
    assert "--refresh" in cmd, (
        "RPM upgrade command must include --refresh to force COPR metadata sync"
    )


def test_apt_command_contains_apt_update():
    cmd = PACKAGE_MANAGER_COMMANDS[InstallMethod.APT]
    assert "apt update" in cmd, "APT command must run apt update before upgrading"


# ── FIX 2: detect pip --user shadow install ───────────────────────────────

def _base_run_side_effect(cmd, **kwargs):
    """Default: rpm/pacman/dpkg/pipx all report package not installed."""
    if cmd[0] == "rpm":
        return _fail()
    if cmd[0] == "pacman":
        return _fail()
    if cmd[0] == "dpkg":
        return _fail()
    if cmd[0] == "pipx":
        return _ok(stdout="")
    if cmd[:2] == ["bash", "-c"]:
        # command -v -a asm-daemon — single result → not shadowed
        return _ok(stdout="/usr/bin/asm-daemon\n")
    return _fail()


def test_detect_pip_user_shadow_via_user_site(tmp_path):
    """When arctis_sound_manager.__file__ lives under user-site, PIP is detected."""
    user_site = tmp_path / "user_site"
    pkg_dir = user_site / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    fake_asm = types.ModuleType("arctis_sound_manager")
    fake_asm.__file__ = str(fake_init)

    with (
        mock.patch.dict("sys.modules", {"arctis_sound_manager": fake_asm}),
        mock.patch("site.getusersitepackages", return_value=str(user_site)),
        mock.patch("shutil.which", side_effect=lambda b: None),  # no rpm/pacman/dpkg/pipx
        mock.patch("subprocess.run", side_effect=_base_run_side_effect),
    ):
        result = detect_all_install_methods()

    assert InstallMethod.PIP in result, (
        "detect_all_install_methods should return PIP when the running package "
        "lives under user site-packages"
    )


def test_detect_pip_user_shadow_via_multiple_daemon_binaries(tmp_path):
    """When asm-daemon appears twice on PATH (system + ~/.local), PIP is detected."""
    # Package NOT under user-site (so signal 1 doesn't fire)
    sys_site = tmp_path / "sys_site"
    pkg_dir = sys_site / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    user_site = tmp_path / "user_site"
    user_site.mkdir(parents=True)

    fake_asm = types.ModuleType("arctis_sound_manager")
    fake_asm.__file__ = str(fake_init)

    def _run_side(cmd, **kwargs):
        if cmd[:2] == ["bash", "-c"]:
            # Two asm-daemon binaries found → shadowing pip install
            return _ok(stdout="/home/user/.local/bin/asm-daemon\n/usr/bin/asm-daemon\n")
        return _fail()

    with (
        mock.patch.dict("sys.modules", {"arctis_sound_manager": fake_asm}),
        mock.patch("site.getusersitepackages", return_value=str(user_site)),
        mock.patch("shutil.which", side_effect=lambda b: None),
        mock.patch("subprocess.run", side_effect=_run_side),
    ):
        result = detect_all_install_methods()

    assert InstallMethod.PIP in result, (
        "detect_all_install_methods should return PIP when multiple asm-daemon "
        "binaries are found on PATH"
    )


def test_detect_no_pip_shadow_clean_rpm_install(tmp_path):
    """When RPM is installed and no pip shadow exists, only RPM is returned."""
    sys_site = tmp_path / "sys_site"
    pkg_dir = sys_site / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    user_site = tmp_path / "user_site"
    user_site.mkdir(parents=True)

    fake_asm = types.ModuleType("arctis_sound_manager")
    fake_asm.__file__ = str(fake_init)

    def _run_side(cmd, **kwargs):
        if cmd[0] == "rpm":
            return _ok(stdout="arctis-sound-manager-1.0.86-1.x86_64\n")
        if cmd[:2] == ["bash", "-c"]:
            # Only one asm-daemon
            return _ok(stdout="/usr/bin/asm-daemon\n")
        return _fail()

    with (
        mock.patch.dict("sys.modules", {"arctis_sound_manager": fake_asm}),
        mock.patch("site.getusersitepackages", return_value=str(user_site)),
        mock.patch("shutil.which", side_effect=lambda b: "/usr/bin/rpm" if b == "rpm" else None),
        mock.patch("subprocess.run", side_effect=_run_side),
    ):
        result = detect_all_install_methods()

    assert InstallMethod.RPM in result
    assert InstallMethod.PIP not in result


def test_detect_rpm_plus_pip_shadow_returns_both(tmp_path):
    """RPM + pip --user shadow → both methods in the result list."""
    user_site = tmp_path / "user_site"
    pkg_dir = user_site / "arctis_sound_manager"
    pkg_dir.mkdir(parents=True)
    fake_init = pkg_dir / "__init__.py"
    fake_init.touch()

    fake_asm = types.ModuleType("arctis_sound_manager")
    fake_asm.__file__ = str(fake_init)

    def _run_side(cmd, **kwargs):
        if cmd[0] == "rpm":
            return _ok(stdout="arctis-sound-manager-1.0.86-1.x86_64\n")
        if cmd[:2] == ["bash", "-c"]:
            return _ok(stdout="/home/user/.local/bin/asm-daemon\n/usr/bin/asm-daemon\n")
        return _fail()

    with (
        mock.patch.dict("sys.modules", {"arctis_sound_manager": fake_asm}),
        mock.patch("site.getusersitepackages", return_value=str(user_site)),
        mock.patch("shutil.which", side_effect=lambda b: "/usr/bin/rpm" if b == "rpm" else None),
        mock.patch("subprocess.run", side_effect=_run_side),
    ):
        result = detect_all_install_methods()

    assert InstallMethod.RPM in result
    assert InstallMethod.PIP in result
    assert len(result) == 2
