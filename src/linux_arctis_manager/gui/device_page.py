"""
Device page — wraps the existing QStatusWidget and QSettingsWidget
with the new dark theme styling.
"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.settings_widget import QSettingsWidget
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


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {TEXT_PRIMARY}; font-size: 14pt; font-weight: bold; background: transparent;"
    )
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {BORDER}; border: none;")
    return line


def _card_container() -> tuple[QWidget, QVBoxLayout]:
    """Returns a styled card widget and its inner layout."""
    card = QWidget()
    card.setStyleSheet(
        f"background-color: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    return card, layout


class DevicePage(QWidget):
    """
    Combines device status (battery, signal strength, …) and device
    settings (sidetone, EQ, ANC …) in a single scrollable page.

    Receives:
        sig_status  → update_status(status)
        sig_settings → update_settings(settings)
    from DbusWrapper — caller wires these up.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        # ── Outer layout ──────────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Page title ────────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background-color: {BG_MAIN};")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(32, 24, 32, 12)

        page_title = QLabel("Appareil")
        page_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 16pt; font-weight: bold; background: transparent;"
        )
        header_layout.addWidget(page_title)

        page_sub = QLabel("Statut et paramètres du casque")
        page_sub.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        header_layout.addWidget(page_sub)
        outer.addWidget(header)

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
        content_layout.setContentsMargins(32, 0, 32, 32)
        content_layout.setSpacing(20)

        # ── Status card ───────────────────────────────────────────────────────
        content_layout.addWidget(_section_title("Statut"))
        status_card, status_card_layout = _card_container()

        self._status_widget = QStatusWidget(status_card)
        self._status_widget.setStyleSheet(
            f"""
            QStatusWidget, QWidget {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        status_card_layout.addWidget(self._status_widget)
        content_layout.addWidget(status_card)

        content_layout.addWidget(_separator())

        # ── General settings card ─────────────────────────────────────────────
        content_layout.addWidget(_section_title(I18n.translate("ui", "general")))
        gen_card, gen_card_layout = _card_container()

        self._general_widget = QSettingsWidget(gen_card, "general", "general")
        self._general_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        gen_card_layout.addWidget(self._general_widget)
        content_layout.addWidget(gen_card)

        content_layout.addWidget(_separator())

        # ── Device settings card ──────────────────────────────────────────────
        content_layout.addWidget(_section_title(I18n.translate("ui", "device")))
        dev_card, dev_card_layout = _card_container()

        self._device_widget = QSettingsWidget(dev_card, "device", "device")
        self._device_widget.setStyleSheet(
            f"""
            QWidget {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
            }}
            QLabel {{
                background-color: transparent;
                color: {TEXT_PRIMARY};
                font-size: 11pt;
            }}
            """
        )
        dev_card_layout.addWidget(self._device_widget)
        content_layout.addWidget(dev_card)

        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Signal forwarding ─────────────────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        self._status_widget.update_status(status)

    @Slot(object)
    def update_settings(self, settings: dict):
        self._general_widget.update_settings(settings)
        self._device_widget.update_settings(settings)
