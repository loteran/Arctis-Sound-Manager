# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""clips_setup.py — the one place that installs, removes and reports Clips.

Three screens ask the same questions: the Settings row, the Video tab's install
page, and the recorder's own Uninstall. Before this module each carried its own
copy of the ``pkexec`` batch, and the copies had already drifted — one of them
re-probed after installing and one trusted the exit code.

Most of it draws nothing — callers own their status text, so the same answers
render as a settings row or as a full page. The exception is
:func:`confirm_and_remove`, which owns the removal confirmation itself: what it
has to say (these packages are shared, here is the exact command, here is what
was kept and why) is the same wherever it is asked from, and three copies of
that conversation is how the wording drifts apart.

Deliberately free of any ``gi`` / GStreamer import: this runs on machines where
the whole point is that none of that is installed yet.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger("ClipsSetup")

# Long enough for a slow mirror to deliver the GStreamer sets; short enough that
# a package manager waiting on a lock it will never get does not hang the GUI
# for the rest of the session.
_BATCH_TIMEOUT_S = 900


# ── what the machine has ──────────────────────────────────────────────────────

def probe() -> list[tuple]:
    """Every Clips dependency check with whether it is present: one pass.

    Each detect() is a subprocess (gst-inspect for the GStreamer elements), so
    a screen that needs several answers should take them all from one probe
    rather than ask missing_checks(), present_names() and so on in turn.

    A probe that raises counts as missing: the question being answered is "can
    this record?", and a check that cannot answer has not said yes.
    """
    from arctis_sound_manager.system_deps_checker import clip_dep_checks

    results = []
    for check in clip_dep_checks():
        try:
            ok = bool(check.detect())
        except Exception:  # noqa: BLE001
            ok = False
        results.append((check, ok))
    return results


def missing_checks() -> list:
    """Every Clips dependency this machine does not have."""
    return [check for check, ok in probe() if not ok]


def present_names() -> set[str]:
    """The names of the dependency groups this machine currently has.

    Compared before and after a removal, this is how the outcome is worked out
    without asking each distro's package manager what it did: a group that has
    stopped being detected is a group that went. Cheaper than parsing three
    package managers' output, and it reports what is *true* rather than what a
    command claimed.
    """
    return {check.name for check, ok in probe() if ok}


def blocking_missing() -> list:
    """The missing checks that stop it recording at all — the ones that decide
    whether the feature works, as opposed to the ones that cost a thumbnail."""
    from arctis_sound_manager.system_deps_checker import Severity

    return [c for c in missing_checks() if c.severity is Severity.BLOCKING]


def runtime_ready() -> bool:
    """True when every blocking package Clips needs is present."""
    try:
        return not blocking_missing()
    except Exception as exc:  # noqa: BLE001 — never let a probe hide the feature
        logger.debug("runtime probe failed, assuming not ready: %s", exc)
        return False


def isolated_venv() -> bool:
    """True when ASM is running from a virtualenv that cannot see the system's
    Python packages — which is what `pipx install` produces.

    This one state makes the whole install screen a loop rather than a path.
    PyGObject is not on PyPI as a wheel that carries GObject with it; it is a
    system package, and a venv created without ``--system-site-packages`` cannot
    import it however many times it is installed. So the screen asks for
    python-gobject, the user installs it, the package manager reports success,
    the re-probe still fails, and the screen says "still missing some
    components" — forever, with nothing about it hinting at the real problem.

    Read from ``pyvenv.cfg`` rather than by probing ``sys.path``: the flag is
    what actually decides whether the interpreter was given the system's
    site-packages, and reading it says so plainly. A venv whose config cannot be
    read is treated as isolated, which is the safer answer — the message it
    produces is an explanation, not a refusal to continue.
    """
    import sys
    from pathlib import Path

    if sys.prefix == sys.base_prefix:
        return False

    try:
        for line in (Path(sys.prefix) / "pyvenv.cfg").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "include-system-site-packages":
                return value.strip().lower() != "true"
    except OSError as exc:
        logger.debug("could not read pyvenv.cfg: %s", exc)
    return True


def bindings_unreachable(missing: list | None = None) -> bool:
    """Whether PyGObject is missing *and* no package manager can fix it here.

    *missing* is a caller's own probe result, to avoid probing again."""
    if not isolated_venv():
        return False
    if missing is None:
        missing = missing_checks()
    return any(c.name.startswith("PyGObject") for c in missing)


def clips_enabled() -> bool:
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().clips_enabled)
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read clips_enabled, reading as off: %s", exc)
        return False


