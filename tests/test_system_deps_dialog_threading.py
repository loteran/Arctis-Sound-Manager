# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for two findings against gui/system_deps_dialog.py:

* ENV-2 (issue #200) — clicking "Install" ran the un-elevated commands
  (asm-setup / asm-cli / systemctl --user / paru / pip --user) via a
  blocking ``subprocess.run`` directly on the Qt GUI thread. At least one of
  those, ``asm-cli udev write-rules``, escalates *itself* internally via
  ``sudo_it()`` with no timeout of its own — so the window froze for as long
  as a nested pkexec/kdesu prompt the user may never even see was left
  unanswered. The fix moves that work onto ``_UserCmdsWorker``, a QThread,
  and always re-enables the dialog's controls once it reports done — success,
  exception, timeout or not.

* EXT-3 — ``_run_with_pkexec`` used to build a `pkexec sh -c "<chained
  cmds>"` line via a hand-rolled quoting helper that only wrapped an argument
  in single quotes if it contained a literal space, and never escaped
  embedded quotes. The fix drops the shell entirely for the common
  single-command case (argv goes straight to pkexec), and uses real
  ``shlex``-based quoting for the multi-command batch case where a shell is
  still needed to chain commands behind one password prompt.

Neither test group ever calls the real ``pkexec`` binary or lets a QProcess
actually spawn — QProcess.start() is overridden to a no-op wherever a pkexec
invocation would otherwise happen, and system_deps_checker's real system
probing (``run_all_checks``) is monkeypatched out.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# Importing the dialog module pulls PySide6 — skip the whole file when the
# test environment doesn't have it (CI containers without GUI deps).
pyside6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui import system_deps_dialog as sdd
from arctis_sound_manager.system_deps_checker import CheckResult, DepCheck, Severity


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _pump_until(condition, timeout: float = 5.0) -> bool:
    """Drain the Qt event loop until *condition* is true or *timeout*
    elapses. Cross-thread signals (QThread.done/finished) are delivered as
    queued events on the GUI thread's event loop — this is what actually
    picks them up in a test that has no running app.exec()."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _make_failing_check(argv: list[str]) -> CheckResult:
    """A single BLOCKING check whose install command is *argv* on every
    distro — enough to make _install_all_btn start enabled, mirroring
    test_system_deps_dialog.py's _make_check."""
    return CheckResult(
        check=DepCheck(
            name="udev rules", severity=Severity.BLOCKING, feature="hotplug",
            detect=lambda: False,
            install_commands={
                "fedora": argv, "debian": argv, "arch": argv, "unknown": argv,
            },
        ),
        ok=False,
    )


class _NoSpawnQProcess(QProcess):
    """Stand-in for QProcess that records what pkexec *would* have been
    told to run, and never actually starts a process — so these tests can
    never pop a real elevation prompt on the developer's own desktop."""

    last_program: str | None = None
    last_arguments: list[str] | None = None

    def start(self, *args, **kwargs):  # noqa: D102 - deliberately a no-op
        _NoSpawnQProcess.last_program = self.program()
        _NoSpawnQProcess.last_arguments = list(self.arguments())


# ── EXT-3: quoting / shell-removal ──────────────────────────────────────────

AWKWARD_PACKAGE_ARGS = [
    "pkg'name",                    # embedded single quote
    "pkg$(touch /tmp/pwned)",      # command substitution
    "pkg;rm -rf ~",                # semicolon
    "-rf",                         # leading dash — looks like a flag
]


@pytest.mark.parametrize("awkward", AWKWARD_PACKAGE_ARGS)
def test_single_elevated_command_bypasses_shell_entirely(monkeypatch, qt_app, awkward):
    """The common case (one row's 'Install' button): argv goes straight to
    pkexec with no shell at all, so there is nothing for an awkward package
    name to break out of."""
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(["true"])])
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        argv = ["dnf", "install", "-y", awkward]
        dialog._run_with_pkexec([argv], context="t")

        assert _NoSpawnQProcess.last_program == "pkexec"
        # Passed through byte-for-byte as a single argv element — no "sh",
        # no "-c", nothing re-interpreted.
        assert _NoSpawnQProcess.last_arguments == argv
        assert "sh" not in _NoSpawnQProcess.last_arguments
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("awkward", AWKWARD_PACKAGE_ARGS)
def test_multi_elevated_commands_quote_safely_for_the_shell(monkeypatch, qt_app, awkward):
    """_install_all groups several elevated commands behind ONE pkexec
    prompt, which still needs a shell to chain them with &&. Round-tripping
    the generated line back through shlex.split must reproduce the exact
    original argv lists — proving the awkward token never becomes a second
    shell word, an operator, or breaks out of its argument."""
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(["true"])])
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        cmd1 = ["dnf", "install", "-y", awkward]
        cmd2 = ["apt-get", "install", "-y", "harmless-pkg"]
        dialog._run_with_pkexec([cmd1, cmd2], context="t")

        assert _NoSpawnQProcess.last_program == "pkexec"
        args = _NoSpawnQProcess.last_arguments
        assert args[:2] == ["sh", "-c"]
        chained = args[2]

        # The old bug: only quoted args containing a literal space, and never
        # escaped embedded quotes — shlex.split would NOT reconstruct the
        # original argv for any of these awkward inputs under that scheme.
        assert shlex.split(chained) == cmd1 + ["&&"] + cmd2
    finally:
        dialog.deleteLater()


