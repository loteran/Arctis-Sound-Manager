"""
ReportBugDialog — modal dialog to review and submit a bug report to GitHub.
"""
import subprocess
import webbrowser
from typing import Optional

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QClipboard, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from arctis_sound_manager.bug_reporter import (
    clear_crash_report,
    format_bug_report,
    format_bug_report_short,
    github_issue_url,
    is_gh_cli_ready,
    submit_via_gh_cli,
    write_full_report_to_file,
)
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_BTN = (
    "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
    "border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
    "QPushButton:hover {{ background-color: {hover}; }}"
)


class ReportBugDialog(QDialog):
    """
    Shows a previewable bug report. Two artefacts:
      - a short summary that goes into the GitHub URL `?body=` (fits in
        the URL, no truncation).
      - a full diagnostic file written to ~/.cache/arctis-sound-manager/
        reports/ that the user drag-and-drops into the issue editor.
    """

    def __init__(
        self,
        traceback_str: Optional[str] = None,
        is_crash: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._is_crash = is_crash
        self._traceback = traceback_str
        self._report_path = None  # set when the user clicks "Open issue"
        self.setWindowTitle("Report a bug — Arctis Sound Manager")
        self.setMinimumSize(720, 580)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(14)

        # ── Title / subtitle ────────────────────────────────────────────────
        if is_crash:
            title_text = "A crash occurred in the last session"
            sub_text = (
                "ASM encountered an unexpected error. Click \"Open GitHub issue\" "
                "to file a report — a short summary opens in your browser, and a "
                "full diagnostic file is saved locally that you can drag-and-drop "
                "into the issue editor."
            )
        else:
            title_text = "Report a bug"
            sub_text = (
                "Click \"Open GitHub issue\" to start filing a report. A short "
                "summary opens in your browser; a full diagnostic file (USB tree, "
                "udev rules, PA/PW sinks, journalctl, etc.) is saved locally — "
                "drag-and-drop it into the GitHub editor as an attachment."
            )

        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(title_lbl)

        sub_lbl = QLabel(sub_text)
        sub_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        sub_lbl.setWordWrap(True)
        layout.addWidget(sub_lbl)

        # ── Preview of the FULL report (read-only, just for review) ─────────
        hint = QLabel("Preview of the full diagnostic (saved on disk, not in the URL):")
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;")
        layout.addWidget(hint)

        self._editor = QPlainTextEdit()
        self._editor.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background-color: {BG_CARD}; color: {TEXT_PRIMARY};"
            f"  border: 1px solid {BORDER}; border-radius: 6px;"
            f"  font-family: monospace; font-size: 9pt; padding: 8px;"
            f"}}"
        )
        self._editor.setPlainText(format_bug_report(traceback_str))
        layout.addWidget(self._editor, stretch=1)

        # ── Status line shown after the report file is written ──────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {ACCENT}; font-size: 9pt; background: transparent;"
        )
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        # ── Buttons ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._copy_btn = QPushButton("Copy full report")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        self._copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self._copy_btn)

        self._open_folder_btn = QPushButton("Open folder")
        self._open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_folder_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        self._open_folder_btn.clicked.connect(self._open_folder)
        self._open_folder_btn.setEnabled(False)
        btn_row.addWidget(self._open_folder_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        # If `gh` CLI is installed and authenticated, offer one-click auto-
        # submission (creates a secret gist with the full diagnostic + an
        # issue linking to it, all without leaving ASM). Falls back to the
        # manual drag-and-drop flow otherwise.
        self._gh_ready = is_gh_cli_ready()

        if self._gh_ready:
            self._auto_btn = QPushButton("Submit automatically (gh CLI) ↗")
            self._auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._auto_btn.setToolTip(
                "Creates a secret GitHub gist with the full diagnostic, "
                "then opens a new issue linking to it. Uses your gh CLI auth."
            )
            self._auto_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
            self._auto_btn.clicked.connect(self._submit_auto)
            btn_row.addWidget(self._auto_btn)

        self._github_btn = QPushButton(
            "Open GitHub issue ↗" if not self._gh_ready else "Open manually ↗"
        )
        self._github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gh_bg = BG_BUTTON if self._gh_ready else ACCENT
        gh_fg = TEXT_PRIMARY if self._gh_ready else "#ffffff"
        self._github_btn.setStyleSheet(_BTN.format(bg=gh_bg, fg=gh_fg, hover=BG_BUTTON_HOVER))
        self._github_btn.clicked.connect(self._open_github)
        btn_row.addWidget(self._github_btn)

        layout.addLayout(btn_row)

        # Clear crash file as soon as the dialog is shown
        if is_crash:
            clear_crash_report()

    def _copy(self) -> None:
        self.activateWindow()
        QApplication.clipboard().setText(self._editor.toPlainText(), QClipboard.Mode.Clipboard)
        self._copy_btn.setText("Copied!")
        self._copy_btn.setEnabled(False)
        QTimer.singleShot(2000, lambda: (self._copy_btn.setText("Copy full report"), self._copy_btn.setEnabled(True)))

    def _open_folder(self) -> None:
        if self._report_path is None:
            return
        # Prefer the FreeDesktop file manager so the file is selected, not
        # just the folder opened. Fallback to QDesktopServices if dbus call
        # isn't available (no DE / minimal session).
        try:
            subprocess.Popen([
                "dbus-send", "--session", "--print-reply",
                "--dest=org.freedesktop.FileManager1",
                "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItems",
                f"array:string:file://{self._report_path}",
                "string:",
            ])
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._report_path.parent)))

    def _submit_auto(self) -> None:
        """One-click submission via gh CLI: write the full report → upload as
        secret gist → create the issue with the gist link → open the new issue
        URL in the browser. Falls back to the manual flow if anything fails."""
        try:
            self._report_path = write_full_report_to_file(self._traceback)
        except Exception as e:
            self._status_lbl.setText(f"Could not write diagnostic file: {e!r}")
            return

        title = "Crash report" if self._is_crash else "Bug report"
        short = format_bug_report_short(self._traceback, attachment_path=None)

        self._auto_btn.setEnabled(False)
        self._auto_btn.setText("Uploading…")
        QApplication.processEvents()

        issue_url = submit_via_gh_cli(title, short, self._report_path)
        if not issue_url:
            self._status_lbl.setText(
                "gh CLI submission failed (auth expired or network issue). "
                "Falling back to the manual flow — opening browser now."
            )
            self._auto_btn.setEnabled(True)
            self._auto_btn.setText("Submit automatically (gh CLI) ↗")
            self._open_github()
            return

        self._status_lbl.setText(f"Issue filed: {issue_url}")
        webbrowser.open(issue_url)
        self._auto_btn.setText("Issue filed ✓")
        self._open_folder_btn.setEnabled(True)

    def _open_github(self) -> None:
        # 1. Save the full report to a local file the user can attach.
        try:
            self._report_path = write_full_report_to_file(self._traceback)
        except Exception as e:
            self._status_lbl.setText(f"Could not write diagnostic file: {e!r}")
            return

        # 2. Build a short URL body that fits in `?body=`.
        title = "Crash report" if self._is_crash else "Bug report"
        short = format_bug_report_short(self._traceback, attachment_path=self._report_path)
        url = github_issue_url(title, body=short)

        # 3. Open the browser. Browsers cap URLs around 8 kB; if we exceed,
        # fall back to a body-less URL and put the short summary on the
        # clipboard so the user can paste manually.
        if len(url) > 7500:
            QApplication.clipboard().setText(short, QClipboard.Mode.Clipboard)
            url = github_issue_url(title)
            self._status_lbl.setText(
                f"Diagnostic saved to {self._report_path}. "
                "Short summary copied to clipboard (URL was too long for the browser) — "
                "paste it as the issue body, then drag-and-drop the file as an attachment."
            )
        else:
            self._status_lbl.setText(
                f"Diagnostic saved to {self._report_path}. "
                "GitHub editor will open with the summary pre-filled — drag-and-drop "
                "the file from the folder below into the editor as an attachment."
            )

        webbrowser.open(url)
        self._open_folder_btn.setEnabled(True)
        self._github_btn.setText("Issue opened ↗ — drop the file in")
        self._github_btn.setEnabled(False)
        QTimer.singleShot(
            6000,
            lambda: (self._github_btn.setText("Open GitHub issue ↗"), self._github_btn.setEnabled(True)),
        )
