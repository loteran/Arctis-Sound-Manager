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


def get_battery_number_pixmap(battery_percent: int, color: str = '#ffffff') -> QPixmap:
    """A square pixmap of the battery number, sized to fill the tray slot (#119).

    Used by a dedicated second tray item so the number gets its own full-size
    slot next to the ASM icon (StatusNotifierItem hosts give each item one
    square slot; a wide "icon + text" pixmap gets squashed into one slot). The
    font auto-shrinks so 3-digit values ("100") still fit.
    """
    size = 64
    text = f"{int(battery_percent)}%"

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setPen(QColor(color))

    font = QFont()
    font.setBold(True)
    px = size - 4
    font.setPixelSize(px)
    painter.setFont(font)
    while px > 10 and painter.fontMetrics().horizontalAdvance(text) > size - 4:
        px -= 2
        font.setPixelSize(px)
        painter.setFont(font)

    painter.drawText(image.rect(), int(Qt.AlignmentFlag.AlignCenter), text)
    painter.end()

    return QPixmap.fromImage(image)
