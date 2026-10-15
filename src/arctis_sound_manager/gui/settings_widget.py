# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

from threading import Lock
from typing import Callable, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

from arctis_sound_manager.config import ConfigSetting, SettingType
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.qt_widgets.no_wheel_combo import NoWheelComboBox
from arctis_sound_manager.gui.qt_widgets.q_dual_state import QDualState
import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n

# options_source values whose backing list can change while the app is
# running (external DACs / USB sound cards plugged in after startup,
# PulseAudio/PipeWire sinks appearing or disappearing). The daemon side
# (dbus_service.get_list_options) recomputes these fresh on every call, so
# re-requesting them each time the panel becomes visible is enough to pick
# up devices attached after the initial load (#106).
_REFRESHABLE_OPTION_SOURCES = frozenset({
    'external_audio_devices', 'pulse_audio_devices', 'pulse_audio_sources',
    # Which Arctis units are plugged in changes while the app runs — a dongle
    # can be plugged or unplugged at any time (issue #199).
    'connected_arctis_devices',
})


# Compact panels (the Headset page's cards): see QSettingsWidget(compact=True).
_COMPACT_MAX_BUTTONS = 4
# Characters across all of a choice's labels before it becomes a drop-down.
_COMPACT_MAX_BUTTON_TEXT = 70
_COMPACT_LABEL_WIDTH = 200
_COMPACT_SLIDER_WIDTH = 240
_COMPACT_SELECT_WIDTH = 320


def _button_group_qss() -> str:
    """Segmented buttons of a BUTTON_GROUP setting: a multi-way switch, so it
    takes the theme's toggle colours (TOGGLE_ON selected, TOGGLE_OFF the rest)."""
    return f"""
        QPushButton {{
            background-color: {_theme.c('TOGGLE_OFF')};
            color: {_theme.c('TEXT_SECONDARY')};
            border: 1px solid {_theme.c('BG_BUTTON_HOVER')};
            border-radius: 6px;
            padding: 5px 12px;
            font-size: 10pt;
        }}
        QPushButton[active=true] {{
            background-color: {_theme.c('TOGGLE_ON')};
            color: #FFFFFF;
            border: 1px solid {_theme.c('TOGGLE_ON')};
        }}
        QPushButton:hover {{
            background-color: {_theme.c('BG_BUTTON_HOVER')};
            color: {_theme.c('TEXT_PRIMARY')};
        }}
        QPushButton[active=true]:hover {{
            background-color: {_theme.c('TOGGLE_ON')};
            opacity: 0.85;
        }}
        QPushButton:disabled {{
            background-color: {_theme.c('TOGGLE_OFF')};
            color: {_theme.c('TEXT_SECONDARY')};
            border: 1px dashed {_theme.c('BORDER')};
        }}
    """


def _option_label(option: dict) -> str:
    """Display label for a SELECT entry, with an optional CPU-cost hint.

    Only the HRIR list carries ``cpu_cost`` today (#183): convolution cost
    scales with the impulse response, and the catalog spans about 1 KB to
    918 KB, so the choice quietly decides whether the surround chain keeps up
    on a busy machine. Every other option source is unaffected — no key, no
    suffix.

    Phrased as a cost, never as a quality: a bigger HRIR is not a better one,
    and "high CPU" must not read as a recommendation.
    """
    label = option.get('name', '')
    cost = option.get('cpu_cost')
    if not cost:
        return label
    return f"{label} · {I18n.get_instance().translate('settings_values', f'hrir_cpu_{cost}')}"


def status_key_names(status) -> set[str]:
    """The live status keys a device reports, for set_available_status_keys().

    GetStatus's ``{category: {key: {...}}}`` shape, flattened to the key
    names. An empty or malformed payload (no device yet, or a status shape
    from an old daemon) yields an empty set, which only ever narrows what a
    widget offers, never breaks it further.
    """
    keys: set[str] = set()
    if isinstance(status, dict):
        for category in status.values():
            if isinstance(category, dict):
                keys.update(category.keys())
    return keys


