# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Home page — Audio mixer matching the ArctisSonar GUI visual style.
Shows horizontal audio channel cards (Game, Chat, Media, etc.) with vertical sliders.
"""
import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
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

# Applications the user dismissed from the "other applications" list. Keyed the
# same way as routing_overrides.json (app_override_key), so an app that shares a
# generic application.name with another — every Electron app reports "Chromium"
# — is not hidden by proxy.
#
# Dismissing is not the same as routing: it says "I know where this plays and I
# put it there", which is a real answer for a stream that belongs on speakers or
# a second card. Kept separate from the overrides file precisely so the two
# cannot be confused: nothing here changes where audio goes.
HIDDEN_APPS_FILE = Path.home() / ".config" / "arctis_manager" / "hidden_apps.json"


def _load_hidden_apps() -> set[str]:
    if HIDDEN_APPS_FILE.exists():
        try:
            data = json.loads(HIDDEN_APPS_FILE.read_text())
            if isinstance(data, list):
                return {str(k) for k in data}
        except Exception:
            pass
    return set()


def _save_hidden_apps(keys: set[str]) -> None:
    HIDDEN_APPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HIDDEN_APPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(keys)))
    tmp.replace(HIDDEN_APPS_FILE)

from arctis_sound_manager.gui.components import (
    CHAT_ICON,
    GAME_ICON,
    HDMI_ICON,
    HEADPHONE_ICON,
    MEDIA_ICON,
    SvgIconWidget,
)
import arctis_sound_manager.gui.theme as _theme
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

from arctis_sound_manager.channel_volumes import save_channel_volume
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.power_status import HeadsetPower, normalize_power_value
from arctis_sound_manager.pw_utils import (
    app_override_key,
    get_native_streams,
    is_external_output_sink,
)

logger = logging.getLogger("HomePage")

# PulseAudio sink name fragments to match
SINK_GAME  = "Arctis_Game"
SINK_CHAT  = "Arctis_Chat"
SINK_MEDIA = "Arctis_Media"
SINK_AUX   = "Arctis_Aux"
STEELSERIES_VENDOR_ID = "0x1038"


def _make_vertical_slider_qss(accent_color: str, groove_color: str | None = None) -> str:
    """Build the QSS for a vertical channel-volume slider.

    accent_color  — the per-channel accent (add-page / filled area above handle)
    groove_color  — the empty-groove color; defaults to BG_BUTTON from the active theme
    """
    groove = groove_color or _theme.c("BG_BUTTON")
    return f"""
        QSlider::groove:vertical {{
            width: 6px;
            background: {groove};
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


def _make_chatmix_bar_qss(track_css: str) -> str:
    """Build the QSS for the horizontal software ChatMix bar (#269).

    The track (QSlider's groove) always shows the channel colour(s) on its
    left half and Chat's colour on its right half, built by
    :func:`chatmix_bar_track_css`. sub-page/add-page are left transparent so
    they don't paint a second, handle-position-dependent fill on top —
    only the handle itself should move, not the colours (#269).
    """
    return f"""
        QSlider::groove:horizontal {{
            height: 6px;
            background: {track_css};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: white;
            border: none;
            width: 18px;
            height: 18px;
            margin: -6px 0;
            border-radius: 9px;
        }}
        QSlider::sub-page:horizontal {{
            background: transparent;
        }}
        QSlider::add-page:horizontal {{
            background: transparent;
        }}
    """


# Which theme colour key drives each ChatMix-eligible channel's slider,
# reused so the bar's channel-side fill always matches (#269).
_CHATMIX_CHANNEL_COLOR_KEYS = {
    'game': 'COLOR_GAME',
    'media': 'COLOR_AUX',
    'aux': 'COLOR_AUX2',
}


def _hard_edge_gradient_stops(
    colors: list[str], start: float = 0.0, end: float = 1.0, eps: float = 1e-4
) -> list[tuple[float, str]]:
    """QSS gradient stops splitting *colors* into equal same-width bands
    over [start, end], with a near-zero-width transition at each boundary
    so the bands read as hard edges rather than a blend.
    """
    n = len(colors)
    span = end - start
    stops: list[tuple[float, str]] = []
    for i, color in enumerate(colors):
        seg_start = start + span * i / n
        seg_end = start + span * (i + 1) / n
        stops.append((seg_start, color))
        stops.append((max(seg_start, seg_end - eps), color))
    return stops


def chatmix_bar_track_css(channel_colors: list[str], chat_color: str) -> str:
    """Build the ChatMix bar's *static* track background (#269).

    The channel colour(s) always fill the left half and Chat always fills
    the right half, regardless of the handle's position — only the handle
    itself (the actual indicator) moves; the colours don't shift with it.
    """
    colors = channel_colors or ["#ffffff"]
    stops = _hard_edge_gradient_stops(colors, 0.0, 0.5)
    stops.append((0.5, chat_color))
    stops.append((1.0, chat_color))
    parts = ", ".join(f"stop:{pos:.4f} {color}" for pos, color in stops)
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {parts})"


class _ChatMixSlider(QSlider):
    """Horizontal ChatMix slider (#269) with a fixed tick marking its centre.

    The groove alone gives no visual anchor for "both sides full volume" —
    this draws a short tick over the middle of the groove after the normal
    paint, the same way equalizer_page's curve widgets layer their own
    QPainter drawing on top of the base paintEvent.
    """

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("white"))
        pen.setWidth(2)
        painter.setPen(pen)
        cx = self.width() // 2
        cy = self.height() // 2
        painter.drawLine(cx, cy - 9, cx, cy + 9)
        painter.end()


def chatmix_bar_to_percentages(position: int) -> tuple[int, int]:
    """Translate a 0-100 ChatMix bar *position* into (channels_pct, chat_pct).

    Pure and Qt-free on purpose (testable without a QApplication). Mirrors the
    physical dial's own two-sided taper (see arctis_7.yaml's signed_percentage
    note): centre (50) leaves both sides at their own independent volume.
    Left of centre keeps the selected channel(s) at 100 and ramps Chat up from
    0 (position 0) to 100 (position 50); right of centre keeps Chat at 100
    and ramps the channel(s) down from 100 (position 50) to 0 (position 100)
    — i.e. moving toward the channels (left) pulls Chat down, moving toward
    Chat (right) pulls the channels down, Chat on the right as requested.
    """
    position = max(0, min(100, position))
    if position <= 50:
        return 100, round(position * 2)
    return round((100 - position) * 2), 100


def chatmix_percentages_to_bar_position(channels_pct: int, chat_pct: int) -> int:
    """Inverse of :func:`chatmix_bar_to_percentages`.

    The hardware dial writes straight to the sinks rather than going through
    the bar, so the bar needs this to catch up to whatever position produced
    the (channels_pct, chat_pct) it now reads back (#269).
    """
    channels_pct = max(0, min(100, channels_pct))
    chat_pct = max(0, min(100, chat_pct))
    if chat_pct <= channels_pct:
        return round(chat_pct / 2)
    return round(100 - channels_pct / 2)


# What one channel card needs, and what it may be squeezed to when the optional
# Aux channel makes a fifth. The narrow figure still fits the slider, the value
# and the device picker; below it the picker starts eliding names to nothing.
CARD_MIN_WIDTH = 260
CARD_MIN_WIDTH_TIGHT = 205

# Master isn't a channel with its own theme color — it's the headset's own
# physical volume, so it stays a neutral gray across every theme instead of
# following COLOR_GAME/COLOR_CHAT/etc.
MASTER_COLOR = "#9E9E9E"


def _read_aux_enabled() -> bool:
    """Whether the optional Aux channel is switched on.

    Read from the file rather than cached: the daemon and the GUI both act on
    this, and a stale copy would put a card on screen that has no sink behind
    it (or the reverse).
    """
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().aux_enabled)
    except Exception:  # noqa: BLE001 — a broken settings file is not worth the page
        return False


