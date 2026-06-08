# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

# SteelSeries Stealth dark theme — color constants, theme dict, and global QSS

THEMES = {
    "steelseries": {
        "BG_MAIN": "#16191E", "BG_SIDEBAR": "#1B1E22", "BG_CARD": "#1C2026",
        "BG_BUTTON": "#2D363E", "BG_BUTTON_HOVER": "#3A4550", "BG_SIDEBAR_ACTIVE": "#262B30",
        "ACCENT": "#FB4A00", "ACCENT2": "#FB4A00",
        "TEXT_PRIMARY": "#C8C8C8", "TEXT_SECONDARY": "#8D96AA", "BORDER": "#2A3038",
        "COLOR_GAME": "#04C5A8", "COLOR_CHAT": "#2791CE", "COLOR_AUX": "#FB4A00", "COLOR_HDMI": "#9B59B6",
    },
    "aurora": {
        "BG_MAIN": "#0d0d1f", "BG_SIDEBAR": "#0a0a1a", "BG_CARD": "#121224",
        "BG_BUTTON": "#1a1a3a", "BG_BUTTON_HOVER": "#252545", "BG_SIDEBAR_ACTIVE": "#1e1040",
        "ACCENT": "#7B2FFF", "ACCENT2": "#00D4FF",
        "TEXT_PRIMARY": "#E0DEFF", "TEXT_SECONDARY": "#6060AA", "BORDER": "#2a2050",
        "COLOR_GAME": "#7B2FFF", "COLOR_CHAT": "#00D4FF", "COLOR_AUX": "#f472b6", "COLOR_HDMI": "#34d399",
    },
    "neon": {
        "BG_MAIN": "#07070f", "BG_SIDEBAR": "#050508", "BG_CARD": "#0b0b15",
        "BG_BUTTON": "#0f0f1e", "BG_BUTTON_HOVER": "#141428", "BG_SIDEBAR_ACTIVE": "#0d1f1a",
        "ACCENT": "#00ffcc", "ACCENT2": "#ff00aa",
        "TEXT_PRIMARY": "#C8FFF0", "TEXT_SECONDARY": "#2a6655", "BORDER": "#0a2a20",
        "COLOR_GAME": "#00ffcc", "COLOR_CHAT": "#ff00aa", "COLOR_AUX": "#ff6600", "COLOR_HDMI": "#9b59b6",
    },
    "premium": {
        "BG_MAIN": "#131a24", "BG_SIDEBAR": "#0f1520", "BG_CARD": "#18202c",
        "BG_BUTTON": "#1e2938", "BG_BUTTON_HOVER": "#263344", "BG_SIDEBAR_ACTIVE": "#1a2535",
        "ACCENT": "#F59E0B", "ACCENT2": "#EF4444",
        "TEXT_PRIMARY": "#F0EDE8", "TEXT_SECONDARY": "#5a6880", "BORDER": "#2a3040",
        "COLOR_GAME": "#F59E0B", "COLOR_CHAT": "#EF4444", "COLOR_AUX": "#3b82f6", "COLOR_HDMI": "#8b5cf6",
    },
    "arctic": {
        "BG_MAIN": "#111d2c", "BG_SIDEBAR": "#0c1825", "BG_CARD": "#162030",
        "BG_BUTTON": "#1c2d40", "BG_BUTTON_HOVER": "#24384e", "BG_SIDEBAR_ACTIVE": "#142038",
        "ACCENT": "#4cc9f0", "ACCENT2": "#1d6fa4",
        "TEXT_PRIMARY": "#D8EFF8", "TEXT_SECONDARY": "#3d6080", "BORDER": "#1a3050",
        "COLOR_GAME": "#4cc9f0", "COLOR_CHAT": "#0ea5e9", "COLOR_AUX": "#06b6d4", "COLOR_HDMI": "#6366f1",
    },
}

THEMES_LABELS = {
    "steelseries": "SteelSeries",
    "aurora": "Aurora Glass",
    "neon": "Neon Pulse",
    "premium": "Slate Premium",
    "arctic": "Arctic",
}

# ── Active theme state ────────────────────────────────────────────────────────

_ACTIVE_THEME = "steelseries"