def clips_active() -> bool:
    """Whether the Video tab should show the recorder rather than the install
    screen.

    Both halves are required, and each catches a state the other misses. The
    runtime alone would keep showing the recorder to someone who has just
    uninstalled Clips but kept ffmpeg — which the rest of their desktop needs —
    making Uninstall look broken. The flag alone would show the recorder on a
    machine whose packages were removed from underneath it.
    """
    return clips_enabled() and runtime_ready()


def set_enabled(value: bool) -> None:
    """Persist the opt-in flag. Failing to write it is worth a log and nothing
    more: the caller has already done the part the user asked for."""
    try:
        from arctis_sound_manager.settings import GeneralSettings
        settings = GeneralSettings.read_from_file()
        settings.clips_enabled = bool(value)
        settings.write_to_file()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist clips_enabled=%s: %s", value, exc)


# ── the commands ──────────────────────────────────────────────────────────────

def install_argvs(checks: list | None = None) -> list[list[str]]:
    """Package-manager argv for the given checks (missing ones by default)."""
    from arctis_sound_manager.system_deps_checker import install_command_for

    if checks is None:
        checks = missing_checks()
    return [cmd for cmd in (install_command_for(c) for c in checks) if cmd]


def remove_argvs() -> tuple[list[list[str]], list[str]]:
    """Removal argv for the Clips group, and the package names they name.

    The names are returned so a caller can say out loud what is about to be
    removed — these packages are shared with the rest of the desktop, and the
    user is the only one who knows whether anything else here needs ffmpeg.
    """
    from arctis_sound_manager.system_deps_checker import (clip_dep_checks,
                                                          remove_command_for)

    argvs: list[list[str]] = []
    packages: list[str] = []
    for check in clip_dep_checks():
        cmd = remove_command_for(check)
        if not cmd:
            continue
        argvs.append(cmd)
        packages.extend(a for a in cmd[3:] if not a.startswith("-"))
    return argvs, sorted(set(packages))


