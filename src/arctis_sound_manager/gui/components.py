# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Reusable UI components for the ArctisSonar GUI visual style.
"""
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# ── Icon paths ─────────────────────────────────────────────────────────────────

IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")
HOME_ICON = os.path.join(IMAGES_DIR, "home_icon.svg")
SETTINGS_ICON = os.path.join(IMAGES_DIR, "settings_icon.svg")
HEADPHONE_ICON = os.path.join(IMAGES_DIR, "headphone_icon.svg")
EQUALIZER_ICON = os.path.join(IMAGES_DIR, "equalizer_icon.svg")
OUTPUT_ICON = os.path.join(IMAGES_DIR, "output_icon.svg")
HELP_ICON = os.path.join(IMAGES_DIR, "help_icon.svg")
GAME_ICON = os.path.join(IMAGES_DIR, "game_icon.svg")
CHAT_ICON = os.path.join(IMAGES_DIR, "chat_icon.svg")
MEDIA_ICON = os.path.join(IMAGES_DIR, "media_icon.svg")
AUX_ICON = os.path.join(IMAGES_DIR, "aux_icon.svg")
MIC_ICON = os.path.join(IMAGES_DIR, "mic_icon.svg")
HDMI_ICON = os.path.join(IMAGES_DIR, "hdmi_icon.svg")
GAMEDAC_ICON = os.path.join(IMAGES_DIR, "gamedac_icon.svg")
CLIPS_ICON = os.path.join(IMAGES_DIR, "clips_icon.svg")


# ── SvgIconWidget ──────────────────────────────────────────────────────────────

def tinted_svg_pixmap(svg_path: str, color: str, width: int, height: int) -> QPixmap:
    """Render *svg_path* at *width* x *height* with every fill in *color*.

    Transparent on any error, so a missing icon leaves a gap rather than a crash.
    """
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(0, 0, 0, 0))

    try:
        with open(svg_path, encoding="utf-8") as f:
            svg_data = f.read()

        # Replace existing fill / stroke attributes and style properties
        svg_data = re.sub(r'(fill|stroke)="(?!none)[^"]*"', f'fill="{color}"', svg_data)
        svg_data = re.sub(r'(fill|stroke):\s*(?!none)[^;}"]+', f'fill:{color}', svg_data)

        renderer = QSvgRenderer(svg_data.encode("utf-8"))
        # Fit, never stretch: a square microphone or the near-square headphone
        # drawn into a channel card's 48x36 box came out squashed flat.
        renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        if renderer.isValid():
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
    except Exception:
        pass  # Leave transparent pixmap on error
    return pixmap


class SvgIconWidget(QLabel):
    """Renders an SVG file tinted with a given color."""

    def __init__(self, svg_path: str, color: str, size: int = 55, width: int | None = None, parent=None):
        super().__init__(parent)
        w = width if width is not None else size
        self.setFixedSize(w, size)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self._svg_path, self._size, self._width = svg_path, size, w
        self._load(svg_path, color, size, w)

    def set_color(self, color: str) -> None:
        """Re-tint the icon, e.g. after a theme change."""
        self._load(self._svg_path, color, self._size, self._width)

    def _load(self, svg_path: str, color: str, size: int, width: int | None = None):
        w = width if width is not None else size
        pixmap = tinted_svg_pixmap(svg_path, color, w, size)
        self.setPixmap(pixmap)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("border: none; background: transparent;")


# ── SidebarButton ──────────────────────────────────────────────────────────────

#: The sidebar's standard icon and button height. A button may ask for a
#: smaller icon; its height shrinks by the same amount, so the column gets
#: the space back instead of just drawing a smaller glyph in the same box.
_DEFAULT_ICON_SIZE = 44
_DEFAULT_BUTTON_HEIGHT = 115


class SidebarButton(QPushButton):
    """
    120x115 px sidebar button: SVG icon on top + text label below.
    Active state shows a lighter background and colored icon.
    """

    def __init__(
        self,
        svg_path: str,
        label: str,
        icon_color_inactive: str = TEXT_SECONDARY,
        icon_color_active: str = ACCENT,
        parent: QWidget | None = None,
        icon_size: int = _DEFAULT_ICON_SIZE,
    ):
        super().__init__(parent)
        self.setObjectName("sidebarBtn")
        # The height follows the icon. Shrinking the icon alone would change
        # nothing: the button keeps its fixed height, and the sidebar goes on
        # taking the same room — which is the whole point of shrinking one.
        self.setFixedSize(120, _DEFAULT_BUTTON_HEIGHT - (_DEFAULT_ICON_SIZE - icon_size))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCheckable(False)

        self._svg_path = svg_path
        self._color_inactive = icon_color_inactive
        self._color_active = icon_color_active
        self._label_text = label
        self._active = False

        self._bg_hover = _theme.c("BG_BUTTON_HOVER")
        self._bg_active = _theme.c("BG_SIDEBAR_ACTIVE")
        self._text_primary = _theme.c("TEXT_PRIMARY")
        self._text_secondary = _theme.c("TEXT_SECONDARY")

        # Inner layout: icon + label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._icon_size = icon_size
        self._icon_widget = SvgIconWidget(svg_path, icon_color_inactive, size=self._icon_size)
        layout.addWidget(self._icon_widget, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._label_widget = QLabel(label)
        self._label_widget.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._label_widget.setWordWrap(True)
        self._label_widget.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 10pt; background: transparent;"
        )
        layout.addWidget(self._label_widget)

        self.setStyleSheet(self._get_base_qss())

    def _get_base_qss(self) -> str:
        return (
            f"QPushButton#sidebarBtn {{ background: transparent; color: {self._text_secondary}; "
            f"border: none; border-radius: 16px; font-size: 10pt; text-align: center; padding: 0; }}\n"
            f"QPushButton#sidebarBtn:hover {{ background: {self._bg_hover}; color: {self._text_primary}; }}"
        )

    def _get_active_qss(self) -> str:
        return (
            f"QPushButton#sidebarBtn {{ background: {self._bg_active}; color: {self._text_primary}; "
            f"border: none; border-radius: 16px; font-size: 10pt; text-align: center; padding: 0; }}"
        )

    def update_colors(self, theme: dict) -> None:
        self._color_inactive = theme["TEXT_SECONDARY"]
        self._color_active = theme["ACCENT"]
        self._bg_hover = theme["BG_BUTTON_HOVER"]
        self._bg_active = theme["BG_SIDEBAR_ACTIVE"]
        self._text_primary = theme["TEXT_PRIMARY"]
        self._text_secondary = theme["TEXT_SECONDARY"]
        self.set_active(self._active)

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.setStyleSheet(self._get_active_qss())
            self._label_widget.setStyleSheet(
                f"color: {self._text_primary}; font-size: 10pt; background: transparent;"
            )
            # Reload icon with active color
            pix = QPixmap(self._icon_size, self._icon_size)
            pix.fill(QColor(0, 0, 0, 0))
            try:
                with open(self._svg_path, encoding="utf-8") as f:
                    svg_data = f.read()
                svg_data = re.sub(r'(fill|stroke)="(?!none)[^"]*"', f'fill="{self._color_active}"', svg_data)
                svg_data = re.sub(r'(fill|stroke):\s*(?!none)[^;}"]+', f'fill:{self._color_active}', svg_data)
                renderer = QSvgRenderer(svg_data.encode("utf-8"))
                if renderer.isValid():
                    painter = QPainter(pix)
                    renderer.render(painter)
                    painter.end()
            except Exception:
                pass
            self._icon_widget.setPixmap(pix)
        else:
            self.setStyleSheet(self._get_base_qss())
            self._label_widget.setStyleSheet(
                f"color: {self._text_secondary}; font-size: 10pt; background: transparent;"
            )
            # Reload icon with inactive color
            pix = QPixmap(self._icon_size, self._icon_size)
            pix.fill(QColor(0, 0, 0, 0))
            try:
                with open(self._svg_path, encoding="utf-8") as f:
                    svg_data = f.read()
                svg_data = re.sub(r'(fill|stroke)="(?!none)[^"]*"', f'fill="{self._color_inactive}"', svg_data)
                svg_data = re.sub(r'(fill|stroke):\s*(?!none)[^;}"]+', f'fill:{self._color_inactive}', svg_data)
                renderer = QSvgRenderer(svg_data.encode("utf-8"))
                if renderer.isValid():
                    painter = QPainter(pix)
                    renderer.render(painter)
                    painter.end()
            except Exception:
                pass
            self._icon_widget.setPixmap(pix)


# ── SectionTitle ───────────────────────────────────────────────────────────────

class SectionTitle(QLabel):
    """Gray bold ~20pt section header label."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.apply_theme()

    def apply_theme(self, t=None) -> None:
        self.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 20pt; font-weight: bold; background: transparent;"
        )


# ── DividerLine ────────────────────────────────────────────────────────────────

class DividerLine(QWidget):
    """Thin 1px horizontal separator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"background-color: {_theme.c('BORDER')};")


# ── RoundedButton ──────────────────────────────────────────────────────────────

class RoundedButton(QPushButton):
    """Filled button with #2D363E background and 8px radius."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("roundedBtn")


# ── AccentButton ───────────────────────────────────────────────────────────────

class AccentButton(QPushButton):
    """Orange accent filled button."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("accentBtn")
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
