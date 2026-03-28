"""
Equalizer page — EQ mode toggle (Sonar / Custom).
ArctisSonar GUI visual style.
"""
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.components import AccentButton
from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

STATE_FILE = Path.home() / ".config" / "arctis_manager" / ".eq_mode"
TOGGLE_SCRIPT = Path.home() / ".config" / "arctis_manager" / "toggle_sonar.py"


def _current_mode() -> str:
    return STATE_FILE.read_text().strip() if STATE_FILE.exists() else "custom"


class _ToggleWorker(QThread):
    """Runs the toggle script in a background thread."""
    finished = Signal()

    def run(self):
        try:
            subprocess.Popen(["python3", str(TOGGLE_SCRIPT)]).wait()
        except Exception:
            pass
        self.finished.emit()


class EqualizerPage(QWidget):
    """
    Page showing the current EQ mode (Sonar or Custom EQ)
    with an orange toggle button to switch between them.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── App title ─────────────────────────────────────────────────────────
        app_title = QLabel("Arctis Manager")
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(app_title)
        root.addSpacing(28)

        # ── Section title ──────────────────────────────────────────────────────
        eq_title = QLabel("Equalizer")
        eq_title.setStyleSheet(
            "color: #666666; font-size: 20pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(eq_title)
        root.addSpacing(20)

        # ── Mode card ─────────────────────────────────────────────────────────
        self._card = QWidget()
        self._card.setObjectName("eqCard")
        self._card.setFixedWidth(480)
        self._card.setStyleSheet(
            f"""
            QWidget#eqCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(16)

        # Current mode indicator row
        mode_row = QWidget()
        mode_row.setStyleSheet("background: transparent;")
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        mode_row_layout.setSpacing(12)

        mode_static = QLabel("Mode actuel :")
        mode_static.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;"
        )
        mode_row_layout.addWidget(mode_static)

        self._mode_label = QLabel()
        self._mode_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13pt; font-weight: bold; background: transparent;"
        )
        mode_row_layout.addWidget(self._mode_label)
        mode_row_layout.addStretch(1)

        card_layout.addWidget(mode_row)

        # Description label
        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        card_layout.addWidget(self._desc_label)

        # Toggle button — orange accent
        self._button = AccentButton("")
        self._button.clicked.connect(self._on_toggle)
        card_layout.addWidget(self._button)

        root.addWidget(self._card)
        root.addStretch(1)

        self._worker: _ToggleWorker | None = None
        self._refresh()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh(self):
        mode = _current_mode()
        if mode == "sonar":
            self._mode_label.setText("Sonar")
            self._mode_label.setStyleSheet(
                f"color: {ACCENT}; font-size: 13pt; font-weight: bold; background: transparent;"
            )
            self._desc_label.setText(
                "Le traitement audio SteelSeries Sonar est actif. "
                "Cliquez pour revenir à votre Custom EQ."
            )
            self._button.setText("Passer en Custom EQ")
        else:
            self._mode_label.setText("Custom EQ")
            self._mode_label.setStyleSheet(
                "color: #04C5A8; font-size: 13pt; font-weight: bold; background: transparent;"
            )
            self._desc_label.setText(
                "Votre Custom EQ est actif. "
                "Cliquez pour activer le traitement Sonar."
            )
            self._button.setText("Passer en mode Sonar")

    @Slot()
    def _on_toggle(self):
        self._button.setEnabled(False)
        self._button.setText("Changement en cours…")

        self._worker = _ToggleWorker(self)
        self._worker.finished.connect(self._on_toggle_done)
        self._worker.start()

    @Slot()
    def _on_toggle_done(self):
        self._refresh()
        self._button.setEnabled(True)
        self._worker = None