def test_from_source_build_script_survives_as_one_argument(monkeypatch, qt_app):
    """A concrete instance of the embedded-quote case this file's own
    _copy_command comment warns about: a `bash -c "<script>"` entry (e.g.
    the from-source RNNoise build) chained alongside another command must
    reach the shell as exactly one argument to `bash -c`, quotes and all."""
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(["true"])])
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)
    monkeypatch.setattr(sdd, "QProcess", _NoSpawnQProcess)

    dialog = sdd.SystemDepsDialog()
    try:
        script = "set -e; echo 'building'; touch \"/tmp/it's done\""
        cmd1 = ["bash", "-c", script]
        cmd2 = ["pacman", "-S", "--noconfirm", "cmake"]
        dialog._run_with_pkexec([cmd1, cmd2], context="t")

        chained = _NoSpawnQProcess.last_arguments[2]
        assert shlex.split(chained) == cmd1 + ["&&"] + cmd2
    finally:
        dialog.deleteLater()


# ── ENV-2: off the GUI thread, and always recovers ─────────────────────────

def test_asm_cli_install_does_not_block_the_gui_thread(monkeypatch, qt_app):
    """The exact chain from issue #200: asm-cli is classified as
    'must run un-elevated' (_is_user_run), so it never reaches the pkexec
    QProcess at all — it used to run via a blocking subprocess.run() right
    here on the GUI thread. Simulate the nested elevation prompt hanging and
    prove the call returns immediately regardless."""
    argv = ["asm-cli", "udev", "write-rules", "--force", "--reload"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    # _install_all_btn's initial enabled state depends on install_command_for
    # finding a match for the (unpredictable, real) test-machine distro —
    # pin it directly so the baseline assertion below is deterministic.
    monkeypatch.setattr(sdd, "install_command_for", lambda check: list(argv))
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)

    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run(cmd, **kwargs):
        if cmd != argv:
            # _pump_until() below drives QApplication.processEvents(), which
            # also services timers belonging to leftover widgets from other
            # test modules sharing this session-wide QApplication — some of
            # those call subprocess.run too. Keep them harmless instead of
            # letting them block on `release` or pollute `calls`.
            return subprocess.CompletedProcess(cmd, 0)
        calls.append(cmd)
        started.set()
        # Stand-in for the nested pkexec/kdesu prompt from #200: something
        # the user may not even see, sitting there unanswered.
        release.wait(timeout=2.0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sdd.subprocess, "run", fake_run)

    dialog = sdd.SystemDepsDialog()
    try:
        assert dialog._install_all_btn.isEnabled() is True  # baseline: one BLOCKING check, has an install path

        t0 = time.monotonic()
        dialog._run_with_pkexec([argv], context="udev rules")
        elapsed = time.monotonic() - t0

        # Must return well before the simulated nested prompt resolves.
        assert elapsed < 0.5, f"_run_with_pkexec blocked the caller for {elapsed:.2f}s"
        assert dialog._install_all_btn.isEnabled() is False, "controls should be disabled while busy"

        assert started.wait(timeout=2.0), "the worker thread never even started the command"
        release.set()  # let the "elevation prompt" resolve

        recovered = _pump_until(lambda: dialog._install_all_btn.isEnabled(), timeout=5.0)
        assert recovered, "dialog stayed frozen/disabled after the worker finished"
        assert calls == [argv]
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(cmd=["asm-cli"], timeout=120),  # outer timeout fires
    PermissionError("polkit dialog dismissed"),                # elevation cancelled
    OSError("pkexec: authentication failed"),                  # elevation failed
])
def test_install_recovers_when_nested_elevation_fails_or_is_cancelled(monkeypatch, qt_app, failure):
    """Whatever went wrong with the nested prompt — timed out, cancelled,
    outright failed — the dialog must not stay stuck disabled forever."""
    argv = ["asm-cli", "udev", "write-rules", "--force", "--reload"]
    monkeypatch.setattr(sdd, "run_all_checks", lambda: [_make_failing_check(argv)])
    monkeypatch.setattr(sdd.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)

    def fake_run(cmd, **kwargs):
        if cmd != argv:
            # See the comment in the twin test above: keep unrelated stray
            # calls (from other tests' leftover widget timers, serviced by
            # _pump_until's processEvents()) harmless.
            return subprocess.CompletedProcess(cmd, 0)
        raise failure

    monkeypatch.setattr(sdd.subprocess, "run", fake_run)

    dialog = sdd.SystemDepsDialog()
    try:
        dialog._run_with_pkexec([argv], context="udev rules")
        assert dialog._install_all_btn.isEnabled() is False

        recovered = _pump_until(lambda: dialog._install_all_btn.isEnabled(), timeout=5.0)
        assert recovered, "a failed/cancelled nested elevation left the dialog frozen"
    finally:
        dialog.deleteLater()


