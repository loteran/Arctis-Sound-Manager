# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Headset page — device info, live status and the headset's own settings.
"""
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper

from arctis_sound_manager.gui.anc_widget import QAncWidget
from arctis_sound_manager.gui.components import (
    HEADPHONE_ICON,
    SectionTitle,
    SvgIconWidget,
)
from arctis_sound_manager.gui.headset_cards import (
    SettingsCard,
    StatusTiles,
    card_contents_qss,
    section_title,
)
from arctis_sound_manager.gui.qt_widgets.no_wheel_combo import NoWheelComboBox
from arctis_sound_manager.gui.settings_widget import QSettingsWidget, status_key_names
import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from arctis_sound_manager.i18n import I18n

_CHATMIX_WIDTH = 340

# The base station's settings have their own panel on the DAC page.
_SECTIONS_SHOWN_ELSEWHERE = frozenset({'station'})


class HeadsetPage(QWidget):
    """Page showing the connected headset, its live status and its settings."""

    # Emitted with the raw option list, so the fetch can stay off the UI thread
    # the way settings_widget does it.
    sig_list_received = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Everything here is sized to fit the window; a sideways scrollbar
        # would only ever mean a control asking for more than it needs.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._scroll = scroll

        content = QWidget()
        content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._content = content
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(28, 20, 28, 24)
        layout.setSpacing(0)

        # Devices section: the title, and on its right the headset picker
        # when more than one is plugged in (see _device_selector below).
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(SectionTitle(I18n.translate("ui", "devices")))
        header.addStretch(1)
        layout.addLayout(header)
        layout.addSpacing(12)

        # Device card
        self._device_card = QWidget()
        self._device_card.setObjectName("deviceCard")
        self._device_card.setStyleSheet(f"""
            QWidget#deviceCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)

        card_layout = QHBoxLayout(self._device_card)
        card_layout.setContentsMargins(16, 10, 16, 10)
        card_layout.setSpacing(12)

        card_layout.addWidget(SvgIconWidget(HEADPHONE_ICON, ACCENT, size=32))

        device_info = QWidget()
        device_info.setStyleSheet("background: transparent;")
        di_layout = QVBoxLayout(device_info)
        di_layout.setContentsMargins(0, 0, 0, 0)
        di_layout.setSpacing(2)

        self._device_name_label = QLabel(I18n.translate("ui", "no_device"))
        self._device_name_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 12pt; font-weight: bold; background: transparent;"
        )
        di_layout.addWidget(self._device_name_label)

        self._vendor_label = QLabel("")
        self._vendor_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        di_layout.addWidget(self._vendor_label)

        self._product_label = QLabel("")
        self._product_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        di_layout.addWidget(self._product_label)

        card_layout.addWidget(device_info)
        card_layout.addStretch(1)

        # Which headset ASM drives, when more than one is plugged in (#199).
        # The same setting as the one in Settings, not a copy of it: both write
        # through change_setting and both read their current value out of the
        # settings payload, which the daemon pushes every second. So whichever
        # one you use, the other follows without either knowing about it.
        #
        # Hidden while there is nothing to choose. A picker offering a single
        # item is not a choice, and this card is the first thing you see.
        #
        # In the header rather than the card: the card's right side holds the
        # ChatMix dial, and the two together made it wider than the window.
        self._device_selector = NoWheelComboBox()
        self._device_selector.setMinimumWidth(220)
        self._device_selector.setPlaceholderText(I18n.translate("settings", "preferred_device"))
        self._device_selector.setVisible(False)
        self._device_selector.activated.connect(self._on_device_picked)
        header.addWidget(self._device_selector, 0, Qt.AlignmentFlag.AlignVCenter)

        # Device ids in the order the combo shows them. The combo carries the
        # label; this carries what the setting is actually written from.
        self._device_ids: list[str] = []
        self._preferred_id: str | None = None

        layout.addWidget(self._device_card)
        layout.addSpacing(8)

        # Live status, one tile per value the headset reports.
        self._status_tiles = StatusTiles(content)
        self._status_tiles.setVisible(False)
        layout.addWidget(self._status_tiles)

        # The ChatMix dial goes in the device card's empty right-hand side, at
        # the length of a dial rather than of the page.
        chatmix = self._status_tiles.chatmix
        chatmix.setParent(self._device_card)
        chatmix.embed(_CHATMIX_WIDTH)
        chatmix.setVisible(False)
        card_layout.addWidget(chatmix)
        layout.addSpacing(16)

        # ── The headset's own settings ────────────────────────────────────────
        # Sidetone, mic volume, the volume limiter, auto power-off… everything
        # the device profile declares, one card per section of the profile.
        # They used to sit in Settings among the app's own options, where
        # nobody looking at their headset found them.
        #
        # Noise cancelling first: on a headset that has it, it is the control
        # people reach for most. Hidden wholesale on the ones without it (the
        # Nova 7P, for one) — dead controls read as a broken app rather than as
        # an absent feature. Shown only once the daemon reports the setting.
        self._anc_widget = QAncWidget()
        self._anc_section = SettingsCard(
            I18n.translate("ui", "noise_cancelling"), self._anc_widget, content)
        self._anc_section.setVisible(False)
        layout.addWidget(self._anc_section)
        layout.addSpacing(8)

        # Rebuilt only when the device's set of sections changes; see
        # _sync_setting_cards().
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        layout.addLayout(self._cards_layout)
        self._setting_cards: list[SettingsCard] = []
        self._card_groups: list[tuple[str | None, tuple[str, ...]]] = []
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # The list arrives on this signal, from the fetch started in showEvent.
        self.sig_list_received.connect(self.on_options_list_received)

        # Apply the currently-active theme on first paint.
        self.apply_theme()

    # ── Theme propagation ─────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle the headset page for the current active theme."""
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        self._device_card.setStyleSheet(f"""
            QWidget#deviceCard {{
                background-color: {_theme.c('BG_CARD')};
                border: 1px solid {_theme.c('BORDER')};
                border-radius: 12px;
            }}
        """)

        self._device_name_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 12pt; font-weight: bold; background: transparent;"
        )
        self._vendor_label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        self._product_label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )

        self._device_selector.setStyleSheet(card_contents_qss())
        self._status_tiles.apply_theme()
        self._anc_section.apply_theme()
        for card in self._setting_cards:
            card.apply_theme()

    # ── preferred device (#199) ───────────────────────────────────────────────

    def showEvent(self, event):
        """Ask for the device list whenever the page comes back into view.

        Devices are plugged and unplugged while the app is open, and a list
        fetched once at startup would still be offering a dongle that left.
        """
        super().showEvent(event)
        DbusWrapper.request_list_options(
            'connected_arctis_devices', self.sig_list_received)

    @Slot(object)
    def on_options_list_received(self, option_list: dict) -> None:
        if option_list.get('name') != 'connected_arctis_devices':
            return
        options = option_list.get('list') or []
        if not isinstance(options, list):
            return

        self._device_ids = [str(o.get('id', '')) for o in options]
        self._device_selector.blockSignals(True)
        self._device_selector.clear()
        for opt in options:
            self._device_selector.addItem(str(opt.get('name') or opt.get('id', '')))
        self._select_preferred()
        self._device_selector.blockSignals(False)
        # One device is not a choice; none means nothing to choose between.
        self._device_selector.setVisible(len(options) > 1)

    def _select_preferred(self) -> None:
        """Point the combo at the setting's current value.

        An unset preference, or one naming a device that is not plugged in
        right now, leaves the index at -1 rather than silently showing the
        first entry as though it had been chosen.
        """
        if self._preferred_id and self._preferred_id in self._device_ids:
            self._device_selector.setCurrentIndex(
                self._device_ids.index(self._preferred_id))
        else:
            self._device_selector.setCurrentIndex(-1)

    def _on_device_picked(self, index: int) -> None:
        if not (0 <= index < len(self._device_ids)):
            return
        device_id = self._device_ids[index]
        if device_id == self._preferred_id:
            return
        self._preferred_id = device_id
        # The daemon persists it and re-runs device selection; the Settings tab
        # reads the new value off the next settings push.
        DbusWrapper.change_setting('preferred_device', device_id)

    @Slot(object)
    def update_status(self, status: dict):
        self._status_tiles.update_status(status)
        self._anc_widget.update_status(status)
        keys = status_key_names(status)
        for card in self._setting_cards:
            card.body.set_available_status_keys(keys)

    @Slot(object)
    def update_settings(self, settings: dict):
        vendor_id   = settings.get("vendor_id", "")
        product_id  = settings.get("product_id", "")
        device_name = settings.get("device_name", "")
        if device_name:
            self._device_name_label.setText(device_name)
            from arctis_sound_manager.telemetry import maybe_send
            maybe_send(device_name, str(product_id))
        if vendor_id:
            self._vendor_label.setText(f"Vendor ID:   {vendor_id}")
        if product_id:
            self._product_label.setText(f"Product ID:  {product_id}")

        # Changed in Settings (or by another instance) — follow it. This is the
        # whole of the synchronisation: both pickers read the same value out of
        # the same payload, so neither has to be told the other exists.
        preferred = (settings.get('general') or {}).get('preferred_device')
        preferred = str(preferred) if preferred else None
        if preferred != self._preferred_id:
            self._preferred_id = preferred
            self._device_selector.blockSignals(True)
            self._select_preferred()
            self._device_selector.blockSignals(False)

        self._sync_setting_cards(settings)
        for card in self._setting_cards:
            card.body.update_settings(settings)
        self._update_anc_visibility(settings)

    def _sync_setting_cards(self, settings: dict) -> None:
        """One card per section of the device profile, in the profile's order.

        The noise cancelling setting has its own card above, and the base
        station's settings belong to the DAC page. A daemon too old to send
        each setting's section gets everything in a single card.
        """
        values = settings.get('device') or {}
        groups: dict[str | None, list[str]] = {}
        for name, config in (settings.get('settings_config') or {}).items():
            if name not in values or not isinstance(config, dict):
                continue
            section = config.get('section')
            if section in _SECTIONS_SHOWN_ELSEWHERE:
                continue
            if config.get('hidden') or not config.get('type'):
                continue
            groups.setdefault(section, []).append(name)
        wanted = [(section, tuple(names)) for section, names in groups.items()]
        if wanted == self._card_groups:
            return

        for card in self._setting_cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._setting_cards = []
        for section, names in wanted:
            body = QSettingsWidget(None, None, "device", keys=names, compact=True)
            card = SettingsCard(section_title(section), body, self._content)
            self._cards_layout.addWidget(card)
            self._setting_cards.append(card)
        self._card_groups = wanted

    def _update_anc_visibility(self, settings: dict) -> None:
        """Show the ANC section only for headsets that actually have it.

        The device profile is the authority: a headset without noise
        cancelling simply doesn't declare the setting, so its controls could
        never do anything. An empty config means "no device yet" — keep the
        section as it is rather than flashing it away on a transient update.
        """
        config = settings.get('settings_config') or {}
        if not config:
            return
        self._anc_section.setVisible('noise_cancelling' in config)