def removal_preview(packages: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Ask the package manager what removing each package would actually do,
    without doing it: ``(removable, {package: what still needs it})``.

    Both halves of the answer matter and neither is guessable from the list of
    names. On a normal desktop most of these cannot go — ffmpeg is required by
    firefox, mpv and vlc, libcanberra by kwin and plasma-workspace — and a user
    looking at a list of packages with a Remove button has no way to know that
    until they press it and nothing happens. Naming what holds each one turns a
    silent no-op into an answer.

    Unprivileged: `pacman -Rsp` and `apt-get -s remove` both simulate. Anything
    else returns empty lists, and the caller shows the plain package list it
    always did — a preview is worth having, not worth blocking on.
    """
    from arctis_sound_manager.system_deps_checker import (_package_manager_for,
                                                          detect_distro)

    manager = _package_manager_for(detect_distro())
    removable: list[str] = []
    blocked: dict[str, list[str]] = {}

    for package in packages:
        if manager == "pacman":
            argv = ["pacman", "-Rsp", package]
        elif manager == "apt":
            argv = ["apt-get", "-s", "remove", package]
        else:
            return [], {}

        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("could not preview removing %s: %s", package, exc)
            continue

        if proc.returncode == 0:
            removable.append(package)
            continue

        # Both streams, not one: pacman puts the summary ("failed to prepare
        # transaction") on stderr and the lines that actually name the reason on
        # stdout, so reading either alone loses half the answer.
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")

        # Not installed is not "cannot be removed" — there is nothing to remove,
        # and listing it as held by something would be an invention.
        if "target not found" in output or "unable to locate" in output.lower():
            continue

        # "removing ffmpeg breaks dependency 'libavcodec.so=62-64' required by
        # chromaprint" — the name after "required by" is what is holding it.
        holders: list[str] = []
        for line in output.splitlines():
            _, sep, tail = line.partition("required by")
            if sep:
                holder = tail.strip()
                if holder and holder not in holders:
                    holders.append(holder)
        blocked[package] = holders

    return removable, blocked


def packages_in(argv: list[str]) -> list[str]:
    """The package names in a package-manager argv, dropping the manager, the
    subcommand and any flags: ``apt-get install -y A B`` → ``[A, B]``."""
    return [tok for tok in argv[2:] if not tok.startswith("-")]


_UPGRADE_COMMANDS = {
    "pacman": "sudo pacman -Syu",
    "apt": "sudo apt update && sudo apt full-upgrade",
    "dnf": "sudo dnf upgrade",
}


def system_upgrade_command() -> str | None:
    """The command that brings this machine up to date, or None if unknown.

    Offered when an install fails on a dependency conflict, and only then. It is
    the real fix — on Arch a package is built against the exact versions in the
    repository at that moment, down to soname pins like
    `libpipewire-0.3.so=0-64`, so there is no such thing as upgrading one
    package on its own; the repository's `gst-plugin-pipewire` wants
    `pipewire=1:1.6.8-1` and nothing else will do.

    Not what the Install button runs. Upgrading someone's entire machine behind
    one password prompt is far more than they asked for when they turned on a
    screen recorder, and it is a decision only they can make — so ASM names the
    command and leaves the choice with them.
    """
    from arctis_sound_manager.system_deps_checker import (_package_manager_for,
                                                          detect_distro)
    return _UPGRADE_COMMANDS.get(_package_manager_for(detect_distro()) or "")


def manual_command(argvs: list[list[str]]) -> str | None:
    """One copy-pasteable command for a whole group of argv, or None when there
    is nothing to run.

    Shown before anything is elevated, for installing and for removing alike.
    Someone about to hand a GUI their root password is entitled to see the
    command it is going to run — and on the removal side that is not a courtesy:
    these are packages the rest of the desktop shares, and the exact line is
    what lets a user check it against their own machine before agreeing, or run
    it themselves with the flags they would rather use.

    The confirmation flags are dropped along with the rest: a command a person
    is going to read and run should stop and ask them, which is exactly what
    `--noconfirm` exists to prevent.
    """
    if not argvs:
        return None
    base = argvs[0][:2]  # e.g. ["pacman", "-S"] / ["apt-get", "install"]
    pkgs: list[str] = []
    for argv in argvs:
        for p in packages_in(argv):
            if p not in pkgs:
                pkgs.append(p)
    if not pkgs:
        return None
    return "sudo " + " ".join(base + pkgs)


# ── running them ──────────────────────────────────────────────────────────────

class NoPkexec(RuntimeError):
    """polkit is not installed, so nothing can be elevated from here."""


# What each package manager says when the install cannot proceed because the
# machine's packages and its repositories disagree — the state Arch calls a
# partial upgrade, and the one a user on a derivative distro is most likely to
# hit here: the repo's gst-plugin-pipewire wants an exact pipewire release, and
# the installed one is the derivative's rebuild of the same upstream version.
_CONFLICT_MARKERS = (
    "could not satisfy dependencies",   # pacman
    "breaks dependency",                # pacman
    "unmet dependencies",               # apt
    "held broken packages",             # apt
    "nothing provides",                 # dnf
    "conflicting requests",             # dnf
)


def looks_like_dependency_conflict(output: str) -> bool:
    """Whether a failed install failed because the machine is mid-upgrade.

    Worth telling apart from every other install failure: nothing the user can
    do on this screen will fix it, and the fix — update the whole system first —
    is not one they would guess from "installing pipewire breaks dependency
    'pipewire=1:1.6.8-1.2' required by pipewire-pulse".
    """
    low = output.lower()
    return any(marker in low for marker in _CONFLICT_MARKERS)


def last_line(output: str) -> str:
    """The most specific line of a package manager's complaint."""
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_batch(argvs: list[list[str]], keep_going: bool = False) -> tuple[bool, str]:
    """Run package commands as one elevated batch; return (ok, detail).

    One ``pkexec`` for the whole batch so the password is asked once rather than
    once per package group. Synchronous because the button has nothing useful to
    offer while it waits, and the result decides whether the feature is on.

    *keep_going* is what removal needs and installing does not. Removing runs
    commands the package manager is *expected* to refuse — every one of these
    packages is shared, and a refusal means something else on the machine still
    needs it, which is the outcome we want rather than an error. Chained with
    ``&&`` the first such refusal abandoned the rest, so on a desktop where
    anything at all depends on ffmpeg, nothing was ever removed: the first
    command in the list took the whole batch down with it. Independent commands
    let each package be judged on its own; the caller re-probes to find out what
    actually went.

    Only ever hand this argv built from the Clips dependency group. It used to
    be reachable with an ``_internal`` remediation, which is how turning on a
    screen recorder ended up able to restart the audio stack.
    """
    if not argvs:
        return True, ""
    from arctis_sound_manager.container import running_in_container

    # Every Clips package lives in the container, next to the ASM that uses
    # it — so inside one, elevate there with the passwordless sudo distrobox
    # sets up, as the system-deps dialog already does. pkexec in a container
    # has no polkit agent to ask anyone and answers "Not authorized. This
    # incident has been reported." without a prompt (#329). -n fails at once
    # on any other setup rather than waiting on a password nobody is asked for.
    if running_in_container():
        elevate = ["sudo", "-n"]
    elif shutil.which("pkexec"):
        elevate = ["pkexec"]
    else:
        raise NoPkexec

    def _quote(args: list[str]) -> str:
        return " ".join(f"'{a}'" if " " in a else a for a in args)

    joiner = "; " if keep_going else " && "
    try:
        proc = subprocess.run(
            [*elevate, "sh", "-c", joiner.join(_quote(a) for a in argvs)],
            capture_output=True, text=True, timeout=_BATCH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("clip package command failed: %s", exc)
        return False, str(exc)

    # With keep_going the exit status is the last command's and says nothing
    # about the others, so it is not an answer worth reporting — except for the
    # one failure that matters here, an authentication the user dismissed.
    if keep_going:
        if proc.returncode == 126:  # pkexec: not authorised / dialog dismissed
            return False, (proc.stderr or "").strip()
        return True, ""

    if proc.returncode != 0:
        # The whole complaint, not its last line: the line that names the cause
        # is rarely the last one a package manager prints, and the caller has to
        # be able to tell a dependency conflict from a mirror that timed out.
        return False, (proc.stderr or proc.stdout or "").strip()
    return True, ""


# ── the removal conversation ──────────────────────────────────────────────────

def _tr(key: str, fallback: str) -> str:
    from arctis_sound_manager.i18n import I18n
    try:
        value = I18n.translate("ui", key)
    except Exception:  # noqa: BLE001
        return fallback
    return fallback if not value or value == key else value


def confirm_and_remove(parent) -> bool:
    """Ask whether to remove the Clips packages, do it, and say what happened.

    Returns False only when the user called the whole thing off; answering "no,
    keep the packages" is a yes to being done with Clips, which is the question
    the caller asked.

    Removing is asked separately from switching off, and defaults to no: every
    one of these packages is shared with the rest of the desktop — video players
    and browsers use ffmpeg and GStreamer too — so "I am done with clips" is not
    the same statement as "nothing else here needs ffmpeg". The exact command is
    shown before anything is elevated, and the removal never forces, so a
    package something else depends on makes the package manager refuse.

    Saved clips are never touched. This removes software, not recordings.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    argvs, packages = remove_argvs()

    answer = QMessageBox.StandardButton.No
    if argvs:
        removable, blocked = removal_preview(packages)

        # A command naming a package that is not installed is one pacman refuses
        # outright — "target not found", nothing removed — and this is a command
        # written to be copied. So it names only what is actually here, and
        # falls back to the full list when there was no preview to narrow it.
        present = [p for p in packages if p in removable or p in blocked]
        command = manual_command([argvs[0][:2] + present] if present else argvs)

        detail = _tr("clips_remove_packages_hint", "Affected: {0}").format(
            ", ".join(packages))
        if removable or blocked:
            # Replace the flat list with what the package manager says will
            # actually happen. It is usually "almost nothing", and that is the
            # part worth knowing before agreeing.
            detail = ""
            if removable:
                detail += _tr("clips_remove_will_go",
                              "These would be removed: {0}").format(
                    ", ".join(removable))
            if blocked:
                held = "\n".join(
                    f"  • {pkg} — {', '.join(holders)}" if holders else f"  • {pkg}"
                    for pkg, holders in blocked.items())
                detail += ("\n\n" if detail else "") + _tr(
                    "clips_remove_held",
                    "These would stay, because other software still needs "
                    "them:") + "\n" + held
        box = QMessageBox(parent)
        box.setWindowTitle(_tr("clips_uninstall", "Uninstall"))
        box.setText(_tr("clips_remove_packages_q",
                        "Also remove the packages Clips installed?"))
        box.setInformativeText(
            detail
            + (("\n\n" + _tr("clips_remove_command", "The command this runs:")
                + "\n" + command) if command else ""))
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Cancel:
            return False

    set_enabled(False)

    if answer != QMessageBox.StandardButton.Yes:
        return True

    before = present_names()
    QApplication.processEvents()
    try:
        ok, detail = run_batch(argvs, keep_going=True)
    except NoPkexec:
        ok, detail = False, _tr(
            "clips_no_pkexec",
            "pkexec not found — install polkit, or remove the packages "
            "yourself.")
    if not ok:
        logger.info("clip package removal did not run: %s", detail)
        return True

    # Said out loud rather than left to be discovered: "kept, because something
    # else needs it" is a different outcome from "removal failed", and the user
    # should not have to open a terminal to tell them apart.
    after = present_names()
    gone = sorted(before - after)
    kept = sorted(after & before)
    lines = []
    if gone:
        lines.append(_tr("clips_removed", "Removed: {0}").format(", ".join(gone)))
    if kept:
        lines.append(_tr(
            "clips_removal_kept",
            "Kept, because other software on this machine still needs them: "
            "{0}").format(", ".join(kept)))
    if lines:
        done = QMessageBox(parent)
        done.setWindowTitle(_tr("clips_uninstall", "Uninstall"))
        done.setText("\n\n".join(lines))
        done.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        done.setIcon(QMessageBox.Icon.Information)
        done.exec()
    return True
