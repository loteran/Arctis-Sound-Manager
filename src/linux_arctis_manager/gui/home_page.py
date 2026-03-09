"""
Home page — Audio mixer matching the ArctisSonar GUI visual style.
Shows horizontal audio channel cards (Game, Chat, Media, etc.) with vertical sliders.
"""
import logging

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
    QFrame,
)

from linux_arctis_manager.gui.components import (
    CHAT_ICON,
    GAME_ICON,
    MEDIA_ICON,
    SvgIconWidget,
)
from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    COLOR_CHAT,
    COLOR_GAME,
    COLOR_MEDIA,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.i18n import I18n

logger = logging.getLogger("HomePage")

# PulseAudio sink name fragments to match
SINK_MEDIA = "Arctis_Media"
SINK_CHAT = "Arctis_Chat"

# Default audio device label
DEFAULT_DEVICE = "Default Audio Device"
COLOR_AUX = "#FB4A00"
COLOR_DEFAULT = "#9B59B6"


def _make_vertical_slider_qss(accent_color: str) -> str:
    return f"""
        QSlider::groove:vertical {{
            width: 6px;
            background: #2D363E;
            border-radius: 3px;
        }}
        QSlider::handle:vertical {{
            background: white;
            border: none;
            width: 18px;
            height: 18px;
            margin: 0 -6px;
            border-radius: 9px;
        }}
        QSlider::sub-page:vertical {{
            background: {accent_color};
            border-radius: 3px;
        }}
    """


