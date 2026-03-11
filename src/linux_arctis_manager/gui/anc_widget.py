"""
ANC / Transparent mode — read-only status indicator.
The mode is set exclusively via the physical button on the headset;
this widget only reflects the current hardware state.
"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from linux_arctis_manager.gui.theme import ACCENT, BG_CARD, BORDER, TEXT_PRIMARY, TEXT_SECONDARY


_MODE_LABELS = {
    'off':         'Off',
    'transparent': 'Transparent',
    'on':          'ANC',
}

_PILL_BASE = """
    QPushButton, QLabel {{
        border: 1px solid {border};
        border-radius: 6px;
        padding: 4px 14px;
        font-size: 10pt;
        background-color: {bg};
        color: {fg};
    }}
"""


class _Pill(QLabel):
    """Non-interactive pill indicator."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                f"border: 1px solid {ACCENT}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {ACCENT}; color: #ffffff; font-weight: bold;"
            )
        else:
            self.setStyleSheet(
                f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {BG_CARD}; color: {TEXT_SECONDARY};"
            )


class QAncWidget(QWidget):
    """
    Read-only indicator for ANC / Transparent mode.
    Call update_status(status_dict) to refresh from live hardware state.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Mode pills row ────────────────────────────────────────────────────
        pills_row = QWidget()
        pills_row.setStyleSheet("background: transparent;")
        pills_layout = QHBoxLayout(pills_row)
        pills_layout.setContentsMargins(0, 0, 0, 0)
        pills_layout.setSpacing(8)

        self._pills: dict[str, _Pill] = {}
        for key, label in _MODE_LABELS.items():
            pill = _Pill(label, pills_row)
            self._pills[key] = pill
            pills_layout.addWidget(pill)

        pills_layout.addStretch(1)
        layout.addWidget(pills_row)

        # ── Transparent level row ─────────────────────────────────────────────
        self._level_row = QWidget()
        self._level_row.setStyleSheet("background: transparent;")
        level_layout = QHBoxLayout(self._level_row)
        level_layout.setContentsMargins(4, 0, 0, 0)
        level_layout.setSpacing(6)

        level_title = QLabel("Level:")
        level_title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        level_layout.addWidget(level_title)

        self._level_label = QLabel("—")
        self._level_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10pt; background: transparent;")
        level_layout.addWidget(self._level_label)
        level_layout.addStretch(1)

        layout.addWidget(self._level_row)
        self._level_row.setVisible(False)

    # ── Public API ────────────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict) -> None:
        headset = status.get('headset', {})

        nc_entry = headset.get('noise_cancelling', {})
        mode = nc_entry.get('value', 'off') if isinstance(nc_entry, dict) else 'off'

        tr_entry = headset.get('transparent_noise_cancelling_level', {})
        level = tr_entry.get('value', None) if isinstance(tr_entry, dict) else None

        self._set_state(mode, level)

    def _set_state(self, mode: str, level) -> None:
        for key, pill in self._pills.items():
            pill._set_active(key == mode)

        is_transparent = (mode == 'transparent')
        self._level_row.setVisible(is_transparent)
        if is_transparent and level is not None:
            self._level_label.setText(f"{level}%")
