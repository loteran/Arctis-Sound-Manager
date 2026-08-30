# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the two container defects fixed in this file:

(a) The container check used to live *inside* `if not shutil.which("pkexec")`
    — so a distrobox with polkit pulled in as a dependency (`which pkexec`
    succeeds) skipped the container question entirely and ran pkexec
    directly inside the container, elevating nothing on the host. Every
    button then silently did nothing. The fix asks "are we in a container?"
    BEFORE asking "is pkexec on PATH?".

(b) The dialog named the *container's* distribution ("arch" on a Bazzite
    host, since /etc/os-release inside an Arch distrobox says so), and every
    generated install/copy line matched the wrong package manager. The fix
    uses container.host_distro() for anything user-facing, and routes actual
    execution through container.host_exec() (distrobox-host-exec) so pkexec
    runs — and prompts — on the host.

Neither test group ever calls the real pkexec/distrobox-host-exec binaries or
lets a QProcess actually spawn — QProcess.start() is overridden to a no-op,
same as tests/test_system_deps_dialog_threading.py.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pyside6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QProcess
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton

from arctis_sound_manager.gui import system_deps_dialog as sdd
from arctis_sound_manager.system_deps_checker import CheckResult, DepCheck, Severity, Scope


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _make_failing_check(argv: list[str], name: str = "udev rules", *, scope: Scope = Scope.HOST) -> CheckResult:
    return CheckResult(
        check=DepCheck(
            name=name, severity=Severity.BLOCKING, feature="hotplug",
            detect=lambda: False,
            install_commands={
                "fedora": argv, "debian": argv, "arch": argv, "unknown": argv,
            },
            scope=scope,
        ),
        ok=False,
    )


class _NoSpawnQProcess(QProcess):
    """Records what would have been run and never actually starts anything —
    see the twin class in test_system_deps_dialog_threading.py."""

    last_program: str | None = None
    last_arguments: list[str] | None = None
    start_called: bool = False

    def start(self, *args, **kwargs):  # noqa: D102 - deliberately a no-op
        _NoSpawnQProcess.last_program = self.program()
        _NoSpawnQProcess.last_arguments = list(self.arguments())
        _NoSpawnQProcess.start_called = True


@pytest.fixture(autouse=True)
def _reset_no_spawn():
    _NoSpawnQProcess.last_program = None
    _NoSpawnQProcess.last_arguments = None
    _NoSpawnQProcess.start_called = False
    yield


# ── Defect (a): container question must come before the pkexec question ────

def test_pkexec_present_in_container_does_not_run_it_there(monkeypatch, qt_app):
    """The exact Bazzite scenario: polkit is installed INSIDE the distrobox
    (`which pkexec` succeeds), but pkexec there cannot elevate anything on
    the host. With no way to reach the host either, the fix must run
    NOTHING and say so — not silently execute pkexec in the container as
    the old code did."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: None)  # no distrobox-host-exec
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")

        assert _NoSpawnQProcess.start_called is False, (
            "pkexec must never be spawned inside the container even though "
            "shutil.which('pkexec') succeeds there"
        )
        assert dialog._status_lbl.text(), "a message must explain why nothing ran"
        assert "polkit" not in dialog._status_lbl.text().lower(), (
            "must not fall through to the native 'install polkit' message"
        )
    finally:
        dialog.deleteLater()


def test_host_exec_none_runs_nothing_and_tells_the_user_to_copy(monkeypatch, qt_app):
    """Same outcome via the other precondition named in the task: pkexec is
    simply absent, host_exec() still can't reach the host — nothing runs,
    status points at 'Copy cmd'."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: None)
    monkeypatch.setattr(sdd.shutil, "which", lambda name: None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")

        assert _NoSpawnQProcess.start_called is False
        assert "copy cmd" in dialog._status_lbl.text().lower()
    finally:
        dialog.deleteLater()


def test_host_reachable_routes_pkexec_through_distrobox_host_exec(monkeypatch, qt_app):
    """The happy path this fix is supposed to deliver: when the host IS
    reachable, the install actually runs there instead of just refusing."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: ["distrobox-host-exec"])
    monkeypatch.setattr(sdd.shutil, "which", lambda name: None)  # pkexec absent IN the container
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")

        assert _NoSpawnQProcess.start_called is True
        assert _NoSpawnQProcess.last_program == "distrobox-host-exec"
        assert _NoSpawnQProcess.last_arguments == ["pkexec", *argv]
    finally:
        dialog.deleteLater()


def test_user_run_command_still_works_unelevated_in_container(monkeypatch, qt_app):
    """#175 commands (systemctl --user, asm-cli, …) never touch pkexec or
    the host boundary at all — they must keep running exactly as they do
    natively, even inside a container with no way to reach the host."""
    argv = ["systemctl", "--user", "restart", "arctis-manager"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "install_command_for", lambda check: list(argv))
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: None)  # would refuse if ever consulted
    monkeypatch.setattr(sdd.shutil, "which", lambda name: None)  # would refuse if ever consulted
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    calls = []

    def fake_run(cmd, **kw):
        # A session-wide QApplication is shared with every other test module;
        # processEvents() below can also service leftover widget timers from
        # unrelated tests that happen to call subprocess.run. Keep those
        # harmless instead of letting them pollute `calls` (same guard as
        # test_system_deps_dialog_threading.py's fake_run).
        if cmd == argv:
            calls.append(cmd)
        return None

    monkeypatch.setattr(sdd.subprocess, "run", fake_run)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")
        # Runs on the worker thread — give it a moment.
        assert dialog._active_worker is not None
        dialog._active_worker.wait(2000)
        QApplication.processEvents()

        assert _NoSpawnQProcess.start_called is False, "must never go anywhere near pkexec"
        assert calls == [argv]
    finally:
        dialog.deleteLater()


# ── Defect (b): naming and Copy cmd must target the host, not the container ─

def test_copy_cmd_in_container_carries_host_prefix(monkeypatch, qt_app):
    """HOST-scope checks (like the polkit check used to be for all deps) get
    the distrobox-host-exec prefix so a pasted command runs on the host."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    # A mutable host: the one case where a command is still offered, and so
    # the only one where the copied line has to be right.
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: False)

    dialog = sdd.SystemDepsDialog()
    # Polkit/pkexec is a HOST-scope check — it elevates on the host.
    check = _make_failing_check(argv, scope=Scope.HOST)
    row = sdd._DepRow(check, parent=None, host_distro_id="fedora")
    try:
        row._copy_command(argv, is_host_scope=True)
        line = QGuiApplication.clipboard().text()
        assert line.startswith("distrobox-host-exec sudo "), line
        assert "dnf install -y polkit" in line
    finally:
        row.deleteLater()
        dialog.deleteLater()