class AudioCard(QWidget):
    """
    Vertical card with:
    - Header row: SVG icon + colored channel name
    - Volume % in white bold
    - Vertical slider with accent color
    - "Applications" section at bottom with a darker background
    """

    def __init__(
        self,
        channel_name: str,
        accent_color: str,
        svg_path: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("audioCard")
        self.setFixedWidth(185)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            f"""
            QWidget#audioCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )

        self._accent = accent_color
        self._ignore_change = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top section: icon + name + volume + slider ─────────────────────────
        top_widget = QWidget()
        top_widget.setStyleSheet("background: transparent;")
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(16, 16, 16, 12)
        top_layout.setSpacing(8)
        top_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Header row: icon + name
        header_row = QWidget()
        header_row.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if svg_path:
            icon = SvgIconWidget(svg_path, accent_color, size=28)
            header_layout.addWidget(icon)

        name_lbl = QLabel(channel_name)
        name_lbl.setStyleSheet(
            f"color: {accent_color}; font-size: 12pt; font-weight: bold; background: transparent;"
        )
        header_layout.addWidget(name_lbl)
        header_layout.addStretch(1)
        top_layout.addWidget(header_row)

        # Volume percentage label
        self._pct_label = QLabel("—")
        self._pct_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._pct_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 18pt; font-weight: bold; background: transparent;"
        )
        top_layout.addWidget(self._pct_label)

        # Vertical slider
        self._slider = QSlider(Qt.Orientation.Vertical)
        self._slider.setMinimum(0)
        self._slider.setMaximum(100)
        self._slider.setTickInterval(10)
        self._slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._slider.setFixedWidth(30)
        self._slider.setMinimumHeight(140)
        self._slider.setStyleSheet(_make_vertical_slider_qss(accent_color))
        self._slider.valueChanged.connect(self._on_slider_changed)
        top_layout.addWidget(self._slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        outer.addWidget(top_widget, stretch=1)

        # ── Divider ────────────────────────────────────────────────────────────
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {BORDER};")
        outer.addWidget(divider)

        # ── Applications section ───────────────────────────────────────────────
        apps_widget = QWidget()
        apps_widget.setStyleSheet(
            f"background-color: #13161A; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;"
        )
        apps_layout = QVBoxLayout(apps_widget)
        apps_layout.setContentsMargins(12, 10, 12, 10)
        apps_layout.setSpacing(6)

        apps_title = QLabel("Applications")
        apps_title.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        apps_layout.addWidget(apps_title)

        self._apps_area = QVBoxLayout()
        self._apps_area.setSpacing(4)
        apps_layout.addLayout(self._apps_area)

        apps_widget.setFixedHeight(100)
        outer.addWidget(apps_widget)

        self._on_change_callback = None

    def set_on_change(self, callback):
        self._on_change_callback = callback

    def set_volume(self, pct: int):
        self._ignore_change = True
        self._slider.setValue(pct)
        self._pct_label.setText(f"{pct}%")
        self._ignore_change = False

    def set_disconnected(self):
        self._ignore_change = True
        self._slider.setValue(0)
        self._pct_label.setText("—")
        self._ignore_change = False
        self._slider.setEnabled(False)

    def set_connected(self):
        self._slider.setEnabled(True)

    def add_app_tag(self, app_name: str, bg_color: str = "#333333"):
        """Add an application pill/tag in the Applications section."""
        tag = QLabel(app_name)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setFixedHeight(24)
        tag.setStyleSheet(
            f"background-color: {bg_color}; color: white; font-size: 9pt; "
            f"border-radius: 4px; padding: 0 6px;"
        )
        self._apps_area.addWidget(tag)

    def clear_apps(self):
        while self._apps_area.count():
            item = self._apps_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_slider_changed(self, value: int):
        self._pct_label.setText(f"{value}%")
        if not self._ignore_change and self._on_change_callback:
            self._on_change_callback(value)


# ── Toggle switch widget ────────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    """Simple visual toggle switch using a styled QCheckBox."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._cb = QCheckBox()
        self._cb.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 44px;
                height: 24px;
                border-radius: 12px;
                background-color: #3A4550;
                border: none;
            }
            QCheckBox::indicator:checked {
                background-color: #2791CE;
            }
            QCheckBox::indicator:unchecked {
                background-color: #3A4550;
            }
            """
        )
        layout.addWidget(self._cb)

    @property
    def checkbox(self):
        return self._cb

    def is_checked(self) -> bool:
        return self._cb.isChecked()

    def set_checked(self, val: bool):
        self._cb.setChecked(val)


# ── Home Page ──────────────────────────────────────────────────────────────────

class HomePage(QWidget):
    """
    Home page showing:
    - App title "Arctis Manager" bold white
    - Subtitle: headset status in orange
    - Toggle row: Enable Game/Chat Volume Sliders
    - Row of audio cards (Game, Chat, Media, …)
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        self._pulse = None
        self._sink_media = None
        self._sink_chat = None
        self._connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── App title ─────────────────────────────────────────────────────────
        app_title = QLabel("Arctis Manager")
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(app_title)
        root.addSpacing(8)

        # ── Headset status subtitle ────────────────────────────────────────────
        self._status_label = QLabel("Chargement du statut…")
        self._status_label.setStyleSheet(
            f"color: {ACCENT}; font-size: 12pt; background: transparent;"
        )
        root.addWidget(self._status_label)
        root.addSpacing(24)

        # ── Enable sliders toggle row ──────────────────────────────────────────
        toggle_row = QWidget()
        toggle_row.setStyleSheet("background: transparent;")
        toggle_layout = QHBoxLayout(toggle_row)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(16)

        toggle_lbl = QLabel("Enable Game/Chat Volume Sliders")
        toggle_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        toggle_layout.addWidget(toggle_lbl)

        self._toggle = ToggleSwitch()
        self._toggle.set_checked(True)
        self._toggle.checkbox.toggled.connect(self._on_toggle_changed)
        toggle_layout.addWidget(self._toggle)
        toggle_layout.addStretch(1)

        root.addWidget(toggle_row)
        root.addSpacing(24)

        # ── "Disconnected" label ───────────────────────────────────────────────
        self._disconnected_label = QLabel("Casque non connecté")
        self._disconnected_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._disconnected_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 14pt; font-style: italic; background: transparent;"
        )
        self._disconnected_label.hide()
        root.addWidget(self._disconnected_label)

        # ── Cards scroll area ─────────────────────────────────────────────────
        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
        )
        self._cards_scroll.setFixedHeight(430)

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        self._cards_layout = QHBoxLayout(self._cards_widget)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._cards_layout.setSpacing(16)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)

        # Game card (Arctis_Media sink → "Game" in the reference)
        self._game_card = AudioCard("Game", COLOR_GAME, GAME_ICON)
        self._game_card.set_on_change(self._on_media_volume_changed)
        self._cards_layout.addWidget(self._game_card)

        # Chat card (Arctis_Chat sink)
        self._chat_card = AudioCard("Chat", COLOR_CHAT, CHAT_ICON)
        self._chat_card.set_on_change(self._on_chat_volume_changed)
        self._cards_layout.addWidget(self._chat_card)

        # Media card (placeholder — shown grayed out when no dedicated sink)
        self._media_card = AudioCard("Media", COLOR_MEDIA, MEDIA_ICON)
        self._media_card.set_disconnected()
        self._cards_layout.addWidget(self._media_card)

        self._cards_scroll.setWidget(self._cards_widget)
        root.addWidget(self._cards_scroll)
        root.addStretch(1)

        # ── Polling timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_volumes)
        self._timer.start()

    # ── Toggle handler ─────────────────────────────────────────────────────────

    def _on_toggle_changed(self, enabled: bool):
        self._game_card.setEnabled(enabled)
        self._chat_card.setEnabled(enabled)

    # ── D-Bus status signal handler ───────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        if not status:
            self._status_label.setText("Aucun appareil détecté")
            return

        parts = []
        for _category, status_obj in status.items():
            for key, info in status_obj.items():
                val = I18n.translate("status_values", info["value"])
                suffix = "%" if info.get("type") == "percentage" else ""
                label = I18n.translate("status", key)
                parts.append(f"{label}: {val}{suffix}")

        self._status_label.setText("  •  ".join(parts) if parts else "Appareil connecté")

    # ── PulseAudio polling ────────────────────────────────────────────────────

    def _get_pulse(self):
        if self._pulse is not None:
            return self._pulse
        try:
            import pulsectl  # type: ignore
            self._pulse = pulsectl.Pulse("arctis-manager-gui")
        except Exception as exc:
            logger.debug("pulsectl not available: %s", exc)
        return self._pulse

    @Slot()
    def _poll_volumes(self):
        pulse = self._get_pulse()
        if pulse is None:
            self._set_disconnected()
            return

        try:
            sinks = pulse.sink_list()
            sink_media = next((s for s in sinks if SINK_MEDIA in s.name), None)
            sink_chat = next((s for s in sinks if SINK_CHAT in s.name), None)

            if sink_media is None and sink_chat is None:
                self._set_disconnected()
                return

            self._set_connected()

            if sink_media is not None:
                pct = round(sink_media.volume.value_flat * 100)
                self._game_card.set_volume(pct)
                self._sink_media = sink_media

            if sink_chat is not None:
                pct = round(sink_chat.volume.value_flat * 100)
                self._chat_card.set_volume(pct)
                self._sink_chat = sink_chat

        except Exception as exc:
            logger.warning("Error polling PulseAudio: %s", exc)
            try:
                self._pulse.close()
            except Exception:
                pass
            self._pulse = None
            self._set_disconnected()

    def _set_disconnected(self):
        if self._connected:
            self._connected = False
            self._disconnected_label.show()
            self._game_card.set_disconnected()
            self._chat_card.set_disconnected()

    def _set_connected(self):
        if not self._connected:
            self._connected = True
            self._disconnected_label.hide()
            self._game_card.set_connected()
            self._chat_card.set_connected()

    # ── Volume change callbacks ───────────────────────────────────────────────

    def _on_media_volume_changed(self, value: int):
        self._apply_volume(self._sink_media, value)

    def _on_chat_volume_changed(self, value: int):
        self._apply_volume(self._sink_chat, value)

    def _apply_volume(self, sink, value: int):
        pulse = self._get_pulse()
        if pulse is None or sink is None:
            return
        try:
            sinks = pulse.sink_list()
            fresh_sink = next((s for s in sinks if s.name == sink.name), None)
            if fresh_sink is not None:
                pulse.volume_set_all_chans(fresh_sink, value / 100.0)
        except Exception as exc:
            logger.warning("Error setting volume: %s", exc)
