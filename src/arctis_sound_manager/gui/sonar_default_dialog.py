# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Offers to put Sonar in the audio path when it is only appearing to be there.

Shown once, and only when the channels are running while the system default
still points past them — the state in which every Sonar control on screen is
inert for any app ASM does not explicitly route. Declining is remembered: a
user who routes apps by hand has a supported setup, not a broken one, and
should not be asked again at every launch.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.sonar_default import SonarRouting, apply_default

logger = logging.getLogger("SonarDefaultDialog")

STATE_FILE = Path.home() / ".config" / "arctis_manager" / "sonar_default_prompt.json"


def _tr(key: str, fallback: str) -> str:
    try:
        value = I18n.translate("ui", key)
    except Exception:
        return fallback
    return fallback if not value or value == key else value


def was_asked() -> bool:
    try:
        return bool(json.loads(STATE_FILE.read_text()).get("asked"))
    except (OSError, ValueError):
        return False


def mark_asked() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"asked": True}))
    except OSError as exc:
        logger.warning("could not record the Sonar default prompt: %s", exc)


class SonarDefaultDialog(QDialog):
    """Explains the bypass and offers to route the system default into Sonar."""

    def __init__(self, channel: str, default_label: str, parent=None):
        super().__init__(parent)
        self._channel = channel
        self.setWindowTitle(_tr("sonar_default_title", "Route audio through Sonar?"))
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel(_tr("sonar_default_title", "Route audio through Sonar?"))
        title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 13pt; "
            f"font-weight: bold; background: transparent;")
        layout.addWidget(title)

        # Two keys, not one paragraph with a blank line in it: an .ini value is
        # single-line, so a multi-paragraph default gets silently truncated to
        # whatever the catalogue holds — and the half that goes missing is the
        # half explaining what accepting will do.
        problem = QLabel(
            _tr("sonar_default_body",
                "Your system output is set to “{device}”, so apps go straight to "
                "it and skip Sonar entirely — the channels and equalisers here "
                "will not affect them.")
            .replace("{device}", default_label or "your output device"))
        action = QLabel(
            _tr("sonar_default_action",
                "ASM can make the Sonar “{channel}” channel your default output. "
                "Audio then passes through Sonar, and you choose the physical "
                "device for each channel inside ASM.")
            .replace("{channel}", channel))
        for widget in (problem, action):
            widget.setWordWrap(True)
            widget.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; "
                f"background: transparent;")
            layout.addWidget(widget)

        self._dont_ask = QCheckBox(_tr("sonar_default_dont_ask", "Don't ask again"))
        self._dont_ask.setChecked(True)
        layout.addWidget(self._dont_ask)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self._skip_btn = QPushButton(_tr("sonar_default_skip", "Keep current output"))
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self.reject)
        buttons.addWidget(self._skip_btn)

        self._apply_btn = QPushButton(_tr("sonar_default_apply", "Use Sonar"))
        self._apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._on_apply)
        buttons.addWidget(self._apply_btn)

        layout.addLayout(buttons)

        hint = QLabel(_tr(
            "sonar_default_revert",
            "You can change this at any time in your system sound settings."))
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; "
            f"background: transparent;")
        layout.addWidget(hint)

    def _on_apply(self) -> None:
        if apply_default(self._channel):
            self.accept()
        else:
            # Say so rather than closing as if it worked — a silent failure here
            # leaves the user believing Sonar is now in the path when it is not,
            # which is the exact confusion this dialog exists to end.
            self._apply_btn.setEnabled(False)
            self._apply_btn.setText(_tr("sonar_default_failed", "Could not change it"))


def maybe_offer(parent=None) -> bool:
    """Show the offer if it applies. Returns True when the default was changed.

    Always records that the question was put, whichever way it was answered —
    including a decline — so this runs at most once per installation.
    """
    from arctis_sound_manager.sonar_default import current_state

    state, channel, label = current_state()
    if state is not SonarRouting.BYPASSED or channel is None or was_asked():
        return False

    dialog = SonarDefaultDialog(channel, label, parent)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if dialog._dont_ask.isChecked() or accepted:
        mark_asked()
    return accepted
