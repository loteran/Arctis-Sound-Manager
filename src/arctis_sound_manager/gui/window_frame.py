# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The window's title bar and frame in the ASM theme, where the desktop allows.

The title bar and frame are drawn by the window manager, not by Qt. On KDE
Plasma an application can still choose their colours: it names a KDE colour
scheme file in the application property ``KDE_COLOR_SCHEME_PATH``, and the
Plasma platform theme passes it to KWin (the server-decoration palette
protocol on Wayland, the ``_KDE_NET_WM_COLOR_SCHEME`` window property on X11).
KWin then draws the decoration — Breeze or another — in those colours, with
its usual buttons, snapping and shadow.

Elsewhere nothing is done and the desktop's own title bar stays: drawing our
own (a frameless window) would mean rebuilding moving, resizing and snapping,
which behaves poorly on Wayland and looks foreign on every desktop.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent

import arctis_sound_manager.gui.theme as _theme

log = logging.getLogger(__name__)

_PROPERTY = "KDE_COLOR_SCHEME_PATH"


def _supported() -> bool:
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")


def _cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "arctis-sound-manager" / "window-frame"


def _rgb(colour: str) -> str:
    colour = colour.lstrip("#")
    return ",".join(str(int(colour[i:i + 2], 16)) for i in (0, 2, 4))


def colour_scheme(colours: dict[str, str]) -> str:
    """A KDE colour scheme (.colors) for the decoration, from a theme's colours.

    KDecoration takes the title bar from the Header colour set when the scheme
    has one (active, and [Inactive] for unfocused windows), and from [WM]
    otherwise; both are given. The title bar matches the sidebar, so the two
    meet without a seam; the window's other sets follow the theme too, should
    the platform theme also read them for the application palette.
    """
    c = {k: _rgb(colours.get(k) or _theme.THEMES["steelseries"][k])
         for k in ("BG_MAIN", "BG_SIDEBAR", "BG_CARD", "BG_BUTTON", "ACCENT",
                   "TEXT_PRIMARY", "TEXT_SECONDARY")}
    title_bg, title_fg, title_fg_inactive = c["BG_SIDEBAR"], c["TEXT_PRIMARY"], c["TEXT_SECONDARY"]

    def colour_set(background: str, foreground: str) -> str:
        return (f"BackgroundNormal={background}\nBackgroundAlternate={background}\n"
                f"ForegroundNormal={foreground}\nForegroundInactive={c['TEXT_SECONDARY']}\n"
                f"DecorationFocus={c['ACCENT']}\nDecorationHover={c['ACCENT']}\n")

    return (
        "[General]\nColorScheme=ArctisSoundManager\nName=Arctis Sound Manager\n\n"
        f"[Colors:Header]\n{colour_set(title_bg, title_fg)}\n"
        f"[Colors:Header][Inactive]\n{colour_set(title_bg, title_fg_inactive)}\n"
        f"[Colors:Window]\n{colour_set(c['BG_MAIN'], c['TEXT_PRIMARY'])}\n"
        f"[Colors:View]\n{colour_set(c['BG_CARD'], c['TEXT_PRIMARY'])}\n"
        f"[Colors:Button]\n{colour_set(c['BG_BUTTON'], c['TEXT_PRIMARY'])}\n"
        f"[Colors:Selection]\n{colour_set(c['ACCENT'], c['TEXT_PRIMARY'])}\n"
        f"[Colors:Tooltip]\n{colour_set(c['BG_CARD'], c['TEXT_PRIMARY'])}\n"
        "[WM]\n"
        f"activeBackground={title_bg}\nactiveBlend={title_bg}\nactiveForeground={title_fg}\n"
        f"inactiveBackground={title_bg}\ninactiveBlend={title_bg}\n"
        f"inactiveForeground={title_fg_inactive}\n"
    )


def apply(app=None) -> None:
    """Colour the title bar and frame after the active theme (KDE Plasma only).

    Call before the first window is shown, and again after each theme switch.
    Never raises: a decoration in the desktop's own colours is no failure.
    """
    if not _supported():
        return
    app = app or QCoreApplication.instance()
    if app is None:
        return
    try:
        text = colour_scheme(_theme.active_theme())
        # A new name for new colours: KWin reads the file by its path and may
        # keep what it read before under the same one.
        name = f"asm-{hashlib.sha1(text.encode()).hexdigest()[:12]}.colors"
        folder = _cache_dir()
        path = folder / name
        if not path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        current = app.property(_PROPERTY)
        if current == str(path):
            return
        app.setProperty(_PROPERTY, str(path))
        # The Plasma platform theme re-sends the scheme to every window when
        # the application palette changes; the palette itself is unchanged.
        QCoreApplication.sendEvent(app, QEvent(QEvent.Type.ApplicationPaletteChange))
        for old in folder.glob("asm-*.colors"):
            if old != path and str(old) != current:
                old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        log.debug("Could not theme the window decoration", exc_info=True)
