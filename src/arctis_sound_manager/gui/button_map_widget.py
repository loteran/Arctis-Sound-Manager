# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Button mappings as a grid: one column per side, one row per gesture.

"Right: 1 tap", "Right: 2 taps", "Left: 1 tap", "Left: 2 taps" as four
captioned rows took a card twice the height it needs, and made the reader
pair up left and right themselves. Here the sides are columns, headed by the
earbud (or headphone) they belong to, the right one mirrored so the two face
each other as they sit on the head, and the gestures are rows, drawn as one
tapping hand per tap.

Only settings named <side>_<gesture> fit — left/right with 1_tap, 2_tap,
3_tap, med_hold or hold — and only a card whose every setting fits is drawn
this way (fits()); anything else keeps the plain list. The values, the
drop-downs and the D-Bus writes are QSettingsWidget's own.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.config import ConfigSetting, SettingType
from arctis_sound_manager.gui.components import HEADPHONE_ICON, IMAGES_DIR, SvgIconWidget
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
from arctis_sound_manager.i18n import I18n

EARBUD_ICON = os.path.join(IMAGES_DIR, "earbud_icon.svg")
TAP_ICON = os.path.join(IMAGES_DIR, "tap_icon.svg")

_PATTERN = re.compile(r"^(left|right)_(1_tap|2_tap|3_tap|med_hold|hold)$")
_SIDES = ("left", "right")
_GESTURES = ("1_tap", "2_tap", "3_tap", "med_hold", "hold")
_ICON = 20


def fits(names: Iterable[str]) -> bool:
    names = list(names)
    return bool(names) and all(_PATTERN.match(n) for n in names)


def _t(key: str, **kw) -> str:
    text = I18n.translate("ui", key)
    return text.format(**kw) if kw else text


class QButtonMapWidget(QSettingsWidget):
    def __init__(self, keys: Iterable[str], parent: QWidget | None = None):
        super().__init__(parent, None, "device", keys=keys, compact=True)
        self._controls_only = True
        self._buds = False
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 4, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(8)
        self.main_layout.addLayout(self._grid)
        self._frame: list[QWidget] = []          # headers, rebuilt with the cells

    # A row of buttons per cell would not fit two columns: always a drop-down.
    def _too_long_for_buttons(self, config: ConfigSetting) -> bool:
        return True

    def set_available_status_keys(self, keys) -> None:
        super().set_available_status_keys(keys)
        buds = "left_bud_battery" in keys or "right_bud_battery" in keys
        if buds != self._buds:
            self._buds = buds
            self.refresh_panel()

    def refresh_panel(self):
        with self.refresh_lock:
            for widget in [*self._settings_widgets.values(), *self._frame]:
                self._grid.removeWidget(widget)
                widget.deleteLater()
            self._settings_widgets.clear()
            self._frame = []

            cells: dict[tuple[str, str], QWidget] = {}
            for name, value in self._ordered_settings():
                config = self.settings_config.get(name)
                match = _PATTERN.match(name)
                if (config is None or match is None or getattr(config, "hidden", False)
                        or config.type is None):
                    continue
                control = self.get_widget(config, value, self.on_settings_updated)
                if control is None:
                    continue
                if config.type == SettingType.BUTTON_GROUP:
                    control.setToolTip(I18n.get_instance().translate("settings", name))
                self._settings_widgets[name] = control
                cells[match.group(1), match.group(2)] = control

            sides = [s for s in _SIDES if any(side == s for side, _ in cells)]
            gestures = [g for g in _GESTURES if any(gesture == g for _, gesture in cells)]
            for column, side in enumerate(sides, start=1):
                self._place(self._side_header(side), 0, column)
            for row, gesture in enumerate(gestures, start=1):
                self._place(self._gesture_header(gesture), row, 0)
                for column, side in enumerate(sides, start=1):
                    control = cells.get((side, gesture))
                    if control is not None:
                        self._grid.addWidget(control, row, column)
            self._grid.setColumnStretch(len(sides) + 1, 1)
            self._apply_conditional_visibility()

    def _place(self, widget: QWidget, row: int, column: int) -> None:
        self._frame.append(widget)
        self._grid.addWidget(widget, row, column)

    def _side_header(self, side: str) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        icon = EARBUD_ICON if self._buds else HEADPHONE_ICON
        # The earbud icon is drawn as a left bud; the right one faces it.
        layout.addWidget(SvgIconWidget(icon, _theme.c("ACCENT"), size=_ICON,
                                       mirrored=self._buds and side == "right"))
        label = QLabel(_t(f"button_side_{side}"))
        label.setStyleSheet(f"color: {_theme.c('TEXT_PRIMARY')}; background: transparent;")
        layout.addWidget(label)
        layout.addStretch(1)
        return header

    def _gesture_header(self, gesture: str) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(4)
        # The theme's text colour, like the side captions above.
        colour = _theme.c("TEXT_PRIMARY")
        if gesture.endswith("_tap"):
            taps = int(gesture[0])
            for i in range(taps):
                if i:
                    plus = QLabel("+")
                    plus.setStyleSheet(f"color: {colour}; font-weight: bold; background: transparent;")
                    layout.addWidget(plus)
                layout.addWidget(SvgIconWidget(TAP_ICON, colour, size=_ICON))
            header.setToolTip(_t("button_gesture_taps", count=taps))
        else:
            layout.addWidget(SvgIconWidget(TAP_ICON, colour, size=_ICON))
            label = QLabel(_t(f"button_gesture_{gesture}"))
            label.setStyleSheet(f"color: {colour}; background: transparent;")
            layout.addWidget(label)
        layout.addStretch(1)
        return header