def set_active_theme(name: str) -> None:
    """Set the globally active theme by name (falls back to 'steelseries')."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = name if name in THEMES else "steelseries"


def active_theme() -> dict:
    """Return the color dict for the currently active theme."""
    return THEMES.get(_ACTIVE_THEME, THEMES["steelseries"])


def c(key: str) -> str:
    """Return color `key` for the currently active theme (falls back to steelseries)."""
    return active_theme().get(key, THEMES["steelseries"].get(key, "#000000"))


# ── Backward-compat constants (pointing at steelseries theme) ──────────────────
BG_MAIN          = THEMES["steelseries"]["BG_MAIN"]
BG_SIDEBAR       = THEMES["steelseries"]["BG_SIDEBAR"]
BG_CARD          = THEMES["steelseries"]["BG_CARD"]
BG_BUTTON        = THEMES["steelseries"]["BG_BUTTON"]
BG_BUTTON_HOVER  = THEMES["steelseries"]["BG_BUTTON_HOVER"]
ACCENT           = THEMES["steelseries"]["ACCENT"]
TEXT_PRIMARY     = THEMES["steelseries"]["TEXT_PRIMARY"]
TEXT_SECONDARY   = THEMES["steelseries"]["TEXT_SECONDARY"]
BORDER           = THEMES["steelseries"]["BORDER"]
COLOR_GAME       = THEMES["steelseries"]["COLOR_GAME"]
COLOR_CHAT       = THEMES["steelseries"]["COLOR_CHAT"]
COLOR_AUX        = THEMES["steelseries"]["COLOR_AUX"]
COLOR_HDMI       = THEMES["steelseries"]["COLOR_HDMI"]


def build_qss(theme_name: str) -> str:
    """Generate the full application QSS for the given theme name."""
    t = THEMES.get(theme_name, THEMES["steelseries"])

    BG_MAIN_         = t["BG_MAIN"]
    BG_SIDEBAR_      = t["BG_SIDEBAR"]
    BG_CARD_         = t["BG_CARD"]
    BG_BUTTON_       = t["BG_BUTTON"]
    BG_BUTTON_HOVER_ = t["BG_BUTTON_HOVER"]
    BG_SIDEBAR_ACT_  = t["BG_SIDEBAR_ACTIVE"]
    ACCENT_          = t["ACCENT"]
    ACCENT2_         = t["ACCENT2"]
    TEXT_PRIMARY_    = t["TEXT_PRIMARY"]
    TEXT_SECONDARY_  = t["TEXT_SECONDARY"]
    BORDER_          = t["BORDER"]

    # Gradient or solid for accent buttons / slider sub-pages
    if ACCENT_ != ACCENT2_:
        accent_bg = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {ACCENT_}, stop:1 {ACCENT2_})"
        )
        slider_sub_bg = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {ACCENT_}, stop:1 {ACCENT2_})"
        )
        slider_sub_v_bg = (
            f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {ACCENT2_}, stop:1 {ACCENT_})"
        )
    else:
        accent_bg = ACCENT_
        slider_sub_bg = ACCENT_
        slider_sub_v_bg = ACCENT_

    return f"""
/* ── Global ── */
QWidget {{
    background-color: {BG_MAIN_};
    color: {TEXT_PRIMARY_};
    font-family: system-ui, sans-serif;
    font-size: 11pt;
}}

/* ── Main window ── */
QMainWindow, QDialog {{
    background-color: {BG_MAIN_};
}}

/* ── Labels ── */
QLabel {{
    background-color: transparent;
    color: {TEXT_PRIMARY_};
}}

/* ── Regular push buttons ── */
QPushButton {{
    background-color: {BG_BUTTON_};
    color: {TEXT_PRIMARY_};
    border: 1px solid {BORDER_};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background-color: {BG_BUTTON_HOVER_};
    border-color: {ACCENT_};
}}
QPushButton:pressed {{
    background-color: {ACCENT_};
    color: {TEXT_PRIMARY_};
}}
QPushButton:disabled {{
    background-color: {BG_CARD_};
    color: {TEXT_SECONDARY_};
    border-color: {BORDER_};
}}

/* ── Sidebar widget ── */
QWidget#sidebar {{
    background-color: {BG_SIDEBAR_};
    border-right: 1px solid {BORDER_};
}}

