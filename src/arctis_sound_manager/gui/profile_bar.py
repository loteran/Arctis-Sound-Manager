# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile bar widget — horizontal chip row for the Home page."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.profile_manager import (
    Profile, active_profile_name, snapshot_current,
)

_CHIP_BASE = """
QPushButton {{
    background-color: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 14px;
    padding: 4px 14px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background-color: {hover};
}}
"""


def _chip_active_ss() -> str:
    return _CHIP_BASE.format(
        bg=_theme.c("ACCENT"), fg="#ffffff",
        border=_theme.c("ACCENT"), hover=_theme.c("ACCENT"),
    )


def _chip_inactive_ss() -> str:
    return _CHIP_BASE.format(
        bg=_theme.c("BG_BUTTON"), fg=_theme.c("TEXT_PRIMARY"),
        border=_theme.c("BORDER"), hover=_theme.c("BG_BUTTON_HOVER"),
    )


def _btn_add_ss() -> str:
    return (
        f"QPushButton {{"
        f"    background-color: transparent;"
        f"    color: {_theme.c('TEXT_PRIMARY')};"
        f"    border: 1px dashed {_theme.c('BORDER')};"
        f"    border-radius: 14px;"
        f"    padding: 4px 12px;"
        f"    font-size: 11pt;"
        f"}}"
        f"QPushButton:hover {{"
        f"    border-color: {_theme.c('ACCENT')};"
        f"    color: {_theme.c('ACCENT')};"
        f"}}"
    )


class ProfileBar(QWidget):
    sig_apply = Signal(object)   # emits Profile
    sig_changed = Signal()       # emits after save/delete

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background: transparent;")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._chips: dict[str, QPushButton] = {}
        self._update_btn: QPushButton | None = None
        self.refresh()

    def apply_theme(self, t=None) -> None:
        """Restyle all profile chips and buttons to the current active theme."""
        self.refresh()

    def refresh(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()

        # Section label — same style as "Enable Game/Chat Volume Sliders"
        lbl = QLabel(I18n.translate('ui', 'profiles_label') + ' :')
        lbl.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
        )
        self._layout.addWidget(lbl)

        profiles = Profile.list_all()
        active = active_profile_name()

        if not profiles:
            hint = QLabel(I18n.translate('ui', 'no_profiles_yet'))
            hint.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; font-style: italic; background: transparent;"
            )
            self._layout.addWidget(hint)
        else:
            for profile in profiles:
                btn = self._make_chip(profile, active == profile.name)
                self._layout.addWidget(btn)
                self._chips[profile.name] = btn

        if active and any(p.name == active for p in profiles):
            self._update_btn = QPushButton("↺  " + I18n.translate('ui', 'update_profile'))
            self._update_btn.setStyleSheet(_btn_add_ss())
            self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._update_btn.setFixedHeight(30)
            self._update_btn.clicked.connect(self._on_update)
            self._layout.addWidget(self._update_btn)
        else:
            self._update_btn = None

        add_btn = QPushButton("＋  " + I18n.translate('ui', 'save_current_settings'))
        add_btn.setStyleSheet(_btn_add_ss())
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedHeight(30)
        add_btn.clicked.connect(self._on_add)
        self._layout.addWidget(add_btn)
        self._layout.addStretch(1)

    def set_active(self, name: str | None) -> None:
        for n, btn in self._chips.items():
            btn.setStyleSheet(_chip_active_ss() if n == name else _chip_inactive_ss())

    def _make_chip(self, profile: Profile, is_active: bool) -> QPushButton:
        btn = QPushButton(profile.name)
        btn.setStyleSheet(_chip_active_ss() if is_active else _chip_inactive_ss())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(30)
        btn.clicked.connect(lambda _=False, p=profile: self._on_chip_click(p))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, p=profile, b=btn: self._on_chip_context(p, b, pos)
        )
        return btn

    def _on_chip_click(self, profile: Profile) -> None:
        self.set_active(profile.name)
        self.sig_apply.emit(profile)

    def _on_chip_context(self, profile: Profile, btn: QPushButton, pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_PRIMARY')}; "
            f"border: 1px solid {_theme.c('BORDER')}; border-radius: 6px; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {_theme.c('ACCENT')}; color: #fff; }}"
        )
        delete_action = menu.addAction(I18n.translate('ui', 'delete'))
        action = menu.exec(btn.mapToGlobal(pos))
        if action == delete_action:
            profile.delete()
            self.refresh()
            self.sig_changed.emit()

    def _on_add(self) -> None:
        dlg = SaveProfileDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.sig_changed.emit()

    def _on_update(self) -> None:
        name = active_profile_name()
        if not name:
            return
        snapshot = snapshot_current()
        snapshot.name = name
        snapshot.save()
        if self._update_btn is not None:
            self._update_btn.setText(I18n.translate('ui', 'profile_updated'))
            self._update_btn.setEnabled(False)
            QTimer.singleShot(1500, self._reset_update_btn)

    def _reset_update_btn(self) -> None:
        try:
            if self._update_btn is not None:
                self._update_btn.setText("↺  " + I18n.translate('ui', 'update_profile'))
                self._update_btn.setEnabled(True)
        except RuntimeError:
            pass


class SaveProfileDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(I18n.translate('ui', 'save_profile'))
        self.setMinimumWidth(360)
        self.setStyleSheet(
            f"background-color: {_theme.c('BG_MAIN')}; color: {_theme.c('TEXT_PRIMARY')};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            I18n.translate('ui', 'save_profile_desc'),
            styleSheet=f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
        ))

        self._name = QLineEdit()
        self._name.setPlaceholderText(I18n.translate('ui', 'profile_name_placeholder'))
        self._name.setStyleSheet(
            f"QLineEdit {{ background: {_theme.c('BG_BUTTON')}; border: 1px solid {_theme.c('BORDER')}; "
            f"border-radius: 6px; color: {_theme.c('TEXT_PRIMARY')}; padding: 6px 10px; font-size: 11pt; }}"
            f"QLineEdit:focus {{ border-color: {_theme.c('ACCENT')}; }}"
        )
        layout.addWidget(self._name)

        cb_style = f"color: {_theme.c('TEXT_SECONDARY')}; background: transparent;"

        self._cb_volumes = QCheckBox(I18n.translate('ui', 'include_volumes'))
        self._cb_volumes.setChecked(True)
        self._cb_volumes.setStyleSheet(cb_style)
        layout.addWidget(self._cb_volumes)

        self._cb_spatial = QCheckBox(I18n.translate('ui', 'include_spatial_audio'))
        self._cb_spatial.setChecked(True)
        self._cb_spatial.setStyleSheet(cb_style)
        layout.addWidget(self._cb_spatial)

        self._cb_eq_mode = QCheckBox(I18n.translate('ui', 'include_eq_mode'))
        self._cb_eq_mode.setChecked(True)
        self._cb_eq_mode.setStyleSheet(cb_style)
        layout.addWidget(self._cb_eq_mode)

        self._cb_output_devices = QCheckBox(I18n.translate('ui', 'include_output_devices'))
        self._cb_output_devices.setChecked(False)
        self._cb_output_devices.setStyleSheet(cb_style)
        layout.addWidget(self._cb_output_devices)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(
            f"QPushButton {{ background: {_theme.c('ACCENT')}; color: #fff; border: none; "
            f"border-radius: 6px; padding: 6px 18px; font-size: 10pt; }}"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(
            f"QPushButton {{ background: {_theme.c('BG_BUTTON')}; color: {_theme.c('TEXT_PRIMARY')}; border: none; "
            f"border-radius: 6px; padding: 6px 18px; font-size: 10pt; }}"
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._name.setFocus()
            return

        profile = snapshot_current()
        profile.name = name

        if not self._cb_volumes.isChecked():
            profile.volumes = {}
        if not self._cb_spatial.isChecked():
            profile.spatial_audio = {}
        if not self._cb_eq_mode.isChecked():
            profile.eq_mode = ""
        if not self._cb_output_devices.isChecked():
            profile.output_devices = {}

        profile.save()
        self.accept()
