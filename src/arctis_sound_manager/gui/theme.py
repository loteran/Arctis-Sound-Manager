# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

# SteelSeries Stealth dark theme — color constants, theme dict, and global QSS

import configparser
import logging
import os
import re
from pathlib import Path

THEMES = {
    "steelseries": {
        "BG_MAIN": "#16191E", "BG_SIDEBAR": "#1B1E22", "BG_CARD": "#1C2026",
        "BG_BUTTON": "#2D363E", "BG_BUTTON_HOVER": "#3A4550", "BG_SIDEBAR_ACTIVE": "#262B30",
        "ACCENT": "#FB4A00", "ACCENT2": "#FB4A00",
        "TEXT_PRIMARY": "#C8C8C8", "TEXT_SECONDARY": "#8D96AA", "BORDER": "#2A3038",
        "COLOR_GAME": "#04C5A8", "COLOR_CHAT": "#2791CE", "COLOR_AUX": "#FB4A00", "COLOR_HDMI": "#9B59B6", "COLOR_AUX2": "#E4B53C",
        "TOGGLE_ON": "#FB4A00", "TOGGLE_OFF": "#2D363E",
        "COLOR_MASTER": "#9E9E9E",
        "COLOR_MIC": "#E05A8A",
    },
    "aurora": {
        "BG_MAIN": "#0d0d1f", "BG_SIDEBAR": "#0a0a1a", "BG_CARD": "#121224",
        "BG_BUTTON": "#1a1a3a", "BG_BUTTON_HOVER": "#252545", "BG_SIDEBAR_ACTIVE": "#1e1040",
        "ACCENT": "#7B2FFF", "ACCENT2": "#00D4FF",
        "TEXT_PRIMARY": "#E0DEFF", "TEXT_SECONDARY": "#6060AA", "BORDER": "#2a2050",
        "COLOR_GAME": "#7B2FFF", "COLOR_CHAT": "#00D4FF", "COLOR_AUX": "#f472b6", "COLOR_HDMI": "#34d399", "COLOR_AUX2": "#facc15",
        "TOGGLE_ON": "#7B2FFF", "TOGGLE_OFF": "#1a1a3a",
        "COLOR_MASTER": "#9E9E9E",
        "COLOR_MIC": "#a3e635",
    },
    "neon": {
        "BG_MAIN": "#07070f", "BG_SIDEBAR": "#050508", "BG_CARD": "#0b0b15",
        "BG_BUTTON": "#0f0f1e", "BG_BUTTON_HOVER": "#141428", "BG_SIDEBAR_ACTIVE": "#0d1f1a",
        "ACCENT": "#00ffcc", "ACCENT2": "#ff00aa",
        "TEXT_PRIMARY": "#C8FFF0", "TEXT_SECONDARY": "#2a6655", "BORDER": "#0a2a20",
        "COLOR_GAME": "#00ffcc", "COLOR_CHAT": "#ff00aa", "COLOR_AUX": "#ff6600", "COLOR_HDMI": "#9b59b6", "COLOR_AUX2": "#ffe600",
        "TOGGLE_ON": "#00ffcc", "TOGGLE_OFF": "#0f0f1e",
        "COLOR_MASTER": "#9E9E9E",
        "COLOR_MIC": "#00aaff",
    },
    "premium": {
        "BG_MAIN": "#131a24", "BG_SIDEBAR": "#0f1520", "BG_CARD": "#18202c",
        "BG_BUTTON": "#1e2938", "BG_BUTTON_HOVER": "#263344", "BG_SIDEBAR_ACTIVE": "#1a2535",
        "ACCENT": "#F59E0B", "ACCENT2": "#EF4444",
        "TEXT_PRIMARY": "#F0EDE8", "TEXT_SECONDARY": "#5a6880", "BORDER": "#2a3040",
        "COLOR_GAME": "#F59E0B", "COLOR_CHAT": "#EF4444", "COLOR_AUX": "#3b82f6", "COLOR_HDMI": "#8b5cf6", "COLOR_AUX2": "#10b981",
        "TOGGLE_ON": "#F59E0B", "TOGGLE_OFF": "#1e2938",
        "COLOR_MASTER": "#9E9E9E",
        "COLOR_MIC": "#f472b6",
    },
    "arctic": {
        "BG_MAIN": "#111d2c", "BG_SIDEBAR": "#0c1825", "BG_CARD": "#162030",
        "BG_BUTTON": "#1c2d40", "BG_BUTTON_HOVER": "#24384e", "BG_SIDEBAR_ACTIVE": "#142038",
        "ACCENT": "#4cc9f0", "ACCENT2": "#1d6fa4",
        "TEXT_PRIMARY": "#D8EFF8", "TEXT_SECONDARY": "#3d6080", "BORDER": "#1a3050",
        "COLOR_GAME": "#4cc9f0", "COLOR_CHAT": "#0ea5e9", "COLOR_AUX": "#06b6d4", "COLOR_HDMI": "#6366f1", "COLOR_AUX2": "#a78bfa",
        "TOGGLE_ON": "#4cc9f0", "TOGGLE_OFF": "#1c2d40",
        "COLOR_MASTER": "#9E9E9E",
        "COLOR_MIC": "#94e2d5",
    },
    "matcha_sv": {
        "BG_MAIN": "#141a16", "BG_SIDEBAR": "#0f1411", "BG_CARD": "#1a221c",
        "BG_BUTTON": "#243028", "BG_BUTTON_HOVER": "#2e3d33", "BG_SIDEBAR_ACTIVE": "#1f2e24",
        "ACCENT": "#8fbf5a", "ACCENT2": "#c3dd9a",
        "TEXT_PRIMARY": "#f1ecdf", "TEXT_SECONDARY": "#7d8f7f", "BORDER": "#2a372d",
        "COLOR_GAME": "#8fbf5a", "COLOR_CHAT": "#e8a0a8", "COLOR_AUX": "#d9b779", "COLOR_HDMI": "#7fb7a4", "COLOR_AUX2": "#b39ddb",
        "TOGGLE_ON": "#8fbf5a", "TOGGLE_OFF": "#243028",
        "COLOR_MASTER": "#b8b2a2",
        "COLOR_MIC": "#9ec1e0",
    },
}

