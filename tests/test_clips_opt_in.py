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

import logging
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


# ── The one way in ────────────────────────────────────────────────────────────
#
# Installing and removing used to live in two places: this row in Settings and
# the Video tab. Two screens for one feature is two things to find and two
# things to keep in step — and they had already drifted, one re-probing after
# an install and the other trusting the exit code. The row is gone; what it
# guaranteed is asked of the tab, which is where the feature explains itself.


@pytest.fixture
def install_page():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui.clips_install_page import ClipsInstallPage

    QApplication.instance() or QApplication([])
    page = ClipsInstallPage()
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


def test_installing_only_ever_runs_its_own_package_commands(monkeypatch):
    """The bug that took a user's sound away.

    The old switch opened SystemDepsDialog whenever anything was missing. That
    dialog lists every failing check in ASM, and one of them — "pipewire-pulse
    running" — is fixed by restarting PipeWire and pipewire-pulse. Pressing its
    Install-all button tore down the audio graph: the headset's card came back
    with its profile off and WirePlumber persisted that, so enabling Clips
    silenced the machine and kept it silenced.

    Clips must only ever run package commands from its own group.
    """
    from arctis_sound_manager.gui import clips_setup

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(False)])

    for argv in clips_setup.install_argvs():
        assert argv[0] in ("pacman", "dnf", "apt-get"), argv
        # An _internal remediation is what could restart a service.
        assert argv[0] != "systemctl", argv


def test_a_package_command_that_fails_leaves_the_feature_off(install_page,
                                                             monkeypatch):
    """A Clips page that is present and does nothing is the outcome shipping
    the feature off is meant to avoid."""
    from arctis_sound_manager.settings import GeneralSettings

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(False)])
    monkeypatch.setattr("arctis_sound_manager.gui.clips_setup.run_batch",
                        lambda argvs, keep_going=False: (False, "no mirrors"))

    install_page._on_install()

    assert GeneralSettings.read_from_file().clips_enabled is False


def test_installing_with_everything_present_asks_for_no_password(install_page,
                                                                 monkeypatch):
    """Nothing to fetch means nothing to elevate — the button says Enable, and
    pressing it must not reach pkexec at all."""
    from arctis_sound_manager.settings import GeneralSettings

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(True)])
    monkeypatch.setattr(
        "arctis_sound_manager.gui.clips_setup.run_batch",
        lambda argvs, keep_going=False: pytest.fail("asked for a password"))

    install_page._refresh()
    install_page._on_install()

    assert GeneralSettings.read_from_file().clips_enabled is True

    settings = GeneralSettings.read_from_file()
    settings.clips_enabled = False
    settings.write_to_file()


def test_the_page_names_every_package_before_anything_is_elevated(install_page,
                                                                  monkeypatch):
    """Turning on a screen recorder should not be how someone finds GStreamer
    on their system. The names and the exact command are on the page, in front
    of the button, rather than behind it."""
    # _blocking_check only carries an "arch" entry, so install_command_for()
    # returns nothing on any other distro and the field comes up empty. Pinning
    # the distro is what makes this assertion mean the same thing everywhere:
    # unpinned, it passed on the two Arch machines it was written on and failed
    # on all seven CI images.
    monkeypatch.setattr(sdc, "detect_distro", lambda: "arch")
    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(False)])

    install_page._refresh()

    rows = [install_page._list_box.itemAt(i).widget().text()
            for i in range(install_page._list_box.count())]
    assert any("gi" in row for row in rows), rows
    assert install_page._manual_field.text() == "sudo pacman -S gi"


def test_a_degraded_dep_does_not_veto_the_feature(install_page, monkeypatch):
    """ffmpeg missing costs thumbnails and export — a real loss, but the
    capture still records, so it is not grounds for refusing to turn it on."""
    from arctis_sound_manager.settings import GeneralSettings

    degraded = sdc.DepCheck(
        name="ffmpeg / ffprobe", severity=sdc.Severity.DEGRADED,
        feature="thumbnails", detect=lambda: False,
        install_commands={"arch": ["pacman", "-S", "ffmpeg"]})
    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [degraded])
    monkeypatch.setattr("arctis_sound_manager.gui.clips_setup.run_batch",
                        lambda argvs, keep_going=False: (True, ""))

    install_page._on_install()

    assert GeneralSettings.read_from_file().clips_enabled is True

    settings = GeneralSettings.read_from_file()
    settings.clips_enabled = False
    settings.write_to_file()


