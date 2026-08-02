# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Clips is opt-in, and the rest of ASM must not pay for it.

The feature needs PyGObject, four GStreamer plugin sets and ffmpeg — none of
which the mixer or the equaliser touch. Making them hard requirements charges
every user who only wants a headset mixer for a screen recorder they never
open, so the feature ships off, its packages are installed from the toggle that
turns it on, and a base install is never told it is missing anything.

What is worth pinning here is the *silence*: an install with Clips off must not
report GStreamer as a problem, and must not import it either.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from pathlib import Path

import pytest

from arctis_sound_manager import system_deps_checker as sdc

ROOT = Path(__file__).parent.parent


# ── The setting ───────────────────────────────────────────────────────────────


def test_clips_is_off_on_a_fresh_install():
    """A rolling buffer of the user's screen is not something to switch on for
    them, however capable the machine turns out to be."""
    from arctis_sound_manager.settings import GeneralSettings

    assert GeneralSettings().clips_enabled is False


# ── The dep group ─────────────────────────────────────────────────────────────


def test_clip_deps_are_absent_from_the_base_check_run(monkeypatch):
    """The whole point. With Clips off, a machine without GStreamer is not an
    incomplete install — it is a machine that does not need GStreamer."""
    monkeypatch.setattr(sdc, "clips_enabled", lambda: False)

    names = {r.name for r in sdc.run_all_checks()}

    assert not [n for n in names if "GStreamer" in n or "PyGObject" in n]


def test_clip_deps_join_the_run_once_the_feature_is_on(monkeypatch):
    monkeypatch.setattr(sdc, "clips_enabled", lambda: True)

    names = {r.name for r in sdc.run_all_checks()}

    assert "PyGObject (gi)" in names
    assert "GStreamer: screen capture (pipewiresrc)" in names


def test_the_clip_group_is_offered_whatever_the_toggle_says(monkeypatch):
    """The toggle installs from this list *before* switching the feature on, so
    gating the list on the feature being on would make it uninstallable."""
    monkeypatch.setattr(sdc, "clips_enabled", lambda: False)

    assert len(sdc.clip_dep_checks()) >= 4


def test_every_clip_dep_can_be_installed_on_all_three_distro_families():
    """A dep the toggle cannot install is a dead end: the dialog would show a
    row with nothing to press."""
    for check in sdc.clip_dep_checks():
        for distro in ("fedora", "debian", "arch"):
            assert distro in check.install_commands, f"{check.name} has no {distro} command"


def test_a_broken_settings_file_reads_as_off(monkeypatch):
    """Failing closed matters more here than elsewhere: the failure mode of
    guessing "on" is a screen recorder the user did not ask for."""
    def _boom():
        raise OSError("no settings for you")

    monkeypatch.setattr(
        "arctis_sound_manager.settings.GeneralSettings.read_from_file",
        staticmethod(_boom))

    assert sdc.clips_enabled() is False


# ── Import weight ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [
    "arctis_sound_manager.clip_capture",
    "arctis_sound_manager.clip_export",
    "arctis_sound_manager.clip_library",
    "arctis_sound_manager.gui.clips_page",
])
def test_no_clip_module_imports_gstreamer_at_module_level(module):
    """What makes the gating possible at all: the sidebar entry and the page
    are built on machines with no GStreamer installed, so `import gi` has to
    stay behind _require_gst() rather than sitting at the top of the file.

    Checked as source rather than by importing, because an import in the test
    environment would prove nothing about a machine that lacks the library.
    """
    path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
    source = path.read_text(encoding="utf-8")

    top_level = re.findall(r"^(?:import gi|from gi\b)", source, re.MULTILINE)

    assert not top_level, f"{module} imports gi at module level"


# ── The toggle ────────────────────────────────────────────────────────────────


@pytest.fixture
def device_page():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui.device_page import DevicePage

    app = QApplication.instance() or QApplication([])
    page = DevicePage()
    yield page
    page.deleteLater()


def _blocking_check(detected: bool) -> sdc.DepCheck:
    return sdc.DepCheck(
        name="PyGObject (gi)",
        severity=sdc.Severity.BLOCKING,
        feature="clip capture",
        detect=lambda: detected,
        install_commands={"arch": ["pacman", "-S", "gi"]},
    )


def test_enabling_without_the_capture_packages_puts_the_switch_back(
        device_page, monkeypatch):
    """The failure this whole change exists to prevent: a Clips page that is
    present and does nothing. If the packages are still missing when the deps
    dialog closes, the feature must not claim to be on."""
    from PySide6.QtCore import Qt

    from arctis_sound_manager.settings import GeneralSettings

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(False)])
    monkeypatch.setattr(
        "arctis_sound_manager.gui.system_deps_dialog.SystemDepsDialog",
        lambda *a, **k: type("_Dlg", (), {"exec": lambda self: 0})())
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.exec", lambda self: 0)

    # Where the user just put it, so the revert is something to observe rather
    # than the state it started in.
    device_page._clips_toggle.set_state("right")
    assert device_page._clips_toggle.toggle.isChecked() is True

    device_page._on_clips_toggled(Qt.CheckState.Checked)

    assert GeneralSettings.read_from_file().clips_enabled is False
    assert device_page._clips_toggle.toggle.isChecked() is False


