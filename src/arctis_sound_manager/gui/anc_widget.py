# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
ANC / Transparent mode — interactive control.
Sends USB commands to change mode and transparent level via D-Bus.
"""
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper


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
                f"QPushButton {{ border: 1px solid {_theme.c('ACCENT')}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {_theme.c('ACCENT')}; color: #ffffff; font-weight: bold; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 4px 14px;"
                f"font-size: 10pt; background-color: {_theme.c('BG_CARD')}; color: {_theme.c('TEXT_SECONDARY')}; }}"
                f"QPushButton:hover {{ border-color: {_theme.c('ACCENT')}; color: {_theme.c('TEXT_PRIMARY')}; }}"
            )


class QAncWidget(QWidget):
    """
    Interactive ANC / Transparent mode control.
    Sends commands via D-Bus and reflects live hardware state.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_mode = 'off'

        # Pending mode/level set by the user but not yet confirmed by a status
        # update from the device — while pending, the widgets are disabled so
        # a slow round-trip doesn't look like the click/drag was dropped, and
        # a stale status update (e.g. a poll that raced the command) can't
        # snap the UI back before the command actually lands.
        self._pending_mode = None
        self._pending_level = None

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

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

        self._level_title = QLabel("Level:")
        level_layout.addWidget(self._level_title)

        self._level_slider = QSlider(Qt.Orientation.Horizontal)
        self._level_slider.setMinimum(1)
        self._level_slider.setMaximum(10)
        self._level_slider.setValue(5)
        self._level_slider.setFixedWidth(160)
        self._level_slider.valueChanged.connect(self._on_level_changed)
        level_layout.addWidget(self._level_slider)

        self._level_label = QLabel("50%")
        self._level_label.setFixedWidth(36)
        self._level_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        level_layout.addWidget(self._level_label)
        level_layout.addStretch(1)

        layout.addWidget(self._level_row)
        self._level_row.setVisible(False)

        self.apply_theme()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle all inline colors to the currently active theme."""
        self._level_title.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        self._level_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; background: transparent;"
        )
        self._level_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {_theme.c('ACCENT')}; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
        """)
        # Refresh pill colors to match the new theme
        for key, pill in self._pills.items():
            pill._set_active(key == self._current_mode)

    # ── Public API ────────────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict) -> None:
        headset = status.get('headset', {})

        nc_entry = headset.get('noise_cancelling', {})
        mode = nc_entry.get('value', 'off') if isinstance(nc_entry, dict) else 'off'

        tr_entry = headset.get('transparent_noise_cancelling_level', {})
        level = tr_entry.get('value', None) if isinstance(tr_entry, dict) else None

        # Check if we are waiting for a pending mode/level change to be
        # confirmed by the device before trusting this status update.
        is_waiting = False
        if self._pending_mode is not None:
            if mode == self._pending_mode:
                self._pending_mode = None
            else:
                is_waiting = True

        if self._pending_level is not None and level is not None:
            if int(level) == self._pending_level:
                self._pending_level = None
            else:
                is_waiting = True

        if is_waiting:
            return

        self._timeout_timer.stop()
        self._set_widgets_enabled(True)
        self._set_state(mode, level)

    # ── Private ───────────────────────────────────────────────────────────────

    def _set_widgets_enabled(self, enabled: bool) -> None:
        for pill in self._pills.values():
            pill.setEnabled(enabled)
        self._level_slider.setEnabled(enabled)

    @Slot()
    def _on_timeout(self) -> None:
        # The device never confirmed the pending change within the grace
        # period — give up waiting rather than leave the widgets disabled
        # forever (e.g. the device was disconnected mid-command).
        self._pending_mode = None
        self._pending_level = None
        self._set_widgets_enabled(True)

    def _set_state(self, mode: str, level) -> None:
        self._current_mode = mode
        for key, pill in self._pills.items():
            pill._set_active(key == mode)

        is_transparent = (mode == 'transparent')
        self._level_row.setVisible(is_transparent)
        if is_transparent and level is not None:
            perc = int(level)
            self._level_slider.blockSignals(True)
            self._level_slider.setValue(max(1, perc // 10))
            self._level_slider.blockSignals(False)
            self._level_label.setText(f"{perc}%")

    def _on_mode_clicked(self, mode_key: str, mode_value: int) -> None:
        if mode_key == self._current_mode:
            return
        self._current_mode = mode_key
        self._pending_mode = mode_key
        for key, pill in self._pills.items():
            pill._set_active(key == mode_key)
        self._level_row.setVisible(mode_key == 'transparent')
        self._set_widgets_enabled(False)
        self._timeout_timer.start(5000)  # 5 second grace period for device confirmation
        DbusWrapper.change_setting('noise_cancelling', mode_value)

    def _on_level_changed(self, value: int) -> None:
        self._level_label.setText(f"{value * 10}%")
        self._pending_level = value * 10
        self._set_widgets_enabled(False)
        self._timeout_timer.start(5000)  # 5 second grace period for device confirmation
        DbusWrapper.change_setting('transparent_level', value)
