# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

# SteelSeries Stealth dark theme — color constants, theme dict, and global QSS

import configparser
import logging
import os
from pathlib import Path

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

THEME_KEYS: tuple[str, ...] = (
    "BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_BUTTON", "BG_BUTTON_HOVER",
    "BG_SIDEBAR_ACTIVE", "ACCENT", "ACCENT2", "TEXT_PRIMARY",
    "TEXT_SECONDARY", "BORDER", "COLOR_GAME", "COLOR_CHAT",
    "COLOR_AUX", "COLOR_HDMI",
)

THEME_GROUPS: dict[str, tuple[str, ...]] = {
    "theme_group_backgrounds": ("BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_BUTTON",
                                "BG_BUTTON_HOVER", "BG_SIDEBAR_ACTIVE"),
    "theme_group_accents":     ("ACCENT", "ACCENT2", "BORDER"),
    "theme_group_text":        ("TEXT_PRIMARY", "TEXT_SECONDARY"),
    "theme_group_channels":    ("COLOR_GAME", "COLOR_CHAT", "COLOR_AUX", "COLOR_HDMI"),
}

COLOR_LABEL_KEYS: dict[str, str] = {
    "BG_MAIN": "theme_color_bg_main", "BG_SIDEBAR": "theme_color_bg_sidebar",
    "BG_CARD": "theme_color_bg_card", "BG_BUTTON": "theme_color_bg_button",
    "BG_BUTTON_HOVER": "theme_color_bg_button_hover",
    "BG_SIDEBAR_ACTIVE": "theme_color_bg_sidebar_active",
    "ACCENT": "theme_color_accent", "ACCENT2": "theme_color_accent2",
    "BORDER": "theme_color_border",
    "TEXT_PRIMARY": "theme_color_text_primary", "TEXT_SECONDARY": "theme_color_text_secondary",
    "COLOR_GAME": "theme_color_game", "COLOR_CHAT": "theme_color_chat",
    "COLOR_AUX": "theme_color_aux", "COLOR_HDMI": "theme_color_hdmi",
}

USER_THEMES_DIR = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config')) / "arctis-sound-manager" / "themes"
BUILTIN_THEME_IDS = frozenset(THEMES)
PREVIEW_THEME_ID = "__preview__"

# ── Active theme state ────────────────────────────────────────────────────────

_ACTIVE_THEME = "steelseries"
_USER_THEMES: dict[str, dict[str, str]] = {}
_USER_LABELS: dict[str, str] = {}
_PREVIEW_COLORS: dict[str, str] | None = None


def _validate_color(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value))


def slugify(label: str) -> str:
    import re
    result = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return result or "custom"


def reload_user_themes() -> None:
    global _USER_THEMES, _USER_LABELS
    _USER_THEMES.clear()
    _USER_LABELS.clear()
    try:
        USER_THEMES_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(USER_THEMES_DIR.glob("*.ini")):
            tid = f.stem
            if tid in BUILTIN_THEME_IDS:
                logging.warning("Skipping user theme %s: conflicts with builtin", tid)
                continue
            try:
                parser = configparser.ConfigParser()
                parser.optionxform = str  # preserve case
                parser.read(f, encoding="utf-8")
                label = parser.get("theme", "name", fallback=tid)
                colors: dict[str, str] = {}
                for key in THEME_KEYS:
                    val = parser.get("colors", key, fallback=THEMES["steelseries"].get(key, "#000000"))
                    colors[key] = val if _validate_color(val) else THEMES["steelseries"].get(key, "#000000")
                _USER_THEMES[tid] = colors
                _USER_LABELS[tid] = label
            except Exception:
                logging.warning("Failed to load user theme %s", f, exc_info=True)
    except Exception:
        logging.warning("Failed to scan user themes dir", exc_info=True)


