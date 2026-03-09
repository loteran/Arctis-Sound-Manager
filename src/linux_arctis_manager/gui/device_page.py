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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.components import (
    HEADPHONE_ICON,
    SvgIconWidget,
    DividerLine,
    SectionTitle,
)
from linux_arctis_manager.gui.settings_widget import QSettingsWidget
from linux_arctis_manager.gui.status_widget import QStatusWidget
from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.i18n import I18n


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
    - Title "Arctis Manager" bold + subtitle "Device Settings"
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
        content_layout.setContentsMargins(36, 28, 36, 36)
        content_layout.setSpacing(0)

        # ── App title ─────────────────────────────────────────────────────────
        app_title = QLabel("Arctis Manager")
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        content_layout.addWidget(app_title)
        content_layout.addSpacing(28)

        # ── General Settings section ───────────────────────────────────────────
        general_title = SectionTitle("General Settings")
        content_layout.addWidget(general_title)
        content_layout.addSpacing(20)

        # Settings form: label + control rows
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
        content_layout.addSpacing(24)

        # ── Horizontal divider ─────────────────────────────────────────────────
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(24)

        # ── Devices section ────────────────────────────────────────────────────
        devices_title = SectionTitle("Devices")
        content_layout.addWidget(devices_title)
        content_layout.addSpacing(20)

        # Device card
        self._device_card = QWidget()
        self._device_card.setObjectName("deviceCard")
        self._device_card.setStyleSheet(
            f"""
            QWidget#deviceCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )

        device_card_layout = QHBoxLayout(self._device_card)
        device_card_layout.setContentsMargins(20, 16, 20, 16)
        device_card_layout.setSpacing(16)

        # Headphone icon (orange)
        headphone_icon = SvgIconWidget(HEADPHONE_ICON, ACCENT, size=44)
        device_card_layout.addWidget(headphone_icon)

        # Device info (name + vendor/product IDs)
        device_info = QWidget()
        device_info.setStyleSheet("background: transparent;")
        device_info_layout = QVBoxLayout(device_info)
        device_info_layout.setContentsMargins(0, 0, 0, 0)
        device_info_layout.setSpacing(2)

        self._device_name_label = QLabel("Aucun appareil")
        self._device_name_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 12pt; font-weight: bold; background: transparent;"
        )
        device_info_layout.addWidget(self._device_name_label)

        self._vendor_label = QLabel("")
        self._vendor_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        device_info_layout.addWidget(self._vendor_label)

        self._product_label = QLabel("")
        self._product_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        device_info_layout.addWidget(self._product_label)

        device_card_layout.addWidget(device_info)
        device_card_layout.addStretch(1)

        content_layout.addWidget(self._device_card)
        content_layout.addSpacing(24)

        # ── Status section (hidden until device connects) ──────────────────────
        self._status_widget = QStatusWidget(content)
        self._status_widget.setStyleSheet(
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
        content_layout.addWidget(self._status_widget)

        # ── Device settings section ────────────────────────────────────────────
        content_layout.addSpacing(8)
        content_layout.addWidget(DividerLine())
        content_layout.addSpacing(16)

        device_settings_title = SectionTitle("Device Settings")
        content_layout.addWidget(device_settings_title)
        content_layout.addSpacing(16)

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
        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._status_widget.update_status(status)
        # Update device card name from status if available
        if status:
            # Try to extract device name from any first category
            for category in status:
                self._device_name_label.setText(
                    I18n.translate("status", category) or "SteelSeries Arctis"
                )
                break

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)

        # Try to update vendor/product IDs from settings
        vendor_id = settings.get("vendor_id", "")
        product_id = settings.get("product_id", "")
        device_name = settings.get("device_name", "")

        if device_name:
            self._device_name_label.setText(device_name)
        if vendor_id:
            self._vendor_label.setText(f"Vendor ID:   {vendor_id}")
        if product_id:
            self._product_label.setText(f"Product ID:  {product_id}")
