"""
Home page — Audio mixer matching the ArctisSonar GUI visual style.
Shows horizontal audio channel cards (Game, Chat, Media, etc.) with vertical sliders.
"""
import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
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
    HDMI_ICON,
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
    COLOR_HDMI,
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
STEELSERIES_VENDOR_ID = "0x1038"


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

        self._name_lbl = QLabel(channel_name)
        self._name_lbl.setStyleSheet(
            f"color: {accent_color}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        header_layout.addWidget(self._name_lbl)
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

        apps_title = QLabel(I18n.translate("ui", "applications"))
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

    def set_name(self, name: str):
        self._name_lbl.setText(name)

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

def _status_label(key):
    if key is None:
        return "—"
    return I18n.translate("status_values", key)

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
        label = _status_label(power_status)
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
        self._sink_ext = None
        self._ext_device_nick: str | None = None  # from settings
        self._connected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── App title ─────────────────────────────────────────────────────────
        app_title = QLabel(I18n.translate("ui", "app_name"))
        app_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        app_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        root.addWidget(app_title)
        root.addSpacing(8)

        # ── Update banner (hidden by default) ─────────────────────────────────
        self._update_banner = QWidget()
        self._update_banner.setObjectName("updateBanner")
        self._update_banner.setStyleSheet(f"""
            QWidget#updateBanner {{
                background-color: {BG_CARD};
                border: 1px solid {ACCENT};
                border-radius: 8px;
                padding: 4px 12px;
            }}
        """)
        banner_layout = QHBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(12, 6, 12, 6)
        banner_layout.setSpacing(8)

        self._update_label = QLabel()
        self._update_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 10pt; background: transparent; border: none;"
        )
        banner_layout.addWidget(self._update_label, 1)

        self._update_link_btn = QPushButton(I18n.translate("ui", "view_release"))
        self._update_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_link_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {ACCENT}; "
            f"font-size: 10pt; text-decoration: underline; }}"
            f"QPushButton:hover {{ color: #FF6A28; }}"
        )
        banner_layout.addWidget(self._update_link_btn)

        self._update_install_btn = QPushButton(I18n.translate("ui", "install_update"))
        self._update_install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_install_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; border: none; border-radius: 4px; "
            f"color: #fff; font-size: 10pt; padding: 3px 12px; }}"
            f"QPushButton:hover {{ background: #FF6A28; }}"
        )
        self._update_install_btn.hide()
        banner_layout.addWidget(self._update_install_btn)

        dismiss_btn = QPushButton("\u2715")
        dismiss_btn.setFixedSize(20, 20)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {TEXT_SECONDARY}; font-size: 12pt; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        dismiss_btn.clicked.connect(self._update_banner.hide)
        banner_layout.addWidget(dismiss_btn)

        self._update_banner.hide()
        root.addWidget(self._update_banner)
        root.addSpacing(4)

        # ── Headset status pills ───────────────────────────────────────────────
        self._status_bar = _DeviceStatusBar()
        root.addWidget(self._status_bar)
        root.addSpacing(24)

        # ── Enable sliders toggle + Profiles bar (same row) ───────────────────
        toggle_row = QWidget()
        toggle_row.setStyleSheet("background: transparent;")
        toggle_layout = QHBoxLayout(toggle_row)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(16)

        toggle_lbl = QLabel(I18n.translate("ui", "enable_volume_sliders"))
        toggle_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        toggle_layout.addWidget(toggle_lbl)

        self._toggle = ToggleSwitch()
        self._toggle.set_checked(True)
        self._toggle.checkbox.toggled.connect(self._on_toggle_changed)
        toggle_layout.addWidget(self._toggle)

        # Spacer between toggle and profiles
        toggle_layout.addSpacing(24)

        # Profiles bar inline
        from arctis_sound_manager.gui.profile_bar import ProfileBar
        self.profile_bar = ProfileBar()
        toggle_layout.addWidget(self.profile_bar, stretch=1)

        root.addWidget(toggle_row)
        root.addSpacing(24)

        # ── "Disconnected" label ───────────────────────────────────────────────
        self._disconnected_label = QLabel(I18n.translate("ui", "headset_not_connected"))
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
        self._game_card = AudioCard(I18n.translate("ui", "game"), COLOR_GAME, GAME_ICON)
        self._game_card.set_on_change(self._on_media_volume_changed)
        self._game_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME))
        self._cards_layout.addWidget(self._game_card, stretch=1)

        # Chat card (Arctis_Chat sink)
        self._chat_card = AudioCard(I18n.translate("ui", "chat"), COLOR_CHAT, CHAT_ICON)
        self._chat_card.set_on_change(self._on_chat_volume_changed)
        self._chat_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT))
        self._cards_layout.addWidget(self._chat_card, stretch=1)

        # Media card (Arctis_Media sink)
        self._media_card = AudioCard(I18n.translate("ui", "media"), COLOR_AUX, MEDIA_ICON)
        self._media_card.set_on_change(self._on_aux_volume_changed)
        self._media_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA))
        self._cards_layout.addWidget(self._media_card, stretch=1)

        # External output card (HDMI, sound card, USB speakers, etc.)
        self._ext_card = AudioCard(I18n.translate("ui", "output"), COLOR_HDMI, HDMI_ICON)
        self._ext_card.set_on_change(self._on_ext_volume_changed)
        self._cards_layout.addWidget(self._ext_card, stretch=1)

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
        _t = lambda k: I18n.translate("ui", k)
        _help_text = (
            f"<b>{_t('help_mixer_title')}</b><br><br>"
            f"<b>{_t('help_mixer_game')}</b><br>"
            f"<b>{_t('help_mixer_chat')}</b><br>"
            f"<b>{_t('help_mixer_media')}</b><br>"
            f"<b>{_t('help_mixer_output')}</b><br><br>"
            f"{_t('help_mixer_sliders')}<br>"
            f"{_t('help_mixer_buttons')}"
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
            ("O", COLOR_HDMI, lambda si, app, pid: self._on_stream_drop_ext(si, app, pid)),
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

    @Slot(object)
    def update_settings(self, settings: dict):
        general = settings.get("general", {})
        self._ext_device_nick = general.get("external_output_device") or None

    # ── Update notification ────────────────────────────────────────────────────

    @Slot(str, str, str)
    def on_update_available(self, version: str, url: str, wheel_url: str = ""):
        if not version:
            return
        self._update_label.setText(
            I18n.translate("ui", "update_available").replace("{version}", version)
        )
        self._update_link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(url))
        )
        if wheel_url:
            self._wheel_url = wheel_url
            self._update_install_btn.show()
            self._update_install_btn.clicked.connect(self._do_install_update)
        self._update_banner.show()

    def _do_install_update(self):
        from arctis_sound_manager.update_checker import (
            InstallMethod, PACKAGE_MANAGER_COMMANDS,
            UpdateInstallWorker, detect_install_method,
        )
        from PySide6.QtGui import QClipboard
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

        method = detect_install_method()
        cmd = PACKAGE_MANAGER_COMMANDS.get(method)

        if cmd:
            # Package manager install — open a terminal with the command, or copy to clipboard
            from arctis_sound_manager.update_checker import build_terminal_cmd
            from arctis_sound_manager.gui.theme import (
                ACCENT, BG_BUTTON, BG_BUTTON_HOVER, BG_CARD, BG_MAIN, BORDER, TEXT_PRIMARY, TEXT_SECONDARY,
            )
            terminal_args = build_terminal_cmd(cmd)

            dlg = QDialog(self)
            dlg.setWindowTitle("Update available")
            dlg.setMinimumWidth(480)
            dlg.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)

            if terminal_args:
                lbl = QLabel("ASM was installed via your package manager.\nClick \"Update now\" to open a terminal and run the update:")
            else:
                lbl = QLabel("ASM was installed via your package manager.\nRun this command in a terminal to update:")
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

            cmd_lbl = QLabel(cmd)
            cmd_lbl.setStyleSheet(
                f"background-color: {BG_CARD}; color: {TEXT_PRIMARY}; font-family: monospace; "
                f"font-size: 10pt; padding: 10px; border-radius: 6px; border: 1px solid {BORDER};"
            )
            cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(cmd_lbl)

            btn_row = QHBoxLayout()
            btn_row.addStretch()

            if terminal_args:
                open_btn = QPushButton("Update now")
                open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                open_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ACCENT}; color: #fff; border: none; "
                    f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                    f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}"
                )
                def _open_terminal():
                    import subprocess as _sp
                    _sp.Popen(terminal_args)
                    dlg.accept()
                open_btn.clicked.connect(_open_terminal)
                btn_row.addWidget(open_btn)

            copy_btn = QPushButton("Copy command")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background-color: {'transparent' if terminal_args else ACCENT}; "
                f"color: {TEXT_PRIMARY if terminal_args else '#fff'}; border: {'1px solid ' + BORDER if terminal_args else 'none'}; "
                f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; color: {TEXT_PRIMARY}; }}"
            )
            def _copy_cmd():
                from PySide6.QtWidgets import QApplication
                from PySide6.QtGui import QClipboard
                QApplication.clipboard().setText(cmd, QClipboard.Mode.Clipboard)
                copy_btn.setText("Copied!")
                copy_btn.setEnabled(False)
                QTimer.singleShot(2000, lambda: (copy_btn.setText("Copy command"), copy_btn.setEnabled(True)))
            copy_btn.clicked.connect(_copy_cmd)
            btn_row.addWidget(copy_btn)

            close_btn = QPushButton("Close")
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            close_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BG_BUTTON}; color: {TEXT_PRIMARY}; border: none; "
                f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}"
            )
            close_btn.clicked.connect(dlg.accept)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)
            dlg.exec()
            return

        # pipx / pip — in-app install
        self._update_install_btn.setEnabled(False)
        self._update_install_btn.setText(I18n.translate("ui", "updating"))
        self._install_worker = UpdateInstallWorker(self._wheel_url)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    @Slot(bool, str)
    def _on_install_finished(self, success: bool, error_msg: str):
        if success:
            self._update_label.setText(I18n.translate("ui", "update_installed"))
            self._update_install_btn.hide()
            self._update_link_btn.hide()
            # Reinstall desktop entries, restart daemon + router + GUI
            import subprocess, sys, os
            subprocess.run(["asm-cli", "desktop", "write"], capture_output=True)
            subprocess.Popen(["systemctl", "--user", "restart", "arctis-manager"])
            subprocess.Popen(["systemctl", "--user", "restart", "arctis-video-router"])
            os.execv(sys.executable, [sys.executable, "-m", "arctis_sound_manager.scripts.gui"])
        else:
            self._update_install_btn.setText(I18n.translate("ui", "install_update"))
            self._update_install_btn.setEnabled(True)
            self._update_label.setText(f"Update failed: {error_msg}")

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

            # External output sink (non-Arctis physical sink)
            if self._ext_device_nick:
                # User chose a specific device in settings
                sink_ext = next(
                    (s for s in sinks
                     if s.proplist.get("node.nick", "") == self._ext_device_nick),
                    None,
                )
            else:
                # Auto-detect: first physical non-SteelSeries sink
                sink_ext = next(
                    (s for s in sinks
                     if s.name.startswith("alsa_output")
                     and s.proplist.get("device.vendor.id", "") != STEELSERIES_VENDOR_ID),
                    None,
                )
            if sink_ext is not None:
                pct = round(sink_ext.volume.value_flat * 100)
                self._ext_card.set_volume(pct)
                nick = sink_ext.proplist.get("node.nick", "")
                self._ext_card.set_name(nick or I18n.translate("ui", "output"))
                self._sink_ext = sink_ext
                self._ext_card.set_connected()
            else:
                self._ext_card.set_disconnected()

            # Update application lists — pass all matching sinks to catch duplicates
            sink_inputs = pulse.sink_input_list()
            pulse_app_names = {si.proplist.get("application.name", "") for si in sink_inputs}
            self._update_apps(sink_inputs, sinks_game,  self._game_card)
            self._update_apps(sink_inputs, sinks_chat,  self._chat_card)
            self._update_apps(sink_inputs, sinks_media, self._media_card)
            if sink_ext is not None:
                # Include both the physical sink and the EQ sink so apps
                # routed through the output EQ still appear on this card.
                ext_sinks = [sink_ext]
                sink_eq = next((s for s in sinks if s.name == "effect_input.sonar-output-eq"), None)
                if sink_eq is not None:
                    ext_sinks.append(sink_eq)
                self._update_apps(sink_inputs, ext_sinks, self._ext_card)
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

    def _on_stream_drop_ext(self, si_index: int, app_name: str, pid: int):
        """Route stream to the output EQ sink when available, else physical sink."""
        EQ_SINK = "effect_input.sonar-output-eq"
        try:
            import pulsectl
            with pulsectl.Pulse("asm-ext-check") as p:
                if any(s.name == EQ_SINK for s in p.sink_list()):
                    self._on_stream_drop(si_index, app_name, pid, EQ_SINK)
                    return
        except Exception:
            pass
        if self._sink_ext is not None:
            self._on_stream_drop(si_index, app_name, pid, self._sink_ext.name)

    # ── Volume change callbacks ───────────────────────────────────────────────

    def _on_media_volume_changed(self, value: int):
        self._apply_volume(self._sink_game, value)

    def _on_chat_volume_changed(self, value: int):
        self._apply_volume(self._sink_chat, value)

    def _on_aux_volume_changed(self, value: int):
        self._apply_volume(self._sink_media, value)

    def _on_ext_volume_changed(self, value: int):
        self._apply_volume(self._sink_ext, value)

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
