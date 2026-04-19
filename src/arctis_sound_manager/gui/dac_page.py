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
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import SectionTitle
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.home_page import ToggleSwitch
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
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

# Fixed top elements (always shown, not orderable)
_FIXED_DISPLAY_KEYS = [
    ('oled_show_time',    'oled_show_time'),
    ('oled_show_battery', 'oled_show_battery'),
]

# Orderable elements below the time/battery row
_ORDERABLE_ITEMS = [
    ('profile', 'oled_show_profile', 'oled_show_profile'),
    ('eq',      'oled_show_eq',      'oled_show_eq'),
    ('weather', 'weather_enabled',   'weather_enabled'),
]

_DAC_WIDGET_EXCLUDE = {
    'oled_custom_display',
    'oled_show_time', 'oled_show_battery', 'oled_show_profile',
    'oled_show_eq',
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
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}")

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_MAIN};")
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(36, 28, 36, 36)
        layout.setSpacing(0)

        app_title = QLabel(I18n.translate("ui", "app_name"))
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(app_title)
        layout.addSpacing(28)

        layout.addWidget(SectionTitle(I18n.translate("ui", "dac_settings")))
        layout.addSpacing(12)

        # ── Custom display toggle ──────────────────────────────────────────────
        layout.addWidget(self._build_custom_display_card())
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
        layout.addWidget(SectionTitle(I18n.translate("ui", "oled_display_elements")))
        layout.addSpacing(12)
        layout.addWidget(self._build_display_elements_card())
        layout.addSpacing(24)

        # ── Weather section ────────────────────────────────────────────────────
        layout.addWidget(SectionTitle(I18n.translate("ui", "weather_settings")))
        layout.addSpacing(12)
        layout.addWidget(self._build_weather_card())

        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Builder helpers ────────────────────────────────────────────────────────

    def _build_custom_display_card(self) -> QWidget:
        card = QWidget()
        card.setStyleSheet(_TOGGLE_CARD_STYLE.format(bg=BG_CARD, border=BORDER))
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        lbl = QLabel(I18n.translate("settings", "oled_custom_display"))
        lbl.setStyleSheet(_LBL_STYLE.format(color=TEXT_PRIMARY))
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
        cb_style = self._checkbox_style()

        # Fixed elements (2 columns)
        fixed_row = QWidget()
        fixed_row.setStyleSheet("background: transparent;")
        fixed_hl = QHBoxLayout(fixed_row)
        fixed_hl.setContentsMargins(0, 0, 0, 4)
        fixed_hl.setSpacing(0)
        for key, label_key in _FIXED_DISPLAY_KEYS:
            cb = QCheckBox(I18n.translate("settings", label_key))
            cb.setChecked(True)
            cb.setStyleSheet(cb_style)
            cb.toggled.connect(lambda checked, k=key: DbusWrapper.change_setting(k, checked))
            fixed_hl.addWidget(cb, stretch=1)
            self._display_checkboxes[key] = cb
        col.addWidget(fixed_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER}; background: {BORDER}; max-height: 1px;")
        col.addWidget(sep)
        col.addSpacing(4)

        # Orderable elements
        self._orderable_order: list[str] = [item[0] for item in _ORDERABLE_ITEMS]
        self._orderable_rows: dict[str, QWidget] = {}
        self._orderable_container = QVBoxLayout()
        self._orderable_container.setSpacing(2)
        self._orderable_container.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._orderable_container)

        for order_key, setting_key, label_key in _ORDERABLE_ITEMS:
            row = self._build_orderable_row(order_key, setting_key, label_key, cb_style)
            self._orderable_rows[order_key] = row
            self._orderable_container.addWidget(row)

        return card

    def _build_orderable_row(self, order_key: str, setting_key: str, label_key: str, cb_style: str) -> QWidget:
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

        hl.addWidget(btn_up)
        hl.addWidget(btn_dn)
        hl.addWidget(cb, stretch=1)

        self._display_checkboxes[setting_key] = cb
        row._order_key = order_key  # type: ignore[attr-defined]
        return row

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
        # Remove all widgets from container then re-add in new order
        while self._orderable_container.count():
            item = self._orderable_container.takeAt(0)
            if item.widget():
                item.widget().setParent(None)  # type: ignore[arg-type]
        for key in self._orderable_order:
            self._orderable_container.addWidget(self._orderable_rows[key])

    def _checkbox_style(self) -> str:
        return (
            f"QCheckBox {{ color: {TEXT_PRIMARY}; font-size: 11pt; "
            f"background: transparent; spacing: 8px; padding: 6px 0px; }}"
            f"QCheckBox::indicator {{ width: 18px; height: 18px; "
            f"border: 1px solid {BORDER}; border-radius: 4px; background-color: {BG_BUTTON}; }}"
            f"QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}"
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
        bg = ACCENT if active else BG_BUTTON
        color = "#fff" if active else TEXT_PRIMARY
        return (
            f"QPushButton {{ background-color: {bg}; color: {color}; border: 1px solid {BORDER}; "
            f"border-radius: 6px; padding: 4px 12px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; color: {TEXT_PRIMARY}; }}"
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
        self._weather_status.setStyleSheet(_HINT_STYLE.format(color=TEXT_SECONDARY))
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
            self._weather_status.setStyleSheet(_HINT_STYLE.format(color=ACCENT))
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

        self._city_input.setText(dac.get('weather_location', ''))
        city_display = dac.get('weather_city_display', '')
        if city_display:
            self._weather_status.setStyleSheet(_HINT_STYLE.format(color=ACCENT))
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
