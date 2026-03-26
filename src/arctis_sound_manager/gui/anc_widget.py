"""
ANC / Transparent mode — interactive control.
Sends USB commands to change mode and transparent level via D-Bus.
"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.theme import ACCENT, BG_BUTTON, BG_CARD, BORDER, TEXT_PRIMARY, TEXT_SECONDARY


_MODE_LABELS = {
    'off':         ('Off',         0),
    'transparent': ('Transparent', 1),
    'on':          ('ANC',         2),
}


class _Pill(QPushButton):
    """Interactive pill button for ANC mode selection."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.setStyleSheet(
                f"border: 1px solid {ACCENT}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {ACCENT}; color: #ffffff; font-weight: bold;"
            )
        else:
            self.setStyleSheet(
                f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {BG_CARD}; color: {TEXT_SECONDARY};"
                f"QPushButton:hover {{ border-color: {ACCENT}; color: {TEXT_PRIMARY}; }}"
            )


class QAncWidget(QWidget):
    """
    Interactive ANC / Transparent mode control.
    Sends commands via D-Bus and reflects live hardware state.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_mode = 'off'

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
        for key, (label, value) in _MODE_LABELS.items():
            pill = _Pill(label, pills_row)
            pill.clicked.connect(lambda _, k=key, v=value: self._on_mode_clicked(k, v))
            self._pills[key] = pill
            pills_layout.addWidget(pill)

        pills_layout.addStretch(1)
        layout.addWidget(pills_row)

        # ── Transparent level row ─────────────────────────────────────────────
        self._level_row = QWidget()
        self._level_row.setStyleSheet("background: transparent;")
        level_layout = QHBoxLayout(self._level_row)
        level_layout.setContentsMargins(4, 0, 0, 0)
        level_layout.setSpacing(10)

        level_title = QLabel("Level:")
        level_title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        level_layout.addWidget(level_title)

        self._level_slider = QSlider(Qt.Orientation.Horizontal)
        self._level_slider.setMinimum(1)
        self._level_slider.setMaximum(10)
        self._level_slider.setValue(5)
        self._level_slider.setFixedWidth(160)
        self._level_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {BG_BUTTON}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        """)
        self._level_slider.valueChanged.connect(self._on_level_changed)
        level_layout.addWidget(self._level_slider)

        self._level_label = QLabel("50%")
        self._level_label.setFixedWidth(36)
        self._level_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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

    # ── Private ───────────────────────────────────────────────────────────────

    def _set_state(self, mode: str, level) -> None:
        self._current_mode = mode
        for key, pill in self._pills.items():
            pill._set_active(key == mode)

        is_transparent = (mode == 'transparent')
        self._level_row.setVisible(is_transparent)
        if is_transparent and level is not None:
            perc = int(level)
            self._level_slider.blockSignals(True)
            self._level_slider.setValue(perc // 10 if perc > 10 else max(1, perc))
            self._level_slider.blockSignals(False)
            self._level_label.setText(f"{perc}%")

    def _on_mode_clicked(self, mode_key: str, mode_value: int) -> None:
        if mode_key == self._current_mode:
            return
        self._current_mode = mode_key
        for key, pill in self._pills.items():
            pill._set_active(key == mode_key)
        self._level_row.setVisible(mode_key == 'transparent')
        DbusWrapper.change_setting('noise_cancelling', mode_value)

    def _on_level_changed(self, value: int) -> None:
        self._level_label.setText(f"{value * 10}%")
        DbusWrapper.change_setting('transparent_level', value)