def test_uninstalling_switches_off_even_when_the_packages_stay(monkeypatch):
    """Removal is offered separately and defaults to No, because these packages
    are shared with the rest of the desktop. Declining must still turn the
    feature off — the user asked to be done with Clips, not to keep it."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.settings import GeneralSettings

    QApplication.instance() or QApplication([])
    settings = GeneralSettings.read_from_file()
    settings.clips_enabled = True
    settings.write_to_file()

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec",
                        lambda self: QMessageBox.StandardButton.No)
    monkeypatch.setattr(clips_setup, "run_batch",
                        lambda argvs, keep_going=False:
                        pytest.fail("removed packages the user declined"))

    assert clips_setup.confirm_and_remove(None) is True
    assert GeneralSettings.read_from_file().clips_enabled is False


def test_cancelling_the_uninstall_changes_nothing(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.settings import GeneralSettings

    QApplication.instance() or QApplication([])
    settings = GeneralSettings.read_from_file()
    settings.clips_enabled = True
    settings.write_to_file()

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec",
                        lambda self: QMessageBox.StandardButton.Cancel)

    assert clips_setup.confirm_and_remove(None) is False
    assert GeneralSettings.read_from_file().clips_enabled is True

    settings = GeneralSettings.read_from_file()
    settings.clips_enabled = False
    settings.write_to_file()


def test_removal_commands_never_force():
    """Every clip package is shared with the desktop. When something else needs
    one, the right outcome is the package manager refusing — not ASM
    overriding it."""
    for check in sdc.clip_dep_checks():
        for distro, argv in check.remove_commands.items():
            joined = " ".join(argv)
            for forced in ("--nodeps", "-d", "--force", "--assume-removed"):
                assert forced not in argv, f"{check.name}/{distro}: {joined}"


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

    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui.main_app import PAGE_CLIPS, QMainApp

    # SonarPage's constructor repairs stale filter-chain configs and restarts
    # the service when it changed anything. On the throwaway HOME this suite
    # runs with, sonar-micro-eq.conf is always missing, so building the window
    # would reach for a real `systemctl restart filter-chain` — which the
    # conftest guard rightly refuses. The window is what is under test here.
    app = QApplication.instance() or QApplication([])
    with patch("arctis_sound_manager.service_control.restart", return_value=True):
        main = QMainApp(app, logging.WARNING)
    try:
        assert main.main_window is not None
        assert main._stack.count() == 8
        # The Video tab is never hidden: with Clips off (the default in tests)
        # the tab shows the install screen rather than disappearing, so someone
        # who does not already know about Clips can still find it.
        #
        # isHidden() rather than isVisible(): the window is built but never
        # shown here, and isVisible() is False for every widget under an unshown
        # window — it would read the same whether the entry was hidden on
        # purpose or not, which is the whole question.
        assert main._sidebar_buttons[PAGE_CLIPS].isHidden() is False
        # The link a settings page walks to reach the sidebar.
        assert getattr(main.main_window, "main_app", None) is main
    finally:
        main.main_window.deleteLater()


# ── The Video tab ─────────────────────────────────────────────────────────────


def test_a_present_runtime_does_not_make_the_tab_show_the_recorder(monkeypatch):
    """Installed is not the same as on.

    Deciding the tab's face on the runtime alone reads well until someone
    uninstalls Clips: the packages stay — ffmpeg and the GStreamer sets belong
    to the rest of the desktop, and removal is offered separately and defaults
    to no — so the probe still says yes and the recorder comes straight back.
    Uninstall then looks broken. Both halves have to agree.
    """
    from arctis_sound_manager.gui import clips_setup

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(True)])
    monkeypatch.setattr(clips_setup, "clips_enabled", lambda: False)

    assert clips_setup.runtime_ready() is True
    assert clips_setup.clips_active() is False


def test_the_tab_offers_to_enable_rather_than_install_what_is_already_there(
        monkeypatch):
    """With every package present, the button that says "Install" would be
    lying about what it is about to do — and there is nothing to re-check by
    hand either."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui.clips_install_page import ClipsInstallPage

    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(True)])
    QApplication.instance() or QApplication([])

    page = ClipsInstallPage()
    try:
        assert page._install_btn.text() == "Enable"
        assert page._recheck_btn.isHidden() is True
    finally:
        page.deleteLater()