def _read_output_channel_visible() -> bool:
    """Whether the Output card should be shown (#262) — a display preference only."""
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().output_channel_visible)
    except Exception:  # noqa: BLE001 — a broken settings file is not worth the page
        return True


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
        # Set by the mixer, not fixed here: the row holds four cards normally
        # and five once the Aux channel is switched on, and 5 × 260 plus the
        # gaps forces a window wider than a 1366 px laptop screen. See
        # HomePage._fit_cards_to_row (#209).
        self.setMinimumWidth(CARD_MIN_WIDTH)
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
            icon = SvgIconWidget(svg_path, accent_color, size=36, width=48)
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
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 18pt; font-weight: bold; background: transparent;"
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
        self._apps_widget = QWidget()
        self._apps_widget.setObjectName("appsWidget")
        self._apps_widget.setStyleSheet(
            f"QWidget#appsWidget {{ background-color: {_theme.c('BG_MAIN')}; border-radius: 12px; }}"
        )
        apps_layout = QVBoxLayout(self._apps_widget)
        apps_layout.setContentsMargins(12, 10, 12, 10)
        apps_layout.setSpacing(6)

        self._apps_title = QLabel(I18n.translate("ui", "applications"))
        self._apps_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._apps_title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 9pt; font-weight: bold; background: transparent;"
        )
        apps_layout.addWidget(self._apps_title)

        self._apps_area = QVBoxLayout()
        self._apps_area.setSpacing(4)
        # What the application row currently shows, so a poll that would draw
        # the same thing can leave it alone. None means "nothing drawn yet".
        self._app_sig: tuple | None = None
        apps_layout.addLayout(self._apps_area)
        apps_layout.addStretch(1)

        self._apps_widget.setFixedHeight(100)
        outer.addWidget(self._apps_widget)

        self._on_change_callback = None
        self._on_drop_callback = None  # fn(si_index, app_name, pid)
        self.setAcceptDrops(False)

    # ── Style helpers ──────────────────────────────────────────────────────────

    def _apply_normal_style(self):
        self.setStyleSheet(
            f"""
            QWidget#audioCard {{
                background-color: {_theme.c("BG_CARD")};
                border: 1px solid {_theme.c("BORDER")};
                border-radius: 12px;
            }}
            """
        )

    def _apply_highlight_style(self):
        self.setStyleSheet(
            f"""
            QWidget#audioCard {{
                background-color: {_theme.c("BG_CARD")};
                border: 2px solid {self._accent};
                border-radius: 12px;
            }}
            """
        )

    def apply_theme(self, t=None) -> None:
        """Restyle all dynamic-color elements using the current active theme."""
        # Card background / border
        self._apply_normal_style()
        # Slider groove color follows the theme; accent stays per-channel
        self._slider.setStyleSheet(_make_vertical_slider_qss(self._accent))
        # Volume label text color
        self._pct_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 18pt; font-weight: bold; background: transparent;"
        )
        # Applications section background and title
        if hasattr(self, "_apps_widget"):
            self._apps_widget.setStyleSheet(
                f"QWidget#appsWidget {{ background-color: {_theme.c('BG_MAIN')}; border-radius: 12px; }}"
            )
        if hasattr(self, "_apps_title"):
            self._apps_title.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 9pt; font-weight: bold; background: transparent;"
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
        self._app_sig = None
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
        # Ignored, not the default Preferred: on the narrow (Aux-on) card
        # width there isn't room for both a long app name and every move
        # button (up to five once Aux and Output are counted). Preferred
        # would keep demanding the name's full width and push the rightmost
        # button — Output — past the card's edge instead of shrinking the
        # name first.
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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