def test_copy_cmd_user_run_command_gets_no_host_prefix_even_in_container(monkeypatch, qt_app):
    """asm-cli / systemctl --user / paru / pip --user run in the container's
    own userland — prefixing them with distrobox-host-exec would send them
    to the wrong place."""
    argv = ["systemctl", "--user", "restart", "arctis-manager"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)

    dialog = sdd.SystemDepsDialog()
    row = sdd._DepRow(_make_failing_check(argv), parent=None, host_distro_id="bazzite")
    try:
        row._copy_command(argv)
        line = QGuiApplication.clipboard().text()
        assert line == "systemctl --user restart arctis-manager"
        assert "distrobox-host-exec" not in line
    finally:
        row.deleteLater()
        dialog.deleteLater()


def test_copy_cmd_native_unchanged(monkeypatch, qt_app):
    """Outside a container, Copy cmd must be byte-for-byte what it was
    before this fix."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: False)

    dialog = sdd.SystemDepsDialog()
    row = sdd._DepRow(_make_failing_check(argv), parent=None, host_distro_id=None)
    try:
        row._copy_command(argv)
        line = QGuiApplication.clipboard().text()
        assert line == "sudo dnf install -y polkit"
    finally:
        row.deleteLater()
        dialog.deleteLater()


# ── Immutable host: never fabricate an rpm-ostree line ──────────────────────

def test_row_hides_install_button_on_immutable_host(monkeypatch, qt_app):
    """HOST-scope checks (e.g. udev rules) are blocked on an immutable host:
    offering a fabricated dnf/pacman line would do nothing useful."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: ["distrobox-host-exec"])

    dialog = sdd.SystemDepsDialog()
    # HOST-scope: the immutable guard must fire.
    check = _make_failing_check(argv, name="pkexec (polkit)", scope=Scope.HOST)
    row = sdd._DepRow(check, parent=None, host_distro_id="bazzite")
    try:
        assert row.action_btn is None, (
            "must not offer a button that would run a fabricated rpm-ostree "
            "line on an immutable host"
        )
        # But Copy cmd must be available so the user can paste the host command.
        copy_btns = row.findChildren(QPushButton)
        assert any(b.text() == "Copy cmd" for b in copy_btns), (
            "must always offer Copy cmd for HOST-scope checks on immutable hosts"
        )
    finally:
        row.deleteLater()
        dialog.deleteLater()


