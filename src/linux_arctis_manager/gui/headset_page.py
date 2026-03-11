"""
Headset page — Device info + status.
"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.components import (
    HEADPHONE_ICON,
    DividerLine,
    SectionTitle,
    SvgIconWidget,
)
from linux_arctis_manager.gui.status_widget import QStatusWidget
from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.i18n import I18n


class HeadsetPage(QWidget):
    """Page showing connected device info and live status."""

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

        # Title
        app_title = QLabel("Arctis Sound Manager")
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        layout.addWidget(app_title)
        layout.addSpacing(28)

        # Devices section
        layout.addWidget(SectionTitle("Devices"))
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

        self._device_name_label = QLabel("Aucun appareil")
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

    @Slot(object)
    def update_status(self, status: dict):
        self._status_widget.update_status(status)
        if status:
            for category in status:
                self._device_name_label.setText(
                    I18n.translate("status", category) or "SteelSeries Arctis"
                )
                break

    @Slot(object)
    def update_settings(self, settings: dict):
        vendor_id   = settings.get("vendor_id", "")
        product_id  = settings.get("product_id", "")
        device_name = settings.get("device_name", "")
        if device_name:
            self._device_name_label.setText(device_name)
        if vendor_id:
            self._vendor_label.setText(f"Vendor ID:   {vendor_id}")
        if product_id:
            self._product_label.setText(f"Product ID:  {product_id}")
