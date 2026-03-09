"""
Home page — Audio mixer for Arctis Media / Chat sinks via pulsectl.
"""
import logging

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from linux_arctis_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from linux_arctis_manager.i18n import I18n

logger = logging.getLogger("HomePage")

# PulseAudio sink name fragments to match
SINK_MEDIA = "Arctis_Media"
SINK_CHAT = "Arctis_Chat"

# Colours for the two channel cards
COLOR_MEDIA = "#04C5A8"
COLOR_CHAT = "#2791CE"


def _make_card_style(accent_color: str) -> str:
    return f"""
        QWidget#volumeCard {{
            background-color: {BG_CARD};
            border: 1px solid {accent_color};
            border-radius: 12px;
        }}
    """


class VolumeCard(QWidget):
    """Vertical card with a vertical slider + percentage label for one sink."""

    def __init__(self, label: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("volumeCard")
        self.setStyleSheet(_make_card_style(accent))
        self.setFixedWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._accent = accent
        self._ignore_change = False

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(12)

        # Channel name label
        self._name_label = QLabel(label)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._name_label.setStyleSheet(f"color: {accent}; font-size: 12pt; font-weight: bold; background: transparent;")
        layout.addWidget(self._name_label)

        # Percentage label
        self._pct_label = QLabel("—")
        self._pct_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._pct_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 20pt; font-weight: bold; background: transparent;")
        layout.addWidget(self._pct_label)

        # Vertical slider
        self._slider = QSlider(Qt.Orientation.Vertical)
        self._slider.setMinimum(0)
        self._slider.setMaximum(100)
        self._slider.setTickInterval(10)
        self._slider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._slider.setFixedWidth(40)
        # Override slider colours to use the card accent
        self._slider.setStyleSheet(f"""
            QSlider::groove:vertical {{
                width: 6px;
                background: #2D363E;
                border-radius: 3px;
            }}
            QSlider::handle:vertical {{
                background: {accent};
                border: none;
                width: 18px;
                height: 18px;
                margin: 0 -6px;
                border-radius: 9px;
            }}
            QSlider::sub-page:vertical {{
                background: {accent};
                border-radius: 3px;
            }}
        """)
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._on_change_callback = None  # callable(value: int)

    def set_on_change(self, callback):
        self._on_change_callback = callback

    def set_volume(self, pct: int):
        """Set slider position without triggering the user callback."""
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

    def _on_slider_changed(self, value: int):
        self._pct_label.setText(f"{value}%")
        if not self._ignore_change and self._on_change_callback:
            self._on_change_callback(value)


class HomePage(QWidget):
    """
    Home page showing:
    - Headset status (battery, connection) at the top
    - Audio mixer for Media and Chat sinks
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        self._pulse = None
        self._sink_media = None
        self._sink_chat = None
        self._connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(20)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status_label = QLabel("Chargement du statut…")
        self._status_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;"
        )
        root.addWidget(self._status_label)

        # ── Section title ─────────────────────────────────────────────────────
        title = QLabel("Mixeur Audio")
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 16pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(title)

        subtitle = QLabel("Contrôle indépendant des volumes Media et Chat")
        subtitle.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        root.addWidget(subtitle)

        # ── "Disconnected" overlay label ──────────────────────────────────────
        self._disconnected_label = QLabel("Casque non connecté")
        self._disconnected_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._disconnected_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 14pt; font-style: italic; background: transparent;"
        )
        self._disconnected_label.hide()
        root.addWidget(self._disconnected_label)

        # ── Volume cards row ──────────────────────────────────────────────────
        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        cards_layout = QHBoxLayout(self._cards_widget)
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        cards_layout.setSpacing(24)
        cards_layout.setContentsMargins(0, 0, 0, 0)

        self._media_card = VolumeCard("Media / Game", COLOR_MEDIA)
        self._media_card.set_on_change(self._on_media_volume_changed)
        cards_layout.addWidget(self._media_card)

        self._chat_card = VolumeCard("Chat", COLOR_CHAT)
        self._chat_card.set_on_change(self._on_chat_volume_changed)
        cards_layout.addWidget(self._chat_card)

        root.addWidget(self._cards_widget)
        root.addStretch(1)

        # ── Polling timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_volumes)
        self._timer.start()

    # ── D-Bus status signal handler ───────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        """Receives sig_status from DbusWrapper and updates the top bar."""
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
                self._media_card.set_volume(pct)
                self._sink_media = sink_media

            if sink_chat is not None:
                pct = round(sink_chat.volume.value_flat * 100)
                self._chat_card.set_volume(pct)
                self._sink_chat = sink_chat

        except Exception as exc:
            logger.warning("Error polling PulseAudio: %s", exc)
            # Reset pulse handle so we reconnect next tick
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
            self._cards_widget.hide()
            self._media_card.set_disconnected()
            self._chat_card.set_disconnected()

    def _set_connected(self):
        if not self._connected:
            self._connected = True
            self._disconnected_label.hide()
            self._cards_widget.show()
            self._media_card.set_connected()
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
            # Refresh sink object before writing
            sinks = pulse.sink_list()
            fresh_sink = next((s for s in sinks if s.name == sink.name), None)
            if fresh_sink is not None:
                pulse.volume_set_all_chans(fresh_sink, value / 100.0)
        except Exception as exc:
            logger.warning("Error setting volume: %s", exc)
