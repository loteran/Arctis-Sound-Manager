# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output device picker for a Sonar channel tab (Game/Chat/Media/Aux, #262).

The Channels page used to be the only place to send a channel's audio
somewhere other than the headset — a control that had nothing to do with EQ
sitting on the mixer instead of with the rest of that channel's settings.
This puts the same picker in the Equalizer tab, next to the EQ it already
lets you tune, and the Channels page combo is retired once every channel has
one here.

Deliberately simpler than :class:`~arctis_sound_manager.gui.output_selector.
OutputSelector`: no preference memory, no automatic fallback ladder. Those
exist there because the Output channel *is* its destination — losing it
means silence with nothing else to point at. These four channels already
have a destination (the headset) the moment their chosen device disappears,
so a plain "send it here, or here by default" picker is enough.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.channel_outputs import (
    channel_output_options, load_channel_outputs, set_channel_output,
)
from arctis_sound_manager.i18n import I18n

logger = logging.getLogger("ChannelOutputSelector")


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


class ChannelOutputSelector(QWidget):
    """A plain "send this channel's audio to…" combo, bound to one channel."""

    target_changed = Signal(str)

    def __init__(self, channel: str, parent=None):
        super().__init__(parent)
        self._channel = channel
        self._devices: list[tuple[str, str]] = []
        self._current = ""
        self._suppress = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._label = QLabel(_tr("output_device", "Output device:"))
        self._label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; background: transparent;")
        row.addWidget(self._label)

        self._combo = QComboBox()
        self._combo.setMinimumWidth(240)
        self._combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._combo.activated.connect(self._on_picked)
        row.addWidget(self._combo)
        row.addStretch(1)

        # Devices appear and vanish without warning — earbuds go in a case, a
        # dock is unplugged — so the list is re-read on a timer rather than
        # only when the page is built. Same cadence as OutputSelector.
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    def _available(self) -> list[tuple[str, str]]:
        try:
            import pulsectl
        except ImportError:
            return []
        try:
            with pulsectl.Pulse("asm-channel-output-selector") as pulse:
                return channel_output_options(pulse.sink_list())
        except Exception as exc:
            logger.debug("could not list outputs: %s", exc)
            return []

    def refresh(self) -> None:
        devices = self._available()
        if devices != self._devices:
            self._devices = devices
            self._rebuild_combo()

        current = load_channel_outputs().get(self._channel, "")
        if current != self._current:
            self._current = current
            self._select(current)

    def _rebuild_combo(self) -> None:
        self._suppress = True
        try:
            self._combo.clear()
            for device_id, label in self._devices:
                self._combo.addItem(label, device_id)
        finally:
            self._suppress = False
        self._select(self._current)

    def _select(self, device_id: str) -> None:
        self._suppress = True
        try:
            index = self._combo.findData(device_id)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        finally:
            self._suppress = False

    def _on_picked(self, index: int) -> None:
        if self._suppress:
            return
        device_id = self._combo.itemData(index) or ""
        self._current = device_id
        set_channel_output(self._channel, device_id or None)
        self.target_changed.emit(device_id)

    def apply_theme(self, t=None) -> None:
        self._label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; background: transparent;")

    def shutdown(self) -> None:
        self._timer.stop()
