"""
Device / Settings page — ArctisSonar GUI visual style.
Matches the ref_settingsPage.png design.
"""
import os

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.anc_widget import QAncWidget
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.gui.components import (
    DividerLine,
    SectionTitle,
)
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

def _styled_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setFixedHeight(44)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {BG_BUTTON};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 6px;
            font-size: 11pt;
            padding: 0 16px;
        }}
        QPushButton:hover {{
            background-color: {BG_BUTTON_HOVER};
        }}
        """
    )
    return btn


class DevicePage(QWidget):
    """
    Settings page with:
    - Title "Arctis Sound Manager" bold + subtitle "Device Settings"
    - "General Settings" section title (gray ~20pt)
    - Settings form rows (labels + controls)
    - Horizontal divider
    - "Devices" section with a card showing connected headset
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Scrollable content area ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}"
        )

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_MAIN};")
        content_layout = QVBoxLayout(content)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.setContentsMargins(36, 12, 36, 12)
        content_layout.setSpacing(0)

        # ── App title + language selector ─────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        app_title = QLabel(I18n.translate("ui", "app_name"))
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        title_row.addWidget(app_title, stretch=1)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lang_label = QLabel("Language:")
        lang_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        lang_row.addWidget(lang_label)

        self._lang_buttons: dict[str, QPushButton] = {}
        for code, display in [("en", "EN"), ("fr", "FR"), ("es", "ES")]:
            btn = QPushButton(display)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=code: self._on_lang(c))
            lang_row.addWidget(btn)
            self._lang_buttons[code] = btn

        self._refresh_lang_buttons()
        title_row.addLayout(lang_row)
        content_layout.addLayout(title_row)
        content_layout.addSpacing(8)

        # ── ANC / Transparent section ─────────────────────────────────────────
        anc_title = SectionTitle(I18n.translate("ui", "noise_cancelling"))
        content_layout.addWidget(anc_title)
        content_layout.addSpacing(4)

        self._anc_widget = QAncWidget(content)
        self._anc_widget.setStyleSheet(f"""
            QWidget {{ background-color: {BG_MAIN}; color: {TEXT_PRIMARY}; }}
            QLabel  {{ background-color: transparent; color: {TEXT_PRIMARY}; font-size: 11pt; }}
        """)
        content_layout.addWidget(self._anc_widget)
        content_layout.addSpacing(6)
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(6)

        # ── Device Settings section ────────────────────────────────────────────
        device_settings_title = SectionTitle(I18n.translate("ui", "device_settings"))
        content_layout.addWidget(device_settings_title)
        content_layout.addSpacing(4)

        self._device_widget = QSettingsWidget(content, "device", "device")
        self._device_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        content_layout.addWidget(self._device_widget)
        content_layout.addSpacing(6)

        # ── Horizontal divider ─────────────────────────────────────────────────
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(6)

        # ── General Settings section ───────────────────────────────────────────
        general_title = SectionTitle(I18n.translate("ui", "general_settings"))
        content_layout.addWidget(general_title)
        content_layout.addSpacing(4)

        self._general_widget = QSettingsWidget(content, "general", "general")
        self._general_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        content_layout.addWidget(self._general_widget)
        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._anc_widget.update_status(status)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)

    # ── Language ───────────────────────────────────────────────────────────────

    def _refresh_lang_buttons(self):
        current = I18n.current_lang()
        for code, btn in self._lang_buttons.items():
            if code == current:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {ACCENT};
                        color: #ffffff;
                        border: 1px solid {ACCENT};
                        border-radius: 6px;
                        padding: 4px 14px;
                        font-size: 10pt;
                        font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {BG_BUTTON};
                        color: {TEXT_SECONDARY};
                        border: 1px solid {BORDER};
                        border-radius: 6px;
                        padding: 4px 14px;
                        font-size: 10pt;
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background-color: {BG_BUTTON_HOVER};
                        color: {TEXT_PRIMARY};
                    }}
                """)

    def _on_lang(self, code: str):
        if code == I18n.current_lang():
            return
        I18n.get_instance().set_language(code)
        self._refresh_lang_buttons()
        _RESTART_MSG = {
            "en": "Language changed. The change will take effect on the next startup.",
            "fr": "Langue modifiée. Le changement sera pris en compte au prochain démarrage.",
            "es": "Idioma cambiado. El cambio se aplicará en el próximo inicio.",
        }
        msg = QMessageBox(self)
        msg.setWindowTitle("Language / Langue / Idioma")
        msg.setText(_RESTART_MSG.get(code, _RESTART_MSG["en"]))
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()