def test_enabling_with_the_packages_present_sticks(device_page, monkeypatch):
    from PySide6.QtCore import Qt

    from arctis_sound_manager.settings import GeneralSettings

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(True)])

    device_page._on_clips_toggled(Qt.CheckState.Checked)

    assert GeneralSettings.read_from_file().clips_enabled is True

    # And back off again, so the test leaves the setting where it found it.
    device_page._on_clips_toggled(Qt.CheckState.Unchecked)
    assert GeneralSettings.read_from_file().clips_enabled is False


def test_a_degraded_dep_does_not_veto_the_feature(device_page, monkeypatch):
    """ffmpeg missing costs thumbnails and export — a real loss, but the
    capture still records, so it is not grounds for refusing to switch on."""
    from PySide6.QtCore import Qt

    from arctis_sound_manager.settings import GeneralSettings

    degraded = sdc.DepCheck(
        name="ffmpeg / ffprobe", severity=sdc.Severity.DEGRADED,
        feature="thumbnails", detect=lambda: False,
        install_commands={"arch": ["pacman", "-S", "ffmpeg"]})
    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [degraded])
    monkeypatch.setattr(
        "arctis_sound_manager.gui.system_deps_dialog.SystemDepsDialog",
        lambda *a, **k: type("_Dlg", (), {"exec": lambda self: 0})())

    device_page._on_clips_toggled(Qt.CheckState.Checked)

    assert GeneralSettings.read_from_file().clips_enabled is True

    device_page._on_clips_toggled(Qt.CheckState.Unchecked)


# ── The window ────────────────────────────────────────────────────────────────


def test_the_main_window_builds_with_clips_off():
    """The gating shipped broken once, and this is the test that would have
    caught it.

    apply_clips_visibility() was called with the sidebar buttons, which is
    long before there is a stack to ask which page is showing — and the branch
    that reads the stack only runs when Clips is *off*, which is the new
    default. So the very first launch after the change raised AttributeError
    inside the window constructor: the tray icon appeared, nothing opened, and
    the traceback was in a log nobody was watching.

    Building the real window is the only thing that catches it. Both pages that
    were smoke-tested at the time — DevicePage and ClipsPage — construct
    perfectly well on their own.
    """
    pytest.importorskip("PySide6")
    import logging

    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui.main_app import PAGE_CLIPS, QMainApp

    app = QApplication.instance() or QApplication([])
    main = QMainApp(app, logging.WARNING)
    try:
        assert main.main_window is not None
        # Hidden, not removed: sidebar index is stack index, so dropping the
        # entry would renumber Settings and Help.
        assert main._stack.count() == 8
        assert main._sidebar_buttons[PAGE_CLIPS].isVisible() is False
        # The link a settings page walks to reach the sidebar.
        assert getattr(main.main_window, "main_app", None) is main
    finally:
        main.main_window.deleteLater()


# ── Packaging ─────────────────────────────────────────────────────────────────


def test_arch_package_does_not_hard_depend_on_the_clip_stack():
    """`depends` is what a base install pulls. The clip packages belong in
    optdepends, where the toggle installs them from."""
    pkgbuild = (ROOT / "aur" / "PKGBUILD").read_text(encoding="utf-8")
    depends = pkgbuild.split("optdepends=(")[0]

    for package in ("python-gobject", "gst-plugins-base", "gst-plugin-pipewire",
                    "ffmpeg"):
        assert f"'{package}'" not in depends, f"{package} is still a hard depend"


def test_arch_package_still_offers_the_clip_stack():
    """Dropped from depends is not the same as dropped — a user who wants Clips
    has to be able to find out what it needs."""
    srcinfo = (ROOT / "aur" / ".SRCINFO").read_text(encoding="utf-8")

    for package in ("python-gobject", "gst-plugins-base", "gst-plugin-pipewire",
                    "ffmpeg"):
        assert f"optdepends = {package}:" in srcinfo, f"{package} missing from .SRCINFO"


def test_rpm_and_deb_do_not_hard_require_the_clip_stack():
    spec = (ROOT / "arctis-sound-manager.spec").read_text(encoding="utf-8")
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")

    assert not re.search(r"^Requires:\s+python3-gobject", spec, re.MULTILINE)
    assert not re.search(r"^Requires:\s+gstreamer1-plugins-base", spec, re.MULTILINE)

    depends_block = control.split("Recommends:")[0]
    assert "python3-gi" not in depends_block
    assert "gstreamer1.0-plugins-base" not in depends_block