def get_theme(name: str) -> dict[str, str]:
    if name == PREVIEW_THEME_ID:
        return _PREVIEW_COLORS or THEMES["steelseries"]
    if name in THEMES:
        return THEMES[name]
    if name in _USER_THEMES:
        return _USER_THEMES[name]
    return THEMES["steelseries"]


def get_theme_label(name: str) -> str:
    if name in THEMES_LABELS:
        return THEMES_LABELS[name]
    if name in _USER_LABELS:
        return _USER_LABELS[name]
    return name


def all_theme_labels() -> dict[str, str]:
    result: dict[str, str] = dict(THEMES_LABELS)
    user_sorted = dict(sorted(_USER_LABELS.items(), key=lambda kv: kv[1].lower()))
    result.update(user_sorted)
    return result


def is_builtin(name: str) -> bool:
    return name in BUILTIN_THEME_IDS


def save_user_theme(label: str, colors: dict[str, str], theme_id: str | None = None) -> str:
    for key, val in colors.items():
        if not _validate_color(val):
            raise ValueError(f"Invalid color for {key}: {val!r}")
    if theme_id is None:
        base = slugify(label)
        tid = base
        counter = 2
        while tid in BUILTIN_THEME_IDS or (tid in _USER_THEMES and theme_id is None):
            tid = f"{base}-{counter}"
            counter += 1
        theme_id = tid
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["theme"] = {"name": label}
    parser["colors"] = {k: colors.get(k, THEMES["steelseries"].get(k, "#000000")) for k in THEME_KEYS}
    try:
        USER_THEMES_DIR.mkdir(parents=True, exist_ok=True)
        with open(USER_THEMES_DIR / f"{theme_id}.ini", "w", encoding="utf-8") as fh:
            parser.write(fh)
    except OSError as exc:
        logging.error("Cannot save theme %s to %s: %s", theme_id, USER_THEMES_DIR, exc)
        raise OSError(f"Cannot save theme: {exc}") from exc
    reload_user_themes()
    return theme_id


def delete_user_theme(theme_id: str) -> None:
    if is_builtin(theme_id):
        raise ValueError(f"Cannot delete builtin theme: {theme_id!r}")
    path = USER_THEMES_DIR / f"{theme_id}.ini"
    if path.exists():
        path.unlink()
    reload_user_themes()


def export_theme_to_file(theme_id: str, dest: Path) -> None:
    colors = get_theme(theme_id)
    label = get_theme_label(theme_id)
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["theme"] = {"name": label}
    parser["colors"] = {k: colors.get(k, THEMES["steelseries"].get(k, "#000000")) for k in THEME_KEYS}
    with open(dest, "w", encoding="utf-8") as fh:
        parser.write(fh)


def import_theme_from_file(src: Path) -> str:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(src, encoding="utf-8")
    if not parser.has_section("colors"):
        raise ValueError("Missing [colors] section")
    label = parser.get("theme", "name", fallback=src.stem)
    colors: dict[str, str] = {}
    for key in THEME_KEYS:
        val = parser.get("colors", key, fallback="")
        if not _validate_color(val):
            raise ValueError(f"Invalid or missing color for {key}")
        colors[key] = val
    return save_user_theme(label, colors)


def set_preview_colors(colors: dict[str, str] | None) -> None:
    global _PREVIEW_COLORS
    _PREVIEW_COLORS = colors


def set_active_theme(name: str) -> None:
    """Set the globally active theme by name (falls back to 'steelseries')."""
    global _ACTIVE_THEME
    if name in THEMES or name in _USER_THEMES or name == PREVIEW_THEME_ID:
        _ACTIVE_THEME = name
    else:
        _ACTIVE_THEME = "steelseries"


def active_theme() -> dict:
    """Return the color dict for the currently active theme."""
    return get_theme(_ACTIVE_THEME)


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
    t = get_theme(theme_name)

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
    font-family: sans-serif;
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

try:
    reload_user_themes()
except Exception:
    logging.warning("Failed to load user themes at startup", exc_info=True)
