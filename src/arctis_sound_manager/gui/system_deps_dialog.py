# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
SystemDepsDialog — runtime self-healing dialog for missing system deps.

Phase 4 of ~/Bureau/ASM_PLAN_DEPS_CHECK.md. Pairs with `system_deps_checker`
(Phase 2) and `asm-daemon --verify-setup` (Phase 3) — same registry, same
distro detection, same install commands. The GUI just renders one row per
failing check and lets the user click their way out of the problem.

Triggered from `scripts/gui.py` after the udev / telemetry dialogs so the
user isn't drowned by 4 modals on first launch.

Severity gate: shown when any BLOCKING or DEGRADED check fails. OPTIONAL
failures (currently only `gh` CLI) never trigger the dialog — the bug
reporter falls back gracefully and we don't want to nag people who never
file tickets.
"""
from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QThread, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.system_deps_checker import (
    CheckResult,
    Severity,
    detect_distro,
    failing,
    install_command_for,
    run_all_checks,
)
from arctis_sound_manager.utils import project_version

log = logging.getLogger(__name__)


def _running_in_container() -> bool:
    """distrobox/toolbox/docker/flatpak/snap.

    Delegates to container.py, the shared home for this check (it used to
    be copy-pasted here, in udev_checker.py and in systemd.py, and the
    copies drifted). Imported lazily and defensively for the same reason
    systemd.py does it: this must never be the thing that stops the dialog
    from opening.
    """
    try:
        from arctis_sound_manager.container import running_in_container
        return running_in_container()
    except Exception:  # noqa: BLE001
        return False


def _host_exec() -> list[str] | None:
    """See container.host_exec(): ``[]`` outside a container, an argv prefix
    when the host is reachable, ``None`` when it is not.

    Same lazy/defensive import as `_running_in_container`. On the (very
    unlikely) failure of the import itself we fall back to "no prefix
    needed" rather than "no way out" — that reproduces the old native
    behaviour instead of refusing to run commands that were working fine
    before this file ever heard of containers.
    """
    try:
        from arctis_sound_manager.container import host_exec
        return host_exec()
    except Exception:  # noqa: BLE001
        return []


def _host_distro() -> str | None:
    """See container.host_distro(). None is the safe fallback: it makes the
    dialog admit the host is unknown instead of asserting the container's
    distribution as if it were the host's — the exact bug this file shipped
    with (announcing "arch" on a Bazzite host)."""
    try:
        from arctis_sound_manager.container import host_distro
        return host_distro()
    except Exception:  # noqa: BLE001
        return None


def _host_is_immutable() -> bool:
    """See container.host_is_immutable(). False is the safe fallback: a host we
    cannot probe must keep the install path it has, never lose it to a guess."""
    try:
        from arctis_sound_manager.container import host_is_immutable
        return host_is_immutable()
    except Exception:  # noqa: BLE001
        return False


# Which maintained installer covers a given immutable host. Keyed on the
# distribution's ID, which is all it takes once immutability is established as
# a fact: Universal Blue images carry their own ID (bazzite, bluefin, …),
# while Silverblue and Kinoite both report plain "fedora" and share a script.
_IMMUTABLE_HOST_SCRIPTS = {
    "bazzite": "bazzite.sh",
    "fedora": "silverblue.sh",
    # SteamOS ships pacman, which is exactly the trap: the rootfs is
    # read-only, so an install appears to work and is gone at the next system
    # update (#181, #88). It gets its own maintained script.
    "steamos": "steamos.sh",
}
_IMMUTABLE_FALLBACK_SCRIPT = "silverblue.sh"


def _immutable_host_script(host_distro_id: str | None) -> str | None:
    """The maintained scripts/distrobox/*.sh installer for an immutable host,
    or None when the host can take a normal package install.

    Immutability is asked of the host as a fact (an ostree marker, or a
    read-only /usr), not guessed from its name. Naming was the first attempt
    and it did not survive contact: Silverblue and Kinoite are both "fedora"
    and would have been missed, and reading VARIANT_ID instead would have
    renamed Fedora Workstation to "workstation", which is not a distribution
    anything can act on.

    Deliberately does NOT synthesize an `rpm-ostree install` command: that
    needs a reboot to take effect, ASM has never exercised that path, and a
    fabricated line we cannot test is a worse failure mode than being honest
    and pointing at the script that already handles it correctly and is
    kept up to date.
    """
    if not _host_is_immutable():
        return None
    if not host_distro_id:
        # Immutable but unidentified: the generic ostree script is still much
        # closer to right than an install command the host will reject.
        return _IMMUTABLE_FALLBACK_SCRIPT
    return _IMMUTABLE_HOST_SCRIPTS.get(
        host_distro_id.lower(), _IMMUTABLE_FALLBACK_SCRIPT)


def _is_user_run(argv: list[str] | None) -> bool:
    """True for install commands that must run as the invoking user, never via
    pkexec/sudo: ASM helpers, ``systemctl --user``, ``paru`` (refuses root), and
    ``pip install --user`` (targets the user's own ~/.local, the immutable-safe
    self-heal path from #175). Elevating any of these would install to the wrong
    place (root's home) or fail outright."""
    if not argv:
        return False
    head = argv[0]
    if head in ("asm-setup", "asm-cli", "paru", "systemctl"):
        return True
    if head in ("pip", "pip3") and "--user" in argv:
        return True
    if head == "python3" and "pip" in argv and "--user" in argv:
        return True
    return False


_SKIP_MARKER = Path.home() / ".config" / "arctis_manager" / ".skip_deps_check"

# Pacman mirror-sync error: the mirror has the index but not the file yet.
# Detected in QProcess output; triggers an automatic `pacman -Syy` + retry.
_PACMAN_MIRROR_ERROR_RE = re.compile(
    r"returned error: 404"
    r"|failed to retrieve"
    r"|echec de recuperation"         # accents stripped in ASCII log paths
    r"|erreur.*recuperation"
    r"|transaction.*failed.*retrieve",
    re.IGNORECASE,
)

def _btn_ss(bg: str, fg: str, hover: str) -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
        f"border-radius: 6px; padding: 6px 14px; font-size: 9pt; }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:disabled {{ background-color: #4a4a4a; color: #888; }}"
    )

# Severity → (badge text, badge bg colour) for the per-row tag
_SEVERITY_BADGE = {
    Severity.BLOCKING: ("BLOCKING", "#c0392b"),
    Severity.DEGRADED: ("DEGRADED", "#d68910"),
    Severity.OPTIONAL: ("OPTIONAL", "#7f8c8d"),
}


def _skip_marker_matches_version() -> bool:
    """Returns True if the user previously chose 'skip until next version'
    AND the marker is still pointing at this same ASM version."""
    try:
        return _SKIP_MARKER.read_text().strip() == project_version()
    except (OSError, FileNotFoundError):
        return False


def _write_skip_marker() -> None:
    try:
        _SKIP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SKIP_MARKER.write_text(project_version())
    except OSError as exc:
        log.warning("Could not write skip marker %s: %s", _SKIP_MARKER, exc)


def should_show_dialog() -> bool:
    """Cheap synchronous check — < 200 ms total. Call from GUI startup
    to decide whether to instantiate the dialog at all."""
    if _skip_marker_matches_version():
        return False
    results = run_all_checks()
    return len(failing(results, min_severity=Severity.DEGRADED)) > 0


class _UserCmdsWorker(QThread):
    """Runs the "must stay un-elevated" commands (asm-setup, asm-cli,
    ``systemctl --user``, ``paru``, ``pip install --user``) off the GUI
    thread.

    These look like plain user subprocess calls, but at least one of them —
    ``asm-cli udev write-rules`` — can escalate *itself* internally via
    ``sudo_it()`` (``scripts/cli.py``), which tries kdesu/pkexec with no
    timeout of its own. Running that synchronously on the Qt main thread
    freezes the whole window for as long as a nested prompt the user may
    never even see is left unanswered (issue #200).

    ``done`` always fires — even if a command raises, times out, or the
    nested elevation prompt is cancelled/ignored/fails — so the dialog can
    always recover its buttons instead of being left looking hung.
    """

    done = Signal()

    def __init__(self, commands: list[list[str]]):
        super().__init__()
        self._commands = commands

    def run(self) -> None:
        for argv in self._commands:
            try:
                subprocess.run(argv, check=False, timeout=120)
            except Exception as exc:  # noqa: BLE001 - a failed/cancelled/
                # timed-out nested elevation prompt must not take the whole
                # dialog down with it; log and move on to the next command.
                log.warning("user command %r failed: %r", argv, exc)
        self.done.emit()


class _DepRow(QFrame):
    """One row inside the dialog — status icon, name, feature, action button."""

    install_requested = Signal(object)  # emits the CheckResult

    def __init__(
        self,
        result: CheckResult,
        parent: QWidget | None = None,
        *,
        host_distro_id: str | None = None,
    ):
        super().__init__(parent)
        self.result = result
        self.setStyleSheet(
            f"_DepRow, QFrame {{ background: #1f1f1f; border-radius: 6px; padding: 4px; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        badge_text, badge_bg = _SEVERITY_BADGE[result.check.severity]
        badge = QLabel(badge_text)
        badge.setFixedWidth(82)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {badge_bg}; color: #ffffff; border-radius: 4px; "
            "font-size: 8pt; font-weight: bold; padding: 4px;"
        )
        layout.addWidget(badge)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        name_lbl = QLabel(result.check.name)
        name_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; font-weight: bold; background: transparent;"
        )
        feature_lbl = QLabel(I18n.translate('ui', 'dep_breaks').format(feature=result.check.feature))
        feature_lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        feature_lbl.setWordWrap(True)
        text_col.addWidget(name_lbl)
        text_col.addWidget(feature_lbl)
        if result.check.user_action:
            note_lbl = QLabel(I18n.translate('ui', 'dep_note').format(note=result.check.user_action))
            note_lbl.setStyleSheet(
                f"color: {_theme.c('ACCENT')}; font-size: 8pt; background: transparent; font-style: italic;"
            )
            note_lbl.setWordWrap(True)
            text_col.addWidget(note_lbl)
        layout.addLayout(text_col, stretch=1)

        argv = install_command_for(result.check)
        # On an immutable host reached through a container, refuse to offer a
        # button at all rather than one that runs a fabricated (and never
        # exercised) `rpm-ostree install` line: `_is_user_run` commands are
        # exempt because they never touch the host's package manager in the
        # first place.
        immutable_script = None
        if argv and not _is_user_run(argv) and _running_in_container():
            immutable_script = _immutable_host_script(host_distro_id)
            if immutable_script:
                argv = None

        if argv:
            label = I18n.translate('ui', 'run') if argv[0] in ("asm-setup", "asm-cli", "systemctl") else I18n.translate('ui', 'install')
            self.action_btn = QPushButton(label)
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setStyleSheet(
                _btn_ss(_theme.c('ACCENT'), "#ffffff", _theme.c('BG_BUTTON_HOVER'))
            )
            self.action_btn.clicked.connect(lambda: self.install_requested.emit(result))
            layout.addWidget(self.action_btn)
        else:
            if immutable_script:
                no_path_text = I18n.translate('ui', 'sysdeps_immutable_host').format(
                    host=host_distro_id, script=immutable_script)
            else:
                no_path_text = I18n.translate('ui', 'no_install_path')
            no_path_lbl = QLabel(no_path_text)
            no_path_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_path_lbl.setWordWrap(True)
            no_path_lbl.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; background: transparent; font-style: italic;"
            )
            layout.addWidget(no_path_lbl)
            self.action_btn = None

        copy_btn = QPushButton(I18n.translate('ui', 'copy_cmd'))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(
            _btn_ss(_theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'), _theme.c('BG_BUTTON_HOVER'))
        )
        copy_btn.setEnabled(argv is not None)
        copy_btn.clicked.connect(lambda: self._copy_command(argv, copy_btn))
        layout.addWidget(copy_btn)

    def _copy_command(self, argv: list[str] | None, button: QPushButton | None = None) -> None:
        if not argv:
            return
        # shlex.join quotes each argument, so multi-word commands like
        # `bash -c "<build script>"` (e.g. the from-source RNNoise build) stay
        # intact when pasted — a plain " ".join would drop the quoting and the
        # command would break.
        cmd = shlex.join(argv)
        # Prepend `sudo ` for system-pkg installs so the clipboard line is
        # ready to paste into a terminal. Skip for anything that runs as the
        # user (ASM helpers, systemctl --user, paru, pip --user — #175) —
        # those run in the container's own userland even when we're in one,
        # so they need no host escape either.
        if _is_user_run(argv):
            line = cmd
        else:
            line = "sudo " + cmd
            if _running_in_container():
                # Whatever terminal this gets pasted into is a container
                # shell: a bare `sudo dnf install …` would elevate root
                # *inside* the container and install nothing where the
                # package actually needs to land. distrobox-host-exec is
                # the standard escape hatch to the host.
                line = "distrobox-host-exec " + line
        # Under Wayland the clipboard belongs to the focused window: a
        # compositor drops setText() from a window that does not have keyboard
        # focus, silently. This dialog used to open behind the main window
        # (fixed in scripts/gui.py), and "Copy cmd" then did nothing at all
        # with no error anywhere — reported on Bazzite. Claim focus first.
        self.activateWindow()
        QGuiApplication.clipboard().setText(line, QClipboard.Mode.Clipboard)
        # And say so. Without a visible acknowledgement there is no way to tell
        # a copy that worked from one the compositor refused.
        if button is not None:
            button.setText(I18n.translate('ui', 'copy_cmd_done'))
            QTimer.singleShot(
                1500, lambda: button.setText(I18n.translate('ui', 'copy_cmd')))


class SystemDepsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("System dependencies — Arctis Sound Manager")  # brand name stays fixed
        self.setMinimumSize(720, 520)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        self._results: list[CheckResult] = []
        self._row_widgets: list[_DepRow] = []
        self._running_processes: list[QProcess] = []
        self._active_worker: _UserCmdsWorker | None = None
        # host_distro() round-trips through distrobox-host-exec (bounded, but
        # not free) and cannot change for the life of this dialog — looked up
        # once here and reused by every row and every refresh, instead of
        # re-querying the host on every click.
        self._host_distro_id: str | None = (
            _host_distro() if _running_in_container() else None
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        header = QLabel(I18n.translate('ui', 'sysdeps_header'))
        header.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        outer.addWidget(header)

        sub = QLabel(self._sysdeps_sub_text())
        sub.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # Scrollable rows area
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_container)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        outer.addWidget(scroll, stretch=1)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {_theme.c('ACCENT')}; font-size: 9pt; background: transparent;"
        )
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        # Bottom row: skip checkbox + Re-check + Install all + Close
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        self._skip_checkbox = QCheckBox(I18n.translate('ui', 'skip_until_upgrade'))
        self._skip_checkbox.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        bottom.addWidget(self._skip_checkbox)
        bottom.addStretch()

        self._refresh_btn = QPushButton(I18n.translate('ui', 'recheck'))
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(
            _btn_ss(_theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'), _theme.c('BG_BUTTON_HOVER'))
        )
        self._refresh_btn.clicked.connect(self._refresh)
        bottom.addWidget(self._refresh_btn)

        self._install_all_btn = QPushButton(I18n.translate('ui', 'install_all_missing'))
        self._install_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_all_btn.setStyleSheet(
            _btn_ss(_theme.c('ACCENT'), "#ffffff", _theme.c('BG_BUTTON_HOVER'))
        )
        self._install_all_btn.clicked.connect(self._install_all)
        bottom.addWidget(self._install_all_btn)

        self._close_btn = QPushButton(I18n.translate('ui', 'close'))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(
            _btn_ss(_theme.c('BG_BUTTON'), _theme.c('TEXT_PRIMARY'), _theme.c('BG_BUTTON_HOVER'))
        )
        self._close_btn.clicked.connect(self._on_close)
        bottom.addWidget(self._close_btn)

        outer.addLayout(bottom)

        self._refresh()

    def _sysdeps_sub_text(self) -> str:
        """Wording for the subtitle under the dialog header.

        Naming the *container's* distro was the second half of the Bazzite
        bug report: an Arch distrobox on a Bazzite host announced itself as
        "arch" and every install line below matched pacman — useless on the
        host, where these packages actually need to land. Name both when we
        can, and admit the host is unknown rather than asserting the
        container's identity as if it were the host's.
        """
        container_distro = detect_distro()
        if not _running_in_container():
            return I18n.translate('ui', 'sysdeps_sub').format(distro=container_distro)
        if self._host_distro_id:
            return I18n.translate('ui', 'sysdeps_sub_container').format(
                container_distro=container_distro, host_distro=self._host_distro_id)
        return I18n.translate('ui', 'sysdeps_sub_container_unknown_host').format(
            container_distro=container_distro)

    # ── Population / refresh ────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Re-run the checker and rebuild the rows. Called on dialog open
        and after every install attempt."""
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()

        self._results = run_all_checks()
        bad = failing(self._results, min_severity=Severity.DEGRADED)

        if not bad:
            self._status_lbl.setText(I18n.translate('ui', 'all_checks_pass'))
            self._install_all_btn.setEnabled(False)
            return

        # Order: BLOCKING first, then DEGRADED
        bad.sort(key=lambda r: 0 if r.check.severity is Severity.BLOCKING else 1)
        for result in bad:
            row = _DepRow(result, parent=self._rows_container, host_distro_id=self._host_distro_id)
            row.install_requested.connect(self._install_one)
            # Insert before the existing stretch
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._row_widgets.append(row)

        self._install_all_btn.setEnabled(any(install_command_for(r.check) for r in bad))
        self._status_lbl.setText(
            I18n.translate_plural('ui', 'issue_count', len(bad))
        )

    # ── Install actions ─────────────────────────────────────────────────────

    def _install_one(self, result: CheckResult) -> None:
        argv = install_command_for(result.check)
        if not argv:
            self._status_lbl.setText(
                f"No automatic install path for '{result.check.name}' on this distro."
            )
            return
        self._run_with_pkexec([argv], context=result.check.name)

    def _install_all(self) -> None:
        """Group all package-install commands by package manager and run
        ONE pkexec per group so the user only types their password once."""
        bad = failing(self._results, min_severity=Severity.DEGRADED)
        if not bad:
            return

        groups: dict[str, list[str]] = {}  # pkgmgr -> packages list
        internals: list[list[str]] = []    # asm-setup / asm-cli / systemctl
        skipped: list[str] = []
        immutable_blocked: list[str] = []  # needs the host, but it's immutable
        batches: list[list[str]] = []      # multi-step bash cmds + per-pkgmgr groups

        for r in bad:
            argv = install_command_for(r.check)
            if not argv:
                skipped.append(r.check.name)
                continue
            if not _is_user_run(argv) and _running_in_container():
                # Same guard as _DepRow: never batch a fabricated install line
                # for a dep that actually needs to land on an immutable host.
                script = _immutable_host_script(self._host_distro_id)
                if script:
                    immutable_blocked.append(r.check.name)
                    continue
            head = argv[0]
            if _is_user_run(argv):
                # asm-setup / asm-cli / systemctl --user / paru / pip --user —
                # run un-elevated after the pkexec batch (#175).
                internals.append(argv)
                continue
            # head is dnf / apt-get / pacman — the package name(s) are
            # the trailing positional args (after subcommand + flags). To keep
            # this robust we re-build the argv from scratch per pkgmgr.
            if head == "dnf":
                groups.setdefault("dnf", []).append(argv[-1])
            elif head == "apt-get":
                groups.setdefault("apt-get", []).append(argv[-1])
            elif head == "pacman":
                groups.setdefault("pacman", []).append(argv[-1])
            elif head == "paru":
                # paru must run as the user (not via pkexec) — fall back to
                # individual sudo run; the user will get its own prompt.
                internals.append(argv)
            elif head == "bash":
                # Multi-step command (e.g. COPR enable + package install) —
                # cannot be batched with other packages; pass through as-is.
                batches.append(argv)
            else:
                # unknown pkgmgr — run as-is
                groups.setdefault(head, []).append(argv[-1])

        for mgr, pkgs in groups.items():
            if mgr == "dnf":
                batches.append(["dnf", "install", "-y", *pkgs])
            elif mgr == "apt-get":
                batches.append(["apt-get", "install", "-y", *pkgs])
            elif mgr == "pacman":
                batches.append(["pacman", "-S", "--noconfirm", *pkgs])
            else:
                batches.append([mgr, "install", "-y", *pkgs])

        notices = []
        if skipped:
            notices.append(
                I18n.translate('ui', 'sysdeps_no_path_for').format(names=', '.join(skipped))
            )
        if immutable_blocked:
            notices.append(
                I18n.translate('ui', 'sysdeps_immutable_blocked').format(
                    host=self._host_distro_id or '?', names=', '.join(immutable_blocked))
            )
        if notices:
            self._status_lbl.setText(' '.join(notices))

        all_cmds = batches + internals
        if not all_cmds:
            return
        self._run_with_pkexec(all_cmds, context="all missing deps")

    def _run_with_pkexec(
        self,
        commands: list[list[str]],
        context: str,
        *,
        _mirror_retry: bool = False,
    ) -> None:
        """Run the commands sequentially via pkexec (or directly if the
        head is an internal helper that shouldn't be elevated). The dialog
        stays alive; on completion of the last command we re-run the
        checker to refresh the rows.

        On pacman 404 mirror errors the method retries once automatically
        with `pacman -Syy` prepended to the elevated batch (_mirror_retry
        guards against a second retry loop).
        """
        # Split FIRST, before asking anything about pkexec or a container
        # boundary: ASM helpers, `systemctl --user`, paru and
        # `pip install --user` (#175) must run un-elevated as the invoking
        # user regardless of where we are, and a batch made up only of those
        # (e.g. a lone `asm-cli` command) never touches root or the host at
        # all — gating it on either question would just be a new way to
        # block work that was never going to need them.
        elevated, user_local = [], []
        for argv in commands:
            if _is_user_run(argv):
                # ASM helpers, systemctl --user, paru, pip --user (#175) — must
                # run as the invoking user, never elevated via pkexec.
                user_local.append(argv)
            else:
                elevated.append(argv)

        if not elevated:
            # Nothing needs pkexec at all (e.g. a lone `asm-cli` command) —
            # hand it straight to the worker thread instead of blocking here
            # (issue #200). Still marks the dialog busy: the worker thread
            # takes a moment (up to the 120s per-command timeout), and the
            # buttons must reflect that exactly as they do for the elevated
            # path below.
            self._set_busy(True)
            self._status_lbl.setText(f"Installing: {context}…")
            self._start_user_cmds(user_local)
            return

        # From here on there is at least one system-package install, which
        # needs root — and, inside a container, needs the HOST's root, not
        # the container's. Ask the container question before even looking
        # at pkexec: polkit is frequently pulled into a distrobox as a
        # dependency of something else, so `shutil.which("pkexec")`
        # succeeding proves nothing about where it will elevate. The old
        # code checked container-ness only *inside* the "pkexec missing"
        # branch, so a distrobox with polkit already installed skipped the
        # container question entirely and ran pkexec inside the container —
        # elevating nothing on the host, with no message at all.
        pkexec_prefix = ["pkexec"]
        if _running_in_container():
            host_prefix = _host_exec()
            if not host_prefix:
                # No way out of the container (distrobox-host-exec missing,
                # or the host unreachable). Run nothing — a pkexec that
                # "succeeds" inside the container would look like progress
                # while installing nothing where it matters.
                self._status_lbl.setText(I18n.translate('ui', 'sysdeps_container_no_host'))
                return
            # Route pkexec itself through distrobox-host-exec so the prompt
            # — and the actual install — happens on the host.
            pkexec_prefix = [*host_prefix, "pkexec"]
        elif not shutil.which("pkexec"):
            self._status_lbl.setText(I18n.translate('ui', 'sysdeps_no_pkexec'))
            return

        self._set_busy(True)
        self._status_lbl.setText(f"Installing: {context}…")

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setProgram(pkexec_prefix[0])
        if len(elevated) == 1:
            # The common case: exactly one command to elevate. Pass its argv
            # straight through pkexec — no shell in the privileged path at
            # all, so there is nothing left to quote or get wrong (EXT-3).
            proc.setArguments([*pkexec_prefix[1:], *elevated[0]])
        else:
            # Multiple commands: they still need chaining into one shell so
            # only a single pkexec prompt covers the whole batch (that's the
            # point of grouping in _install_all). shlex.join is real shell
            # quoting — it escapes embedded quotes, `$(...)`, `;`, and a
            # leading `-`, unlike the old ad-hoc "quote only if it contains a
            # space" check (EXT-3).
            chained = " && ".join(shlex.join(a) for a in elevated)
            proc.setArguments([*pkexec_prefix[1:], "sh", "-c", chained])

        uses_pacman = any(a[0] == "pacman" for a in elevated)

        def _on_finished(exit_code: int, _exit_status):
            output = bytes(proc.readAll()).decode(errors="replace")
            if exit_code != 0:
                # Auto-retry once on pacman 404 mirror sync errors.
                if (
                    not _mirror_retry
                    and uses_pacman
                    and _PACMAN_MIRROR_ERROR_RE.search(output)
                ):
                    log.warning(
                        "pacman 404 mirror error detected — forcing -Syy and retrying"
                    )
                    self._status_lbl.setText(
                        "Mirror out of sync (404) — refreshing package databases…"
                    )
                    # Prepend a forced DB refresh to the same elevated batch.
                    resync_cmd = ["pacman", "-Syy", "--noconfirm"]
                    self._run_with_pkexec(
                        [resync_cmd, *elevated] + user_local,
                        context,
                        _mirror_retry=True,
                    )
                    return

                self._status_lbl.setText(
                    f"Install failed (pkexec exit {exit_code}). "
                    "Try 'Copy cmd' on individual rows and run them in a terminal."
                )
                self._set_busy(False)
                # Even on failure, re-check so partial progress is reflected.
                self._refresh()
            else:
                if user_local:
                    self._start_user_cmds(user_local)
                else:
                    self._set_busy(False)
                    self._refresh()

        proc.finished.connect(_on_finished)
        self._running_processes.append(proc)
        proc.start()

    def _start_user_cmds(self, commands: list[list[str]]) -> None:
        """Hand *commands* to a worker thread instead of running them
        inline on the GUI thread (issue #200)."""
        worker = _UserCmdsWorker(commands)
        self._active_worker = worker
        worker.done.connect(self._on_user_cmds_result)
        # Drop the reference only once the OS thread has actually stopped.
        # Doing it from the `done` handler above instead — which fires from
        # inside run(), before the thread has fully returned — risks
        # "QThread: Destroyed while thread is still running" the moment this
        # object is garbage-collected (see _ApplyWorker in sonar_page.py,
        # issue #63).
        worker.finished.connect(self._on_user_cmds_finished)
        worker.start()

    def _on_user_cmds_result(self) -> None:
        # Refresh the dialog after EVERYTHING is done — no matter whether
        # every command succeeded, one raised, one timed out, or a nested
        # elevation prompt was cancelled: _UserCmdsWorker.done always fires.
        self._set_busy(False)
        self._refresh()

    def _on_user_cmds_finished(self) -> None:
        worker = self._active_worker
        self._active_worker = None
        if worker is not None:
            worker.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self._install_all_btn.setEnabled(not busy)
        self._refresh_btn.setEnabled(not busy)
        for row in self._row_widgets:
            if row.action_btn is not None:
                row.action_btn.setEnabled(not busy)

    def _on_close(self) -> None:
        if self._skip_checkbox.isChecked():
            _write_skip_marker()
        self.accept()