def test_install_all_skips_immutable_blocked_checks(monkeypatch, qt_app):
    """_install_all must apply the same immutable-host guard as _DepRow —
    otherwise 'Install all missing' would still batch a pacman/dnf line the
    row itself refused to offer."""
    argv = ["dnf", "install", "-y", "polkit"]
    check = _make_failing_check(argv, name="pkexec (polkit)", scope=Scope.HOST)
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [check])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)
    monkeypatch.setattr(sdd, "_host_exec", lambda: ["distrobox-host-exec"])
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    dialog._host_distro_id = "bazzite"
    try:
        dialog._install_all()
        assert _NoSpawnQProcess.start_called is False
        # The status message should mention the blocked check and the host.
        text = dialog._status_lbl.text().lower()
        assert "bazzite" in text
        assert "polkit" in text
    finally:
        dialog.deleteLater()


# ── NOT in a container: every behaviour strictly unchanged ──────────────────

def test_native_single_command_unaffected(monkeypatch, qt_app):
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: False)
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")
        assert _NoSpawnQProcess.last_program == "pkexec"
        assert _NoSpawnQProcess.last_arguments == argv
    finally:
        dialog.deleteLater()


def test_native_no_pkexec_message_unchanged(monkeypatch, qt_app):
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: False)
    monkeypatch.setattr(sdd.shutil, "which", lambda name: None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="t")
        assert _NoSpawnQProcess.start_called is False
        assert "polkit" in dialog._status_lbl.text().lower()
    finally:
        dialog.deleteLater()


def test_native_sub_header_names_the_only_distro_there_is(monkeypatch, qt_app):
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: False)
    monkeypatch.setattr(sdd, "detect_distro", lambda: "fedora")

    dialog = sdd.SystemDepsDialog()
    try:
        assert dialog._host_distro_id is None
        assert "fedora" in dialog._sysdeps_sub_text()
    finally:
        dialog.deleteLater()


def test_container_sub_header_names_container_and_host(monkeypatch, qt_app):
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_distro", lambda: "bazzite")
    monkeypatch.setattr(sdd, "detect_distro", lambda: "arch")

    dialog = sdd.SystemDepsDialog()
    try:
        assert dialog._host_distro_id == "bazzite"
        text = dialog._sysdeps_sub_text()
        assert "arch" in text and "bazzite" in text
    finally:
        dialog.deleteLater()


def test_container_sub_header_admits_unknown_host(monkeypatch, qt_app):
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_distro", lambda: None)
    monkeypatch.setattr(sdd, "detect_distro", lambda: "arch")

    dialog = sdd.SystemDepsDialog()
    try:
        assert dialog._host_distro_id is None
        text = dialog._sysdeps_sub_text()
        assert "could not be determined" in text.lower() or "unknown" in text.lower()
        # Must not claim "arch" as the distro to install on.
        assert "the detected distribution is arch" not in text.lower()
    finally:
        dialog.deleteLater()


def test_container_scope_dep_keeps_button_on_immutable_host(monkeypatch, qt_app):
    """CONTAINER-scope checks (e.g. pactl) are installed inside the container,
    which is always writable — immutability of the host is irrelevant. The
    Install button must stay and the Copy cmd must NOT carry the
    distrobox-host-exec prefix."""
    argv = ["pacman", "-S", "--noconfirm", "libpulse"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: True)

    dialog = sdd.SystemDepsDialog()
    # pactl is Scope.CONTAINER (the default) — host immutability must not
    # affect it.
    check = _make_failing_check(argv, name="pactl CLI (pulseaudio-utils)", scope=Scope.CONTAINER)
    row = sdd._DepRow(check, parent=None, host_distro_id="bazzite")
    try:
        assert row.action_btn is not None, (
            "CONTAINER-scope dep must keep the Install button even on an "
            "immutable host — the container filesystem is writable"
        )
        # Copy cmd must NOT carry the distrobox-host-exec prefix for
        # CONTAINER-scope checks.
        row._copy_command(argv, is_host_scope=False)
        line = QGuiApplication.clipboard().text()
        assert "distrobox-host-exec" not in line, (
            f"Copy cmd for CONTAINER scope must not carry distrobox-host-exec: {line}"
        )
        assert "pacman -S --noconfirm libpulse" in line
    finally:
        row.deleteLater()
        dialog.deleteLater()


def test_row_keeps_install_button_on_a_mutable_host(monkeypatch, qt_app):
    """The counterpart of the immutable case, and the one that guards against
    over-reach: a container on an ordinary writable host must keep the button
    it has always had. Only the routing changes there, never the offer."""
    argv = ["dnf", "install", "-y", "polkit"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [])
    monkeypatch.setattr(sdd, "_running_in_container", lambda: True)
    monkeypatch.setattr(sdd, "_host_is_immutable", lambda: False)

    dialog = sdd.SystemDepsDialog()
    row = sdd._DepRow(_make_failing_check(argv), parent=None, host_distro_id="fedora")
    try:
        assert row.action_btn is not None
    finally:
        row.deleteLater()
        dialog.deleteLater()