class _UnassignedRow(QWidget):
    """One application that is playing but is not on any ASM channel.

    Same move buttons as :class:`_AppTag`, plus two things that only make sense
    here: where the stream is currently playing, and a dismiss button.

    Showing the current output is what makes the list intelligible. Without it
    the user sees applications with no explanation of why they are listed
    separately rather than in a card, which is the confusion this area exists
    to remove in the first place.
    """

    def __init__(self, app_name: str, playing_on: str, si_index: int, pid: int,
                 on_dismiss, color: str, muted_color: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(8)
        self.setFixedHeight(26)
        self.setStyleSheet(
            f"background-color: #1e2530; border-radius: 4px; border: 1px solid {muted_color};"
        )

        name_lbl = QLabel(app_name)
        name_lbl.setStyleSheet(
            f"color: {color}; font-size: 11pt; font-weight: bold; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(name_lbl)

        where_lbl = QLabel(playing_on)
        where_lbl.setStyleSheet(
            f"color: {muted_color}; font-size: 9pt; "
            f"background: transparent; border: none;"
        )
        where_lbl.setToolTip(playing_on)
        layout.addWidget(where_lbl, stretch=1)

        btns = QWidget()
        btns.setStyleSheet("background: transparent; border: none;")
        btn_layout = QHBoxLayout(btns)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(3)

        # Reuse the channel registry so these behave exactly like the buttons on
        # the cards, and cannot drift from them.
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

        dismiss = QPushButton("×")
        dismiss.setFixedSize(18, 18)
        dismiss.setToolTip(I18n.translate("ui", "unassigned_dismiss_tip"))
        dismiss.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {muted_color}; "
            f"border: 1px solid {muted_color}; border-radius: 3px; "
            f"font-size: 9pt; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ background: {muted_color}; color: #000; }}"
        )
        dismiss.clicked.connect(lambda checked=False: on_dismiss())
        btn_layout.addWidget(dismiss)

        layout.addWidget(btns)


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

_STATUS_ONLINE_COLOR  = "#04C5A8"   # teal
_STATUS_CHARGING_COLOR = "#2791CE"  # blue
_STATUS_UNKNOWN_COLOR = "#8D96AA"   # gray


def _status_color(key) -> str:
    """Pill colour for a headset_power_status value, in either vocabulary.

    Device YAMLs speak two dialects: 'online'/'offline'/'cable_charging'
    (Nova Pro Wireless, Elite, Omni, Arctis Pro Wireless) and plain 'on'/'off'
    (Nova 5, Nova 7*, Arctis 7+, 9, 1 Wireless). This map only listed the first
    set, so on half the supported headsets a powered-on device fell through to
    the gray default and looked indistinguishable from a disconnected one —
    the same split power_status.normalize_power_value() exists to paper over.
    'cable_charging' normalizes to ON but keeps its own blue.
    """
    if isinstance(key, str) and key.strip().lower() == "cable_charging":
        return _STATUS_CHARGING_COLOR
    if normalize_power_value(key) is HeadsetPower.ON:
        return _STATUS_ONLINE_COLOR
    return _STATUS_UNKNOWN_COLOR

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
            f"background: transparent; font-size: 11pt; font-weight: bold; color: {_theme.c('TEXT_PRIMARY')}; border: none;"
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
            f"QWidget#pill {{ background-color: {_theme.c('BG_CARD')}; border-radius: 14px; "
            f"border: 1px solid {color}; }}"
        )

    def apply_theme(self, t=None) -> None:
        self._text.setStyleSheet(
            f"background: transparent; font-size: 11pt; font-weight: bold; color: {_theme.c('TEXT_PRIMARY')}; border: none;"
        )
        # Re-apply the pill background; preserve the current border color from the dot style
        dot_style = self._dot.styleSheet()
        # Extract current dot color for the border
        color = "#8D96AA"
        for part in dot_style.split(";"):
            part = part.strip()
            if part.startswith("color:"):
                color = part.split(":", 1)[1].strip()
                break
        self._update_style(color)

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

    def apply_theme(self, t=None) -> None:
        for pill in (self._conn_pill, self._headset_bat_pill, self._dac_bat_pill):
            pill.apply_theme()

    def set_no_device(self):
        # Through I18n like every other user-visible string: this one was
        # hardcoded, so it stayed English in every translation (#202 shows it
        # in a screenshot). The key already existed.
        self._conn_pill.set_value(
            I18n.get_instance().translate('ui', 'no_device_detected'), "#8D96AA")
        self._headset_bat_pill.set_visible(False)
        self._dac_bat_pill.set_visible(False)

    def update(self, power_status, headset_bat, dac_bat):
        color = _status_color(power_status)
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
        self._sink_aux = None
        self._sink_master = None
        self._sink_ext = None
        self._ext_device_nick: str | None = None  # from settings
        self._connected = False
        self._last_device_name: str = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)


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

        self._dismiss_btn = QPushButton("\u2715")
        self._dismiss_btn.setFixedSize(20, 20)
        self._dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {TEXT_SECONDARY}; font-size: 12pt; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._dismiss_btn.clicked.connect(self._update_banner.hide)
        banner_layout.addWidget(self._dismiss_btn)

        self._update_banner.hide()
        root.addWidget(self._update_banner)
        root.addSpacing(4)

        # ── Headset name ───────────────────────────────────────────────────────
        self._headset_name_lbl = QLabel("")
        self._headset_name_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14pt; font-weight: bold; background: transparent;"
        )
        self._headset_name_lbl.hide()
        root.addWidget(self._headset_name_lbl)
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

        self._toggle_lbl = QLabel(I18n.translate("ui", "enable_volume_sliders"))
        self._toggle_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 11pt; background: transparent;"
        )
        toggle_layout.addWidget(self._toggle_lbl)

        self._toggle = ToggleSwitch()
        self._toggle.set_checked(True)
        self._toggle.checkbox.toggled.connect(self._on_toggle_changed)
        toggle_layout.addWidget(self._toggle)

        # Spacer between toggle and profiles
        toggle_layout.addSpacing(24)

        # Profiles bar inline
        from arctis_sound_manager.gui.profile_bar import ProfileBar
        self.profile_bar = ProfileBar()
        self.profile_bar.sig_toggle_aux.connect(self._set_aux_enabled)
        self.profile_bar.sig_toggle_output.connect(self._set_output_channel_visible)
        toggle_layout.addWidget(self.profile_bar, stretch=1)

        # Reclaim audio — one-click fix for apps stuck on a non-ASM output
        # device (HDMI, S/PDIF, another DAC…)
        self._reclaim_btn = QPushButton(I18n.translate("ui", "reclaim_audio"))
        self._reclaim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reclaim_btn.setToolTip(I18n.translate("ui", "reclaim_audio_tip"))
        self._reclaim_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; border: none; border-radius: 4px; "
            f"color: #fff; font-size: 10pt; padding: 4px 14px; }}"
            f"QPushButton:hover {{ background: #FF6A28; }}"
        )
        self._reclaim_btn.clicked.connect(self._on_reclaim_audio)
        toggle_layout.addWidget(self._reclaim_btn)

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
        cards_outer.setStyleSheet("background: transparent;")
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

        # Master card: the headset's own physical volume, downstream of every
        # channel below. Kept separate from Output (an unrelated external
        # device the user may have pinned) so the hardware volume wheel has
        # its own dedicated target instead of being capped by whatever
        # headroom that device's own slider allows. Placed leftmost since it
        # isn't one of the routable channels.
        self._master_card = AudioCard(I18n.translate("ui", "master"), MASTER_COLOR, HEADPHONE_ICON)
        self._master_card.set_on_change(self._on_master_volume_changed)
        self._cards_layout.addWidget(self._master_card, stretch=1)

        # Game card — use active-theme color at construction time
        self._game_card = AudioCard(I18n.translate("ui", "game"), _theme.c("COLOR_GAME"), GAME_ICON)
        self._game_card.set_on_change(self._on_media_volume_changed)
        self._game_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME))
        self._cards_layout.addWidget(self._game_card, stretch=1)

        # Chat card (Arctis_Chat sink)
        self._chat_card = AudioCard(I18n.translate("ui", "chat"), _theme.c("COLOR_CHAT"), CHAT_ICON)
        self._chat_card.set_on_change(self._on_chat_volume_changed)
        self._chat_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT))
        self._cards_layout.addWidget(self._chat_card, stretch=1)

        # Media card (Arctis_Media sink)
        self._media_card = AudioCard(I18n.translate("ui", "media"), _theme.c("COLOR_AUX"), MEDIA_ICON)
        self._media_card.set_on_change(self._on_aux_volume_changed)
        self._media_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA))
        self._cards_layout.addWidget(self._media_card, stretch=1)

        # Aux card — the opt-in fourth playback channel (#209). Built once and
        # hidden, rather than created on demand: a card that exists from the
        # start keeps its device picker, its drop target and its styling in
        # step with the other four without a second code path.
        self._aux_card = AudioCard(I18n.translate("ui", "aux"), _theme.c("COLOR_AUX2"), MEDIA_ICON)
        self._aux_card.set_on_change(self._on_aux_channel_volume_changed)
        self._aux_card.set_on_drop(lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_AUX))
        self._aux_card.setVisible(False)
        self._cards_layout.addWidget(self._aux_card, stretch=1)

        # External output card (HDMI, sound card, USB speakers, etc.)
        self._ext_card = AudioCard(I18n.translate("ui", "output"), _theme.c("COLOR_HDMI"), HDMI_ICON)
        self._ext_card.set_on_change(self._on_ext_volume_changed)
        self._cards_layout.addWidget(self._ext_card, stretch=1)

        cards_outer_layout.addWidget(self._cards_widget, stretch=6)
        cards_outer_layout.addStretch(1)

        # ── ChatMix bar (#269) — software crossfade between Chat and the
        # configured channel(s), aligned under the cards above.
        chatmix_bar_outer = QWidget()
        chatmix_bar_outer.setStyleSheet("background: transparent;")
        chatmix_bar_outer_layout = QHBoxLayout(chatmix_bar_outer)
        chatmix_bar_outer_layout.setContentsMargins(0, 0, 0, 0)
        chatmix_bar_outer_layout.setSpacing(0)
        chatmix_bar_outer_layout.addStretch(1)

        chatmix_column = QWidget()
        chatmix_column.setStyleSheet("background: transparent;")
        chatmix_column_layout = QVBoxLayout(chatmix_column)
        chatmix_column_layout.setContentsMargins(0, 0, 0, 0)
        chatmix_column_layout.setSpacing(6)

        chatmix_bar_row = QWidget()
        chatmix_bar_row.setStyleSheet("background: transparent;")
        chatmix_bar_row_layout = QHBoxLayout(chatmix_bar_row)
        chatmix_bar_row_layout.setContentsMargins(0, 12, 0, 0)
        chatmix_bar_row_layout.setSpacing(10)

        self._chatmix_bar_channels_lbl = QLabel(I18n.translate("ui", "game"))
        self._chatmix_bar_channels_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        chatmix_bar_row_layout.addWidget(self._chatmix_bar_channels_lbl)

        self._chatmix_bar = _ChatMixSlider(Qt.Orientation.Horizontal)
        self._chatmix_bar.setMinimum(0)
        self._chatmix_bar.setMaximum(100)
        self._chatmix_bar.setStyleSheet(
            _make_chatmix_bar_qss(
                chatmix_bar_track_css([_theme.c("COLOR_GAME")], _theme.c("COLOR_CHAT"))
            )
        )
        self._chatmix_bar.setToolTip(I18n.translate("ui", "chatmix_bar_hint"))
        self._chatmix_bar.blockSignals(True)
        self._chatmix_bar.setValue(50)
        self._chatmix_bar.blockSignals(False)
        self._chatmix_bar.valueChanged.connect(self._on_chatmix_bar_changed)
        chatmix_bar_row_layout.addWidget(self._chatmix_bar, stretch=1)

        self._chatmix_bar_chat_lbl = QLabel(I18n.translate("ui", "chat"))
        self._chatmix_bar_chat_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9pt; background: transparent;"
        )
        chatmix_bar_row_layout.addWidget(self._chatmix_bar_chat_lbl)

        chatmix_column_layout.addWidget(chatmix_bar_row)

        # Which channel(s) ride alongside Game on the bar's non-chat side
        # (#249, #269): small checkboxes next to the channel name, same
        # pattern used for sink selection elsewhere, rather than a full
        # sentence repeated on every card.
        chatmix_channels_row = QWidget()
        chatmix_channels_row.setStyleSheet("background: transparent;")
        chatmix_channels_layout = QHBoxLayout(chatmix_channels_row)
        chatmix_channels_layout.setContentsMargins(0, 0, 0, 0)
        chatmix_channels_layout.setSpacing(16)
        chatmix_channels_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._chatmix_channel_checkboxes: dict[str, QCheckBox] = {}
        for channel in ("game", "media", "aux"):
            cb = QCheckBox(I18n.translate("ui", channel))
            cb.setToolTip(I18n.translate("ui", "chatmix_include_hint"))
            cb.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
            )
            cb.toggled.connect(lambda checked, ch=channel: self._on_chatmix_toggle(ch, checked))
            self._chatmix_channel_checkboxes[channel] = cb
            chatmix_channels_layout.addWidget(cb)

        chatmix_column_layout.addWidget(chatmix_channels_row)

        chatmix_bar_outer_layout.addWidget(chatmix_column, stretch=2)
        chatmix_bar_outer_layout.addStretch(1)

        self._apply_aux_visibility(_read_aux_enabled())
        self._apply_output_visibility(_read_output_channel_visible())

        root.addWidget(cards_outer, stretch=1)
        root.addWidget(chatmix_bar_outer)

        # ── Other applications ────────────────────────────────────────────────
        # Applications that are playing but sit on no ASM channel are invisible
        # in the cards above, because those only list what is on their own sink.
        # That happens whenever the system default is something other than a
        # channel: the headset's own hardware device (common with a Nova Pro
        # dock, where the AUX output feeds speakers), an HDMI output, a second
        # card. The audio is audible, so nothing looks broken, yet the mixer
        # appears empty and the only way to route anything is an external tool.
        #
        # Deliberately quiet: hidden entirely when empty, collapsible, and
        # worded as a list rather than a warning. Sending a stream elsewhere is
        # a legitimate choice, and an app that nags about it every day is worse
        # than one that stays silent.
        self._unassigned_wrap = QWidget()
        self._unassigned_wrap.setStyleSheet("background: transparent;")
        unassigned_outer = QVBoxLayout(self._unassigned_wrap)
        unassigned_outer.setContentsMargins(0, 16, 0, 0)
        unassigned_outer.setSpacing(6)

        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self._unassigned_toggle = QPushButton()
        self._unassigned_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unassigned_toggle.clicked.connect(self._on_unassigned_toggle)
        header_layout.addWidget(self._unassigned_toggle)
        header_layout.addStretch(1)

        self._unassigned_show_hidden = QPushButton()
        self._unassigned_show_hidden.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unassigned_show_hidden.setToolTip(
            I18n.translate("ui", "unassigned_show_hidden_tip"))
        self._unassigned_show_hidden.clicked.connect(self._on_unhide_all)
        header_layout.addWidget(self._unassigned_show_hidden)

        unassigned_outer.addWidget(header)

        self._unassigned_body = QWidget()
        self._unassigned_body.setStyleSheet("background: transparent;")
        self._unassigned_area = QVBoxLayout(self._unassigned_body)
        self._unassigned_area.setContentsMargins(0, 0, 0, 0)
        self._unassigned_area.setSpacing(4)
        unassigned_outer.addWidget(self._unassigned_body)

        self._unassigned_expanded = True
        self._hidden_apps: set[str] = _load_hidden_apps()
        # The row the "other applications" list is currently showing, so a
        # poll that would draw the same one can leave it alone.
        self._unassigned_sig: tuple | None = None
        self._unassigned_wrap.setVisible(False)
        self._style_unassigned()
        root.addWidget(self._unassigned_wrap)

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
        # Note: apply_theme() rebuilds this registry with fresh colors on every theme change.
        _AppTag._cards_registry = [
            ("G", _theme.c("COLOR_GAME"), lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME)),
            ("C", _theme.c("COLOR_CHAT"), lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT)),
            ("M", _theme.c("COLOR_AUX"),  lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA)),
            ("O", _theme.c("COLOR_HDMI"), lambda si, app, pid: self._on_stream_drop_ext(si, app, pid)),
        ]
        self._refresh_app_tag_buttons()

        # ── Polling timer ─────────────────────────────────────────────────────
        # Started from showEvent, not here: every tick forks a `pw-dump` to
        # enumerate native streams, and a timer started in the constructor
        # keeps running for the lifetime of the tray process once the window
        # has been opened even once — invisible work, twice a second, forever.
        # On a large PipeWire graph that was measured at 40-75% CPU with the
        # window closed, enough to push the HeSuVi convolver into xruns and
        # make the audio crackle (#182).
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_volumes)

        self._combo_tick = 0
        # Last get_native_streams() result — reused on the ticks that skip the
        # pw-dump rescan (#182).
        self._native_cache: list = []

        # Apply the currently active theme so the initial render is correct
        # even if a non-default theme was saved in settings.
        self.apply_theme()

    # ── Theme propagation ──────────────────────────────────────────────────────

    def apply_theme(self, t=None) -> None:
        """Restyle the home page for the current active theme."""
        # Update page background
        self.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._cards_widget.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")

        # App title
        if hasattr(self, "_app_title"):
            self._app_title.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 28pt; font-weight: bold; background: transparent;"
            )

        # Headset name label (only restyle if online — offline stays gray)
        if hasattr(self, "_headset_name_lbl") and self._headset_name_lbl.isVisible():
            style = self._headset_name_lbl.styleSheet()
            if "#8D96AA" not in style:
                self._headset_name_lbl.setStyleSheet(
                    f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 14pt; font-weight: bold; background: transparent;"
                )

        # Update banner widgets
        if hasattr(self, "_update_banner"):
            self._update_banner.setStyleSheet(f"""
                QWidget#updateBanner {{
                    background-color: {_theme.c('BG_CARD')};
                    border: 1px solid {_theme.c('ACCENT')};
                    border-radius: 8px;
                    padding: 4px 12px;
                }}
            """)
        if hasattr(self, "_update_label"):
            self._update_label.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; background: transparent; border: none;"
            )
        if hasattr(self, "_update_link_btn"):
            self._update_link_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {_theme.c('ACCENT')}; "
                f"font-size: 10pt; text-decoration: underline; }}"
                f"QPushButton:hover {{ color: #FF6A28; }}"
            )
        if hasattr(self, "_update_install_btn"):
            self._update_install_btn.setStyleSheet(
                f"QPushButton {{ background: {_theme.c('ACCENT')}; border: none; border-radius: 4px; "
                f"color: #fff; font-size: 10pt; padding: 3px 12px; }}"
                f"QPushButton:hover {{ background: #FF6A28; }}"
            )
        if hasattr(self, "_dismiss_btn"):
            self._dismiss_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {_theme.c('TEXT_SECONDARY')}; font-size: 12pt; }}"
                f"QPushButton:hover {{ color: {_theme.c('TEXT_PRIMARY')}; }}"
            )

        # Toggle label and disconnected label
        if hasattr(self, "_toggle_lbl"):
            self._toggle_lbl.setStyleSheet(
                f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 11pt; background: transparent;"
            )
        if hasattr(self, "_disconnected_label"):
            self._disconnected_label.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 14pt; font-style: italic; background: transparent;"
            )

        # Status bar pills
        if hasattr(self, "_status_bar"):
            self._status_bar.apply_theme()

        # Pull fresh per-channel accent colors from the active theme
        color_game = _theme.c("COLOR_GAME")
        color_chat = _theme.c("COLOR_CHAT")
        color_aux  = _theme.c("COLOR_AUX")
        color_hdmi = _theme.c("COLOR_HDMI")
        color_master = MASTER_COLOR

        # Update each card's accent and restyle
        self._game_card._accent = color_game
        self._chat_card._accent = color_chat
        self._media_card._accent = color_aux
        self._ext_card._accent = color_hdmi
        self._master_card._accent = color_master

        for card in (self._game_card, self._chat_card, self._media_card,
                     self._master_card, self._ext_card):
            card.apply_theme(t)

        # Update _AppTag registry with fresh accent colors
        _AppTag._cards_registry = [
            ("G", color_game, lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME)),
            ("C", color_chat, lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT)),
            ("M", color_aux,  lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA)),
            *([("A", _theme.c("COLOR_AUX2"),
                lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_AUX))]
              if not self._aux_card.isHidden() else []),
            ("O", color_hdmi, lambda si, app, pid: self._on_stream_drop_ext(si, app, pid)),
        ]

        self._style_unassigned()

        # Update channel-name labels in cards
        self._game_card._name_lbl.setStyleSheet(
            f"color: {color_game}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        self._chat_card._name_lbl.setStyleSheet(
            f"color: {color_chat}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        self._media_card._name_lbl.setStyleSheet(
            f"color: {color_aux}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        self._ext_card._name_lbl.setStyleSheet(
            f"color: {color_hdmi}; font-size: 14pt; font-weight: normal; background: transparent;"
        )
        self._master_card._name_lbl.setStyleSheet(
            f"color: {color_master}; font-size: 14pt; font-weight: normal; background: transparent;"
        )

    # ── Toggle handler ─────────────────────────────────────────────────────────

    def _on_toggle_changed(self, enabled: bool):
        self._game_card.setEnabled(enabled)
        self._chat_card.setEnabled(enabled)
        self._media_card.setEnabled(enabled)

    # ── Reclaim audio handler ────────────────────────────────────────────────

    def _on_reclaim_audio(self):
        try:
            from arctis_sound_manager.pw_utils import reclaim_misrouted_streams
            count, names = reclaim_misrouted_streams()
            if count > 0:
                logger.info("Reclaimed %d misrouted stream(s): %s", count, ", ".join(names))
            else:
                logger.debug("Reclaim audio: nothing to move, all apps already on headset")
            # Refresh the app lists shown on the cards immediately rather than
            # waiting for the next poll tick.
            self._poll_volumes()
        except Exception as exc:
            logger.warning("_on_reclaim_audio failed: %s", exc)

    # ── D-Bus status signal handler ───────────────────────────────────────────

    @Slot(object)
    def update_status(self, status: dict):
        if not status:
            self._status_bar.set_no_device()
            self._headset_name_lbl.hide()
            return

        headset = status.get("headset", {})
        gamedac = status.get("gamedac", {})

        power = headset.get("headset_power_status", {}).get("value")
        headset_bat = headset.get("headset_battery_charge", {})
        dac_bat = gamedac.get("charge_slot_battery_charge", {})

        headset_bat_val = headset_bat.get("value") if headset_bat.get("type") == "percentage" else None
        dac_bat_val = dac_bat.get("value") if dac_bat.get("type") == "percentage" else None

        # The wireless adapter keeps serving the last battery percentage it saw
        # long after the headset is switched off, so the number alone is not a
        # "headset present" signal — the home page went on displaying a frozen
        # "Headset 57%" for a headset that was off, which reads as a battery
        # gauge that is simply wrong. The tray already gates on power status for
        # exactly this reason (#124 / PR #125, QSystrayApp._extract_battery_percent);
        # the main window never got the same treatment. Only a definite OFF
        # hides it: UNKNOWN ('standby', a vocabulary we have no rule for, or a
        # device that reports no power status at all) must keep showing the
        # reading rather than silently dropping a working gauge.
        if normalize_power_value(power) is HeadsetPower.OFF:
            headset_bat_val = None

        self._status_bar.update(power, headset_bat_val, dac_bat_val)

        if self._last_device_name:
            # Was `power == "offline"`, which only ever matched the Nova Pro
            # dialect: on a Nova 7 (reporting 'off') the name stayed lit as if
            # the headset were still connected.
            if normalize_power_value(power) is HeadsetPower.OFF:
                self._headset_name_lbl.setStyleSheet(
                    "color: #8D96AA; font-size: 14pt; font-weight: bold; background: transparent;"
                )
            else:
                self._headset_name_lbl.setStyleSheet(
                    f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 14pt; font-weight: bold; background: transparent;"
                )
            self._headset_name_lbl.setText(self._last_device_name)
            self._headset_name_lbl.show()
        else:
            self._headset_name_lbl.hide()

    @Slot(object)
    def update_settings(self, settings: dict):
        general = settings.get("general", {})
        self._ext_device_nick = general.get("external_output_device") or None
        device_name = settings.get("device_name", "")
        if device_name:
            self._last_device_name = device_name

    # ── Update notification ────────────────────────────────────────────────────

    @Slot(str, str, str)
    def on_update_available(self, version: str, url: str, wheel_url: str = ""):
        if not version:
            self._update_banner.hide()
            return

        # Proactive multi-install guard: if ASM is installed by more than one
        # method (e.g. a stale pip --user shadowing an RPM), the "update
        # available" signal itself may be a phantom caused by the older copy
        # reporting its version.  Surface the multi-install situation right
        # away instead of showing a misleading "update available" banner.
        from arctis_sound_manager.update_checker import detect_all_install_methods
        _all_methods = detect_all_install_methods()
        if len(_all_methods) > 1:
            self._update_label.setText(I18n.translate("ui", "update_multi_install"))
            self._update_link_btn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(url))
            )
            # Hide the "Update now" button — it would install on top of one
            # copy without cleaning the others, making the situation worse.
            self._update_install_btn.hide()
            self._update_banner.show()
            return

        self._update_label.setText(
            I18n.translate("ui", "update_available").replace("{version}", version)
        )
        self._update_link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(url))
        )
        self._wheel_url = wheel_url
        self._update_install_btn.show()
        self._update_install_btn.clicked.connect(self._do_install_update)
        self._update_banner.show()

    @Slot(str)
    def on_restart_required(self, version: str):
        """The package was upgraded while this window stayed open.

        Takes precedence over "an update is available": the update is no longer
        available, it is installed — what is missing is a restart. Leaving the
        old banner up would invite an upgrade that has already happened.
        """
        if not version:
            return

        self._update_label.setText(
            I18n.translate("ui", "restart_required").replace("{version}", version)
        )
        self._update_link_btn.hide()

        # The banner's action button is reused rather than added to: a second
        # button next to "Update now" would be two ways to do different things
        # under one message.
        try:
            self._update_install_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._update_install_btn.setText(I18n.translate("ui", "restart_now"))
        self._update_install_btn.clicked.connect(self._do_restart)
        self._update_install_btn.show()
        self._update_banner.show()

    def _do_restart(self):
        from arctis_sound_manager.runtime_staleness import (
            restart_gui, restart_user_services,
        )

        # Services first: the daemon owns the USB device and the PipeWire links,
        # so it must be the new code before the GUI reconnects to it.
        restart_user_services()
        restart_gui()

    def _do_install_update(self):
        from arctis_sound_manager.update_checker import (
            InstallMethod, UpdateInstallWorker, detect_all_install_methods,
            package_manager_command,
        )
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

        # Multi-install detection: refuse to update if ASM is installed by more
        # than one method (would silently create the dup-binary mess described
        # in #22). Surface a clear dialog pointing to clean-reinstall.sh.
        all_methods = detect_all_install_methods()
        if len(all_methods) > 1:
            from arctis_sound_manager.gui.install_dialogs import show_multi_install_warning
            show_multi_install_warning(self, all_methods)
            return

        method = all_methods[0] if all_methods else InstallMethod.PIP
        cmd = package_manager_command(method)

        # A hand-downloaded .deb/.rpm/.pkg is tracked by no repository, so the
        # upgrade command would report success and change nothing, and the
        # banner would come back forever (#163). Detect that and offer the
        # repository-setup command or a release download instead.
        if cmd and method in (InstallMethod.APT, InstallMethod.RPM, InstallMethod.PACMAN):
            from arctis_sound_manager.update_checker import (
                repo_setup_command, upgrade_source_available)
            if not upgrade_source_available(method):
                self._show_hand_installed_update_dialog(repo_setup_command(method))
                return

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
                # Under Wayland the clipboard belongs to the focused window;
                # a compositor drops setText() from a window without focus
                # (same fix as system_deps_dialog.py:427).
                dlg.activateWindow()
                QApplication.clipboard().setText(cmd, QClipboard.Mode.Clipboard)
                copy_btn.setText("Copied!")
                copy_btn.setEnabled(False)
                # context=copy_btn: the timer is cancelled if the dialog (and its
                # button) is closed before it fires, avoiding a shiboken
                # use-after-free on the deleted C++ object (issue #100).
                QTimer.singleShot(2000, copy_btn, lambda: (copy_btn.setText("Copy command"), copy_btn.setEnabled(True)))
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

    def _show_hand_installed_update_dialog(self, setup_cmd: str | None) -> None:
        """ASM was installed from a hand-downloaded package that no repository
        tracks, so the upgrade command would change nothing (#163). Offer the two
        real ways forward instead: add the repository, or download the release."""
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QClipboard, QDesktopServices
        from PySide6.QtWidgets import (
            QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )
        from arctis_sound_manager.gui.theme import (
            ACCENT, BG_BUTTON, BG_BUTTON_HOVER, BG_CARD, BG_MAIN, BORDER,
            TEXT_PRIMARY, TEXT_SECONDARY,
        )

        releases_url = "https://github.com/loteran/Arctis-Sound-Manager/releases/latest"

        dlg = QDialog(self)
        dlg.setWindowTitle("Update available")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet(f"background-color: {BG_MAIN}; color: {TEXT_PRIMARY};")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        msg = QLabel(
            "ASM was installed from a package you downloaded by hand, so it isn't "
            "tracked by any repository — an automatic update has nowhere to fetch "
            "the new version from, which is why running it changes nothing.\n\n"
            "Two ways to update:")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        layout.addWidget(msg)

        if setup_cmd:
            opt1 = QLabel("1. Add the repository (recommended) — updates then apply automatically:")
            opt1.setWordWrap(True)
            opt1.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10pt; background: transparent;")
            layout.addWidget(opt1)

            cmd_lbl = QLabel(setup_cmd)
            cmd_lbl.setWordWrap(True)
            cmd_lbl.setStyleSheet(
                f"background-color: {BG_CARD}; color: {TEXT_PRIMARY}; font-family: monospace; "
                f"font-size: 9.5pt; padding: 10px; border-radius: 6px; border: 1px solid {BORDER};")
            cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(cmd_lbl)

        opt2 = QLabel("2. Or download the latest release and install it the same way you did before:")
        opt2.setWordWrap(True)
        opt2.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10pt; background: transparent;")
        layout.addWidget(opt2)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if setup_cmd:
            copy_btn = QPushButton("Copy command")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {TEXT_PRIMARY}; "
                f"border: 1px solid {BORDER}; border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
                f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}")

            def _copy():
                QApplication.clipboard().setText(setup_cmd, QClipboard.Mode.Clipboard)
                copy_btn.setText("Copied!")
                copy_btn.setEnabled(False)
                QTimer.singleShot(2000, copy_btn, lambda: (
                    copy_btn.setText("Copy command"), copy_btn.setEnabled(True)))
            copy_btn.clicked.connect(_copy)
            btn_row.addWidget(copy_btn)

        dl_btn = QPushButton("Download latest release")
        dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dl_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT}; color: #fff; border: none; "
            f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}")
        dl_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(releases_url)))
        btn_row.addWidget(dl_btn)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_BUTTON}; color: {TEXT_PRIMARY}; border: none; "
            f"border-radius: 6px; padding: 8px 18px; font-size: 10pt; }}"
            f"QPushButton:hover {{ background-color: {BG_BUTTON_HOVER}; }}")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    @Slot(bool, str)
    def _on_install_finished(self, success: bool, error_msg: str):
        if success:
            self._update_label.setText(I18n.translate("ui", "update_installed"))
            self._update_install_btn.hide()
            self._update_link_btn.hide()
            # Run the full asm-setup so udev rules are reloaded, pipewire
            # restarted, services reenabled with the new binary path. Streamed
            # in FirstRunDialog so the user sees progress + the pkexec prompt.
            from pathlib import Path
            (Path.home() / ".config" / "arctis_manager" / ".setup_done").unlink(missing_ok=True)
            from arctis_sound_manager.gui.first_run_dialog import FirstRunDialog
            FirstRunDialog(self).exec()
            # Restart the daemon + router + GUI on the new binary.
            import sys, os
            from arctis_sound_manager import service_control as sc
            sc.restart("arctis-manager", "arctis-video-router")
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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Poll once straight away so the page is current the moment it appears,
        # rather than showing stale values until the first tick lands.
        self._poll_volumes()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        # Nothing here is visible any more, and each tick costs a pw-dump
        # subprocess — see the timer's construction and #182.
        self._timer.stop()

    def _sync_chatmix_bar(self, game_pct, chat_pct, media_pct, aux_pct) -> None:
        """Follow the hardware dial: it writes straight to the sinks (via the
        daemon's set_mix), bypassing the bar, so without this the bar would
        sit still while the vertical cards it mirrors visibly move (#269).
        """
        bar = getattr(self, "_chatmix_bar", None)
        if bar is None or chat_pct is None or bar.isSliderDown():
            return

        try:
            from arctis_sound_manager.settings import GeneralSettings
            channels = GeneralSettings.read_from_file().chatmix_channels_or_default()
        except Exception:  # noqa: BLE001 — a broken settings file just skips this tick
            channels = ["game"]

        pct_by_channel = {"game": game_pct, "media": media_pct, "aux": aux_pct}
        values = [pct_by_channel[ch] for ch in channels if pct_by_channel.get(ch) is not None]
        if not values:
            return

        channels_pct = round(sum(values) / len(values))
        position = chatmix_percentages_to_bar_position(channels_pct, chat_pct)
        if position == bar.value():
            return

        bar.blockSignals(True)
        bar.setValue(position)
        bar.blockSignals(False)

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
            # Empty while the optional channel is off, which _update_apps
            # already reads as "no rows".
            sinks_aux   = _find_all(SINK_AUX)

            sink_game  = _primary(sinks_game)
            sink_chat  = _primary(sinks_chat)
            sink_media = _primary(sinks_media)

            if sink_game is None and sink_chat is None and sink_media is None:
                self._set_disconnected()
                return

            self._set_connected()

            game_pct = chat_pct = media_pct = aux_pct = None

            if sink_game is not None:
                game_pct = round(sink_game.volume.value_flat * 100)
                self._game_card.set_volume(game_pct)
                self._sink_game = sink_game

            if sink_chat is not None:
                chat_pct = round(sink_chat.volume.value_flat * 100)
                self._chat_card.set_volume(chat_pct)
                self._sink_chat = sink_chat

            if sink_media is not None:
                media_pct = round(sink_media.volume.value_flat * 100)
                self._media_card.set_volume(media_pct)
                self._sink_media = sink_media

            # Aux exists only while the channel is switched on, so a missing
            # sink here is the normal case and not a fault to report.
            sink_aux = next((s for s in sinks if s.name == SINK_AUX), None)
            if sink_aux is not None:
                aux_pct = round(sink_aux.volume.value_flat * 100)
                self._aux_card.set_volume(aux_pct)
                self._sink_aux = sink_aux

            self._sync_chatmix_bar(game_pct, chat_pct, media_pct, aux_pct)

            # Master: the headset's own physical output, downstream of every
            # channel above — independent of whatever device is selected for
            # Output below, which may be a different, unrelated sink entirely.
            sink_master = next(
                (s for s in sinks
                 if (s.name.startswith("alsa_output") or s.name.startswith("bluez_output"))
                 and ("SteelSeries" in s.name
                      or s.proplist.get("device.vendor.id", "") == STEELSERIES_VENDOR_ID)),
                None,
            )
            if sink_master is not None:
                master_pct = round(sink_master.volume.value_flat * 100)
                self._master_card.set_volume(master_pct)
                self._sink_master = sink_master

            # External output sink (non-Arctis physical sink)
            if self._ext_device_nick:
                # User chose a specific device in settings. Match node.nick OR
                # node.name: a Bluetooth device saved by its node.name (no
                # node.nick) must still resolve here (issue #134).
                sink_ext = next(
                    (s for s in sinks
                     if self._ext_device_nick in (
                         s.proplist.get("node.nick", ""), s.name)),
                    None,
                )
            else:
                # Auto-detect: first physical non-SteelSeries sink (ALSA or
                # Bluetooth — issue #134)
                sink_ext = next(
                    (s for s in sinks if is_external_output_sink(s)),
                    None,
                )
            if sink_ext is not None:
                pct = round(sink_ext.volume.value_flat * 100)
                self._ext_card.set_volume(pct)
                nick = (sink_ext.proplist.get("node.description")
                        or sink_ext.proplist.get("node.nick", ""))
                self._ext_card.set_name(nick or I18n.translate("ui", "output"))
                self._sink_ext = sink_ext
                self._ext_card.set_connected()
            else:
                self._ext_card.set_disconnected()

            # Update application lists — pass all matching sinks to catch duplicates
            sink_inputs = pulse.sink_input_list()
            pulse_app_names = {si.proplist.get("application.name", "") for si in sink_inputs}
            rows_by_card = {
                id(self._game_card):  self._update_apps(sink_inputs, sinks_game,  self._game_card),
                id(self._chat_card):  self._update_apps(sink_inputs, sinks_chat,  self._chat_card),
                id(self._media_card): self._update_apps(sink_inputs, sinks_media, self._media_card),
                # Aux was missing from this list entirely, so its card never
                # listed anything routed to it (#209).
                id(self._aux_card):   self._update_apps(sink_inputs, sinks_aux,   self._aux_card),
            }
            if sink_ext is not None:
                # Include both the physical sink and the EQ sink so apps
                # routed through the output EQ still appear on this card.
                ext_sinks = [sink_ext]
                sink_eq = next((s for s in sinks if s.name == "effect_input.sonar-output-eq"), None)
                if sink_eq is not None:
                    ext_sinks.append(sink_eq)
                rows_by_card[id(self._ext_card)] = self._update_apps(
                    sink_inputs, ext_sinks, self._ext_card)
            self._combo_tick += 1

            # Also show native PipeWire streams (mpv, haruna…), skip duplicates.
            # The scan forks a `pw-dump` and parses the whole graph, which is
            # the expensive half of this tick — twice a second was enough to
            # cost measurable CPU and starve the surround convolver on a large
            # graph (#182). Native streams are a handful of long-lived players,
            # so rescan every 4th tick (2s); the cards are still repopulated
            # from the cached result on every tick, because _update_apps above
            # clears them each time and skipping this would make those entries
            # blink.
            native_by_card = self._update_native_apps(
                sinks, pulse_app_names, rescan=(self._combo_tick % 4 == 1))

            # Both sources in, one rebuild decision per card.
            for card in self._all_cards():
                self._apply_app_rows(
                    card,
                    list(rows_by_card.get(id(card), []))
                    + list(native_by_card.get(id(card), [])))

            # Anything playing that no card above represents.
            self._refresh_unassigned(sink_inputs, sinks, sink_ext)

        except Exception as exc:
            logger.warning("Error polling PulseAudio: %s", exc)
            try:
                self._pulse.close()
            except Exception:
                pass
            self._pulse = None
            self._set_disconnected()

    # application.name is often a generic audio-engine label rather than the
    # actual program name (e.g. Discord streams report "WEBRTC VoiceEngine").
    # For those we fall back to the process binary so the user sees "Discord".
    _GENERIC_APP_NAMES = {
        "WEBRTC VoiceEngine", "AudioStream", "Playback", "audio stream",
        "Chromium", "cras", "libcanberra", "speech-dispatcher",
    }

    @staticmethod
    def _friendly_app_name(proplist) -> str:
        name = (proplist.get("application.name", "") or "").strip()
        if name and name not in HomePage._GENERIC_APP_NAMES:
            return name
        binary = (proplist.get("application.process.binary", "") or "").strip()
        binary = binary.rsplit("/", 1)[-1]
        if binary:
            return binary[:1].upper() + binary[1:]
        return name or "Audio"

    def _apply_app_rows(self, card: "AudioCard", rows: list) -> None:
        """Draw a card's application tags, rebuilding only when they changed.

        One signature for both sources. They used to be drawn separately —
        _update_apps cleared the card and drew the PulseAudio streams, then
        _update_native_apps appended the native ones — and that worked only
        while the first half cleared on every tick. Once it learned to skip
        the rebuild when nothing had changed (it runs twice a second, and
        tearing down every widget that often is what made dragging an app
        stutter), the second half kept appending to a card nobody had cleared:
        one extra copy of every native stream per tick, which is a game
        listed three times after you closed it.
        """
        signature = tuple(rows)
        if card._app_sig == signature:
            return
        card.clear_apps()
        for app_name, si_index, pid in rows:
            card.add_app_tag(app_name, si_index, pid, bg_color=card._accent)
        # After the rebuild, not before: clear_apps() resets the signature, so
        # setting it first threw it away and rebuilt on every tick.
        card._app_sig = signature

    def _update_apps(self, sink_inputs, sinks: list, card: "AudioCard") -> list:
        """Redraw a card's application tags — but only when they changed.

        This runs on the 500 ms poll, and it used to clear the row and build a
        fresh widget per application every single time: two full teardowns and
        rebuilds a second, for a list that changes when somebody opens a game.
        The cost lands exactly where it is most visible — dragging an app to
        another channel is a drag, a rebuild, and a repaint fighting each other
        — which is what "moving applications between channels stutters" is.

        The tags are rebuilt when the row they would produce differs from the
        row that is already there, and left alone otherwise.
        """
        if not sinks:
            return []
        sink_indices = {s.index for s in sinks}
        matching = [
            si for si in sink_inputs
            if si.sink in sink_indices and "application.name" in si.proplist
        ]

        rows: list[tuple[str, int, int]] = []
        seen_names: set[str] = set()
        for si in matching:
            app_name = self._friendly_app_name(si.proplist)
            if app_name in seen_names:
                continue
            seen_names.add(app_name)
            rows.append((app_name, si.index,
                         int(si.proplist.get("application.process.id", 0))))

        return rows
        card._app_sig = signature

    def _style_unassigned(self) -> None:
        """Header styling, kept flat and quiet: this is a list, not an alert."""
        if not hasattr(self, "_unassigned_toggle"):
            return
        muted = _theme.c("TEXT_SECONDARY")
        self._unassigned_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {muted}; "
            f"font-size: 10pt; font-weight: bold; padding: 2px 0; text-align: left; }}"
            f"QPushButton:hover {{ color: {_theme.c('TEXT_PRIMARY')}; }}"
        )
        self._unassigned_show_hidden.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {muted}; "
            f"border: 1px solid {muted}; border-radius: 4px; "
            f"font-size: 9pt; padding: 2px 8px; }}"
            f"QPushButton:hover {{ color: {_theme.c('TEXT_PRIMARY')}; }}"
        )

    # ── Other applications (streams on no ASM channel) ───────────────────────

    # Internal plumbing that must never be offered to the user as "an app".
    _INTERNAL_BINARIES = {"pipewire", "pw-loopback", "pw-cat", "wireplumber"}
    _INTERNAL_MEDIA_HINTS = ("EQ output", "Virtual Surround", "Sonar", "loopback")

    def _channel_sink_indices(self, sinks, sink_ext) -> set[int]:
        """Indices of every sink already represented by a card above.

        The headset's own hardware device is deliberately NOT in here. A stream
        playing on it is audible but on no channel, so it gets none of ASM's
        per-channel handling and cannot be moved from the mixer. That is the
        case this whole area exists for: with a Nova Pro dock feeding speakers
        from its AUX port, setting the hardware device as the system default is
        a sensible choice, and it made every application invisible to ASM.
        """
        wanted = (SINK_GAME, SINK_CHAT, SINK_MEDIA, SINK_AUX,
                  "effect_input.sonar-", "effect_input.virtual-surround")
        indices = {s.index for s in sinks if any(w in s.name for w in wanted)}
        if sink_ext is not None:
            indices.add(sink_ext.index)
            # The Output card also lists streams sitting on the output EQ.
            for s in sinks:
                if s.name == "effect_input.sonar-output-eq":
                    indices.add(s.index)
        return indices

    def _collect_unassigned(self, sink_inputs, sinks, sink_ext) -> list[dict]:
        """Streams that are playing but belong to no card, newest first."""
        from arctis_sound_manager.pw_utils import app_override_key

        on_cards = self._channel_sink_indices(sinks, sink_ext)
        names = {s.index: s.name for s in sinks}
        descs = {s.index: (s.description or s.name) for s in sinks}

        rows: list[dict] = []
        seen: set[str] = set()
        for si in sink_inputs:
            if si.sink in on_cards:
                continue
            props = si.proplist
            app = props.get("application.name", "")
            if not app:
                continue
            binary = props.get("application.process.binary", "")
            if binary in self._INTERNAL_BINARIES:
                continue
            media = props.get("media.name", "")
            if any(h in media for h in self._INTERNAL_MEDIA_HINTS):
                continue
            # ASM's own loopbacks appear as sink-inputs too; never offer them.
            if any(k in app for k in ("Arctis_", "effect_output", "effect_input")):
                continue

            key = app_override_key(app, binary)
            if key in self._hidden_apps or key in seen:
                continue
            seen.add(key)
            rows.append({
                "key": key,
                "label": self._friendly_app_name(props),
                "where": descs.get(si.sink, names.get(si.sink, "?")),
                "si_index": si.index,
                "pid": int(props.get("application.process.id", 0) or 0),
            })
        return rows

    def _refresh_unassigned(self, sink_inputs, sinks, sink_ext) -> None:
        rows = self._collect_unassigned(sink_inputs, sinks, sink_ext)
        hidden_count = len(self._hidden_apps)

        # Nothing to show and nothing hidden: the area stays out of the way
        # entirely rather than sitting there empty.
        if not rows and not hidden_count:
            self._unassigned_wrap.setVisible(False)
            self._unassigned_sig = None
            return
        self._unassigned_wrap.setVisible(True)

        arrow = "▾" if self._unassigned_expanded else "▸"
        self._unassigned_toggle.setText(
            f"{arrow}  {I18n.translate('ui', 'unassigned_title')}  ({len(rows)})")
        self._unassigned_show_hidden.setText(
            I18n.translate("ui", "unassigned_show_hidden").format(count=hidden_count))
        self._unassigned_show_hidden.setVisible(hidden_count > 0)
        self._unassigned_body.setVisible(self._unassigned_expanded and bool(rows))

        # Same rule as the cards above: this is on the 500 ms poll, and each row
        # is a composite widget with its own buttons. Rebuilding a list that has
        # not changed, twice a second, is work the user feels as a stutter in
        # whatever they are doing on top of it.
        signature = tuple((r["key"], r["label"], r["where"], r["si_index"], r["pid"])
                          for r in rows)
        if signature == self._unassigned_sig:
            return
        self._unassigned_sig = signature

        while self._unassigned_area.count():
            item = self._unassigned_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        primary = _theme.c("TEXT_PRIMARY")
        muted = _theme.c("TEXT_SECONDARY")
        for row in rows:
            self._unassigned_area.addWidget(_UnassignedRow(
                row["label"],
                I18n.translate("ui", "unassigned_playing_on").format(device=row["where"]),
                row["si_index"], row["pid"],
                (lambda k=row["key"]: self._on_dismiss_app(k)),
                primary, muted,
            ))

    def _on_unassigned_toggle(self) -> None:
        self._unassigned_expanded = not self._unassigned_expanded
        self._poll_volumes()

    def _on_dismiss_app(self, key: str) -> None:
        """Stop offering this application. Never touches where it plays."""
        self._hidden_apps.add(key)
        _save_hidden_apps(self._hidden_apps)
        self._poll_volumes()

    def _on_unhide_all(self) -> None:
        self._hidden_apps.clear()
        _save_hidden_apps(self._hidden_apps)
        self._poll_volumes()

    # ASM's own filter-chain / loopback nodes (effect_output.sonar-*-eq,
    # effect_input.virtual-surround-*, Arctis_<Channel>_sink_out — see
    # sonar_to_pipewire.py's _hesuvi_output_node()/_hesuvi_input_node() and
    # loopback_manager.py's LoopbackSpec playback names) have neither
    # application.name nor application.process.binary set, so
    # get_native_streams() falls back to their raw node.name as "app_name".
    # If one of these is ever (even transiently, e.g. a routing race) linked
    # to a channel's virtual sink, it must never be shown as a user app
    # dragged onto that channel. This is separate from, and additive to,
    # _INTERNAL_BINARIES/_INTERNAL_MEDIA_HINTS above, which filter a
    # different code path (PulseAudio sink-inputs in "Other applications").
    _ASM_NODE_NAME_PREFIXES = ("effect_output.", "effect_input.")
    _ASM_LOOPBACK_PLAYBACK_NAMES = frozenset(
        f"Arctis_{ch}_sink_out" for ch in ("Game", "Chat", "Media", "Aux")
    )

    @classmethod
    def _is_asm_internal_node(cls, node_name: str) -> bool:
        return (
            node_name.startswith(cls._ASM_NODE_NAME_PREFIXES)
            or node_name in cls._ASM_LOOPBACK_PLAYBACK_NAMES
        )

    def _update_native_apps(self, pulse_sinks, already_shown: set[str] = frozenset(),
                            *, rescan: bool = True):
        """Add native PipeWire streams (e.g. haruna/mpv) to the correct card.

        *rescan* runs the `pw-dump` behind :func:`get_native_streams`; when
        False the previous result is reused. The caller repopulates the cards
        on every tick either way — only the graph scan is throttled (#182).
        """
        if rescan:
            try:
                self._native_cache = get_native_streams()
            except Exception as e:
                logger.debug("get_native_streams failed: %s", e)
                self._native_cache = []
                return
        native = self._native_cache

        card_map = {
            SINK_GAME:  self._game_card,
            SINK_CHAT:  self._chat_card,
            SINK_MEDIA: self._media_card,
            SINK_AUX:   self._aux_card,
        }

        per_card: dict[int, list] = {}
        for s in native:
            if s["app_name"] in already_shown:
                continue  # already listed via PulseAudio
            node_name = s.get("props", {}).get("node.name", "") or s["app_name"]
            if self._is_asm_internal_node(node_name):
                continue  # ASM's own node, not a user application
            sink_name = s.get("sink_name") or ""
            card = next((c for bound, c in card_map.items() if bound in sink_name), None)
            if card is None:
                continue
            per_card.setdefault(id(card), []).append(
                (s["app_name"], s["id"], int(s["pid"] or 0)))
        return per_card

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
        pulse = self._get_pulse()
        if pulse is None:
            return
        try:
            sinks = pulse.sink_list()
            target = next((s for s in sinks if target_sink_name in s.name), None)
            if target is None:
                logger.warning("Sink %s not found", target_sink_name)
                return
            # Resolve the real PipeWire identity of the stream (issue #108).
            # ``app_name`` here is the friendly display label (often the process
            # binary), which does NOT match the key video_router.py stores the
            # override under. We must key on application.name + binary via the
            # shared app_override_key() so GUI-set channels persist across
            # restarts. Two Electron apps that both report application.name
            # "Chromium" (e.g. Vesktop and Pear Desktop) otherwise collide.
            si = next((x for x in pulse.sink_input_list() if x.index == si_index), None)
            if si is not None:
                key = app_override_key(
                    si.proplist.get("application.name", "") or app_name,
                    si.proplist.get("application.process.binary", ""),
                )
            else:
                # Native/PipeWire stream without a matching PA sink-input:
                # fall back to the label so behaviour is unchanged for it.
                key = app_name
            pulse.sink_input_move(si_index, target.index)
            logger.info("Moved '%s' (pid=%d) -> %s [key=%s]", app_name, pid, target_sink_name, key)
            # Record manual override — keyed identically to video_router.py so
            # it persists across restarts (issue #108).
            overrides = _load_overrides()
            overrides[key] = target_sink_name
            _save_overrides(overrides)
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

    def _on_master_volume_changed(self, value: int):
        self._apply_volume(self._sink_master, value)

    def _on_aux_channel_volume_changed(self, value: int):
        self._apply_volume(getattr(self, "_sink_aux", None), value)

    # ── the optional Aux channel (#209) ──────────────────────────────────────

    def _set_aux_enabled(self, enabled: bool) -> None:
        """Turn the channel on or off, and tell the daemon.

        The daemon owns the loopback and the filter-chain stage; all this does
        is persist the choice and let it act. The card follows immediately so
        the click has a visible effect without waiting for a round trip.
        """
        try:
            from arctis_sound_manager.settings import GeneralSettings
            gs = GeneralSettings.read_from_file()
            gs.aux_enabled = bool(enabled)
            gs.write_to_file()
        except Exception:  # noqa: BLE001
            logger.warning("could not persist aux_enabled", exc_info=True)
        self._apply_aux_visibility(enabled)
        try:
            from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
            DbusWrapper.change_setting("aux_enabled", bool(enabled))
        except Exception:  # noqa: BLE001
            logger.debug("could not notify the daemon about aux_enabled", exc_info=True)

    def _apply_aux_visibility(self, enabled: bool) -> None:
        """Show or hide the Aux card."""
        self._aux_card.setVisible(bool(enabled))
        self._fit_cards_to_row()
        self._refresh_app_tag_buttons()
        self._refresh_chatmix_toggles()

    # ── the optional Output card (#262) ──────────────────────────────────────

    def _set_output_channel_visible(self, visible: bool) -> None:
        """Persist the Output card's visibility. Display-only — no daemon call.

        Unlike Aux, Output isn't a channel the daemon creates or tears down:
        it's always the physical/external routing card, just optionally out
        of the way for someone who never uses it.
        """
        try:
            from arctis_sound_manager.settings import GeneralSettings
            gs = GeneralSettings.read_from_file()
            gs.output_channel_visible = bool(visible)
            gs.write_to_file()
        except Exception:  # noqa: BLE001
            logger.warning("could not persist output_channel_visible", exc_info=True)
        self._apply_output_visibility(visible)

    def _apply_output_visibility(self, visible: bool) -> None:
        self._ext_card.setVisible(bool(visible))
        self._fit_cards_to_row()
        self._refresh_app_tag_buttons()

    # ── ChatMix channels (#249, #269) ─────────────────────────────────────────

    def _refresh_chatmix_toggles(self) -> None:
        """Show/hide and (re)set the channel-inclusion checkboxes under the bar.

        Game and Media always offer theirs. Aux only does while the Aux
        channel itself is on — its card is already hidden entirely
        otherwise, but the checkbox is kept in step too rather than relying
        on that alone. Called at startup and whenever Aux's own enabled
        state changes, since that's the only thing that can make the Aux
        checkbox go from irrelevant to relevant (or back) during a running
        session.
        """
        try:
            from arctis_sound_manager.settings import GeneralSettings
            channels = set(GeneralSettings.read_from_file().chatmix_channels_or_default())
        except Exception:  # noqa: BLE001
            channels = {"game"}

        aux_on = not self._aux_card.isHidden()
        self._set_chatmix_checkbox("game", True, "game" in channels)
        self._set_chatmix_checkbox("media", True, "media" in channels)
        self._set_chatmix_checkbox("aux", aux_on, "aux" in channels)

        self._refresh_chatmix_bar_label(channels)

    def _set_chatmix_checkbox(self, channel: str, visible: bool, checked: bool) -> None:
        """Set a channel checkbox's visibility/state without firing its toggle.

        Blocked while set: without it, setting the initial state on
        population would fire the toggle callback and re-write the setting
        to whatever it already was.
        """
        cb = self._chatmix_channel_checkboxes[channel]
        cb.setVisible(visible)
        cb.blockSignals(True)
        cb.setChecked(checked)
        cb.blockSignals(False)

    def _on_chatmix_toggle(self, channel: str, enabled: bool) -> None:
        """Add/remove *channel* ('game'/'media'/'aux') from the non-chat side.

        Unchecking the last remaining channel puts Game straight back
        (#269) — the bar/dial must always drive something — and the Game
        checkbox is corrected back to checked so it doesn't lie about what
        just happened, even when Game itself wasn't the one just toggled.

        Mirrors _set_aux_enabled's dual write: the settings file is updated
        immediately (the next manage_mix_change() tick in the daemon reads it
        fresh — see PulseAudioManager.set_mix), and the D-Bus notify keeps a
        currently-running daemon's own read of general_settings.yaml from
        lagging, in a separate try/except so neither half can block the
        other.
        """
        updated: list[str] = []
        try:
            from arctis_sound_manager.settings import GeneralSettings
            gs = GeneralSettings.read_from_file()
            updated = list(gs.chatmix_channels)
            if enabled and channel not in updated:
                updated.append(channel)
            elif not enabled and channel in updated:
                updated.remove(channel)
            if not updated:
                updated = ['game']
                try:
                    cb = self._chatmix_channel_checkboxes["game"]
                    cb.blockSignals(True)
                    cb.setChecked(True)
                    cb.blockSignals(False)
                except Exception:  # noqa: BLE001
                    pass
            gs.chatmix_channels = updated
            gs.write_to_file()
        except Exception:  # noqa: BLE001
            logger.warning("could not persist chatmix_channels", exc_info=True)
        try:
            from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
            DbusWrapper.change_setting("chatmix_channels", updated)
        except Exception:  # noqa: BLE001
            logger.debug("could not notify the daemon about chatmix_channels", exc_info=True)
        self._refresh_chatmix_bar_label(set(updated))

    def _refresh_chatmix_bar_label(self, channels: set) -> None:
        """Keep the bar's left-hand label and fill colour in step with the
        toggled channels: the label lists what's included, and the fill
        matches those channels' own vertical sliders (#269) — a flat colour
        for one, a band per colour when several ride together. Chat sits on
        the right, so its label and fill stay fixed.
        """
        order = ["game", "media", "aux"]
        included = [ch for ch in order if ch in channels]
        names = [I18n.translate("ui", ch) for ch in included]
        label = getattr(self, "_chatmix_bar_channels_lbl", None)
        if label is not None:
            label.setText(", ".join(names) or I18n.translate("ui", "game"))

        bar = getattr(self, "_chatmix_bar", None)
        if bar is not None:
            colors = [_theme.c(_CHATMIX_CHANNEL_COLOR_KEYS[ch]) for ch in included] or [_theme.c("COLOR_GAME")]
            bar.setStyleSheet(
                _make_chatmix_bar_qss(chatmix_bar_track_css(colors, _theme.c("COLOR_CHAT")))
            )

    def _on_chatmix_bar_changed(self, position: int) -> None:
        channels_pct, chat_pct = chatmix_bar_to_percentages(position)
        self._apply_chatmix_bar(channels_pct, chat_pct)

    def _apply_chatmix_bar(self, channels_pct: int, chat_pct: int) -> None:
        """Drive the configured channel(s) and Chat straight from the bar (#269).

        Software-only for now: this goes through the same PipeWire calls the
        vertical sliders already make from this process, without touching the
        headset's own dial. The vertical sliders themselves catch up on the
        next _poll_volumes() tick, at most half a second later.
        """
        try:
            from arctis_sound_manager.settings import GeneralSettings
            channels = GeneralSettings.read_from_file().chatmix_channels_or_default()
        except Exception:  # noqa: BLE001
            channels = ['game']

        sink_by_channel = {
            'game': self._sink_game,
            'media': self._sink_media,
            'aux': getattr(self, '_sink_aux', None),
        }
        for channel in channels:
            sink = sink_by_channel.get(channel)
            if sink is not None:
                self._apply_volume(sink, channels_pct)

        if self._sink_chat is not None:
            self._apply_volume(self._sink_chat, chat_pct)

    def _fit_cards_to_row(self) -> None:
        """Give every card a minimum width the window can actually satisfy.

        The row is five cards wide normally (Game/Chat/Media/Master/Output)
        and six with Aux on, laid out side by side with 20 px gaps and taking
        three quarters of the window. At 260 px each, six cards need a window
        around 2080 px — wider than a 1366 px laptop screen, and the row has
        no scroll area to fall back on, so Qt would simply force the window
        past the edge of the display.

        The cards keep their Expanding policy, so this only lowers the floor:
        on a wide screen they still spread out exactly as before.
        """
        # isHidden(), not isVisible(): isVisible() is False for every child of
        # a window that has not been shown yet, so asking it during start-up
        # counts zero cards and picks the wrong width. isHidden() answers about
        # the widget itself, whatever its parent is doing.
        shown = [c for c in self._all_cards() if not c.isHidden()]
        width = CARD_MIN_WIDTH_TIGHT if len(shown) > 5 else CARD_MIN_WIDTH
        for card in self._all_cards():
            card.setMinimumWidth(width)

    def _refresh_app_tag_buttons(self) -> None:
        """Keep the per-application routing buttons in step with the channels.

        Each running application shows one small square per destination — G, C,
        M, O — and Aux adds an A when it is on. The registry is rebuilt rather
        than patched because apply_theme() rebuilds it too, with fresh colours,
        and two places writing the same list in different ways is how one of
        them ends up stale.
        """
        registry = [
            ("G", _theme.c("COLOR_GAME"),
             lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_GAME)),
            ("C", _theme.c("COLOR_CHAT"),
             lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_CHAT)),
            ("M", _theme.c("COLOR_AUX"),
             lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_MEDIA)),
        ]
        # No button for a channel with no sink behind it: pressing it would
        # move the stream to a target that does not exist and lose the audio.
        if not self._aux_card.isHidden():
            registry.append(
                ("A", _theme.c("COLOR_AUX2"),
                 lambda si, app, pid: self._on_stream_drop(si, app, pid, SINK_AUX)))
        registry.append(
            ("O", _theme.c("COLOR_HDMI"),
             lambda si, app, pid: self._on_stream_drop_ext(si, app, pid)))
        _AppTag._cards_registry = registry

        # Each tag reads this list once, when it is built, so changing it does
        # nothing to the tags already on screen — hiding Aux left an "A" under
        # every application, pointing at a channel that was gone. Drop the
        # remembered row signature so the next poll rebuilds them with the
        # buttons the registry now describes.
        for card in self._all_cards():
            card._app_sig = None

    def _all_cards(self) -> tuple:
        return (self._game_card, self._chat_card, self._media_card,
                self._aux_card, self._master_card, self._ext_card)

    def _apply_volume(self, sink, value: int):
        pulse = self._get_pulse()
        if pulse is None or sink is None:
            return
        try:
            sinks = pulse.sink_list()
            fresh_sink = next((s for s in sinks if s.name == sink.name), None)
            if fresh_sink is not None:
                pulse.volume_set_all_chans(fresh_sink, value / 100.0)
                # Persist the level keyed by the sink's stable node.name so the
                # daemon can re-assert it after any loopback recreation, instead
                # of the sink silently reverting to 100% (issue #134). Only the
                # three virtual sinks are remembered; the external device sink is
                # system-owned and left to WirePlumber's own restore.
                node = next(
                    (frag for frag in (SINK_GAME, SINK_CHAT, SINK_MEDIA)
                     if frag in fresh_sink.name),
                    None,
                )
                if node is not None:
                    save_channel_volume(node, value)
        except Exception as exc:
            logger.warning("Error setting volume: %s", exc)