THEMES_LABELS = {
    "steelseries": "SteelSeries",
    "aurora": "Aurora Glass",
    "neon": "Neon Pulse",
    "premium": "Slate Premium",
    "arctic": "Arctic",
    "matcha_sv": "Matcha SV",
}

THEME_KEYS: tuple[str, ...] = (
    "BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_BUTTON", "BG_BUTTON_HOVER",
    "BG_SIDEBAR_ACTIVE", "ACCENT", "ACCENT2", "TEXT_PRIMARY",
    "TEXT_SECONDARY", "BORDER", "COLOR_GAME", "COLOR_CHAT",
    "COLOR_AUX", "COLOR_HDMI", "COLOR_AUX2", "TOGGLE_ON", "TOGGLE_OFF",
    "COLOR_MASTER", "COLOR_MIC",
)

# Colours added after the share format shipped. A link written before they
# existed cannot carry them, so theme_share fills them from the default palette
# instead of refusing the import — while a link missing one of the original
# keys is still rejected, because that means it is truncated or corrupt rather
# than merely older. Anything added here in future goes in this set too.
THEME_KEYS_OPTIONAL: frozenset[str] = frozenset({"COLOR_AUX2", "TOGGLE_ON", "TOGGLE_OFF",
                                                "COLOR_MASTER", "COLOR_MIC"})

# Optional colours that are better derived from the theme's own palette than
# taken from the default one: a theme made before the toggle colours existed
# keeps the switches it always had (accent when on, button background off).
# The Mic card (#315) arrived after many themes were made: it takes their
# Master colour, which those themes already use for a non-routable card.
DERIVED_DEFAULTS: dict[str, str] = {"TOGGLE_ON": "ACCENT", "TOGGLE_OFF": "BG_BUTTON",
                                    "COLOR_MIC": "COLOR_MASTER"}


def default_for(key: str, colors: dict[str, str]) -> str:
    """Fallback for an optional *key* missing from *colors*."""
    source = DERIVED_DEFAULTS.get(key)
    if source and source in colors:
        return colors[source]
    return THEMES["steelseries"].get(key, "#000000")

