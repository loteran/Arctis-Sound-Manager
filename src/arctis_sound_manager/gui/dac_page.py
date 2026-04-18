"""
DAC page — digital-to-analog converter settings.
"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import SectionTitle
from arctis_sound_manager.gui.settings_widget import QSettingsWidget
from arctis_sound_manager.gui.theme import (
    BG_MAIN,
    TEXT_PRIMARY,
)
from arctis_sound_manager.i18n import I18n


class DacPage(QWidget):
    """Page showing DAC-specific settings."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}")

        content = QWidget()
        content.setStyleSheet(f"background-color: {BG_MAIN};")
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(36, 28, 36, 36)
        layout.setSpacing(0)

        app_title = QLabel(I18n.translate("ui", "app_name"))
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(app_title)
        layout.addSpacing(28)

        layout.addWidget(SectionTitle(I18n.translate("ui", "dac_settings")))
        layout.addSpacing(4)

        self._dac_widget = QSettingsWidget(content, "dac_settings", "dac")
        self._dac_widget.setStyleSheet(
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
        layout.addWidget(self._dac_widget)
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._dac_widget.update_settings({
            'settings_config': settings.get('dac_settings_config', {}),
            'dac': settings.get('dac', {}),
        })
