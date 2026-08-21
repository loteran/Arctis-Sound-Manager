# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for update_checker — detect_all_install_methods, PACKAGE_MANAGER_COMMANDS."""

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arctis_sound_manager.update_checker import (
    PACKAGE_MANAGER_COMMANDS,
    InstallMethod,
    detect_all_install_methods,
    repo_setup_command,
    upgrade_source_available,
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


def test_usrmerge_symlink_not_flagged_as_second_install(tmp_path):
    """A single binary seen through /bin AND /usr/bin (usr-merge) is NOT a dup.

    On modern Ubuntu/Fedora/Arch /bin is a symlink to /usr/bin and both are on
    PATH, so `command -v -a asm-daemon` lists the same physical file twice
    (/usr/bin/asm-daemon and /bin/asm-daemon). Before the fix this counted as a
    second install and raised a phantom "Multiple ASM installations detected"
    banner that blocked the update (issue #114).
    """
    # Simulate usr-merge: <root>/usr/bin/asm-daemon is the real file,
    # <root>/bin is a symlink to <root>/usr/bin.
    usr_bin = tmp_path / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    real_daemon = usr_bin / "asm-daemon"
    real_daemon.touch()
    try:
        (tmp_path / "bin").symlink_to(usr_bin, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/privilege level")

    # Package NOT under user-site (signal 1 must not fire)
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
        if cmd[0] == "dpkg":
            return _ok()  # apt install present
        if cmd[:2] == ["bash", "-c"]:
            # Same binary, two PATH spellings via the /bin -> /usr/bin symlink
            return _ok(stdout=f"{tmp_path / 'usr' / 'bin' / 'asm-daemon'}\n"
                              f"{tmp_path / 'bin' / 'asm-daemon'}\n")
        return _fail()

    with (
        mock.patch.dict("sys.modules", {"arctis_sound_manager": fake_asm}),
        mock.patch("site.getusersitepackages", return_value=str(user_site)),
        mock.patch("shutil.which", side_effect=lambda b: None),
        mock.patch("subprocess.run", side_effect=_run_side),
    ):
        result = detect_all_install_methods()

    assert InstallMethod.APT in result
    assert InstallMethod.PIP not in result, (
        "usr-merge /bin symlink must not be counted as a second (pip) install"
    )
    assert len(result) == 1


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


# ── Packages that don't go by our name (discussion #140) ─────────────────────

def test_owning_package_is_looked_up_when_the_name_differs(monkeypatch):
    """Fedora's Terra ships ASM as python3-arctis-sound-manager."""
    from arctis_sound_manager import update_checker as uc

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["rpm", "-qf"]:
            return SimpleNamespace(returncode=0, stdout="python3-arctis-sound-manager\n")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(uc.subprocess, "run", fake_run)

    assert uc.installed_package_name(uc.InstallMethod.RPM) == "python3-arctis-sound-manager"


def test_upgrade_command_targets_the_installed_package(monkeypatch):
    """Upgrading the wrong package name reports success and changes nothing."""
    from arctis_sound_manager import update_checker as uc

    monkeypatch.setattr(uc, "installed_package_name",
                        lambda method: "python3-arctis-sound-manager")

    cmd = uc.package_manager_command(uc.InstallMethod.RPM)

    assert "python3-arctis-sound-manager" in cmd
    assert "--refresh" in cmd


def test_upgrade_command_falls_back_to_the_default_name(monkeypatch):
    from arctis_sound_manager import update_checker as uc

    monkeypatch.setattr(uc, "installed_package_name", lambda method: None)

    assert "arctis-sound-manager" in uc.package_manager_command(uc.InstallMethod.RPM)


def test_dpkg_output_is_reduced_to_the_package_name(monkeypatch):
    """dpkg -S answers "package: /path"."""
    from arctis_sound_manager import update_checker as uc

    monkeypatch.setattr(uc.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout="arctis-sound-manager: /usr/lib/python3/dist-packages/x.py\n"))

    assert uc.installed_package_name(uc.InstallMethod.APT) == "arctis-sound-manager"


def test_a_renamed_package_is_still_detected_as_a_system_install(monkeypatch):
    """Otherwise the app offers a pip update to someone running an RPM."""
    from arctis_sound_manager import update_checker as uc

    monkeypatch.setattr(uc.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout=""))
    monkeypatch.setattr(uc.shutil, "which", lambda name: None)
    monkeypatch.setattr(uc, "installed_package_name",
                        lambda method: "python3-arctis-sound-manager"
                        if method is uc.InstallMethod.RPM else None)

    assert uc.InstallMethod.RPM in uc.detect_all_install_methods()


# ── Arch: binary repository vs AUR ───────────────────────────────────────────

def test_pacman_upgrade_uses_pacman_when_the_package_is_in_a_repository(monkeypatch):
    """Installed from our signed repository — no AUR helper should be needed."""
    from arctis_sound_manager import update_checker as uc

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["pacman", "-Si"]:
            return SimpleNamespace(returncode=0, stdout="Repository : arctis-sound-manager\n")
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(uc.subprocess, "run", fake_run)
    monkeypatch.setattr(uc, "installed_package_name", lambda method: "arctis-sound-manager")

    cmd = uc.package_manager_command(uc.InstallMethod.PACMAN)

    assert cmd.startswith("sudo pacman -Syu arctis-sound-manager")
    assert "paru" not in cmd and "yay" not in cmd


