"""Profile bar widget — horizontal chip row for the Home page."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from arctis_sound_manager.gui.theme import (
    ACCENT, BG_BUTTON, BG_BUTTON_HOVER, BG_MAIN,
    BORDER, TEXT_PRIMARY, TEXT_SECONDARY,
)
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
    font-size: 10pt;
}}
QPushButton:hover {{
    background-color: {hover};
}}
"""

_CHIP_ACTIVE = _CHIP_BASE.format(
    bg=ACCENT, fg="#ffffff", border=ACCENT, hover=ACCENT,
)
_CHIP_INACTIVE = _CHIP_BASE.format(
    bg=BG_BUTTON, fg=TEXT_PRIMARY, border=BORDER, hover=BG_BUTTON_HOVER,
)
_BTN_ADD = """
QPushButton {
    background-color: transparent;
    color: """ + TEXT_SECONDARY + """;
    border: 1px dashed """ + BORDER + """;
    border-radius: 14px;
    padding: 4px 12px;
    font-size: 10pt;
}
QPushButton:hover {
    border-color: """ + ACCENT + """;
    color: """ + ACCENT + """;
}
"""


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
        self.refresh()

    def refresh(self) -> None:
        # Clear existing chips
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()

        active = active_profile_name()
        for profile in Profile.list_all():
            btn = self._make_chip(profile, active == profile.name)
            self._layout.addWidget(btn)
            self._chips[profile.name] = btn

        add_btn = QPushButton("＋")
        add_btn.setStyleSheet(_BTN_ADD)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedHeight(30)
        add_btn.clicked.connect(self._on_add)
        self._layout.addWidget(add_btn)
        self._layout.addStretch(1)

    def set_active(self, name: str | None) -> None:
        for n, btn in self._chips.items():
            btn.setStyleSheet(_CHIP_ACTIVE if n == name else _CHIP_INACTIVE)

    def _make_chip(self, profile: Profile, is_active: bool) -> QPushButton:
        btn = QPushButton(profile.name)
        btn.setStyleSheet(_CHIP_ACTIVE if is_active else _CHIP_INACTIVE)
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
            f"QMenu {{ background: {BG_BUTTON}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {ACCENT}; color: #fff; }}"
        )
        delete_action = menu.addAction("Delete")
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


class SaveProfileDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Save profile")
        self.setMinimumWidth(360)
        self.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            "Save current settings as a profile:",
            styleSheet=f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        ))

        self._name = QLineEdit()
        self._name.setPlaceholderText("Profile name…")
        self._name.setStyleSheet(
            f"QLineEdit {{ background: {BG_BUTTON}; border: 1px solid {BORDER}; "
            f"border-radius: 6px; color: {TEXT_PRIMARY}; padding: 6px 10px; font-size: 11pt; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT}; }}"
        )
        layout.addWidget(self._name)

        self._cb_volumes = QCheckBox("Include volumes")
        self._cb_volumes.setChecked(True)
        self._cb_volumes.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(self._cb_volumes)

        self._cb_spatial = QCheckBox("Include spatial audio")
        self._cb_spatial.setChecked(True)
        self._cb_spatial.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent;")
        layout.addWidget(self._cb_spatial)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #fff; border: none; "
            f"border-radius: 6px; padding: 6px 18px; font-size: 10pt; }}"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(
            f"QPushButton {{ background: {BG_BUTTON}; color: {TEXT_PRIMARY}; border: none; "
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

        profile.save()
        self.accept()
