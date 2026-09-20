# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output device picker for the Sonar Output channel.

The Output channel already had a target, but no way to see or change it from
the Sonar page — the device lived in Settings, and the Sonar side only read it.
That is fine until the device is something that comes and goes: someone on
Bluetooth earbuds has no indication of what the channel is currently on, no way
to switch, and nothing to fall back to when the earbuds go quiet.

This widget shows the live target, lets the user pick another, and leans on
:class:`~arctis_sound_manager.output_memory.OutputMemory` so a disappearing
device falls back to the headset and is restored the moment it returns.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.output_memory import OutputMemory

logger = logging.getLogger("OutputSelector")

#: How many refresh passes a pick may stay ahead of the settings file
#: before the widget stops believing it (5s each).
_PENDING_MAX_TICKS = 6


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


class OutputSelector(QWidget):
    """Live output picker with automatic fallback and memory."""

    target_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._memory = OutputMemory.load()
        self._devices: list[tuple[str, str]] = []   # (id, label)
        # Labels outlive the device list on purpose: the fallback note names
        # the device being waited for, and that device is by definition no
        # longer present to be asked for its name.
        self._labels: dict[str, str] = {}
        self._current: str | None = None
        #: True while the combo shows the device the settings ask for, as
        #: opposed to a fallback the memory picked. Keeps the "waiting
        #: for …" note off a device that is not a stand-in at all.
        self._on_configured = False
        #: A pick that has not reached the settings file yet. Persisting
        #: goes out over D-Bus on a worker thread, so it lands some time
        #: after the click — and this widget re-reads the settings every
        #: 5s. Without holding the choice, that read still sees the old
        #: value and puts it back, which looks exactly like the Equalizer
        #: refusing to change.
        self._pending_pick: str | None = None
        self._pending_ticks = 0
        self._synced = False
        self._suppress = False

        # Fixed, not Preferred (the QWidget default): the Output tab's
        # settings card ends in an addStretch that already claims any
        # leftover vertical space, so this happened to render at its
        # natural height there — but relying on a sibling card to win that
        # race is fragile. Fixed makes it explicit and keeps this row's
        # height independent of whatever else is on the tab.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

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

        self._note = QLabel("")
        self._note.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; "
            f"background: transparent;")
        row.addWidget(self._note)
        row.addStretch(1)

        # Devices appear and vanish without warning — earbuds go in a case, a
        # dock is unplugged — so the list is re-read on a timer rather than only
        # when the page is built.
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    # ── device list ───────────────────────────────────────────────────────────

    def _available(self) -> list[tuple[str, str]]:
        """Every output the user may route to, as (id, label).

        Uses the same predicate as the rest of the app with allow_headset=True:
        this is a deliberate user choice, so the headset belongs in the list
        (issue #139) — and it is also the device the fallback needs to reach.
        """
        try:
            import pulsectl
            from arctis_sound_manager.pw_utils import is_external_output_sink
        except ImportError:
            return []

        devices: list[tuple[str, str]] = []
        try:
            with pulsectl.Pulse("asm-output-selector") as pulse:
                for sink in pulse.sink_list():
                    if not is_external_output_sink(sink, allow_headset=True):
                        continue
                    props = sink.proplist
                    label = (props.get("node.description")
                             or props.get("node.nick")
                             or getattr(sink, "description", "")
                             or sink.name)
                    devices.append((sink.name, label))
        except Exception as exc:
            logger.debug("could not list outputs: %s", exc)
        return devices

    def _headset_id(self) -> str | None:
        """The SteelSeries sink, which is what fallback aims for."""
        for device_id, _ in self._devices:
            if "SteelSeries" in device_id:
                return device_id
        return None

    def _configured_target(self) -> str | None:
        """The device the rest of ASM is set to, when it is present.

        ``external_output_device`` is the setting the Settings page and the
        Channels tab's Output card both write, and the one the daemon reads to
        aim the Output chain. This widget used to resolve purely from
        :class:`OutputMemory`, which nothing else writes — so a device picked
        anywhere else never reached this combo, and the two disagreed silently.

        The stored value may be a ``node.nick`` (that is what the Channels card
        saves) or a ``node.name`` (what this combo uses as its item data), so
        both are matched and the ``node.name`` is returned — the id the combo
        is keyed on.

        ``None`` when nothing is configured, or when the configured device is
        not in the graph right now; the memory's fallback then takes over,
        which is exactly what it is for.
        """
        try:
            import pulsectl
        except ImportError:
            return None

        from pathlib import Path

        from arctis_sound_manager.constants import SETTINGS_FOLDER

        settings_file = Path(SETTINGS_FOLDER) / "general_settings.yaml"
        if not settings_file.exists():
            return None
        try:
            from ruamel.yaml import YAML
            nick = (YAML(typ="safe").load(settings_file) or {}).get(
                "external_output_device")
        except Exception:
            return None
        if not nick:
            return None

        try:
            with pulsectl.Pulse("asm-output-configured") as pulse:
                for sink in pulse.sink_list():
                    if nick in (sink.name, sink.proplist.get("node.nick", "")):
                        return sink.name
        except Exception as exc:
            logger.debug("could not resolve the configured output: %s", exc)
        return None

    def refresh(self) -> None:
        devices = self._available()
        if devices != self._devices:
            self._devices = devices
            self._labels.update(dict(devices))
            self._rebuild_combo()

        # What the rest of ASM is configured for wins over this widget's own
        # memory. The memory is the fallback ladder for a device that has gone
        # away, not a second place to keep the user's choice.
        configured = self._configured_target()
        if self._pending_pick is not None:
            if configured == self._pending_pick:
                self._pending_pick = None      # persisted; back to normal
                self._pending_ticks = 0
            else:
                self._pending_ticks += 1
                # Give up eventually: if the write never lands (daemon
                # gone, setting refused), holding the pick for ever would
                # hide the real state instead of showing it.
                if self._pending_ticks > _PENDING_MAX_TICKS:
                    self._pending_pick = None
                    self._pending_ticks = 0
                else:
                    configured = self._pending_pick
        self._on_configured = configured is not None
        resolved = configured or self._memory.resolve(
            [d for d, _ in self._devices], fallback=self._headset_id())
        if resolved != self._current:
            first = self._current is None and not self._synced
            self._current = resolved
            self._select(resolved)
            # A change that came *from* the settings is already applied: the
            # daemon aims the Output chain from the same value, and whoever
            # wrote it has done the work. Emitting here would send it straight
            # back out and restart the filter-chain for nothing, every time the
            # device is changed from the Channels tab or from Settings.
            if configured is not None and resolved == configured:
                self._synced = True
                self._update_note()
                return
            # Every emission ends in a filter-chain restart, and this runs on a
            # timer — so emitting for the initial resolve would rebuild the
            # chain (and interrupt audio) simply because the page was opened,
            # once per launch and again on every theme or tab rebuild. The first
            # resolve only synchronises the combo with what is already
            # configured; nothing has changed yet, so nothing should be applied.
            if resolved and not first:
                logger.info("output resolved to %s", resolved)
                self.target_changed.emit(resolved)
            self._synced = True
        self._update_note()

    def _rebuild_combo(self) -> None:
        self._suppress = True
        try:
            self._combo.clear()
            for device_id, label in self._devices:
                self._combo.addItem(label, device_id)
            if not self._devices:
                self._combo.addItem(_tr("output_none", "No output available"), None)
        finally:
            self._suppress = False

    def _select(self, device_id: str | None) -> None:
        self._suppress = True
        try:
            index = self._combo.findData(device_id)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        finally:
            self._suppress = False

    # ── interaction ───────────────────────────────────────────────────────────

    def _on_picked(self, index: int) -> None:
        if self._suppress:
            return
        device_id = self._combo.itemData(index)
        if not device_id:
            return
        self._memory.remember(device_id)
        self._memory.save()
        self._pending_pick = device_id
        self._pending_ticks = 0
        self._current = device_id
        self._update_note()
        self.target_changed.emit(device_id)

    def _update_note(self) -> None:
        """Say when the current device is a stand-in rather than the choice.

        Without this the fallback looks like the setting was lost: the combo
        shows the headset, the user remembers picking the earbuds, and nothing
        explains the difference.
        """
        if self._on_configured:
            # Showing exactly what the user configured — nothing temporary.
            self._note.setText("")
            return
        if self._current and self._memory.is_fallback(self._current):
            preferred = self._memory.preferred or ""
            label = self._labels.get(preferred, preferred)
            self._note.setText(
                _tr("output_fallback", "(temporary — waiting for {device})")
                .replace("{device}", _short(label)))
        else:
            self._note.setText("")

    def apply_theme(self, t=None) -> None:
        self._label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; background: transparent;")
        self._note.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; "
            f"background: transparent;")

    def shutdown(self) -> None:
        self._timer.stop()


def _short(label: str, limit: int = 28) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"