THEME_GROUPS: dict[str, tuple[str, ...]] = {
    "theme_group_backgrounds": ("BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_BUTTON",
                                "BG_BUTTON_HOVER", "BG_SIDEBAR_ACTIVE"),
    "theme_group_accents":     ("ACCENT", "ACCENT2", "BORDER"),
    "theme_group_text":        ("TEXT_PRIMARY", "TEXT_SECONDARY"),
    "theme_group_channels":    ("COLOR_MIC", "COLOR_MASTER", "COLOR_GAME", "COLOR_CHAT", "COLOR_AUX",
                                "COLOR_AUX2", "COLOR_HDMI"),
    "theme_group_toggles":     ("TOGGLE_ON", "TOGGLE_OFF"),
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
    "COLOR_AUX2": "theme_color_aux2",
    "TOGGLE_ON": "theme_color_toggle_on", "TOGGLE_OFF": "theme_color_toggle_off",
    "COLOR_MASTER": "theme_color_master",
    "COLOR_MIC": "theme_color_mic",
}

USER_THEMES_DIR = Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config')) / "arctis-sound-manager" / "themes"
# "Follow the system": colours read from the desktop's palette (the KDE colour
# scheme, through the platform theme) each time they are asked for.
SYSTEM_THEME_ID = "system"

BUILTIN_THEME_IDS = frozenset(THEMES) | {SYSTEM_THEME_ID}
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
                    val = parser.get("colors", key, fallback="")
                    colors[key] = val if _validate_color(val) else default_for(key, colors)
                _USER_THEMES[tid] = colors
                _USER_LABELS[tid] = label
            except Exception:
                logging.warning("Failed to load user theme %s", f, exc_info=True)
    except Exception:
        logging.warning("Failed to scan user themes dir", exc_info=True)


def _mix(a, b, amount: float) -> str:
    """*a* moved *amount* of the way towards *b*, as #rrggbb."""
    from PySide6.QtGui import QColor
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * amount),
                  round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount)).name()


def system_colors() -> dict[str, str]:
    """The ASM palette made from the desktop's: background, cards, buttons and
    text from its window and button colours, the accent from its selection
    colour. The channel colours keep the default theme's. Without a running
    application (or a palette) the default theme stands in."""
    colors = dict(THEMES["steelseries"])
    try:
        from PySide6.QtGui import QGuiApplication, QPalette
        if QGuiApplication.instance() is None:
            return colors
        pal = QGuiApplication.palette()
        role = QPalette.ColorRole
        window = pal.color(role.Window)
        text = pal.color(role.WindowText).name()
        button = pal.color(role.Button)
        accent = pal.color(role.Highlight).name()
        dark = window.lightness() < 128
        bg = window.name()
        toward = "#ffffff" if dark else "#000000"
        colors.update({
            "BG_MAIN": bg,
            "BG_SIDEBAR": _mix(bg, "#000000" if dark else "#ffffff", 0.18),
            "BG_CARD": _mix(bg, toward, 0.05),
            "BG_BUTTON": button.name(),
            "BG_BUTTON_HOVER": _mix(button.name(), toward, 0.10),
            "BG_SIDEBAR_ACTIVE": _mix(bg, accent, 0.25),
            "ACCENT": accent,
            "ACCENT2": pal.color(role.Link).name(),
            "TEXT_PRIMARY": text,
            "TEXT_SECONDARY": _mix(text, bg, 0.40),
            "BORDER": _mix(bg, text, 0.15),
            "TOGGLE_ON": accent,
            "TOGGLE_OFF": button.name(),
        })
    except Exception:
        logging.debug("could not read the system palette", exc_info=True)
    return colors


def get_theme(name: str) -> dict[str, str]:
    if name == SYSTEM_THEME_ID:
        return system_colors()
    if name == PREVIEW_THEME_ID:
        return _PREVIEW_COLORS or THEMES["steelseries"]
    if name in THEMES:
        return THEMES[name]
    if name in _USER_THEMES:
        return _USER_THEMES[name]
    return THEMES["steelseries"]


def _system_label() -> str:
    try:
        from arctis_sound_manager.i18n import I18n
        return I18n.translate("ui", "theme_follow_system")
    except Exception:
        return "Follow the system"


def get_theme_label(name: str) -> str:
    if name == SYSTEM_THEME_ID:
        return _system_label()
    if name in THEMES_LABELS:
        return THEMES_LABELS[name]
    if name in _USER_LABELS:
        return _USER_LABELS[name]
    return name


