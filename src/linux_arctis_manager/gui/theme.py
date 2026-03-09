# SteelSeries Stealth dark theme — color constants and global QSS

BG_MAIN = "#16191E"
BG_SIDEBAR = "#1B1E22"
BG_CARD = "#1C2026"
BG_BUTTON = "#2D363E"
BG_BUTTON_HOVER = "#3A4550"
ACCENT = "#FB4A00"
TEXT_PRIMARY = "#C8C8C8"
TEXT_SECONDARY = "#8D96AA"
BORDER = "#2A3038"

# Audio channel accent colors
COLOR_GAME = "#04C5A8"
COLOR_CHAT = "#2791CE"
COLOR_MEDIA = "#C4006C"
COLOR_AUX = "#FB4A00"

APP_QSS = f"""
/* ── Global ── */
QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: system-ui, sans-serif;
    font-size: 11pt;
}}

/* ── Main window ── */
QMainWindow, QDialog {{
    background-color: {BG_MAIN};
}}

/* ── Labels ── */
QLabel {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
}}

/* ── Regular push buttons ── */
QPushButton {{
    background-color: {BG_BUTTON};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background-color: {BG_BUTTON_HOVER};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {TEXT_PRIMARY};
}}
QPushButton:disabled {{
    background-color: {BG_CARD};
    color: {TEXT_SECONDARY};
    border-color: {BORDER};
}}

/* ── Combo boxes ── */
QComboBox {{
    background-color: {BG_BUTTON};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 10px;
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid {BORDER};
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: {TEXT_PRIMARY};
}}

/* ── Sliders (horizontal) ── */
QSlider::groove:horizontal {{
    height: 6px;
    background: {BG_BUTTON};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    border: none;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}

/* ── Sliders (vertical) ── */
QSlider::groove:vertical {{
    width: 6px;
    background: {BG_BUTTON};
    border-radius: 3px;
}}
QSlider::handle:vertical {{
    background: {ACCENT};
    border: none;
    width: 16px;
    height: 16px;
    margin: 0 -5px;
    border-radius: 8px;
}}
QSlider::sub-page:vertical {{
    background: {ACCENT};
    border-radius: 3px;
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: {BG_CARD};
    width: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BG_BUTTON};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BG_BUTTON_HOVER};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}
QScrollBar:horizontal {{
    background: {BG_CARD};
    height: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {BG_BUTTON};
    min-width: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {BG_BUTTON_HOVER};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* ── Separators ── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {BORDER};
}}

/* ── List widget ── */
QListWidget {{
    background-color: {BG_SIDEBAR};
    border: none;
    color: {TEXT_PRIMARY};
    font-size: 11pt;
}}
QListWidget::item:selected {{
    background-color: {ACCENT};
    color: {TEXT_PRIMARY};
}}
QListWidget::item:hover {{
    background-color: {BG_BUTTON_HOVER};
}}
"""
