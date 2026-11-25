# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headset page: separate left/right output level (output_balance.py).

Behind a toggle so the two sliders cannot be moved by accident; off means both
sides at the main volume. The daemon owns applying it: this only sends the
three settings over D-Bus and mirrors what the settings push says.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.qt_widgets.q_dual_state import QDualState
from arctis_sound_manager.i18n import I18n

_SEND_DELAY_MS = 200  # coalesce a drag into a few writes, not one per pixel


def _t(key: str) -> str:
    return I18n.translate('ui', key)


class QOutputBalanceWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(8)

        # Sits at the bottom of the General card: a line sets it apart from
        # the device's own settings above.
        self._divider = QFrame()
        self._divider.setFrameShape(QFrame.Shape.HLine)
        self._divider.setFixedHeight(1)
        layout.addWidget(self._divider)

        # Laid out like the device's own toggle rows above it in the card
        # (QSettingsWidget, compact): a 200 px label column, then the switch.
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 4, 0, 4)
        self._toggle_label = QLabel(_t('output_balance_toggle'))
        self._toggle_label.setWordWrap(True)
        self._toggle_label.setFixedWidth(200)
        toggle_row.addWidget(self._toggle_label)
        self._toggle = QDualState(
            off_text=I18n.translate('settings_values', 'off'),
            on_text=I18n.translate('settings_values', 'on'),
            init_state='left', accent=True)
        self._toggle.checkStateChanged.connect(self._on_toggled)
        toggle_row.addWidget(self._toggle)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        self._hint = QLabel(_t('output_balance_hint'))
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._sliders_box = QWidget()
        box = QVBoxLayout(self._sliders_box)
        box.setContentsMargins(4, 4, 0, 0)
        box.setSpacing(6)
        self._rows: dict[str, tuple[QLabel, QSlider, QLabel]] = {}
        for side in ('left', 'right'):
            row = QHBoxLayout()
            row.setSpacing(10)
            name = QLabel(_t(f'output_volume_{side}'))
            name.setMinimumWidth(80)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(100)
            value = QLabel('100%')
            value.setMinimumWidth(44)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            slider.valueChanged.connect(lambda v, s=side: self._on_slider(s, v))
            row.addWidget(name)
            row.addWidget(slider, 1)
            row.addWidget(value)
            box.addLayout(row)
            self._rows[side] = (name, slider, value)
        layout.addWidget(self._sliders_box)
        self._sliders_box.setVisible(False)

        self._pending: dict[str, int] = {}
        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(_SEND_DELAY_MS)
        self._send_timer.timeout.connect(self._send_pending)

        self.apply_theme()

    # ── Settings push → widget ────────────────────────────────────────────────

    def update_from_settings(self, general: dict) -> None:
        """Mirror the saved settings, except for a control the user is moving."""
        enabled = bool(general.get('output_balance_enabled', False))
        self._toggle.set_state('right' if enabled else 'left')
        self._sliders_box.setVisible(enabled)
        for side in ('left', 'right'):
            _, slider, value = self._rows[side]
            if slider.isSliderDown() or side in self._pending:
                continue
            pct = general.get(f'output_volume_{side}', 100)
            if isinstance(pct, int) and slider.value() != pct:
                slider.blockSignals(True)
                slider.setValue(pct)
                slider.blockSignals(False)
                value.setText(f'{pct}%')

    # ── Widget → daemon ───────────────────────────────────────────────────────

    def _on_toggled(self, state) -> None:
        enabled = state == Qt.CheckState.Checked
        self._sliders_box.setVisible(enabled)
        DbusWrapper.change_setting('output_balance_enabled', enabled)

    def _on_slider(self, side: str, value: int) -> None:
        self._rows[side][2].setText(f'{value}%')
        self._pending[side] = value
        self._send_timer.start()

    def _send_pending(self) -> None:
        pending, self._pending = self._pending, {}
        for side, value in pending.items():
            DbusWrapper.change_setting(f'output_volume_{side}', value)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        primary, secondary = _theme.c('TEXT_PRIMARY'), _theme.c('TEXT_SECONDARY')
        # Same base as the card's own settings (SettingsCard styles its body
        # this way), so nothing in here paints the page colour inside the card.
        from arctis_sound_manager.gui.headset_cards import card_contents_qss
        self.setStyleSheet(card_contents_qss())
        self._divider.setStyleSheet(f"background: {_theme.c('BORDER')}; border: none;")
        self._toggle_label.setStyleSheet(
            f"color: {primary}; font-size: 10pt; background: transparent;")
        self._hint.setStyleSheet(
            f"color: {secondary}; font-size: 9pt; background: transparent;")
        for name, slider, value in self._rows.values():
            name.setStyleSheet(f"color: {secondary}; font-size: 10pt; background: transparent;")
            value.setStyleSheet(f"color: {primary}; font-size: 10pt; background: transparent;")
            slider.setStyleSheet(f"""
                QSlider {{ background: transparent; }}
                QSlider::groove:horizontal {{
                    height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
                }}
                QSlider::handle:horizontal {{
                    background: {_theme.c('ACCENT')}; width: 14px; height: 14px;
                    margin: -5px 0; border-radius: 7px;
                }}
                QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
            """)