# ── _UserCmdsWorker in isolation ────────────────────────────────────────────

def test_user_cmds_worker_runs_each_command_and_emits_done(monkeypatch):
    calls = []
    monkeypatch.setattr(sdd.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)))

    worker = sdd._UserCmdsWorker([["asm-setup"], ["systemctl", "--user", "restart", "x"]])
    results = []
    worker.done.connect(lambda: results.append(True))
    worker.run()  # exercised synchronously, like sonar_page's _ApplyWorker tests

    assert results == [True]
    assert [c[0] for c in calls] == [["asm-setup"], ["systemctl", "--user", "restart", "x"]]
    assert calls[0][1] == {"check": False, "timeout": 120}


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(cmd=["x"], timeout=120),
    FileNotFoundError("no such file"),
    PermissionError("polkit dialog dismissed"),
    RuntimeError("something unexpected"),
])
def test_user_cmds_worker_always_emits_done_even_on_failure(monkeypatch, failure):
    """A raising command must not stop the remaining commands from running,
    and must never prevent `done` from firing — that's what lets the dialog
    recover instead of staying disabled forever."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        if argv == ["boom"]:
            raise failure
        return None

    monkeypatch.setattr(sdd.subprocess, "run", fake_run)

    worker = sdd._UserCmdsWorker([["boom"], ["still-runs"]])
    results = []
    worker.done.connect(lambda: results.append(True))
    worker.run()

    assert results == [True], "done must fire even when a command raises"
    assert calls == [["boom"], ["still-runs"]], "one failure must not skip the rest"