def test_pacman_upgrade_falls_back_to_an_aur_helper(monkeypatch):
    """Installed from the AUR — pacman cannot upgrade it, and would say so forever."""
    from arctis_sound_manager import update_checker as uc

    monkeypatch.setattr(uc.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(uc.shutil, "which", lambda name: "/usr/bin/yay" if name == "yay" else None)
    monkeypatch.setattr(uc, "installed_package_name", lambda method: "arctis-sound-manager")

    assert uc.package_manager_command(uc.InstallMethod.PACMAN) == \
        "yay -S arctis-sound-manager && asm-setup"


def test_pacman_upgrade_survives_pacman_being_absent(monkeypatch):
    """Never raise out of a command-string builder."""
    from arctis_sound_manager import update_checker as uc

    def boom(cmd, **kwargs):
        raise FileNotFoundError("pacman")

    monkeypatch.setattr(uc.subprocess, "run", boom)
    monkeypatch.setattr(uc.shutil, "which", lambda name: None)
    monkeypatch.setattr(uc, "installed_package_name", lambda method: "arctis-sound-manager")

    assert "arctis-sound-manager" in uc.package_manager_command(uc.InstallMethod.PACMAN)


# ── Hand-installed vs repository-tracked (#163) ──────────────────────────────

def _route(routes):
    """subprocess.run side effect keyed on the executable (argv[0])."""
    def _side(cmd, *a, **k):
        key = cmd[0] if isinstance(cmd, (list, tuple)) else cmd
        return routes.get(key, _fail(returncode=127))
    return _side


def test_upgrade_source_apt_hand_installed_is_false():
    policy = ("arctis-sound-manager:\n  Installed: 1.2.20\n  Candidate: 1.2.20\n"
              "  Version table:\n *** 1.2.20 100\n        100 /var/lib/dpkg/status\n")
    with mock.patch("subprocess.run", side_effect=_route({"apt-cache": _ok(stdout=policy)})):
        assert upgrade_source_available(InstallMethod.APT, "arctis-sound-manager") is False


def test_upgrade_source_apt_ppa_is_true():
    policy = ("  Version table:\n     1.2.21 500\n"
              "        500 https://ppa.launchpadcontent.net/loteran/arctis/ubuntu\n"
              " *** 1.2.20 100\n        100 /var/lib/dpkg/status\n")
    with mock.patch("subprocess.run", side_effect=_route({"apt-cache": _ok(stdout=policy)})):
        assert upgrade_source_available(InstallMethod.APT, "arctis-sound-manager") is True


def test_upgrade_source_rpm_hand_installed_is_false():
    with mock.patch("subprocess.run", side_effect=_route({"dnf": _ok(stdout="@commandline\n")})):
        assert upgrade_source_available(InstallMethod.RPM, "arctis-sound-manager") is False


def test_upgrade_source_rpm_copr_is_true():
    repo = "copr:copr.fedorainfracloud.org:loteran:arctis-sound-manager\n"
    with mock.patch("subprocess.run", side_effect=_route({"dnf": _ok(stdout=repo)})):
        assert upgrade_source_available(InstallMethod.RPM, "arctis-sound-manager") is True


def test_upgrade_source_pacman_sync_repo_is_true():
    with mock.patch("subprocess.run", side_effect=_route({"pacman": _ok()})):
        assert upgrade_source_available(InstallMethod.PACMAN, "arctis-sound-manager") is True


def test_upgrade_source_pacman_aur_with_helper_is_true():
    with mock.patch("subprocess.run", side_effect=_route({"pacman": _fail()})), \
         mock.patch("shutil.which", side_effect=lambda b: "/usr/bin/paru" if b == "paru" else None):
        assert upgrade_source_available(InstallMethod.PACMAN, "arctis-sound-manager") is True


def test_upgrade_source_pacman_no_repo_no_helper_is_false():
    with mock.patch("subprocess.run", side_effect=_route({"pacman": _fail()})), \
         mock.patch("shutil.which", side_effect=lambda b: None):
        assert upgrade_source_available(InstallMethod.PACMAN, "arctis-sound-manager") is False


def test_upgrade_source_pip_is_always_true():
    # No distro repo involved — pip upgrades itself, no subprocess needed.
    assert upgrade_source_available(InstallMethod.PIP, "arctis-sound-manager") is True


def test_upgrade_source_unknowable_apt_error_assumes_tracked():
    # apt-cache failing must not falsely accuse a repo install of being hand-dropped.
    with mock.patch("subprocess.run", side_effect=_route({"apt-cache": _fail()})):
        assert upgrade_source_available(InstallMethod.APT, "arctis-sound-manager") is True


def test_repo_setup_command_per_manager():
    assert "add-apt-repository" in repo_setup_command(InstallMethod.APT)
    assert "copr enable" in repo_setup_command(InstallMethod.RPM)
    assert repo_setup_command(InstallMethod.PIP) is None

    # Arch gets the signed pacman repository the README documents. This used
    # to assert `"install.sh" in …`, which held right up until someone ran the
    # command: piping that script into bash cannot work. It copies PipeWire
    # configs and device YAMLs out of the checkout it expects around it, and a
    # piped script has neither a checkout nor a BASH_SOURCE. The assertion
    # pinned the command's spelling and never its premise.
    pacman = repo_setup_command(InstallMethod.PACMAN)
    assert "install.sh" not in pacman
    assert "pacman-key --add" in pacman
    assert "[arctis-sound-manager]" in pacman
    assert "pacman -Sy arctis-sound-manager" in pacman


def test_pacman_repo_setup_can_be_run_twice():
    """Appending the repo block unconditionally would duplicate it in
    pacman.conf every time the update dialog is used."""
    pacman = repo_setup_command(InstallMethod.PACMAN)
    assert "grep -q" in pacman, \
        f"nothing stops the repo block being appended twice:\n{pacman}"
