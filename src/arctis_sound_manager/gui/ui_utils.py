# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6 import QtSvg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

ICON_PATH = Path(__file__).parent / 'images' / 'steelseries_logo.svg'
_LOGO_PATH = Path(__file__).parent / 'images' / 'asm_logo.png'


def resolve_tray_icon_color(choice: int) -> str:
    """Resolve the systray_icon_color setting (0=auto, 1=white, 2=black) to a
    hex color usable by get_icon_pixmap/get_battery_number_pixmap (#130).

    Auto (0) follows the desktop color scheme via QStyleHints, so the icon
    stays legible against both light and dark panels/themes. Falls back to
    white when there's no QApplication instance yet (e.g. very early startup)
    or the PySide6 version predates the colorScheme() API (6.5+).
    """
    if choice == 1:
        return '#ffffff'
    if choice == 2:
        return '#000000'

    # choice == 0 (or any unknown value): auto-detect from the desktop theme.
    try:
        app = QApplication.instance()
        if app is None:
            return '#ffffff'
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return '#ffffff'
        if scheme == Qt.ColorScheme.Light:
            return '#000000'
    except Exception:
        pass

    return '#ffffff'


def get_logo_label(height: int = 40):
    """Return a QLabel displaying the ASM logo scaled to *height* logical pixels."""
    from PySide6.QtWidgets import QLabel
    lbl = QLabel()
    lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    lbl.setStyleSheet("background: transparent;")
    px = QPixmap(str(_LOGO_PATH))
    if not px.isNull():
        lbl.setPixmap(px.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation))
    return lbl

def get_icon_pixmap(icon_path: Path = ICON_PATH, color: str = '#ffffff') -> QPixmap:
    brush_color = QColor(color)

    xml_tree = ET.parse(icon_path.absolute().as_posix())
    xml_root = xml_tree.getroot()

    for path in xml_root.findall('.//{http://www.w3.org/2000/svg}path'):
        path.set('fill', brush_color.name())

    xml_str = ET.tostring(xml_root)

    svg_renderer = QtSvg.QSvgRenderer(xml_str)

    # Create the empty image
    image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    # Initialize the painter
    painter = QPainter(image)
    painter.setBrush(brush_color)
    painter.setPen(Qt.PenStyle.NoPen)

    # Render the image on the QImage
    svg_renderer.render(painter)

    # Rendering end
    painter.end()

    pixmap = QPixmap.fromImage(image)

    return pixmap


def get_battery_number_pixmap(battery_percent: int, color: str = '#ffffff') -> QPixmap:
    """A square pixmap of the battery number, sized to fill the tray slot (#119).

    Used by a dedicated second tray item so the number gets its own full-size
    slot next to the ASM icon (StatusNotifierItem hosts give each item one
    square slot; a wide "icon + text" pixmap gets squashed into one slot). The
    font auto-shrinks so 3-digit values ("100") still fit.
    """
    size = 64
    margin = 2
    text = f"{int(battery_percent)}%"

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setPen(QColor(color))

    font = QFont()
    font.setBold(True)

    # Grow the font until the *tight* glyph box nearly fills the square (fit to
    # whichever of width/height binds — 3-digit "100%" is width-bound). Using
    # the tight box (not pixelSize, which includes ascent/descent padding) makes
    # the digits as large as possible so they read at tray size (#119).
    px = 12
    while px < 200:
        font.setPixelSize(px + 2)
        painter.setFont(font)
        br = painter.fontMetrics().tightBoundingRect(text)
        if br.width() > size - margin or br.height() > size - margin:
            break
        px += 2
    font.setPixelSize(px)
    painter.setFont(font)

    # Centre the tight box in the square (drawText positions on the baseline).
    br = painter.fontMetrics().tightBoundingRect(text)
    x = (size - br.width()) / 2 - br.x()
    y = (size - br.height()) / 2 - br.y()
    painter.drawText(int(round(x)), int(round(y)), text)
    painter.end()

    return QPixmap.fromImage(image)
