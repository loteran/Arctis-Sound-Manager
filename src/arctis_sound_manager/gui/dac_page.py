# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
DAC page — digital-to-analog converter settings.
"""
from PySide6.QtCore import Qt, QMetaObject, Q_ARG, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import SectionTitle
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.home_page import ToggleSwitch
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
import arctis_sound_manager.gui.theme as _theme
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
from arctis_sound_manager.i18n import I18n

_TOGGLE_CARD_STYLE = (
    "background-color: {bg}; border-radius: 8px; border: 1px solid {border};"
)
_LBL_STYLE = "color: {color}; font-size: 11pt; background: transparent; border: none;"
_HINT_STYLE = "color: {color}; font-size: 9pt; background: transparent; border: none;"

# Fixed top elements (always shown, not orderable): (setting_key, label_key, font_size_key, default_size)
# font_size_key=None means no spinbox (e.g. mic icon has no configurable font)
_FIXED_DISPLAY_KEYS = [
    ('oled_show_time',       'oled_show_time',       'oled_font_time',    20),
    ('oled_show_battery',    'oled_show_battery',     'oled_font_battery', 16),
    ('oled_show_mic_status', 'oled_show_mic_status',  'oled_font_mic',     12),
]

# Orderable elements: (order_key, setting_key, label_key, font_size_key, default_size)
_ORDERABLE_ITEMS = [
    ('sonar_mode',  'oled_show_sonar_mode',  'oled_show_sonar_mode',  'oled_font_sonar_mode',    8),
    ('profile',     'oled_show_profile',     'oled_show_profile',     'oled_font_profile',        8),
    ('eq',          'oled_show_eq',          'oled_show_eq',          'oled_font_eq',             8),
    ('eq_chat',     'oled_show_eq_chat',     'oled_show_eq_chat',     'oled_font_eq_chat',        8),
    ('weather',     'weather_enabled',       'weather_enabled',       'oled_font_weather_temp',  20),
]

_DAC_WIDGET_EXCLUDE = {
    'oled_custom_display',
    'oled_show_time', 'oled_show_battery', 'oled_show_profile',
    'oled_show_eq', 'oled_show_mic_status',
    'oled_show_sonar_mode', 'oled_show_eq_chat',
    'oled_show_weather_city',
}

# Sub-options rendered indented below their parent orderable row.
# Format: parent_order_key → [(setting_key, label_key), ...]
_ORDERABLE_SUB_OPTIONS: dict[str, list[tuple[str, str]]] = {
    'weather': [('oled_show_weather_city', 'oled_show_weather_city')],
}


class DacPage(QWidget):
    """Page showing DAC-specific settings."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._scroll = scroll

        content = QWidget()
        content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._content = content
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(36, 28, 36, 36)
        layout.setSpacing(0)

        self._app_title = QLabel("Arctis Sound Manager")
        self._app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(self._app_title)
        layout.addSpacing(28)

        self._section_dac = SectionTitle(I18n.translate("ui", "dac_settings"))
        layout.addWidget(self._section_dac)
        layout.addSpacing(12)

        # ── Custom display toggle ──────────────────────────────────────────────
        self._custom_display_card = self._build_custom_display_card()
        layout.addWidget(self._custom_display_card)
        layout.addSpacing(16)

        # ── Brightness / Timeout sliders ───────────────────────────────────────
        self._dac_widget = QSettingsWidget(content, "dac_settings", "dac")
        self._dac_widget.setStyleSheet(f"""
            QWidget {{ background-color: {BG_MAIN}; color: {TEXT_PRIMARY}; }}
            QLabel {{ background-color: transparent; color: {TEXT_PRIMARY}; font-size: 11pt; }}
        """)
        layout.addWidget(self._dac_widget)
        layout.addSpacing(24)

        # ── Display elements ───────────────────────────────────────────────────
        self._section_display = SectionTitle(I18n.translate("ui", "oled_display_elements"))
        layout.addWidget(self._section_display)
        layout.addSpacing(12)
        self._display_elements_card = self._build_display_elements_card()
        layout.addWidget(self._display_elements_card)
        layout.addSpacing(24)

        # ── Weather section ────────────────────────────────────────────────────
        self._section_weather = SectionTitle(I18n.translate("ui", "weather_settings"))
        layout.addWidget(self._section_weather)
        layout.addSpacing(12)
        self._weather_card = self._build_weather_card()
        layout.addWidget(self._weather_card)

        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Apply the currently-active theme on first paint.
        self.apply_theme()

    # ── Theme propagation ─────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle the DAC page for the current active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        # Rebuild the string-template card styles used during construction.
        _card_style = (
            f"background-color: {_theme.c('BG_CARD')}; "
            f"border-radius: 8px; border: 1px solid {_theme.c('BORDER')};"
        )
        _lbl_style = f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent; border: none;"
        _hint_style = f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent; border: none;"

        # App title
        if hasattr(self, "_app_title"):
            self._app_title.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 28pt; font-weight: bold; background: transparent;"
            )

        # Section titles
        for attr in ("_section_dac", "_section_display", "_section_weather"):
            if hasattr(self, attr):
                getattr(self, attr).apply_theme()

        # Card backgrounds
        for attr in ("_custom_display_card", "_display_elements_card", "_weather_card"):
            if hasattr(self, attr):
                getattr(self, attr).setStyleSheet(_card_style)

        # In-card labels
        if hasattr(self, "_custom_display_lbl"):
            self._custom_display_lbl.setStyleSheet(_lbl_style)
        if hasattr(self, "_weather_lbl_enable"):
            self._weather_lbl_enable.setStyleSheet(_lbl_style)
        if hasattr(self, "_weather_lbl_units"):
            self._weather_lbl_units.setStyleSheet(_lbl_style)

        # Separator in display elements card
        if hasattr(self, "_display_sep"):
            self._display_sep.setStyleSheet(
                f"color: {_theme.c('BORDER')}; background: {_theme.c('BORDER')}; max-height: 1px;"
            )

        # Custom display card
        if hasattr(self, "_custom_display_hint"):
            self._custom_display_hint.setStyleSheet(_hint_style)

        # DAC settings widget background
        if hasattr(self, "_dac_widget"):
            self._dac_widget.setStyleSheet(f"""
                QWidget {{ background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')}; }}
                QLabel {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
            """)

        # Weather status label
        if hasattr(self, "_weather_status"):
            self._weather_status.setStyleSheet(_hint_style)

        # City input
        if hasattr(self, "_city_input"):
            self._city_input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_PRIMARY')};
                    border: 1px solid {_theme.c('BORDER')}; border-radius: 6px;
                    padding: 6px 10px; font-size: 10pt;
                }}
                QLineEdit:focus {{ border-color: {_theme.c('ACCENT')}; }}
            """)

        # Weather save button
        if hasattr(self, "_weather_save_btn"):
            self._weather_save_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_theme.c('ACCENT')}; color: #fff; border: none;
                    border-radius: 6px; padding: 6px 14px; font-size: 10pt;
                }}
                QPushButton:hover {{ background-color: {_theme.c('BG_BUTTON_HOVER')}; }}
                QPushButton:disabled {{ background-color: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_SECONDARY')}; }}
            """)

        # Unit buttons — re-apply via existing method to pick the active state
        if hasattr(self, "_weather_units"):
            if hasattr(self, "_unit_btn_celsius"):
                self._unit_btn_celsius.setStyleSheet(
                    self._unit_btn_style(self._weather_units == "celsius")
                )
            if hasattr(self, "_unit_btn_fahrenheit"):
                self._unit_btn_fahrenheit.setStyleSheet(
                    self._unit_btn_style(self._weather_units == "fahrenheit")
                )

        # Checkbox and spinbox styles — rebuild dynamic style strings
        _cb_style = self._checkbox_style()
        _sp_style = self._spinbox_style()
        for cb in self._display_checkboxes.values():
            cb.setStyleSheet(_cb_style)
        for sp in self._font_spinboxes.values():
            sp.setStyleSheet(_sp_style)

        # Orderable row up/down buttons
        from PySide6.QtWidgets import QPushButton as _QPB
        _btn_style = (
            f"QPushButton {{ background: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_PRIMARY')}; "
            f"border: 1px solid {_theme.c('BORDER')}; border-radius: 4px; "
            f"font-size: 9pt; min-width: 22px; max-width: 22px; "
            f"min-height: 20px; max-height: 20px; padding: 0; }}"
            f"QPushButton:hover {{ background: {_theme.c('BG_BUTTON_HOVER')}; }}"
        )
        for row_w in self._orderable_rows.values():
            for btn in row_w.findChildren(_QPB):
                btn.setStyleSheet(_btn_style)

    # ── Builder helpers ────────────────────────────────────────────────────────

    def _build_custom_display_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet(_TOGGLE_CARD_STYLE.format(bg=BG_CARD, border=BORDER))
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        lbl = QLabel(I18n.translate("settings", "oled_custom_display"))
        lbl.setStyleSheet(_LBL_STYLE.format(color=TEXT_PRIMARY))
        self._custom_display_lbl = lbl
        row.addWidget(lbl, stretch=1)

        self._custom_display_hint = QLabel()
        self._custom_display_hint.setStyleSheet(_HINT_STYLE.format(color=TEXT_SECONDARY))
        row.addWidget(self._custom_display_hint)

        self._custom_display_toggle = ToggleSwitch()
        self._custom_display_toggle.set_checked(True)
        self._custom_display_toggle.checkbox.toggled.connect(self._on_custom_display_toggled)
        row.addWidget(self._custom_display_toggle)
        return card

    def _build_display_elements_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet(_TOGGLE_CARD_STYLE.format(bg=BG_CARD, border=BORDER))
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(2)

        self._display_checkboxes: dict[str, QCheckBox] = {}
        self._font_spinboxes: dict[str, QSpinBox] = {}
        cb_style = self._checkbox_style()
        sp_style = self._spinbox_style()

        # Fixed elements (one row per item with checkbox + font spinbox)
        for key, label_key, font_key, default_sz in _FIXED_DISPLAY_KEYS:
            fixed_row = QWidget()
            fixed_row.setStyleSheet("background: transparent;")
            fixed_hl = QHBoxLayout(fixed_row)
            fixed_hl.setContentsMargins(0, 0, 0, 2)
            fixed_hl.setSpacing(6)
            cb = QCheckBox(I18n.translate("settings", label_key))
            cb.setChecked(True)
            cb.setStyleSheet(cb_style)
            cb.toggled.connect(lambda checked, k=key: DbusWrapper.change_setting(k, checked))
            fixed_hl.addWidget(cb, stretch=1)
            self._display_checkboxes[key] = cb
            if font_key is not None:
                sp = self._build_font_spinbox(font_key, default_sz, sp_style)
                fixed_hl.addWidget(sp)
                self._font_spinboxes[font_key] = sp
            col.addWidget(fixed_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER}; background: {BORDER}; max-height: 1px;")
        self._display_sep = sep
        col.addWidget(sep)
        col.addSpacing(4)

        # Orderable elements
        self._orderable_order: list[str] = [item[0] for item in _ORDERABLE_ITEMS]
        self._orderable_rows: dict[str, QWidget] = {}
        self._orderable_sub_rows: dict[str, list[QWidget]] = {}
        self._orderable_container = QVBoxLayout()
        self._orderable_container.setSpacing(2)
        self._orderable_container.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._orderable_container)

        for order_key, setting_key, label_key, font_key, default_sz in _ORDERABLE_ITEMS:
            row = self._build_orderable_row(order_key, setting_key, label_key, font_key, default_sz, cb_style, sp_style)
            self._orderable_rows[order_key] = row
            self._orderable_container.addWidget(row)
            sub_rows: list[QWidget] = []
            for sub_key, sub_label in _ORDERABLE_SUB_OPTIONS.get(order_key, []):
                sub_row = self._build_sub_option_row(sub_key, sub_label, cb_style)
                self._orderable_container.addWidget(sub_row)
                sub_rows.append(sub_row)
            self._orderable_sub_rows[order_key] = sub_rows

        return card

    def _build_orderable_row(
        self, order_key: str, setting_key: str, label_key: str,
        font_key: str, default_sz: int, cb_style: str, sp_style: str,
    ) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {BG_BUTTON}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 4px; "
            f"font-size: 9pt; min-width: 22px; max-width: 22px; "
            f"min-height: 20px; max-height: 20px; padding: 0; }}"
            f"QPushButton:hover {{ background: {BG_BUTTON_HOVER}; }}"
        )

        btn_up = QPushButton("▲")
        btn_up.setStyleSheet(btn_style)
        btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_up.clicked.connect(lambda _, k=order_key: self._move_element(k, -1))

        btn_dn = QPushButton("▼")
        btn_dn.setStyleSheet(btn_style)
        btn_dn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dn.clicked.connect(lambda _, k=order_key: self._move_element(k, +1))

        cb = QCheckBox(I18n.translate("settings", label_key))
        cb.setChecked(True)
        cb.setStyleSheet(cb_style)
        cb.toggled.connect(lambda checked, k=setting_key: DbusWrapper.change_setting(k, checked))

        sp = self._build_font_spinbox(font_key, default_sz, sp_style)
        self._font_spinboxes[font_key] = sp

        hl.addWidget(btn_up)
        hl.addWidget(btn_dn)
        hl.addWidget(cb, stretch=1)
        hl.addWidget(sp)

        self._display_checkboxes[setting_key] = cb
        row._order_key = order_key  # type: ignore[attr-defined]
        return row

    def _build_sub_option_row(self, setting_key: str, label_key: str, cb_style: str) -> QWidget:
        """Indented checkbox row (no ▲▼, no spinbox) for sub-options of an orderable item."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(52, 1, 0, 1)
        hl.setSpacing(4)
        cb = QCheckBox(I18n.translate("settings", label_key))
        cb.setChecked(True)
        cb.setStyleSheet(cb_style)
        cb.toggled.connect(lambda checked, k=setting_key: DbusWrapper.change_setting(k, checked))
        hl.addWidget(cb, stretch=1)
        self._display_checkboxes[setting_key] = cb
        return row

    def _build_font_spinbox(self, font_key: str, default_sz: int, sp_style: str) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(7, 30)
        sp.setValue(default_sz)
        sp.setSuffix(" pt")
        sp.setFixedWidth(62)
        sp.setStyleSheet(sp_style)
        sp.valueChanged.connect(lambda v, k=font_key: DbusWrapper.change_setting(k, v))
        return sp

    def _move_element(self, order_key: str, direction: int) -> None:
        order = self._orderable_order
        idx = order.index(order_key)
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(order):
            return
        order[idx], order[new_idx] = order[new_idx], order[idx]
        self._rebuild_orderable_ui()
        DbusWrapper.change_setting('oled_display_order', order)

    def _rebuild_orderable_ui(self) -> None:
        # Detach all items from the layout without destroying them (no setParent(None)).
        while self._orderable_container.count():
            self._orderable_container.takeAt(0)
        # Re-add in new order, including sub-option rows that follow each parent.
        for key in self._orderable_order:
            self._orderable_container.addWidget(self._orderable_rows[key])
            for sub_row in self._orderable_sub_rows.get(key, []):
                self._orderable_container.addWidget(sub_row)

    def _spinbox_style(self) -> str:
        return (
            f"QSpinBox {{ background: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_PRIMARY')}; "
            f"border: 1px solid {_theme.c('BORDER')}; border-radius: 4px; "
            f"padding: 2px 4px; font-size: 9pt; }}"
            f"QSpinBox:focus {{ border-color: {_theme.c('ACCENT')}; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}"
        )

    def _checkbox_style(self) -> str:
        return (
            f"QCheckBox {{ color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; "
            f"background: transparent; spacing: 8px; padding: 6px 0px; }}"
            f"QCheckBox::indicator {{ width: 18px; height: 18px; "
            f"border: 1px solid {_theme.c('BORDER')}; border-radius: 4px; background-color: {_theme.c('BG_BUTTON')}; }}"
            f"QCheckBox::indicator:checked {{ background-color: {_theme.c('ACCENT')}; border-color: {_theme.c('ACCENT')}; }}"
        )

    def _build_weather_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet(_TOGGLE_CARD_STYLE.format(bg=BG_CARD, border=BORDER))
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(10)

        # Row 1 — enable toggle
        row1 = QWidget()
        row1.setStyleSheet("background: transparent;")
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(12)
        lbl_enable = QLabel(I18n.translate("settings", "weather_enabled"))
        lbl_enable.setStyleSheet(_LBL_STYLE.format(color=TEXT_PRIMARY))
        self._weather_lbl_enable = lbl_enable
        r1.addWidget(lbl_enable, stretch=1)
        self._weather_toggle = ToggleSwitch()
        self._weather_toggle.set_checked(False)
        self._weather_toggle.checkbox.toggled.connect(self._on_weather_toggled)
        r1.addWidget(self._weather_toggle)
        col.addWidget(row1)

        # Row 2 — city input + search button
        row2 = QWidget()
        row2.setStyleSheet("background: transparent;")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(8)

        self._city_input = QLineEdit()
        self._city_input.setPlaceholderText(I18n.translate("settings", "weather_city_placeholder"))
        self._city_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_BUTTON}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 6px;
                padding: 6px 10px; font-size: 10pt;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        self._city_input.returnPressed.connect(self._on_weather_save)
        r2.addWidget(self._city_input, stretch=1)

        self._weather_save_btn = QPushButton(I18n.translate("settings", "weather_search"))
        self._weather_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._weather_save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT}; color: #fff; border: none;
                border-radius: 6px; padding: 6px 14px; font-size: 10pt;
            }}
            QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}
            QPushButton:disabled {{ background-color: {BG_BUTTON}; color: {TEXT_SECONDARY}; }}
        """)
        self._weather_save_btn.clicked.connect(self._on_weather_save)
        r2.addWidget(self._weather_save_btn)
        col.addWidget(row2)

        # Row 3 — units toggle (°C / °F)
        row3 = QWidget()
        row3.setStyleSheet("background: transparent;")
        r3 = QHBoxLayout(row3)
        r3.setContentsMargins(0, 0, 0, 0)
        r3.setSpacing(8)

        lbl_units = QLabel(I18n.translate("settings", "weather_units"))
        lbl_units.setStyleSheet(_LBL_STYLE.format(color=TEXT_PRIMARY))
        self._weather_lbl_units = lbl_units
        r3.addWidget(lbl_units, stretch=1)

        for unit, label in [("celsius", "°C"), ("fahrenheit", "°F")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("unit_value", unit)
            btn.setStyleSheet(self._unit_btn_style(False))
            btn.clicked.connect(lambda checked, u=unit: self._on_unit_selected(u))
            r3.addWidget(btn)
            setattr(self, f"_unit_btn_{unit}", btn)

        col.addWidget(row3)

        # Row 4 — status label
        self._weather_status = QLabel("")
        self._weather_status.setStyleSheet(_HINT_STYLE.format(color=TEXT_SECONDARY))
        self._weather_status.setWordWrap(True)
        col.addWidget(self._weather_status)

        self._weather_units = "celsius"
        return card

    def _unit_btn_style(self, active: bool) -> str:
        bg = _theme.c('ACCENT') if active else _theme.c('BG_BUTTON')
        color = "#fff" if active else _theme.c('TEXT_PRIMARY')
        return (
            f"QPushButton {{ background-color: {bg}; color: {color}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; padding: 4px 12px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background-color: {_theme.c('BG_BUTTON_HOVER')}; color: {_theme.c('TEXT_PRIMARY')}; }}"
        )

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_custom_display_toggled(self, checked: bool) -> None:
        hint = I18n.translate("settings", "oled_custom_display_on" if checked else "oled_custom_display_off")
        self._custom_display_hint.setText(hint)
        DbusWrapper.change_setting("oled_custom_display", checked)

    def _on_weather_toggled(self, checked: bool) -> None:
        self._save_weather_settings()

    def _on_unit_selected(self, unit: str) -> None:
        self._weather_units = unit
        self._unit_btn_celsius.setStyleSheet(self._unit_btn_style(unit == "celsius"))
        self._unit_btn_fahrenheit.setStyleSheet(self._unit_btn_style(unit == "fahrenheit"))
        self._save_weather_settings()

    def _on_weather_save(self) -> None:
        self._weather_save_btn.setEnabled(False)
        self._weather_status.setText(I18n.translate("settings", "weather_searching"))
        self._weather_status.setStyleSheet(_HINT_STYLE.format(color=_theme.c('TEXT_SECONDARY')))
        self._save_weather_settings()

    def _save_weather_settings(self) -> None:
        DbusWrapper.set_weather_settings(
            enabled=self._weather_toggle.checkbox.isChecked(),
            location=self._city_input.text().strip(),
            units=self._weather_units,
            callback=self._on_weather_result,
        )

    def _on_weather_result(self, result: dict) -> None:
        QMetaObject.invokeMethod(
            self, "_apply_weather_result",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG("QVariant", result),
        )

    @Slot("QVariant")
    def _apply_weather_result(self, result: dict) -> None:
        self._weather_save_btn.setEnabled(True)
        if result.get("ok"):
            city = result.get("city", "")
            msg = I18n.translate("settings", "weather_found").format(city=city) if city else \
                  I18n.translate("settings", "weather_saved")
            self._weather_status.setStyleSheet(_HINT_STYLE.format(color=_theme.c('ACCENT')))
            self._weather_status.setText(msg)
        else:
            err = result.get("error", "Unknown error")
            self._weather_status.setStyleSheet(_HINT_STYLE.format(color="#FF5555"))
            self._weather_status.setText(f"{I18n.translate('settings', 'weather_not_found')}: {err}")

    @Slot(object)
    def update_settings(self, settings: dict):
        dac = settings.get('dac', {})

        # Custom display toggle
        custom = bool(dac.get('oled_custom_display', True))
        self._custom_display_toggle.checkbox.blockSignals(True)
        self._custom_display_toggle.set_checked(custom)
        self._custom_display_toggle.checkbox.blockSignals(False)
        hint = I18n.translate("settings", "oled_custom_display_on" if custom else "oled_custom_display_off")
        self._custom_display_hint.setText(hint)

        # Display element checkboxes
        for key, cb in self._display_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(dac.get(key, True)))
            cb.blockSignals(False)

        # Restore font sizes
        for font_key, sp in self._font_spinboxes.items():
            val = dac.get(font_key)
            if isinstance(val, int) and 7 <= val <= 30:
                sp.blockSignals(True)
                sp.setValue(val)
                sp.blockSignals(False)

        # Restore display order
        saved_order = dac.get('oled_display_order')
        if saved_order and isinstance(saved_order, list):
            valid = [k for k in saved_order if k in self._orderable_rows]
            # append any missing keys at the end
            for k in self._orderable_order:
                if k not in valid:
                    valid.append(k)
            if valid != self._orderable_order:
                self._orderable_order = valid
                self._rebuild_orderable_ui()

        # Weather
        self._weather_toggle.checkbox.blockSignals(True)
        self._weather_toggle.set_checked(bool(dac.get('weather_enabled', False)))
        self._weather_toggle.checkbox.blockSignals(False)

        if not self._city_input.hasFocus():
            self._city_input.setText(dac.get('weather_location', ''))
        city_display = dac.get('weather_city_display', '')
        if city_display:
            self._weather_status.setStyleSheet(_HINT_STYLE.format(color=_theme.c('ACCENT')))
            self._weather_status.setText(
                I18n.translate("settings", "weather_found").format(city=city_display)
            )

        units = dac.get('weather_units', 'celsius')
        self._weather_units = units
        self._unit_btn_celsius.setStyleSheet(self._unit_btn_style(units == "celsius"))
        self._unit_btn_fahrenheit.setStyleSheet(self._unit_btn_style(units == "fahrenheit"))

        # Sliders — exclude toggle-only settings
        sliders_config = {
            k: v for k, v in settings.get('dac_settings_config', {}).items()
            if k not in _DAC_WIDGET_EXCLUDE
        }
        self._dac_widget.update_settings({
            'settings_config': sliders_config,
            'dac': dac,
        })
