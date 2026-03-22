"""
Home page — Audio mixer matching the ArctisSonar GUI visual style.
Shows horizontal audio channel cards (Game, Chat, Media, etc.) with vertical sliders.
"""
import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

OVERRIDES_FILE = Path.home() / ".config" / "arctis_manager" / "routing_overrides.json"


def _load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_overrides(overrides: dict) -> None:
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides))
    tmp.replace(OVERRIDES_FILE)

from arctis_sound_manager.gui.components import (
    CHAT_ICON,
    GAME_ICON,
    MEDIA_ICON,
    SvgIconWidget,
)
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_CARD,
    BG_MAIN,
    BORDER,
    COLOR_AUX,
    COLOR_CHAT,
    COLOR_GAME,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.pw_utils import get_native_streams

logger = logging.getLogger("HomePage")

# PulseAudio sink name fragments to match
SINK_GAME  = "Arctis_Game"
SINK_CHAT  = "Arctis_Chat"
SINK_MEDIA = "Arctis_Media"


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
            background: white;
            border-radius: 3px;
        }}
        QSlider::add-page:vertical {{
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
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._accent = accent_color
        self._ignore_change = False
        self._apply_normal_style()

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
        header_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        if svg_path:
            icon = SvgIconWidget(svg_path, accent_color, size=72, width=96)
            header_layout.addWidget(icon)

        name_lbl = QLabel(channel_name)
        name_lbl.setStyleSheet(
            f"color: {accent_color}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        header_layout.addWidget(name_lbl)
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

        outer.addSpacing(8)

        # ── Applications section ───────────────────────────────────────────────
        apps_widget = QWidget()
        apps_widget.setObjectName("appsWidget")
        apps_widget.setStyleSheet(
            "QWidget#appsWidget { background-color: #13161A; border-radius: 12px; }"
        )
        apps_layout = QVBoxLayout(apps_widget)
        apps_layout.setContentsMargins(12, 10, 12, 10)
        apps_layout.setSpacing(6)

        apps_title = QLabel("Applications")
        apps_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        apps_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 9pt; font-weight: bold; background: transparent;"
        )
        apps_layout.addWidget(apps_title)

        self._apps_area = QVBoxLayout()
        self._apps_area.setSpacing(4)
        apps_layout.addLayout(self._apps_area)
        apps_layout.addStretch(1)

        apps_widget.setFixedHeight(100)
        outer.addWidget(apps_widget)

        self._on_change_callback = None
        self._on_drop_callback = None  # fn(si_index, app_name, pid)
        self.setAcceptDrops(False)

    # ── Style helpers ──────────────────────────────────────────────────────────

    def _apply_normal_style(self):
        self.setStyleSheet(
            f"""
            QWidget#audioCard {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            """
        )

    def _apply_highlight_style(self):
        self.setStyleSheet(
            f"""
            QWidget#audioCard {{
                background-color: {BG_CARD};
                border: 2px solid {self._accent};
                border-radius: 12px;
            }}
            """
        )

    def set_highlight(self, active: bool):
        """Highlight this card visually when an app tag is dragged over it."""
        if active:
            self._apply_highlight_style()
        else:
            self._apply_normal_style()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_on_drop(self, callback):
        self._on_drop_callback = callback

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

    def add_app_tag(self, app_name: str, si_index: int, pid: int, bg_color: str = "#333333"):
        """Add a draggable application pill/tag in the Applications section."""
        tag = _AppTag(app_name, si_index, pid, bg_color)
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


# ── App tag with inline move buttons ──────────────────────────────────────────

class _AppTag(QWidget):
    """
    App tag row:  [app name ·············· G  C  M]
    G/C/M are small colored buttons to move the stream instantly.
    """

    # Set by HomePage: list of (short_label, color, callback)
    _cards_registry: list = []

    def __init__(self, app_name: str, si_index: int, pid: int, color: str):
        super().__init__()
        self._si_index = si_index
        self._pid = pid

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(4)

        self.setFixedHeight(24)
        self.setStyleSheet(
            f"background-color: #1e2530; border-radius: 4px; border: 1px solid {color};"
        )

        lbl = QLabel(app_name)
        lbl.setStyleSheet(
            f"color: {color}; font-size: 11pt; font-weight: bold; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(lbl, stretch=1)

        # Move buttons — built lazily from registry when first painted
        self._btn_container = QWidget()
        self._btn_container.setStyleSheet("background: transparent; border: none;")
        btn_layout = QHBoxLayout(self._btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(3)

        for short, btn_color, cb in _AppTag._cards_registry:
            btn = QPushButton(short)
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {btn_color}; "
                f"border: 1px solid {btn_color}; border-radius: 3px; "
                f"font-size: 7pt; font-weight: bold; padding: 0; }}"
                f"QPushButton:hover {{ background: {btn_color}; color: #000; }}"
            )
            btn.clicked.connect(
                lambda checked=False, c=cb, si=si_index, a=app_name, p=pid: c(si, a, p)
            )
            btn_layout.addWidget(btn)

        layout.addWidget(self._btn_container)


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


# ── Device status bar ─────────────────────────────────────────────────────────

_STATUS_COLORS = {
    "online":         "#04C5A8",   # teal
    "cable_charging": "#2791CE",   # blue
    "offline":        "#8D96AA",   # gray
    None:             "#8D96AA",
}

_STATUS_LABELS = {
    "online":         "Online",
    "cable_charging": "Charging",
    "offline":        "Offline",
    None:             "—",
}

_PILL_QSS = (
    "QWidget#pill {{ "
    "  background-color: {bg}; "
    "  border-radius: 14px; "
    "  border: 1px solid {border}; "
    "}}"
)


class _Pill(QWidget):
    """Rounded pill: colored dot + text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 14, 0)
        layout.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("background: transparent; font-size: 9pt; border: none;")
        layout.addWidget(self._dot)

        self._text = QLabel("—")
        self._text.setStyleSheet(
            f"background: transparent; font-size: 11pt; font-weight: bold; color: {TEXT_PRIMARY}; border: none;"
        )
        layout.addWidget(self._text)
        self._update_style("#8D96AA")

    def set_value(self, text: str, color: str):
        self._text.setText(text)
        self._dot.setStyleSheet(
            f"background: transparent; font-size: 9pt; color: {color}; border: none;"
        )
        self._update_style(color)

    def _update_style(self, color: str):
        self.setStyleSheet(
            f"QWidget#pill {{ background-color: {BG_CARD}; border-radius: 14px; "
            f"border: 1px solid {color}; }}"
        )

    def set_visible(self, visible: bool):
        self.setVisible(visible)


class _DeviceStatusBar(QWidget):
    """Row of status pills: connection state, headset battery, DAC battery."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._conn_pill = _Pill()
        self._headset_bat_pill = _Pill()
        self._dac_bat_pill = _Pill()

        layout.addWidget(self._conn_pill)
        layout.addWidget(self._headset_bat_pill)
        layout.addWidget(self._dac_bat_pill)

        self.set_no_device()

    def set_no_device(self):
        self._conn_pill.set_value("No device detected", "#8D96AA")
        self._headset_bat_pill.set_visible(False)
        self._dac_bat_pill.set_visible(False)

    def update(self, power_status, headset_bat, dac_bat):
        color = _STATUS_COLORS.get(power_status, "#8D96AA")
        label = _STATUS_LABELS.get(power_status, str(power_status) if power_status else "—")
        self._conn_pill.set_value(label, color)

        if headset_bat is not None:
            bat_color = _battery_color(headset_bat)
            self._headset_bat_pill.set_value(f"Headset  {headset_bat}%", bat_color)
            self._headset_bat_pill.set_visible(True)
        else:
            self._headset_bat_pill.set_visible(False)

        if dac_bat is not None:
            bat_color = _battery_color(dac_bat)
            self._dac_bat_pill.set_value(f"DAC  {dac_bat}%", bat_color)
            self._dac_bat_pill.set_visible(True)
        else:
            self._dac_bat_pill.set_visible(False)


def _battery_color(pct: int) -> str:
    if pct <= 20:
        return "#E04040"   # red
    if pct <= 50:
        return "#FFA040"   # orange
    return "#04C5A8"       # teal


# ── Home Page ──────────────────────────────────────────────────────────────────

class HomePage(QWidget):
    """
    Home page showing:
    - App title "Arctis Sound Manager" bold white
    - Subtitle: headset status in orange
    - Toggle row: Enable Game/Chat Volume Sliders
    - Row of audio cards (Game, Chat, Media, …)
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        self._pulse = None
        self._sink_game = None
        self._sink_chat = None
        self._sink_media = None
        self._connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── App title ─────────────────────────────────────────────────────────
        app_title = QLabel("Arctis Sound Manager")
        app_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(app_title)
        root.addSpacing(8)

        # ── Headset status pills ───────────────────────────────────────────────
        self._status_bar = _DeviceStatusBar()
        root.addWidget(self._status_bar)
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
        self._disconnected_label = QLabel("Headset not connected")
        self._disconnected_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._disconnected_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 14pt; font-style: italic; background: transparent;"
        )
        self._disconnected_label.hide()
        root.addWidget(self._disconnected_label)

        # ── Cards area (3/4 of window width, centered) ────────────────────────
        cards_outer = QWidget()
        cards_outer.setStyleSheet(f"background: transparent;")
        cards_outer_layout = QHBoxLayout(cards_outer)
        cards_outer_layout.setContentsMargins(0, 0, 0, 0)
        cards_outer_layout.setSpacing(0)

        # Side spacers each take 1/8 so cards fill 3/4
        cards_outer_layout.addStretch(1)

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        self._cards_layout = QHBoxLayout(self._cards_widget)
        self._cards_layout.setSpacing(20)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)

        # Game card
        self._game_card = AudioCard("Game", COLOR_GAME, GAME_ICON)
        self._game_card.set_on_change(self._on_media_volume_changed)
        self._game_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME))
        self._cards_layout.addWidget(self._game_card, stretch=1)

        # Chat card (Arctis_Chat sink)
        self._chat_card = AudioCard("Chat", COLOR_CHAT, CHAT_ICON)
        self._chat_card.set_on_change(self._on_chat_volume_changed)
        self._chat_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT))
        self._cards_layout.addWidget(self._chat_card, stretch=1)

        # Media card (Arctis_Media sink)
        self._media_card = AudioCard("Media", COLOR_AUX, MEDIA_ICON)
        self._media_card.set_on_change(self._on_aux_volume_changed)
        self._media_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA))
        self._cards_layout.addWidget(self._media_card, stretch=1)

        cards_outer_layout.addWidget(self._cards_widget, stretch=6)
        cards_outer_layout.addStretch(1)

        root.addWidget(cards_outer, stretch=1)

        # ── Help button ───────────────────────────────────────────────────────
        help_row = QWidget()
        help_row.setStyleSheet("background: transparent;")
        help_row_layout = QHBoxLayout(help_row)
        help_row_layout.setContentsMargins(0, 0, 0, 0)
        help_row_layout.addStretch(1)

        _help_icon_path = str(
            __import__("pathlib").Path(__file__).parent / "images" / "help_icon.png"
        )
        _help_text = (
            "<b>How to use the mixer</b><br><br>"
            "<b>Game</b> — Games (Arctis_Game)<br>"
            "<b>Chat</b> — Voice / Discord (Arctis_Chat)<br>"
            "<b>Media</b> — Music / Videos (Arctis_Media)<br><br>"
            "Sliders control the volume of each channel.<br>"
            "The <b>G C M</b> buttons on an app tag move<br>"
            "that audio stream to the desired channel."
        )

        self._help_btn = QPushButton()
        self._help_btn.setFixedSize(32, 32)
        self._help_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { opacity: 0.8; }"
        )
        _pixmap = QPixmap(_help_icon_path).scaled(
            32, 32,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._help_btn.setIcon(__import__("PySide6.QtGui", fromlist=["QIcon"]).QIcon(_pixmap))
        self._help_btn.setIconSize(_pixmap.size())
        self._help_btn.setToolTip(_help_text)
        self._help_btn.clicked.connect(
            lambda: __import__("PySide6.QtWidgets", fromlist=["QToolTip"]).QToolTip.showText(
                self._help_btn.mapToGlobal(self._help_btn.rect().bottomLeft()),
                _help_text,
                self._help_btn,
            )
        )
        help_row_layout.addWidget(self._help_btn)
        root.addWidget(help_row)

        # Register cards so _AppTag inline buttons know where to send streams
        # Format: (short_label, button_color, callback)
        _AppTag._cards_registry = [
            ("G", COLOR_GAME, lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME)),
            ("C", COLOR_CHAT, lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT)),
            ("M", COLOR_AUX,  lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA)),
        ]

        # ── Polling timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_volumes)
        self._timer.start()

    # ── Toggle handler ─────────────────────────────────────────────────────────

    def _on_toggle_changed(self, enabled: bool):
        self._game_card.setEnabled(enabled)
        self._chat_card.setEnabled(enabled)
        self._media_card.setEnabled(enabled)

    # ── D-Bus status signal handler ───────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        if not status:
            self._status_bar.set_no_device()
            return

        headset = status.get("headset", {})
        gamedac = status.get("gamedac", {})

        power = headset.get("headset_power_status", {}).get("value")
        headset_bat = headset.get("headset_battery_charge", {})
        dac_bat = gamedac.get("charge_slot_battery_charge", {})

        headset_bat_val = headset_bat.get("value") if headset_bat.get("type") == "percentage" else None
        dac_bat_val = dac_bat.get("value") if dac_bat.get("type") == "percentage" else None

        self._status_bar.update(power, headset_bat_val, dac_bat_val)

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

            def _find_all(fragment) -> list:
                return [s for s in sinks if fragment in s.name]

            def _primary(lst):
                """Pick the running sink if any, else first."""
                running = [s for s in lst if getattr(s, 'state', None) and str(s.state) == "running"]
                return (running or lst or [None])[0]

            sinks_game  = _find_all(SINK_GAME)
            sinks_chat  = _find_all(SINK_CHAT)
            sinks_media = _find_all(SINK_MEDIA)

            sink_game  = _primary(sinks_game)
            sink_chat  = _primary(sinks_chat)
            sink_media = _primary(sinks_media)

            if sink_game is None and sink_chat is None and sink_media is None:
                self._set_disconnected()
                return

            self._set_connected()

            if sink_game is not None:
                pct = round(sink_game.volume.value_flat * 100)
                self._game_card.set_volume(pct)
                self._sink_game = sink_game

            if sink_chat is not None:
                pct = round(sink_chat.volume.value_flat * 100)
                self._chat_card.set_volume(pct)
                self._sink_chat = sink_chat

            if sink_media is not None:
                pct = round(sink_media.volume.value_flat * 100)
                self._media_card.set_volume(pct)
                self._sink_media = sink_media

            # Update application lists — pass all matching sinks to catch duplicates
            sink_inputs = pulse.sink_input_list()
            pulse_app_names = {si.proplist.get("application.name", "") for si in sink_inputs}
            self._update_apps(sink_inputs, sinks_game,  self._game_card)
            self._update_apps(sink_inputs, sinks_chat,  self._chat_card)
            self._update_apps(sink_inputs, sinks_media, self._media_card)
            # Also show native PipeWire streams (mpv, haruna…), skip duplicates
            self._update_native_apps(sinks, pulse_app_names)

        except Exception as exc:
            logger.warning("Error polling PulseAudio: %s", exc)
            try:
                self._pulse.close()
            except Exception:
                pass
            self._pulse = None
            self._set_disconnected()

    def _update_apps(self, sink_inputs, sinks: list, card: "AudioCard"):
        if not sinks:
            card.clear_apps()
            return
        sink_indices = {s.index for s in sinks}
        matching = [
            si for si in sink_inputs
            if si.sink in sink_indices and "application.name" in si.proplist
        ]
        card.clear_apps()
        for si in matching:
            pid = int(si.proplist.get("application.process.id", 0))
            card.add_app_tag(si.proplist["application.name"], si.index, pid, bg_color=card._accent)

    def _update_native_apps(self, pulse_sinks, already_shown: set[str] = frozenset()):
        """Add native PipeWire streams (e.g. haruna/mpv) to the correct card."""
        try:
            native = get_native_streams()
        except Exception as e:
            logger.debug("get_native_streams failed: %s", e)
            return

        card_map = {
            SINK_GAME:  self._game_card,
            SINK_CHAT:  self._chat_card,
            SINK_MEDIA: self._media_card,
        }

        for s in native:
            if s["app_name"] in already_shown:
                continue  # already listed via PulseAudio
            sink_name = s.get("sink_name") or ""
            card = next((c for bound, c in card_map.items() if bound in sink_name), None)
            if card is None:
                continue
            card.add_app_tag(s["app_name"], s["id"], int(s["pid"] or 0), bg_color=card._accent)

    def _set_disconnected(self):
        if self._connected:
            self._connected = False
            self._disconnected_label.show()
            self._game_card.set_disconnected()
            self._chat_card.set_disconnected()
            self._media_card.set_disconnected()

    def _set_connected(self):
        if not self._connected:
            self._connected = True
            self._disconnected_label.hide()
            self._game_card.set_connected()
            self._chat_card.set_connected()
            self._media_card.set_connected()

    # ── Drag & drop stream routing ────────────────────────────────────────────

    def _on_stream_drop(self, si_index: int, app_name: str, pid: int, target_sink_name: str):
        import subprocess
        pulse = self._get_pulse()
        if pulse is None:
            return
        try:
            sinks = pulse.sink_list()
            target = next((s for s in sinks if target_sink_name in s.name), None)
            if target is None:
                logger.warning("Sink %s not found", target_sink_name)
                return
            pulse.sink_input_move(si_index, target.index)
            logger.info("Moved '%s' (pid=%d) -> %s", app_name, pid, target_sink_name)
            # Record manual override — keyed by app name so it persists across restarts
            overrides = _load_overrides()
            overrides[app_name] = target_sink_name
            _save_overrides(overrides)
            subprocess.Popen([
                "pw-metadata", "0",
                "default.configured.audio.sink",
                json.dumps({"name": target_sink_name}),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            logger.warning("Error moving stream: %s", exc)

    # ── Volume change callbacks ───────────────────────────────────────────────

    def _on_media_volume_changed(self, value: int):
        self._apply_volume(self._sink_game, value)

    def _on_chat_volume_changed(self, value: int):
        self._apply_volume(self._sink_chat, value)

    def _on_aux_volume_changed(self, value: int):
        self._apply_volume(self._sink_media, value)

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
