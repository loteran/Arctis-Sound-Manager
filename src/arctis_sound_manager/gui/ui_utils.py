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
    hex color usable by get_icon_pixmap/get_tray_pixmap (#130).

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


def get_tray_pixmap(battery_percent: int | None = None,
                    color: str = '#ffffff') -> QPixmap:
    """The one tray icon: the ASM logo, with the battery % under it when known.

    ASM used to put the battery in a *second* tray item and take that item away
    whenever the level became unknown — which is exactly what happens when the
    headset powers off. `hide()` on a QSystemTrayIcon destroys the
    KStatusNotifierItem behind it, so a click already in flight from the tray
    host landed on freed memory and took the whole app down (#194). The
    coredump showed no ASM frame at all: KStatusNotifierItem::activate() on a
    dead object.

    Deferring the hide narrowed that window but could not close it. Clips runs
    nested GLib main loops (the shortcut portal, the ScreenCast portal), and a
    nested loop dispatches posted Qt events — including the tray's D-Bus
    Activate — at a moment nothing on our side chooses. The only version of
    this with no race in it is one item that is created once and never
    destroyed, which is what this pixmap is for: the level changes the *icon*,
    never the item's existence.

    `battery_percent` None (headset off, or the setting turned off) simply
    means no number — the logo alone, exactly as it was before #119.
    """
    size = 64
    logo = get_icon_pixmap(color=color)
    if battery_percent is None:
        return logo

    # The logo keeps the top ~62%, the number takes the strip underneath. Both
    # have to survive being scaled down to a 24px tray slot, which is what
    # decides these numbers — at that size a corner badge is unreadable mush.
    band_h = 24
    logo_h = size - band_h + 2          # slight overlap; the logo has padding

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    scaled = logo.scaled(logo_h, logo_h, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    painter.drawPixmap(int((size - scaled.width()) / 2), -2, scaled)

    text = f"{int(battery_percent)}%"
    painter.setPen(QColor(color))
    font = QFont()
    font.setBold(True)

    # Grow to fill the strip, bounded by both axes: "100%" is width-bound and
    # "9%" is height-bound, and a fixed size that suits one wrecks the other.
    px = 8
    while px < 60:
        font.setPixelSize(px + 1)
        painter.setFont(font)
        br = painter.fontMetrics().tightBoundingRect(text)
        if br.width() > size - 4 or br.height() > band_h - 2:
            break
        px += 1
    font.setPixelSize(px)
    painter.setFont(font)

    br = painter.fontMetrics().tightBoundingRect(text)
    x = (size - br.width()) / 2 - br.x()
    y = size - 1 - (band_h - br.height()) / 2 - (br.y() + br.height())
    painter.drawText(int(round(x)), int(round(y)), text)
    painter.end()

    return QPixmap.fromImage(image)