def all_theme_labels() -> dict[str, str]:
    result: dict[str, str] = {SYSTEM_THEME_ID: _system_label(), **THEMES_LABELS}
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
        if not val and key in THEME_KEYS_OPTIONAL:
            colors[key] = default_for(key, colors)
            continue
        if not _validate_color(val):
            raise ValueError(f"Invalid or missing color for {key}")
        colors[key] = val
    return save_user_theme(label, colors)


def set_preview_colors(colors: dict[str, str] | None) -> None:
    global _PREVIEW_COLORS
    _PREVIEW_COLORS = colors


def install_stylesheet_dedup() -> None:
    """Make QWidget.setStyleSheet() a no-op when the sheet is unchanged, and
    translate default-theme colours in it to the active theme's (see
    retheme_widgets).

    Every setStyleSheet() makes Qt re-style the widget and its whole subtree,
    even when the text is the one it already has. The pages theme themselves
    when built, and their parents theme them again right after (the Sonar
    channel tabs three times over): on the Equalizer page 273 of 760 calls
    repeated a widget's current sheet, ~85 % of its styling time. A changed
    sheet — a theme switch — still goes through.

    Nothing here relies on re-setting the same sheet to re-evaluate property
    selectors: [active=true] buttons refresh with style().unpolish/polish().
    Python-side only; Qt's own C++ calls are unaffected. Idempotent.
    """
    from PySide6.QtWidgets import QWidget

    if getattr(QWidget.setStyleSheet, "_asm_dedup", False):
        return
    original = QWidget.setStyleSheet

    def setStyleSheet(self, sheet):  # noqa: N802 — Qt's name
        # Default-theme colours (the module constants) -> the active theme.
        sheet = translate_sheet(sheet, _default_to_active())
        if self.styleSheet() == sheet:
            return
        original(self, sheet)

    setStyleSheet._asm_dedup = True
    QWidget.setStyleSheet = setStyleSheet


def set_active_theme(name: str) -> None:
    """Set the globally active theme by name (falls back to 'steelseries')."""
    global _ACTIVE_THEME, _APPLIED
    if name in THEMES or name in _USER_THEMES or name in (PREVIEW_THEME_ID, SYSTEM_THEME_ID):
        _ACTIVE_THEME = name
    else:
        _ACTIVE_THEME = "steelseries"
    if _APPLIED is None:
        # The first theme set, before any widget exists: what they are built in.
        _APPLIED = dict(active_theme())


# ── Inline stylesheets follow the theme ───────────────────────────────────────
# Widgets colour themselves with inline stylesheets, written either from c()
# (the theme active when they were built) or from the module-level constants
# below, which are the default theme's. Neither followed a theme switch unless
# the widget's own apply_theme() rewrote it, and many had none: after a switch
# parts of each page kept the old theme, and the constants showed the default
# theme's orange and greys on every theme. Rather than chase each widget, every
# inline stylesheet is translated by colour role: on setStyleSheet() from the
# default palette to the active one, and on a theme switch (retheme_widgets())
# from the colours the widgets carry to the new ones. No built-in theme uses a
# default-theme colour in a different role (tests/test_theme_follow.py), so a
# colour written through c() is never mistaken for a constant; within a theme
# where roles share a value, the first role in this order wins.
_ROLE_PRIORITY = (
    "ACCENT", "TEXT_PRIMARY", "TEXT_SECONDARY", "BG_MAIN", "BG_CARD", "BG_BUTTON",
    "BG_BUTTON_HOVER", "BORDER", "BG_SIDEBAR", "BG_SIDEBAR_ACTIVE", "TOGGLE_ON",
    "TOGGLE_OFF", "ACCENT2", "COLOR_GAME", "COLOR_CHAT", "COLOR_AUX", "COLOR_HDMI",
    "COLOR_AUX2", "COLOR_MASTER", "COLOR_MIC",
)
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")
_APPLIED: dict[str, str] | None = None      # the colours the widgets carry
_DEFAULT_MAP: tuple[tuple, dict[str, str]] = ((), {})


def colour_map(src: dict[str, str], dst: dict[str, str]) -> dict[str, str]:
    """Lower-case colour in `src` -> the colour of the same role in `dst`."""
    mapping: dict[str, str] = {}
    for key in _ROLE_PRIORITY:
        value = src.get(key)
        if value and key in dst:
            mapping.setdefault(value.lower(), dst[key])
    return {k: v for k, v in mapping.items() if k != v.lower()}