/* ── GitHub link button ── */
QPushButton#ghLink {{
    background: transparent;
    border: none;
    color: {TEXT_SECONDARY_};
    font-size: 8pt;
    text-decoration: underline;
    padding: 4px 0;
}}
QPushButton#ghLink:hover {{
    color: {ACCENT_};
    background: transparent;
    border: none;
}}

/* ── Version label ── */
QLabel#versionLabel {{
    color: {TEXT_SECONDARY_};
    font-size: 8pt;
    background: transparent;
    padding: 2px 0;
}}

/* ── Accent button ── */
QPushButton#accentBtn {{
    background: {accent_bg};
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    font-size: 12pt;
    font-weight: bold;
    padding: 0 20px;
}}
QPushButton#accentBtn:hover {{
    background: {BG_BUTTON_HOVER_};
    color: {TEXT_PRIMARY_};
}}
QPushButton#accentBtn:pressed {{
    background-color: {ACCENT_};
    color: {TEXT_PRIMARY_};
}}
QPushButton#accentBtn:disabled {{
    background-color: {BG_CARD_};
    color: {TEXT_SECONDARY_};
}}

/* ── Rounded button ── */
QPushButton#roundedBtn {{
    background-color: {BG_BUTTON_};
    color: {TEXT_PRIMARY_};
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 11pt;
}}
QPushButton#roundedBtn:hover {{
    background-color: {BG_BUTTON_HOVER_};
    border: none;
}}

/* ── Theme chip buttons ── */
QPushButton#themeChip {{
    background-color: {BG_BUTTON_};
    color: {TEXT_SECONDARY_};
    border: 2px solid {BORDER_};
    border-radius: 14px;
    padding: 4px 14px;
    font-size: 10pt;
}}
QPushButton#themeChip:hover {{
    background-color: {BG_BUTTON_HOVER_};
    color: {TEXT_PRIMARY_};
    border-color: {ACCENT_};
}}
QPushButton#themeChip[active=true] {{
    border-color: {ACCENT_};
    color: {TEXT_PRIMARY_};
    background-color: {BG_SIDEBAR_ACT_};
}}

/* ── Combo boxes ── */
QComboBox {{
    background-color: {BG_BUTTON_};
    color: {TEXT_PRIMARY_};
    border: 1px solid {BORDER_};
    border-radius: 6px;
    padding: 4px 10px;
    min-width: 120px;
}}
QComboBox:hover {{
    border-color: {ACCENT_};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid {BORDER_};
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD_};
    color: {TEXT_PRIMARY_};
    border: 1px solid {BORDER_};
    selection-background-color: {ACCENT_};
    selection-color: {TEXT_PRIMARY_};
}}

/* ── Sliders (horizontal) ── */
QSlider::groove:horizontal {{
    height: 6px;
    background: {BG_BUTTON_};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT_};
    border: none;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {slider_sub_bg};
    border-radius: 3px;
}}

/* ── Sliders (vertical) ── */
QSlider::groove:vertical {{
    width: 6px;
    background: {BG_BUTTON_};
    border-radius: 3px;
}}
QSlider::handle:vertical {{
    background: {ACCENT_};
    border: none;
    width: 16px;
    height: 16px;
    margin: 0 -5px;
    border-radius: 8px;
}}
QSlider::sub-page:vertical {{
    background: {slider_sub_v_bg};
    border-radius: 3px;
}}

/* ── Scroll bars ── */
QScrollBar:vertical {{
    background: {BG_CARD_};
    width: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BG_BUTTON_};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BG_BUTTON_HOVER_};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}
QScrollBar:horizontal {{
    background: {BG_CARD_};
    height: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {BG_BUTTON_};
    min-width: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {BG_BUTTON_HOVER_};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* ── Separators ── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {BORDER_};
}}

/* ── List widget ── */
QListWidget {{
    background-color: {BG_SIDEBAR_};
    border: none;
    color: {TEXT_PRIMARY_};
    font-size: 11pt;
}}
QListWidget::item:selected {{
    background-color: {ACCENT_};
    color: {TEXT_PRIMARY_};
}}
QListWidget::item:hover {{
    background-color: {BG_BUTTON_HOVER_};
}}
"""


APP_QSS = build_qss("steelseries")
