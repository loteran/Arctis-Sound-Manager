# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headset page: what happens automatically, organised by event.

The settings that react to something — the headset connecting or
disconnecting, its mic being muted, nobody using it for a while, a bud taken
out, logging in — used to be spread over the Settings page and the Headset
page as bare switches, and what they did together, and when, was left to be
guessed. Here each event is a row: when it happens, and in a sentence built
from the current settings what will happen then. "Change" opens the same
controls as before, in place.

The controls are ordinary QSettingsWidget panels, so a change is saved
exactly as it was from the other pages; this module only decides which
events apply and words their outcome.
"""
from __future__ import annotations

import html
import os
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.components import (HEADPHONE_ICON, IMAGES_DIR, MIC_ICON,
                                                 SvgIconWidget)
from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
from arctis_sound_manager.gui.headset_cards import POWER_ICON
from arctis_sound_manager.gui.qt_widgets.q_dual_state import QDualState
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
from arctis_sound_manager.i18n import I18n

CLOCK_ICON = os.path.join(IMAGES_DIR, "clock_icon.svg")
EARBUD_ICON = os.path.join(IMAGES_DIR, "earbud_icon.svg")
LOGIN_ICON = os.path.join(IMAGES_DIR, "login_icon.svg")

# Shown here and nowhere else: the Settings page and the Headset page's own
# cards leave these out.
EVENT_GENERAL_KEYS = (
    'redirect_audio_on_connect', 'redirect_audio_on_connect_channel',
    'redirect_audio_on_disconnect', 'redirect_audio_on_disconnect_device',
    'micro_autoswitch', 'micro_alt_source', 'preset_sync_enabled',
)
EVENT_DEVICE_KEYS = ('pm_shutdown', 'auto_off_time_minutes', 'wear_sense')

# micro_autoswitch values (settings.py): 0 off, 1 on (dis)connection, 2 on
# mute, 3 both — see core.resolve_mic_autoswitch_target.
_MIC_ON_CONNECTION = (1, 3)
_MIC_ON_MUTE = (2, 3)
_REDIRECT_CHANNELS = {0: 'redirect_channel_media', 1: 'redirect_channel_game',
                      2: 'redirect_channel_chat'}


def _t(key: str, **values: str) -> str:
    text = I18n.translate('ui', key)
    return text.format(**{k: html.escape(v) for k, v in values.items()}) if values else text


def _mapped(mapping: dict | None, value: Any) -> str | None:
    """values_mapping[value], whether the keys came through as ints or (JSON) strings."""
    if not mapping:
        return None
    for key, label in mapping.items():
        try:
            if int(key) == int(value):
                return label
        except (TypeError, ValueError):
            continue
    return None


def _row_qss() -> str:
    """A row's own sheet: the card's base styles in the row's (darker) colour,
    so labels, panels and setting rows inside it don't paint card-coloured
    bands across it."""
    from arctis_sound_manager.gui.headset_cards import card_contents_qss
    c = _theme.c
    base = card_contents_qss().replace(c('BG_CARD'), c('BG_MAIN'))
    return base + f"""
        QFrame#eventRow {{
            background-color: {c('BG_MAIN')}; border: 1px solid {c('BORDER')};
            border-radius: 10px;
        }}
        QLabel#eventTitle {{ color: {c('TEXT_PRIMARY')}; font-size: 11.5pt; font-weight: bold; }}
        QLabel#eventWhen {{ color: {c('TEXT_SECONDARY')}; font-size: 9pt; }}
        QLabel#eventArrowOn {{ color: {c('ACCENT')}; font-size: 11pt; font-weight: bold; }}
        QLabel#eventArrowOff {{ color: {c('TEXT_SECONDARY')}; font-size: 11pt; font-weight: bold; }}
        QLabel#eventTextOn {{ color: {c('TEXT_PRIMARY')}; font-size: 10.5pt; }}
        QLabel#eventTextOff {{ color: {c('TEXT_SECONDARY')}; font-size: 10.5pt; }}
        QFrame#eventSep {{ background-color: {c('BORDER')}; border: none; }}
        QPushButton#eventToggle {{
            background-color: {c('BG_BUTTON')}; color: {c('TEXT_PRIMARY')};
            border: none; border-radius: 6px; padding: 4px 12px; font-size: 9pt;
        }}
        QPushButton#eventToggle:hover {{ background-color: {c('BG_BUTTON_HOVER')}; }}
    """


def _events_qss() -> str:
    return f"QLabel#eventsIntro {{ color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; }}"


class _EventRow(QFrame):
    """One event: icon, "When …", what counts as it, the outcome sentences,
    and its controls behind Change."""

    def __init__(self, icon_path: str, panels: list[QWidget], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("eventRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._active = True
        self._state: tuple | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(12)
        self._icon_path = icon_path
        self._icon = SvgIconWidget(icon_path, _theme.c('ACCENT'), size=24)
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self._title = QLabel()
        self._title.setObjectName("eventTitle")
        titles.addWidget(self._title)
        self._when = QLabel()
        self._when.setObjectName("eventWhen")
        self._when.setWordWrap(True)
        titles.addWidget(self._when)
        head.addLayout(titles, 1)
        self._toggle = QPushButton()
        self._toggle.setObjectName("eventToggle")
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self._on_toggle)
        head.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        body = QVBoxLayout()
        body.setContentsMargins(36, 4, 0, 0)
        body.setSpacing(4)
        self._outcomes = QVBoxLayout()
        self._outcomes.setSpacing(4)
        body.addLayout(self._outcomes)

        self._panel = QWidget()
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 8, 0, 0)
        panel_layout.setSpacing(2)
        sep = QFrame()
        sep.setObjectName("eventSep")
        sep.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sep.setFixedHeight(1)
        panel_layout.addWidget(sep)
        panel_layout.addSpacing(4)
        for panel in panels:
            panel_layout.addWidget(panel)
        self._panel.setVisible(False)
        body.addWidget(self._panel)
        layout.addLayout(body)
        self._update_toggle()

    def _on_toggle(self) -> None:
        self._panel.setVisible(self._panel.isHidden())
        self._update_toggle()

    def _update_toggle(self) -> None:
        if self._panel.isHidden():
            self._toggle.setText(f"{_t('events_change')} ▾")
        else:
            self._toggle.setText(f"{_t('events_close')} ▴")

    def set_content(self, title: str, when: str, outcomes: list[tuple[str, bool]]) -> None:
        """*outcomes*: (sentence, happens). One that does not happen is greyed."""
        state = (title, when, tuple(outcomes))
        if state == self._state:
            return                      # pushed every second; rebuild on change only
        self._state = state
        self._title.setText(title)
        self._when.setText(when)
        self._when.setVisible(bool(when))
        self._active = any(on for _, on in outcomes)
        self._icon.set_color(_theme.c('ACCENT') if self._active else _theme.c('TEXT_SECONDARY'))

        while self._outcomes.count():
            item = self._outcomes.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for text, on in outcomes:
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            arrow = QLabel("→")
            arrow.setObjectName("eventArrowOn" if on else "eventArrowOff")
            arrow.setFixedWidth(16)
            row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(text)
            label.setObjectName("eventTextOn" if on else "eventTextOff")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            self._outcomes.addWidget(line)

    def set_icon(self, icon_path: str) -> None:
        """Earbuds or a headset: the picture follows the device."""
        if icon_path != self._icon_path:
            self._icon_path = icon_path
            self._icon._svg_path = icon_path
            self.recolor()

    def recolor(self) -> None:
        self.setStyleSheet(_row_qss())
        self._icon.set_color(_theme.c('ACCENT') if self._active else _theme.c('TEXT_SECONDARY'))


class QEventsSection(QWidget):
    """The Headset page's Events card body. Fed the daemon's settings and
    status pushes like the rest of the page."""

    _sig_options = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings: dict = {}
        self._buds = False
        self._has_mic_status = False
        self._options: dict[str, dict[str, str]] = {}   # list name -> {id: label}
        self._requested: set[tuple[str, str]] = set()
        self._sig_options.connect(self._on_options)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        intro = QLabel(_t('events_intro'))
        intro.setObjectName("eventsIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._panels: list[QSettingsWidget] = []

        def panel(section: str, *keys: str) -> QSettingsWidget:
            widget = QSettingsWidget(None, None, section, keys=keys, compact=True)
            self._panels.append(widget)
            return widget

        self._autostart_toggle = QDualState(
            off_text=I18n.translate('settings_values', 'off'),
            on_text=I18n.translate('settings_values', 'on'),
            init_state='left', accent=True)
        self._autostart_toggle.checkStateChanged.connect(self._on_autostart_toggled)
        autostart_row = QWidget()
        ar = QHBoxLayout(autostart_row)
        ar.setContentsMargins(0, 4, 0, 4)
        autostart_label = QLabel(I18n.translate('ui', 'launch_at_startup'))
        autostart_label.setFixedWidth(200)
        ar.addWidget(autostart_label)
        ar.addWidget(self._autostart_toggle)
        ar.addStretch(1)
        self._autostart = self._read_autostart()
        self._autostart_toggle.set_state('right' if self._autostart else 'left')

        self._rows = {
            'connect': _EventRow(HEADPHONE_ICON, [
                panel('general', 'redirect_audio_on_connect', 'redirect_audio_on_connect_channel'),
                panel('general', 'micro_autoswitch', 'micro_alt_source')]),
            'disconnect': _EventRow(POWER_ICON, [
                panel('general', 'redirect_audio_on_disconnect', 'redirect_audio_on_disconnect_device'),
                panel('general', 'micro_autoswitch', 'micro_alt_source')]),
            'mute': _EventRow(MIC_ICON, [panel('general', 'micro_autoswitch', 'micro_alt_source')]),
            'idle': _EventRow(CLOCK_ICON, [panel('device', 'pm_shutdown', 'auto_off_time_minutes')]),
            'wear': _EventRow(HEADPHONE_ICON, [panel('device', 'wear_sense')]),
            'login': _EventRow(LOGIN_ICON, [autostart_row, panel('general', 'preset_sync_enabled')]),
        }
        for row in self._rows.values():
            row.setVisible(False)
            layout.addWidget(row)
        self.apply_theme()

    # ── Pushes ────────────────────────────────────────────────────────────────

    def update_settings(self, settings: dict) -> None:
        for widget in self._panels:
            widget.update_settings(settings)
        self._settings = settings
        self._refresh()

    def update_status(self, status: dict) -> None:
        headset = status.get('headset') or {}
        buds = 'left_bud_battery' in headset or 'right_bud_battery' in headset
        mic = any(isinstance(cat, dict) and 'mic_status' in cat for cat in status.values())
        if (buds, mic) != (self._buds, self._has_mic_status):
            self._buds, self._has_mic_status = buds, mic
            self._refresh()

    # ── Names for ids (output devices, microphones) ───────────────────────────

    def _label(self, list_name: str, item_id: str) -> str:
        """The name the settings lists show for *item_id*; the id itself until
        the list has arrived (asked for once per id)."""
        names = self._options.get(list_name, {})
        if item_id in names:
            return names[item_id]
        if (list_name, item_id) not in self._requested:
            self._requested.add((list_name, item_id))
            DbusWrapper.request_list_options(list_name, self._sig_options)
        return item_id

    def _on_options(self, payload: dict) -> None:
        names = {}
        for item in payload.get('list') or []:
            if isinstance(item, dict) and 'id' in item:
                names[str(item['id'])] = str(item.get('description') or item.get('name') or item['id'])
        self._options[payload.get('name', '')] = names
        self._refresh()

    # ── Wording ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        settings = self._settings
        general = settings.get('general') or {}
        device = settings.get('device') or {}
        config = settings.get('settings_config') or {}
        if not general:
            return
        who = 'buds' if self._buds else 'headset'
        rows = self._rows
        device_icon = EARBUD_ICON if self._buds else HEADPHONE_ICON
        rows['connect'].set_icon(device_icon)
        rows['wear'].set_icon(device_icon)

        mode = int(general.get('micro_autoswitch', 0) or 0)
        alt = general.get('micro_alt_source') or ''
        alt_name = self._label('pulse_audio_sources', alt) if alt else ''

        def mic_outcome(trigger_modes, text_with_mic):
            if mode not in trigger_modes:
                return _t('event_mic_unchanged'), False
            if not alt:
                return _t('event_mic_no_alt'), False
            return text_with_mic, True

        # Connects
        if general.get('redirect_audio_on_connect', True):
            channel = I18n.translate('settings_values', _REDIRECT_CHANNELS.get(
                int(general.get('redirect_audio_on_connect_channel', 0) or 0), 'redirect_channel_media'))
            sound = (_t('event_sound_to_channel', device=settings.get('device_name') or 'Arctis',
                        channel=channel), True)
        else:
            sound = (_t('event_sound_stays'), False)
        rows['connect'].set_content(
            _t(f'event_connect_title_{who}'), _t(f'event_connect_when_{who}'),
            [sound, mic_outcome(_MIC_ON_CONNECTION, _t(f'event_mic_to_headset_{who}'))])
        rows['connect'].setVisible(True)

        # Disconnects
        target = general.get('redirect_audio_on_disconnect_device') or ''
        if not general.get('redirect_audio_on_disconnect', False):
            sound = (_t('event_sound_not_moved'), False)
        elif not target:
            sound = (_t('event_sound_no_device'), False)
        else:
            sound = (_t('event_sound_to', device=self._label('pulse_audio_devices', target)), True)
        rows['disconnect'].set_content(
            _t(f'event_disconnect_title_{who}'), _t(f'event_disconnect_when_{who}'),
            [sound, mic_outcome(_MIC_ON_CONNECTION, _t('event_mic_to', mic=alt_name))])
        rows['disconnect'].setVisible(True)

        # Mic muted: only where the headset reports it (mic_status)
        rows['mute'].setVisible(self._has_mic_status)
        if self._has_mic_status:
            rows['mute'].set_content(
                _t('event_mute_title'), _t('event_mute_when'),
                [mic_outcome(_MIC_ON_MUTE, _t('event_mic_to_while_muted', mic=alt_name))])

        # Not used for a while: the profile's own auto power-off
        key = next((k for k in ('pm_shutdown', 'auto_off_time_minutes') if k in device), None)
        rows['idle'].setVisible(key is not None)
        if key is not None:
            label = _mapped((config.get(key) or {}).get('values_mapping'), device[key])
            if not label or label == 'never' or not device[key]:
                rows['idle'].set_content(_t(f'event_idle_title_never_{who}'), '',
                                         [(_t(f'event_idle_stays_on_{who}'), False)])
            else:
                time = I18n.translate('settings_values', label).lower()
                rows['idle'].set_content(_t(f'event_idle_title_{who}', time=time),
                                         _t('event_idle_when'),
                                         [(_t(f'event_idle_off_{who}'), True)])

        # A bud taken out / the headset taken off: wear detection
        rows['wear'].setVisible('wear_sense' in device)
        if 'wear_sense' in device:
            on = bool(device['wear_sense'])
            bluetooth_only = (config.get('wear_sense') or {}).get('only_over') == 'bluetooth'
            if not on:
                outcome = (_t('event_wear_nothing'), False)
            elif bluetooth_only:
                outcome = (_t('event_wear_bt_only'), False)
            else:
                outcome = (_t('event_wear_pause'), True)
            rows['wear'].set_content(_t(f'event_wear_title_{who}'),
                                     _t('event_wear_when_on' if on else 'event_wear_when_off'),
                                     [outcome])

        # Logging in
        rows['login'].set_content(_t('event_login_title'), _t('event_login_when'), [
            (_t('event_login_asm_starts'), True) if self._autostart
            else (_t('event_login_asm_no_start'), False),
            (_t('event_login_presets'), True) if general.get('preset_sync_enabled', True)
            else (_t('event_login_no_presets'), False),
        ])
        rows['login'].setVisible(True)

    # ── Start at login (not a daemon setting: a systemd/XDG autostart entry) ──

    @staticmethod
    def _read_autostart() -> bool:
        try:
            from arctis_sound_manager.autostart import autostart_enabled
            return bool(autostart_enabled())
        except Exception:  # noqa: BLE001 — unknown reads as off, as on the Settings page
            return False

    def _on_autostart_toggled(self, state: Qt.CheckState) -> None:
        from arctis_sound_manager.autostart import set_autostart
        set_autostart(state == Qt.CheckState.Checked)
        self._autostart = state == Qt.CheckState.Checked
        self._refresh()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        from arctis_sound_manager.gui.headset_cards import card_contents_qss
        self.setStyleSheet(card_contents_qss() + _events_qss())
        for widget in self._panels:
            widget.apply_theme()
        for row in self._rows.values():
            row.recolor()