class QSettingsWidget(QWidget):
    sig_list_received = Signal(object)

    main_layout: QVBoxLayout

    title: str
    dbus_settings_section: str
    settings: dict[str, int|bool|str]
    settings_config: dict[str, ConfigSetting]

    def __init__(self, parent: QWidget, i18n_section_name: str | None, dbus_settings_section: str,
                 keys: Iterable[str] | None = None, exclude: Iterable[str] = (),
                 compact: bool = False):
        """*keys* limits the panel to those settings, shown in that order;
        *exclude* drops settings from it. A D-Bus section can so be split over
        several panels, one per group of related settings. No
        *i18n_section_name* means no title: the page supplies its own.

        *compact* sizes controls to their content instead of the row: buttons
        as wide as their label, sliders capped, and a choice of more than
        _COMPACT_MAX_BUTTONS options offered as a drop-down, so a panel fits a
        narrow card without scrolling sideways."""
        super().__init__(parent)
        self._keys: list[str] | None = list(keys) if keys is not None else None
        self._exclude = frozenset(exclude)
        self._compact = compact

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # No left/right margin so the setting rows line up with the manually
        # built toggle rows (Startup, Telemetry…) that are added straight to the
        # page layout — otherwise the widget's default ~11 px indent misaligns
        # every toggle in this section.
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        if i18n_section_name:
            title_widget = QLabel(I18n.get_instance().translate('ui', i18n_section_name))
            title_font = title_widget.font()
            title_font.setBold(True)
            title_font.setPointSize(16)
            title_widget.setFont(title_font)
            layout.addWidget(title_widget)

        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(3)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.main_layout)

        self.title = I18n.get_instance().translate('ui', i18n_section_name) if i18n_section_name else ''
        self.dbus_settings_section = dbus_settings_section
        self.settings = {}
        self._settings_widgets: dict[str, QWidget] = {}
        self._option_lists: dict[str, list[dict[str, str]]] = {}
        # Live status keys the *active device profile* actually reports (fed by
        # DevicePage.update_status via set_available_status_keys). Used to grey
        # out a BUTTON_GROUP option whose ConfigSetting declares
        # `option_requires_status` for a status key the profile doesn't map —
        # see get_widget(). Empty until the first status push, which is fine:
        # every option is simply treated as available (no dead-looking control
        # before we know better) until proven otherwise.
        self._available_status_keys: frozenset[str] = frozenset()

        self.sig_list_received.connect(self.on_options_list_received)

        self.refresh_lock = Lock()

    def _in_scope(self, name: str) -> bool:
        if name in self._exclude:
            return False
        return self._keys is None or name in self._keys

    def _ordered_settings(self):
        if self._keys is None:
            return [(k, v) for k, v in self.settings.items() if k not in self._exclude]
        return [(k, self.settings[k]) for k in self._keys if k in self.settings]

    def apply_theme(self, t=None) -> None:
        """Restyle after a theme change, in place: rebuilding the rows through
        refresh_panel() would re-read toggle states from self.settings, which
        holds booleans rather than the profile's on/off values."""
        qss = _button_group_qss()
        for btn in self.findChildren(QPushButton):
            if btn.property('active') is not None:
                btn.setStyleSheet(qss)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_device_option_lists()

    def _refresh_device_option_lists(self):
        # Re-fetch the volatile option lists (external output device,
        # redirect-on-disconnect device) every time the panel is (re)shown,
        # instead of relying solely on the startup cache in update_settings.
        # on_options_list_received() refreshes _option_lists and calls
        # refresh_panel(), which rebuilds each SELECT widget from the
        # current self.settings value, so the user's existing selection is
        # preserved even if the underlying list changed (#106).
        settings_config = getattr(self, 'settings_config', None)
        if not settings_config:
            return
        for config in settings_config.values():
            if config.type == SettingType.SELECT and getattr(config, 'options_source', None) in _REFRESHABLE_OPTION_SOURCES:
                DbusWrapper.request_list_options(config.options_source, self.sig_list_received)

    def on_options_list_received(self, option_list: dict[str, str|list[dict[str, str]]]):
        name = option_list['name']
        lst = option_list['list']

        if isinstance(name, str) and isinstance(lst, list):
            self._option_lists[name] = lst
        
        self.refresh_panel()
    
    def set_available_status_keys(self, keys) -> None:
        """Record which live status keys the active device profile reports.

        A no-op — no rebuild — when the set hasn't changed, since this is fed
        from every status poll (roughly once a second) and a full
        refresh_panel() tears down and recreates every settings row; only a
        genuine device change (or the very first status after connect)
        actually needs one.
        """
        new_keys = frozenset(keys)
        if new_keys == self._available_status_keys:
            return
        self._available_status_keys = new_keys
        if getattr(self, 'settings_config', None):
            self.refresh_panel()

    def _option_available(self, config: ConfigSetting, option_value: int) -> bool:
        """Whether a BUTTON_GROUP option can have any effect right now.

        Reads `option_requires_status` off the ConfigSetting: a mapping of
        option value -> one or more live status keys, at least one of which
        must be in `self._available_status_keys` for that option to do
        anything (see settings.py's `micro_autoswitch` for the motivating
        case — HW-1). No entry for a given value, or no attribute at all,
        means "always available" — this only ever narrows a control the
        profile would otherwise offer unconditionally.
        """
        requirements = getattr(config, 'option_requires_status', None)
        if not requirements:
            return True
        # JSON round-trips int dict keys as strings; accept either.
        required = requirements.get(option_value, requirements.get(str(option_value)))
        if not required:
            return True
        names = [required] if isinstance(required, str) else required
        return any(name in self._available_status_keys for name in names)

    def _apply_conditional_visibility(self):
        for name, widget in self._settings_widgets.items():
            config = self.settings_config.get(name)
            if config is None:
                continue
            visible_when = getattr(config, 'visible_when', None)
            if visible_when is None:
                continue
            visible = all(
                int(self.settings.get(dep_key, -1)) == int(dep_value)
                for dep_key, dep_value in visible_when.items()
            )
            widget.setVisible(visible)

        self._apply_inert_warnings()

    def _apply_inert_warnings(self):
        """Mark a setting that is switched on but cannot do anything yet.

        Some toggles only act through a companion setting: turning
        `redirect_audio_on_disconnect` on while
        `redirect_audio_on_disconnect_device` is unset makes the daemon return
        without redirecting anything, silently — which reads as "the feature
        is broken" (discussion #48). `inert_without` names the companion; when
        it has no value the row says so instead of looking armed.
        """
        for name, widget in self._settings_widgets.items():
            config = self.settings_config.get(name)
            if config is None:
                continue
            companion = getattr(config, 'inert_without', None)
            if companion is None:
                continue

            enabled = bool(self.settings.get(name))
            companion_value = self.settings.get(companion)
            inert = enabled and not companion_value

            label = widget.findChild(QLabel, 'setting_inert_note')
            if label is None:
                continue
            label.setText(
                I18n.get_instance().translate('settings_values', 'setting_inert_without')
                if inert else ''
            )
            label.setVisible(inert)

    def refresh_panel(self):
        with self.refresh_lock:
            # Clear all the previous settings
            keys_to_remove = list(self._settings_widgets.keys())
            for key in keys_to_remove:
                self._settings_widgets[key].deleteLater()
                del self._settings_widgets[key]

            # Mapp all the settings
            for name, value in self._ordered_settings():
                if not name in self._settings_widgets:
                    config = self.settings_config.get(name)
                    if config is None or getattr(config, 'hidden', False) or config.type is None:
                        continue
                    widget = self.get_widget(config, value, self.on_settings_updated)

                    if widget is None:
                        continue

                    self._settings_widgets[name] = widget
                    self.main_layout.addWidget(self._settings_widgets[name])

            self._apply_conditional_visibility()
    
    def update_settings(self, new_settings: dict):
        self.settings_config = {}
        for config_name, kwargs in new_settings.get('settings_config', {}).items():
            if not self._in_scope(config_name):
                continue
            self.settings_config[config_name] = ConfigSetting(name=config_name, **kwargs)
            if self.settings_config[config_name].type == SettingType.SELECT \
                and self.settings_config[config_name].options_source not in self._option_lists:
                DbusWrapper.request_list_options(self.settings_config[config_name].options_source, self.sig_list_received)

        settings: dict[str, int|bool|str]|None = new_settings.get(self.dbus_settings_section, None)
        if settings is None or settings == self.settings:
            return

        # Clear all the previous settings that don't apply anymore (device disconnected? new device? etc)
        remove_keys = [key for key in self._settings_widgets if key not in settings]
        for key in remove_keys:
            self._settings_widgets[key].deleteLater()
            del self._settings_widgets[key]
        
        self.settings = settings

        self.refresh_panel()


    def on_settings_updated(self, config: ConfigSetting, value: int|str|bool):
        self.settings[config.name] = value
        self._apply_conditional_visibility()

        dbus_value = value
        if config.type == SettingType.TOGGLE:
            dbus_value = config.values.get('on', True) if value else config.values.get('off', False)

        DbusWrapper.change_setting(config.name, dbus_value)

    def get_widget(self, config: ConfigSetting, value: bool|str|int, callback: Callable) -> QWidget|None:
        main_widget = QWidget()
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 4, 0, 4)
        main_widget.setLayout(main_layout)

        widget: QWidget|None = None
        if config.type == SettingType.TOGGLE:
            widget = QDualState(
                off_text=I18n.get_instance().translate('settings_values', config.values.get('off_label', 'off')),
                on_text=I18n.get_instance().translate('settings_values', config.values.get('on_label', 'on')),
                init_state='right' if value == config.values.get('on', True) else 'left',
                accent=(config.values.get('off_label', 'off'), config.values.get('on_label', 'on')) == ('off', 'on'),
            )
            widget.checkStateChanged.connect(lambda state: callback(config, state == Qt.CheckState.Checked))
        elif config.type == SettingType.SLIDER:
            widget = QWidget()
            widget_layout = QHBoxLayout()
            widget_layout.setContentsMargins(0, 0, 0, 0)
            widget.setLayout(widget_layout)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(config.min)
            slider.setMaximum(config.max)
            slider.setSingleStep(config.step)
            slider.setValue(int(float(value)))
            if self._compact:
                slider.setFixedWidth(_COMPACT_SLIDER_WIDTH)
            widget_layout.addWidget(slider)

            def slider_value_callback(config: ConfigSetting) -> Callable[[bool|str|int], str]:
                def get_slider_value(value: bool|str|int) -> str:
                    return I18n.get_instance().translate(
                        'settings_values',
                        config.get_kwargs().get('values_mapping', {}).get(f'{value}', value)
                    )

                return get_slider_value

            slider_value = slider_value_callback(config)
            widget_value_label = QLabel(slider_value(value))
            widget_value_label.setFixedWidth(80)
            widget_layout.addWidget(widget_value_label)

            slider.valueChanged.connect(lambda value: widget_value_label.setText(slider_value(value)))
            slider.valueChanged.connect(lambda value: callback(config, value))
            if self._compact:
                widget_value_label.setFixedWidth(40)
                widget_layout.addStretch(1)
        elif (config.type == SettingType.BUTTON_GROUP and self._compact
              and self._too_long_for_buttons(config)):
            widget = self._button_group_as_combo(config, value, callback)
        elif config.type == SettingType.BUTTON_GROUP:
            widget = QWidget()
            widget_layout = QHBoxLayout()
            widget_layout.setContentsMargins(0, 0, 0, 0)
            widget_layout.setSpacing(4)
            widget.setLayout(widget_layout)


            values_mapping: dict = getattr(config, 'values_mapping', {})

            def parse_key(k) -> int:
                return int(k, 16) if isinstance(k, str) and k.startswith('0x') else int(k)

            current_value = parse_key(value) if isinstance(value, str) else int(value)
            btn_entries: list[tuple[int, QPushButton]] = []

            for raw_key, label_key in values_mapping.items():
                btn_value = parse_key(raw_key)
                label = I18n.get_instance().translate('settings_values', label_key)
                btn = QPushButton(label)
                btn.setProperty('active', btn_value == current_value)
                btn.setStyleSheet(_button_group_qss())
                # Grey out (never hide) an option the active device profile can
                # never make do anything — e.g. micro_autoswitch's "mute"
                # trigger on a headset whose profile doesn't map mic_status
                # (HW-1). Kept selectable-looking (shown as active) if it's
                # already the stored value, so a setting made on a different
                # headset isn't hidden away or silently overwritten; it just
                # can't be (re)selected from here while this device is active.
                if not self._option_available(config, btn_value):
                    btn.setEnabled(False)
                    btn.setToolTip(I18n.get_instance().translate('settings_values', 'option_unavailable_status'))
                if self._compact:
                    # As wide as the label, never narrower: a squeezed button
                    # clips its text ("hen headset disconnec").
                    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    btn.setMinimumWidth(64)
                widget_layout.addWidget(btn)
                btn_entries.append((btn_value, btn))
            if self._compact:
                widget_layout.addStretch(1)

            def make_btn_callback(selected_value: int, entries: list, cfg: ConfigSetting):
                def on_click():
                    callback(cfg, selected_value)
                    for v, b in entries:
                        b.setProperty('active', v == selected_value)
                        b.style().unpolish(b)
                        b.style().polish(b)
                return on_click

            for btn_value, btn in btn_entries:
                btn.clicked.connect(make_btn_callback(btn_value, btn_entries, config))

        elif config.type == SettingType.SELECT:
            widget = NoWheelComboBox()
            options = self._option_lists.get(config.options_source, [])
            if options:
                widget.addItems([_option_label(o) for o in options])
                option = next((o for o in options if o['id'] == value), None)
                # Only show a selection when the saved value actually matches an
                # available option. Previously we fell back to options[0] and
                # displayed it as if selected, even when nothing valid was stored
                # — so a USB device whose node.nick was never persisted (or had
                # changed) looked configured while the daemon read None and the
                # "redirect on disconnect" fallback silently did nothing (#97).
                # Leaving the index at -1 makes the unset state honest.
                widget.setCurrentIndex(options.index(option) if option is not None else -1)
            def _on_select_change(index: int, cfg=config) -> None:
                options = self._option_lists.get(cfg.options_source, [])
                if 0 <= index < len(options):
                    callback(cfg, options[index]['id'])
            # 'activated' fires on every user pick — including re-selecting the
            # item that is already current — so the value is always persisted.
            # 'currentIndexChanged' only fired on an index *change*, so picking
            # the already-displayed device saved nothing (#97).
            widget.activated.connect(_on_select_change)
            if self._compact:
                widget.setMinimumWidth(200)
                widget.setMaximumWidth(_COMPACT_SELECT_WIDTH)
        else:
            widget = QLabel(f'UNKNOWN TYPE: {config.type}')

        if widget:
            label = QLabel(I18n.get_instance().translate('settings', config.name))
            label.setFixedWidth(_COMPACT_LABEL_WIDTH if self._compact else 260)
            label.setWordWrap(True)
            main_layout.addWidget(label)
            if config.type == SettingType.TOGGLE:
                main_layout.addWidget(widget)
                if getattr(config, 'inert_without', None):
                    # Filled in by _apply_inert_warnings() whenever the toggle
                    # is on and its companion setting is still unset: the
                    # daemon would silently do nothing, and the row must say
                    # so rather than look armed (discussion #48).
                    note = QLabel('')
                    note.setObjectName('setting_inert_note')
                    note.setWordWrap(True)
                    note.setStyleSheet(f"color: {_theme.c('ACCENT')};")
                    note.setVisible(False)
                    main_layout.addWidget(note, 1)
                else:
                    main_layout.addStretch(1)
            elif self._compact:
                # Sized to its content and kept against the label; with a
                # stretch factor a capped drop-down got centred in the row.
                main_layout.addWidget(widget)
                main_layout.addStretch(1)
            else:
                main_layout.addWidget(widget, 1)

        return main_widget if widget else None

    def _too_long_for_buttons(self, config: ConfigSetting) -> bool:
        """A choice a compact row offers as a drop-down: too many options,
        or labels too long to sit side by side in a narrow card."""
        mapping = getattr(config, 'values_mapping', {})
        if len(mapping) > _COMPACT_MAX_BUTTONS:
            return True
        translate = I18n.get_instance().translate
        text = sum(len(translate('settings_values', label)) for label in mapping.values())
        return text > _COMPACT_MAX_BUTTON_TEXT

    def _button_group_as_combo(self, config: ConfigSetting, value, callback: Callable) -> QWidget:
        """A BUTTON_GROUP with too many options for a row of buttons (ten
        auto power-off delays), offered as a drop-down instead. Same values,
        same unavailable-option rule, same callback."""
        def parse_key(k) -> int:
            return int(k, 16) if isinstance(k, str) and k.startswith('0x') else int(k)

        values = [parse_key(k) for k in config.values_mapping]
        combo = NoWheelComboBox()
        for raw_key, label_key in config.values_mapping.items():
            combo.addItem(I18n.get_instance().translate('settings_values', label_key))
        current = parse_key(value) if isinstance(value, str) else int(value)
        combo.setCurrentIndex(values.index(current) if current in values else -1)
        model = combo.model()
        for index, option in enumerate(values):
            if not self._option_available(config, option):
                model.item(index).setEnabled(False)
        combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        combo.setMinimumWidth(160)
        combo.activated.connect(lambda index: callback(config, values[index]))

        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(combo)
        holder_layout.addStretch(1)
        return holder
