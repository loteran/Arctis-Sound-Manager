# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""GUI-1: the Clips page's two timers.

The page is not constructed here — building it binds a global shortcut through
the desktop portal, i.e. a real request to the compositor of whoever runs the
suite (see test_clips_page_grid.py). These check the two things that made the
timers cost something: shutdown() stopped only one of them, and the
once-a-second tick did a PulseAudio round trip through detect_game() for a
label that only changes when a game starts.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

from arctis_sound_manager.gui.clips_page import ClipsPage


def _calls_in(method_name: str) -> set[str]:
    """Every function called by name inside *method_name*."""
    import textwrap
    src = textwrap.dedent(inspect.getsource(getattr(ClipsPage, method_name)))
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_the_one_second_tick_never_probes_pulseaudio():
    """detect_game() asks PulseAudio what is routed to the Game channel. On
    the 1 s status timer that is a round trip per second, for a label the 5 s
    game poll already knows."""
    assert "detect_game" not in _calls_in("_update_status")


def test_the_game_poll_is_where_detection_lives():
    assert "detect_game" in _calls_in("_poll_game")


def test_shutdown_stops_both_timers():
    """The game poll used to outlive the page: a PulseAudio round trip every
    five seconds for a widget nobody is looking at any more."""
    page = MagicMock()
    page._shortcut = None

    ClipsPage.shutdown(page)

    page._timer.stop.assert_called_once()
    page._game_timer.stop.assert_called_once()


def test_the_game_poll_records_what_it_saw_even_with_autostart_off():
    """The status line still names the game when autostart is unchecked —
    that is what lets _update_status stop probing on its own."""
    page = MagicMock()
    page._closing = False
    page._autostart.isChecked.return_value = False

    import arctis_sound_manager.clip_capture as cc
    original = cc.detect_game
    cc.detect_game = lambda: "Elden Ring"
    try:
        ClipsPage._poll_game(page)
    finally:
        cc.detect_game = original

    assert page._last_detected_game == "Elden Ring"
    page._on_toggle.assert_not_called()