def test_uninstalling_from_the_tab_switches_off_and_asks_for_the_swap(
        monkeypatch):
    """The recorder's own Uninstall has to do both halves: persist the flag, and
    tell the window, or the user is left looking at a recorder for a feature
    they just turned off."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.gui.clips_page import ClipsPage

    QApplication.instance() or QApplication([])

    written: list[bool] = []
    monkeypatch.setattr(clips_setup, "set_enabled", written.append)
    # Answering the "also remove the packages?" question with No.
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec", lambda self: 0)
    monkeypatch.setattr(clips_setup, "run_batch",
                        lambda argvs: pytest.fail("nothing should be removed"))

    page = ClipsPage()
    swapped: list[bool] = []
    page.clips_disabled.connect(lambda: swapped.append(True))
    try:
        page._on_uninstall()
        assert written == [False]
        assert swapped == [True]
    finally:
        page.deleteLater()


def test_the_switch_turns_clips_off_without_touching_any_package(monkeypatch):
    """The on/off switch is the everyday control, and it must not be the
    uninstaller wearing a different hat.

    Before it existed the only way off the recorder was Uninstall, which opens
    by asking whether ffmpeg should be removed from the machine — so "stop
    recording" could not be said without answering for the rest of the desktop,
    and most people read the feature as having no off switch at all. Off here
    means: the flag is written, the window is asked to swap the tab, and no
    package manager and no dialog is involved.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.gui.clips_page import ClipsPage

    QApplication.instance() or QApplication([])

    written: list[bool] = []
    monkeypatch.setattr(clips_setup, "set_enabled", written.append)
    monkeypatch.setattr(clips_setup, "remove_argvs",
                        lambda: pytest.fail("the switch must not reach removal"))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.exec",
                        lambda self: pytest.fail("the switch must not ask anything"))

    page = ClipsPage()
    swapped: list[bool] = []
    page.clips_disabled.connect(lambda: swapped.append(True))
    try:
        assert page._power_switch.isChecked()
        page._power_switch.setChecked(False)
        assert written == [False]
        assert swapped == [True]
    finally:
        page.deleteLater()


def test_switching_the_page_on_does_not_write_the_flag_again(monkeypatch):
    """The switch is built checked on a page that only exists while Clips is
    on, so the constructor's own `setChecked(True)` is state being restored —
    not a user asking for anything, and not something to persist."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.gui.clips_page import ClipsPage

    QApplication.instance() or QApplication([])

    written: list[bool] = []
    monkeypatch.setattr(clips_setup, "set_enabled", written.append)

    page = ClipsPage()
    try:
        assert written == []
    finally:
        page.deleteLater()


def test_swapping_the_tab_hands_back_what_the_recorder_held(monkeypatch):
    """Switching Clips off has to release the ScreenCast portal session and the
    compositor's global shortcut, not just drop the widget.

    Both live outside this process and outlive a deleted QWidget, so a page
    removed without `shutdown()` leaves a capture session and a keybinding
    registered for a feature that is now off — and the next time Clips is
    switched back on, the new recorder contends with one nobody can see.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

    from arctis_sound_manager.gui import clips_setup
    from arctis_sound_manager.gui.main_app import QMainApp

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(clips_setup, "clips_active", lambda: False)

    released: list[bool] = []

    class _Recorder(QWidget):
        def shutdown(self):
            released.append(True)

    class _Window:
        _sync_clips_page = QMainApp._sync_clips_page
        _build_clips_page = QMainApp._build_clips_page
        logger = logging.getLogger("test")

        def _switch_page(self, index):
            pass

    window = _Window()
    window._stack = QStackedWidget()
    window._clips_page = _Recorder()
    window._stack.addWidget(window._clips_page)

    window._sync_clips_page()

    assert released == [True]
    assert window._clips_page is not None
    assert not isinstance(window._clips_page, _Recorder)


def test_one_package_the_machine_still_needs_does_not_abandon_the_rest(
        monkeypatch):
    """Removal runs commands the package manager is *expected* to refuse.

    Every clip package is shared: ffmpeg is required by firefox, mpv and vlc,
    python-gobject by whatever else on the machine imports gi. A refusal is the
    outcome we want. Chained with `&&` the first one abandoned the rest, so on
    any desktop that plays video, pressing "yes, remove them" removed nothing at
    all — the first command took the batch down before the one package that
    *could* go was ever reached.
    """
    from arctis_sound_manager.gui import clips_setup

    seen: list[str] = []

    class _Proc:
        returncode = 0
        stdout = stderr = ""

    def _fake_run(argv, **kwargs):
        seen.append(argv[-1])
        return _Proc()

    monkeypatch.setattr(clips_setup.shutil, "which", lambda _: "/usr/bin/pkexec")
    monkeypatch.setattr(clips_setup.subprocess, "run", _fake_run)

    clips_setup.run_batch([["pacman", "-Rs", "a"], ["pacman", "-Rs", "b"]],
                          keep_going=True)

    assert "&&" not in seen[0]
    assert seen[0].count(";") == 1


