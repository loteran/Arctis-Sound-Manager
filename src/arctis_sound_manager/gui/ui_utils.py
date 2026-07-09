# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6 import QtSvg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

ICON_PATH = Path(__file__).parent / 'images' / 'steelseries_logo.svg'
_LOGO_PATH = Path(__file__).parent / 'images' / 'asm_logo.png'


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


def get_tray_icon_pixmap(battery_percent: int | None = None,
                         color: str = '#ffffff') -> QPixmap:
    """Tray icon pixmap, optionally with the battery percentage next to it.

    When *battery_percent* is None the plain ASM icon is returned. Otherwise the
    icon is drawn on the left and "<n>%" to its right on a wider canvas, so the
    number rides alongside the icon in the system tray (discussion #119).
    """
    base = get_icon_pixmap(color=color)  # 64×64 ASM glyph
    if battery_percent is None:
        return base

    text = f"{int(battery_percent)}%"
    font = QFont()
    font.setPixelSize(46)
    font.setBold(True)

    # Measure the text to size the canvas so nothing is clipped.
    probe = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    fm_painter = QPainter(probe)
    fm_painter.setFont(font)
    text_w = fm_painter.fontMetrics().horizontalAdvance(text)
    fm_painter.end()

    gap = 10
    total_w = 64 + gap + text_w + 4
    image = QImage(total_w, 64, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.drawPixmap(0, 0, base)
    painter.setPen(QColor(color))
    painter.setFont(font)
    painter.drawText(
        64 + gap, 0, text_w + 4, 64,
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        text,
    )
    painter.end()

    return QPixmap.fromImage(image)
