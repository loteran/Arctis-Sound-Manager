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
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, Signal
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

from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
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

_SKIP_MARKER = Path.home() / ".config" / "arctis_manager" / ".skip_deps_check"

_BTN = (
    "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
    "border-radius: 6px; padding: 6px 14px; font-size: 9pt; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
    "QPushButton:disabled {{ background-color: #4a4a4a; color: #888; }}"
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


class _DepRow(QFrame):
    """One row inside the dialog — status icon, name, feature, action button."""

    install_requested = Signal(object)  # emits the CheckResult

    def __init__(self, result: CheckResult, parent: QWidget | None = None):
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
            f"color: {TEXT_PRIMARY}; font-size: 10pt; font-weight: bold; background: transparent;"
        )
        feature_lbl = QLabel(f"Breaks: {result.check.feature}")
        feature_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        feature_lbl.setWordWrap(True)
        text_col.addWidget(name_lbl)
        text_col.addWidget(feature_lbl)
        if result.check.user_action:
            note_lbl = QLabel(f"Note: {result.check.user_action}")
            note_lbl.setStyleSheet(
                f"color: {ACCENT}; font-size: 8pt; background: transparent; font-style: italic;"
            )
            note_lbl.setWordWrap(True)
            text_col.addWidget(note_lbl)
        layout.addLayout(text_col, stretch=1)

        argv = install_command_for(result.check)
        if argv:
            label = "Run" if argv[0] in ("asm-setup", "asm-cli", "systemctl") else "Install"
            self.action_btn = QPushButton(label)
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
            self.action_btn.clicked.connect(lambda: self.install_requested.emit(result))
            layout.addWidget(self.action_btn)
        else:
            no_path_lbl = QLabel("No automatic\ninstall path")
            no_path_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_path_lbl.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 8pt; background: transparent; font-style: italic;"
            )
            layout.addWidget(no_path_lbl)
            self.action_btn = None

        copy_btn = QPushButton("Copy cmd")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        copy_btn.setEnabled(argv is not None)
        copy_btn.clicked.connect(lambda: self._copy_command(argv))
        layout.addWidget(copy_btn)

    def _copy_command(self, argv: list[str] | None) -> None:
        if not argv:
            return
        # Prepend `sudo ` for system-pkg installs so the clipboard line is
        # ready to paste into a terminal. Skip for internal helpers.
        line = (
            " ".join(argv)
            if argv[0] in ("asm-setup", "asm-cli", "systemctl")
            else "sudo " + " ".join(argv)
        )
        QGuiApplication.clipboard().setText(line, QClipboard.Mode.Clipboard)


class SystemDepsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("System dependencies — Arctis Sound Manager")
        self.setMinimumSize(720, 520)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        self._results: list[CheckResult] = []
        self._row_widgets: list[_DepRow] = []
        self._running_processes: list[QProcess] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        header = QLabel("Some required components are missing")
        header.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        outer.addWidget(header)

        sub = QLabel(
            "ASM features that depend on missing system packages will not work "
            "until they are installed. The detected distribution is "
            f"<b>{detect_distro()}</b> — install commands below match that "
            "package manager. Each install will prompt for your administrator "
            "password (via polkit)."
        )
        sub.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
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
            f"color: {ACCENT}; font-size: 9pt; background: transparent;"
        )
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        # Bottom row: skip checkbox + Re-check + Install all + Close
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        self._skip_checkbox = QCheckBox("Don't show again until ASM is upgraded")
        self._skip_checkbox.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        bottom.addWidget(self._skip_checkbox)
        bottom.addStretch()

        self._refresh_btn = QPushButton("Re-check")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        self._refresh_btn.clicked.connect(self._refresh)
        bottom.addWidget(self._refresh_btn)

        self._install_all_btn = QPushButton("Install all missing")
        self._install_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_all_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
        self._install_all_btn.clicked.connect(self._install_all)
        bottom.addWidget(self._install_all_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        self._close_btn.clicked.connect(self._on_close)
        bottom.addWidget(self._close_btn)

        outer.addLayout(bottom)

        self._refresh()

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
            self._status_lbl.setText("All blocking and degraded checks now pass — you can close this dialog.")
            self._install_all_btn.setEnabled(False)
            return

        # Order: BLOCKING first, then DEGRADED
        bad.sort(key=lambda r: 0 if r.check.severity is Severity.BLOCKING else 1)
        for result in bad:
            row = _DepRow(result, parent=self._rows_container)
            row.install_requested.connect(self._install_one)
            # Insert before the existing stretch
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._row_widgets.append(row)

        self._install_all_btn.setEnabled(any(install_command_for(r.check) for r in bad))
        self._status_lbl.setText(
            f"{len(bad)} issue(s) detected. Use 'Install all missing' for a single "
            "polkit prompt, or fix items individually."
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

        for r in bad:
            argv = install_command_for(r.check)
            if not argv:
                skipped.append(r.check.name)
                continue
            head = argv[0]
            if head in ("asm-setup", "asm-cli", "systemctl"):
                internals.append(argv)
                continue
            # head is dnf / apt-get / pacman / paru — the package name(s) are
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
            else:
                # unknown pkgmgr — run as-is
                groups.setdefault(head, []).append(argv[-1])

        batches: list[list[str]] = []
        for mgr, pkgs in groups.items():
            if mgr == "dnf":
                batches.append(["dnf", "install", "-y", *pkgs])
            elif mgr == "apt-get":
                batches.append(["apt-get", "install", "-y", *pkgs])
            elif mgr == "pacman":
                batches.append(["pacman", "-S", "--noconfirm", *pkgs])
            else:
                batches.append([mgr, "install", "-y", *pkgs])

        if skipped:
            self._status_lbl.setText(
                f"No install path on this distro for: {', '.join(skipped)} — "
                "use 'Copy cmd' on each row instead."
            )

        all_cmds = batches + internals
        if not all_cmds:
            return
        self._run_with_pkexec(all_cmds, context="all missing deps")

    def _run_with_pkexec(self, commands: list[list[str]], context: str) -> None:
        """Run the commands sequentially via pkexec (or directly if the
        head is an internal helper that shouldn't be elevated). The dialog
        stays alive; on completion of the last command we re-run the
        checker to refresh the rows."""
        if not shutil.which("pkexec"):
            self._status_lbl.setText(
                "pkexec not found — install polkit, or copy each command manually."
            )
            return

        self._set_busy(True)
        self._status_lbl.setText(f"Installing: {context}…")

        # Chain commands sequentially via a small shell snippet so we only
        # raise one pkexec prompt for the whole batch. Internals that don't
        # need root (asm-setup, asm-cli, paru) run un-elevated AFTER the
        # pkexec batch.
        elevated, user_local = [], []
        for argv in commands:
            head = argv[0]
            if head in ("asm-setup", "asm-cli", "paru"):
                user_local.append(argv)
            elif head == "systemctl":
                # `systemctl --user` must run as the user — never via pkexec
                user_local.append(argv)
            else:
                elevated.append(argv)

        def _shell_quote(args: list[str]) -> str:
            return " ".join(f"'{a}'" if " " in a else a for a in args)

        def _run_user_cmds() -> None:
            for argv in user_local:
                try:
                    subprocess.run(argv, check=False, timeout=120)
                except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    log.warning("user command %r failed: %r", argv, exc)
            # Refresh the dialog after EVERYTHING is done
            self._set_busy(False)
            self._refresh()

        if not elevated:
            _run_user_cmds()
            return

        chained = " && ".join(_shell_quote(a) for a in elevated)
        proc = QProcess(self)
        proc.setProgram("pkexec")
        proc.setArguments(["sh", "-c", chained])

        def _on_finished(exit_code: int, _exit_status):
            if exit_code != 0:
                self._status_lbl.setText(
                    f"Install failed (pkexec exit {exit_code}). "
                    "Try 'Copy cmd' on individual rows and run them in a terminal."
                )
                self._set_busy(False)
                # Even on failure, re-check so partial progress is reflected.
                self._refresh()
            else:
                _run_user_cmds()

        proc.finished.connect(_on_finished)
        self._running_processes.append(proc)
        proc.start()

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