def test_the_removal_preview_reads_both_streams_and_skips_what_is_not_there(
        monkeypatch):
    """pacman splits its answer across two streams: the summary ("failed to
    prepare transaction") goes to stderr and the lines that actually name what
    holds the package go to stdout. Reading either alone loses half of it — the
    first cut of this reported every package as blocked by nothing at all.

    And a package that is not installed is not a package that cannot be removed.
    """
    from arctis_sound_manager.gui import clips_setup

    class _Proc:
        def __init__(self, rc, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    answers = {
        "ffmpeg": _Proc(
            1,
            ":: removing ffmpeg breaks dependency 'libavcodec.so=62-64' "
            "required by chromaprint\n"
            ":: removing ffmpeg breaks dependency 'libavutil.so=60-64' "
            "required by mpv\n",
            "error: failed to prepare transaction (could not satisfy dependencies)\n"),
        "gst-plugins-good": _Proc(0, "gst-plugins-good-1.28.5-4\nwavpack-5.9.0-1.1\n", ""),
        "gone": _Proc(1, "", "error: target not found: gone\n"),
    }

    # Pinned rather than inherited: the preview asks the running machine which
    # package manager it has, and this test asserts on pacman's wording. Left
    # unpinned it would pass here and fail on a dnf or apt runner.
    monkeypatch.setattr(sdc, "detect_distro", lambda: "arch")
    monkeypatch.setattr(clips_setup.subprocess, "run",
                        lambda argv, **kw: answers[argv[-1]])

    removable, blocked = clips_setup.removal_preview(
        ["ffmpeg", "gst-plugins-good", "gone"])

    assert removable == ["gst-plugins-good"]
    assert blocked == {"ffmpeg": ["chromaprint", "mpv"]}
    assert "gone" not in blocked, "a package that is not installed cannot be held"


def test_a_partial_upgrade_is_told_apart_from_an_ordinary_install_failure():
    """The failure a user on an Arch derivative actually hits.

    `gst-plugin-pipewire` in the Arch repos depends on an exact pipewire
    release, and a derivative that rebuilds pipewire under its own pkgrel ships
    a version that does not match it. Installing then asks to downgrade the
    whole audio stack, and pacman refuses. Nothing on the install screen can fix
    that, and pacman's own wording does not lead anyone to the fix — so it must
    not be reported as just another package-manager error.
    """
    from arctis_sound_manager.gui import clips_setup

    pacman = (
        "resolving dependencies...\n"
        "error: failed to prepare transaction (could not satisfy dependencies)\n"
        ":: installing pipewire (1:1.6.8-1) breaks dependency "
        "'pipewire=1:1.6.8-1.2' required by pipewire-pulse")

    assert clips_setup.looks_like_dependency_conflict(pacman) is True
    assert clips_setup.last_line(pacman).startswith(":: installing pipewire")

    # And the screen has to be able to name the fix. Upgrading the whole machine
    # is not something the Install button does on the user's behalf — it is far
    # more than turning on a screen recorder asked for — so the command is
    # offered, not run.
    upgrade = clips_setup.system_upgrade_command()
    assert upgrade is None or upgrade.startswith("sudo ")

    # A mirror that timed out is an ordinary failure, and must stay one.
    assert clips_setup.looks_like_dependency_conflict(
        "error: failed retrieving file from mirror : Connection timed out") is False


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


def test_a_pipx_install_is_told_it_cannot_get_there(monkeypatch, tmp_path):
    """The loop that has no exit.

    PyGObject is a system package, not a wheel that carries GObject with it, so
    a venv made without --system-site-packages can never import it. The screen
    asked for python-gobject, the user installed it, the package manager
    reported success, the re-probe failed again, and the screen said "still
    missing some components" — for as long as anyone kept trying.

    `_can_import` uses find_spec, which cannot see the system module from
    inside the venv, so no amount of installing changes the answer. The only
    honest thing the screen can do is say so before the password prompt.
    """
    import sys

    from arctis_sound_manager.gui import clips_setup

    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = false\nversion = 3.14\n")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(sdc, "clip_dep_checks", lambda: [_blocking_check(False)])

    assert clips_setup.isolated_venv() is True
    assert clips_setup.bindings_unreachable() is True

    # The same venv with the system's packages visible is a normal install.
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = true\nversion = 3.14\n")
    assert clips_setup.isolated_venv() is False
    assert clips_setup.bindings_unreachable() is False


def test_a_distro_install_is_never_mistaken_for_a_venv(monkeypatch):
    """The message must not appear on the install everyone actually has."""
    import sys

    from arctis_sound_manager.gui import clips_setup

    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    assert clips_setup.isolated_venv() is False
