"""
ReportBugDialog — modal dialog to review and submit a bug report to GitHub.
"""
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt
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
    github_issue_url,
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
    Shows an editable bug report pre-filled with system info (+ crash traceback
    if applicable) and offers Copy and Open-GitHub-Issue actions.
    """

    def __init__(
        self,
        traceback_str: Optional[str] = None,
        is_crash: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._is_crash = is_crash
        self.setWindowTitle("Report a bug — Arctis Sound Manager")
        self.setMinimumSize(700, 540)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(14)

        # ── Title / subtitle ────────────────────────────────────────────────
        if is_crash:
            title_text = "A crash occurred in the last session"
            sub_text = (
                "ASM encountered an unexpected error. "
                "Review the report below and open a GitHub issue to help fix it."
            )
        else:
            title_text = "Report a bug"
            sub_text = (
                "The report below is pre-filled with your system info and recent logs. "
                "Add a description of the problem, then open a GitHub issue."
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

        # ── Editable report ─────────────────────────────────────────────────
        hint = QLabel("You can edit the report before submitting:")
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

        # ── Buttons ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        copy_btn = QPushButton("Copy to clipboard")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        github_btn = QPushButton("Open GitHub issue ↗")
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.setStyleSheet(_BTN.format(bg=ACCENT, fg="#ffffff", hover=BG_BUTTON_HOVER))
        github_btn.clicked.connect(self._open_github)
        btn_row.addWidget(github_btn)

        layout.addLayout(btn_row)

        # Clear crash file as soon as the dialog is shown
        if is_crash:
            clear_crash_report()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._editor.toPlainText())

    def _open_github(self) -> None:
        body = self._editor.toPlainText()
        title = "Crash report" if self._is_crash else "Bug report"
        webbrowser.open(github_issue_url(title, body))