_CHEVRON_URL = re.compile(r"url\(([^)]*chevron-(up|down)-([0-9a-fA-F]{6})\.svg)\)")


def translate_sheet(sheet: str, mapping: dict[str, str]) -> str:
    if not sheet or not mapping:
        return sheet
    if "chevron-" in sheet:
        # Drop-down arrows are files named after their colour (_chevron_icon).
        def arrow(m: re.Match) -> str:
            new = mapping.get("#" + m.group(3).lower())
            return f"url({_chevron_icon(new, m.group(2) == 'up')})" if new else m.group(0)
        sheet = _CHEVRON_URL.sub(arrow, sheet)
    if "#" not in sheet:
        return sheet
    return _HEX_COLOR.sub(lambda m: mapping.get(m.group(0).lower(), m.group(0)), sheet)


def translate_colour(colour: str) -> str:
    """One default-theme colour -> the active theme's (for painted widgets)."""
    if not isinstance(colour, str):
        return colour
    return _default_to_active().get(colour.lower(), colour)


def _default_to_active() -> dict[str, str]:
    global _DEFAULT_MAP
    active = active_theme()
    key = (_ACTIVE_THEME, tuple(sorted(active.items())))
    if _DEFAULT_MAP[0] != key:
        _DEFAULT_MAP = (key, colour_map(THEMES["steelseries"], active))
    return _DEFAULT_MAP[1]


def retheme_widgets() -> None:
    """After set_active_theme(): carry every widget's inline stylesheet over
    from the colours it was written in to the active theme's. Call before the
    pages' own apply_theme(), which then rewrite what they know about."""
    global _APPLIED
    from PySide6.QtWidgets import QApplication
    new = dict(active_theme())
    old, _APPLIED = _APPLIED or new, new
    mapping = colour_map(old, new)
    if not mapping:
        return
    for widget in QApplication.allWidgets():
        # Widgets that paint with a remembered colour (tinted SVG icons).
        hook = getattr(widget, "retheme_colours", None)
        if callable(hook):
            hook(mapping)
        sheet = widget.styleSheet()
        if sheet:
            translated = translate_sheet(sheet, mapping)
            if translated != sheet:
                widget.setStyleSheet(translated)


def active_theme_name() -> str:
    return _ACTIVE_THEME


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


def _chevron_icon(color: str, up: bool) -> str:
    """Path to a chevron SVG in *color*, for QComboBox::down-arrow.

    QSS can only load arrow images from a file, so one small SVG per colour is
    written to the user cache the first time a theme asks for it.
    """
    points = "2,8 6,4 10,8" if up else "2,4 6,8 10,4"
    name = f"chevron-{'up' if up else 'down'}-{color.lstrip('#').lower()}.svg"
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = cache / "arctis_sound_manager" / name
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8" '
                'stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).debug("chevron icon not written: %s", exc)
    return path.as_posix()


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
    chevron_down     = _chevron_icon(TEXT_SECONDARY_, up=False)
    chevron_up       = _chevron_icon(ACCENT_, up=True)

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

/* ── Tooltips ── */
/* Without an explicit rule, a tooltip inherits the QWidget background above
   but takes its text colour from the platform palette. On a dark theme under
   GNOME/Wayland that lands as dark-on-dark: the mixer help popup showed up as
   a plain black rectangle with nothing readable in it (#146). */
QToolTip {{
    background-color: {BG_CARD_};
    color: {TEXT_PRIMARY_};
    border: 1px solid {BORDER_};
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 10pt;
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
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 5px 30px 5px 12px;
    min-width: 120px;
    combobox-popup: 0;
}}
QComboBox:hover {{
    background-color: {BG_BUTTON_HOVER_};
    border-color: {BORDER_};
}}
QComboBox:focus, QComboBox:on {{
    border-color: {ACCENT_};
}}
QComboBox:disabled {{
    color: {TEXT_SECONDARY_};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({chevron_down});
    width: 10px;
    height: 10px;
}}
QComboBox::down-arrow:on {{
    image: url({chevron_up});
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD_};
    color: {TEXT_PRIMARY_};
    border: 1px solid {BORDER_};
    border-radius: 8px;
    padding: 4px;
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 2px 10px;
    border-radius: 6px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {BG_BUTTON_HOVER_};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {BG_SIDEBAR_ACT_};
    color: {ACCENT_};
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
