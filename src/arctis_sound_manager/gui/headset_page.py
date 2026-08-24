# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Headset page — Device info + status.
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

from arctis_sound_manager.gui.components import (
    HEADPHONE_ICON,
    DividerLine,
    SectionTitle,
    SvgIconWidget,
)
from arctis_sound_manager.gui.status_widget import QStatusWidget
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


class HeadsetPage(QWidget):
    """Page showing connected device info and live status."""

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
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}")
        self._scroll = scroll

        content = QWidget()
        content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._content = content
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(36, 28, 36, 36)
        layout.setSpacing(0)

        # Devices section
        layout.addWidget(SectionTitle(I18n.translate("ui", "devices")))
        layout.addSpacing(20)

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
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(16)

        card_layout.addWidget(SvgIconWidget(HEADPHONE_ICON, ACCENT, size=44))

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
        self._device_selector = QComboBox()
        self._device_selector.setMinimumWidth(220)
        self._device_selector.setVisible(False)
        self._device_selector.activated.connect(self._on_device_picked)
        card_layout.addWidget(self._device_selector)

        # Device ids in the order the combo shows them. The combo carries the
        # label; this carries what the setting is actually written from.
        self._device_ids: list[str] = []
        self._preferred_id: str | None = None

        layout.addWidget(self._device_card)
        layout.addSpacing(24)

        # Status widget
        self._status_widget = QStatusWidget(content)
        self._status_widget.setStyleSheet(f"""
            QWidget {{ background-color: {BG_MAIN}; color: {TEXT_PRIMARY}; }}
            QLabel  {{ background-color: transparent; color: {TEXT_PRIMARY}; font-size: 11pt; }}
        """)
        layout.addWidget(self._status_widget)
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

        self._status_widget.setStyleSheet(f"""
            QWidget {{ background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')}; }}
            QLabel  {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; }}
        """)

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
        self._status_widget.update_status(status)

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
