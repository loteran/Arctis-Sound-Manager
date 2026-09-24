# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ChatMix channel selector GUI (#249, #269).

Each of Game, Media and Aux can be included in (or excluded from) the
ChatMix crossfade via a small checkbox under the ChatMix bar, one per
channel, labelled with the channel's own name rather than a full sentence
repeated on every card. Game ships on by default, and comes back
automatically whenever unchecking a channel would otherwise leave the
selection empty — the bar/dial must always drive something. Chat never
shows a checkbox: it is the crossfade's fixed other half.

Uses the lightweight SimpleNamespace fake-self pattern from
tests/test_home_page_app_name.py rather than instantiating a real
QApplication/AudioCard: the methods under test only touch attributes they
are handed, so a fake standing in for those attributes is enough.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from arctis_sound_manager.gui.home_page import HomePage


# ── HomePage: the checkbox itself ───────────────────────────────────────────

class _FakeCheckbox:
    """Stands in for the real QCheckBox: records setVisible/blockSignals/
    setChecked calls in order, so the blockSignals-during-population guard
    can be verified without a real Qt event loop."""

    def __init__(self):
        self._checked = False
        self._visible = False
        self.calls: list[tuple[str, object]] = []

    def setVisible(self, value):
        self.calls.append(("setVisible", value))
        self._visible = value

    def blockSignals(self, value):
        self.calls.append(("blockSignals", value))

    def setChecked(self, value):
        self.calls.append(("setChecked", value))
        self._checked = value

    def isChecked(self):
        return self._checked


def test_set_chatmix_checkbox_blocks_signals_around_the_write():
    """Without it, setting the initial state during population would fire
    the toggle callback and re-write the setting right back to what it
    already was."""
    cb = _FakeCheckbox()
    fake_self = SimpleNamespace(_chatmix_channel_checkboxes={"game": cb})

    HomePage._set_chatmix_checkbox(fake_self, "game", True, True)

    assert cb.calls == [
        ("setVisible", True),
        ("blockSignals", True),
        ("setChecked", True),
        ("blockSignals", False),
    ]
    assert cb.isChecked() is True


def test_set_chatmix_checkbox_reflects_false_and_hidden_too():
    cb = _FakeCheckbox()
    cb.setChecked(True)  # pre-existing state
    fake_self = SimpleNamespace(_chatmix_channel_checkboxes={"aux": cb})

    HomePage._set_chatmix_checkbox(fake_self, "aux", False, False)

    assert cb.isChecked() is False
    assert cb._visible is False


# ── HomePage: reflecting the setting on population ─────────────────────────

def _fake_card():
    card = MagicMock()
    card.isHidden.return_value = False
    return card


def test_refresh_chatmix_toggles_reflects_current_setting(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "media"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    aux_card = _fake_card()
    set_cb = MagicMock()
    fake_self = SimpleNamespace(
        _aux_card=aux_card,
        _set_chatmix_checkbox=set_cb,
        _refresh_chatmix_bar_label=MagicMock(),
    )

    HomePage._refresh_chatmix_toggles(fake_self)

    set_cb.assert_any_call("game", True, True)
    set_cb.assert_any_call("media", True, True)
    # Aux card is not hidden in this fake, so its checkbox is relevant too.
    set_cb.assert_any_call("aux", True, False)
    fake_self._refresh_chatmix_bar_label.assert_called_once_with({"game", "media"})


def test_refresh_chatmix_toggles_hides_aux_toggle_when_aux_card_is_hidden(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "aux"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    aux_card = _fake_card()
    aux_card.isHidden.return_value = True  # Aux channel itself is off
    set_cb = MagicMock()
    fake_self = SimpleNamespace(
        _aux_card=aux_card,
        _set_chatmix_checkbox=set_cb,
        _refresh_chatmix_bar_label=MagicMock(),
    )

    HomePage._refresh_chatmix_toggles(fake_self)

    set_cb.assert_any_call("aux", False, True)


# ── HomePage: the update path when the user toggles a card's checkbox ──────

def test_on_chatmix_toggle_adds_the_channel(monkeypatch):
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    write_calls = []
    monkeypatch.setattr(saved, "write_to_file", lambda: write_calls.append(True))
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    fake_self = SimpleNamespace(_refresh_chatmix_bar_label=MagicMock())
    HomePage._on_chatmix_toggle(fake_self, "media", True)

    assert saved.chatmix_channels == ["game", "media"]
    assert write_calls == [True]
    change_setting.assert_called_once_with("chatmix_channels", ["game", "media"])


def test_on_chatmix_toggle_removes_the_channel(monkeypatch):
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "media", "aux"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    monkeypatch.setattr(saved, "write_to_file", lambda: None)
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    fake_self = SimpleNamespace(_refresh_chatmix_bar_label=MagicMock())
    HomePage._on_chatmix_toggle(fake_self, "media", False)

    assert saved.chatmix_channels == ["game", "aux"]
    change_setting.assert_called_once_with("chatmix_channels", ["game", "aux"])


def test_on_chatmix_toggle_removing_game_falls_back_when_selection_would_be_empty(monkeypatch):
    """Unchecking the last remaining channel (here Game itself) restores Game
    (#269) — the bar/dial must always drive something — and the Game
    checkbox is corrected back to checked."""
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    monkeypatch.setattr(saved, "write_to_file", lambda: None)
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    game_cb = _FakeCheckbox()
    fake_self = SimpleNamespace(
        _chatmix_channel_checkboxes={"game": game_cb},
        _refresh_chatmix_bar_label=MagicMock(),
    )
    HomePage._on_chatmix_toggle(fake_self, "game", False)

    assert saved.chatmix_channels == ["game"]
    assert game_cb.isChecked() is True
    change_setting.assert_called_once_with("chatmix_channels", ["game"])


def test_on_chatmix_toggle_removing_last_non_game_channel_falls_back_to_game(monkeypatch):
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["media"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    monkeypatch.setattr(saved, "write_to_file", lambda: None)
    change_setting = MagicMock()
    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(change_setting))

    game_cb = _FakeCheckbox()
    fake_self = SimpleNamespace(
        _chatmix_channel_checkboxes={"game": game_cb},
        _refresh_chatmix_bar_label=MagicMock(),
    )
    HomePage._on_chatmix_toggle(fake_self, "media", False)

    assert saved.chatmix_channels == ["game"]
    assert game_cb.isChecked() is True
    change_setting.assert_called_once_with("chatmix_channels", ["game"])


def test_on_chatmix_toggle_survives_a_failed_daemon_notify(monkeypatch):
    """A failed D-Bus notify must not stop the local file write (mirrors
    _set_aux_enabled's independent try/except halves)."""
    from arctis_sound_manager import settings as settings_mod
    from arctis_sound_manager.gui import dbus_wrapper

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))
    write_calls = []
    monkeypatch.setattr(saved, "write_to_file", lambda: write_calls.append(True))

    def _boom(*_a, **_kw):
        raise RuntimeError("no daemon running")

    monkeypatch.setattr(dbus_wrapper.DbusWrapper, "change_setting", staticmethod(_boom))

    fake_self = SimpleNamespace(_refresh_chatmix_bar_label=MagicMock())
    HomePage._on_chatmix_toggle(fake_self, "aux", True)  # must not raise

    assert saved.chatmix_channels == ["game", "aux"]
    assert write_calls == [True]


# ── HomePage: the ChatMix bar itself (#269) ─────────────────────────────────

def test_chatmix_bar_to_percentages_center_is_full_both_sides():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(50) == (100, 100)


def test_chatmix_bar_to_percentages_full_left_is_channel_only():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(0) == (100, 0)


def test_chatmix_bar_to_percentages_full_right_is_chat_only():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(100) == (0, 100)


def test_chatmix_bar_to_percentages_clamps_out_of_range_input():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(-10) == chatmix_bar_to_percentages(0)
    assert chatmix_bar_to_percentages(150) == chatmix_bar_to_percentages(100)


def test_chatmix_percentages_to_bar_position_is_the_inverse():
    from arctis_sound_manager.gui.home_page import (
        chatmix_bar_to_percentages, chatmix_percentages_to_bar_position)

    for position in range(0, 101, 5):
        channels_pct, chat_pct = chatmix_bar_to_percentages(position)
        assert chatmix_percentages_to_bar_position(channels_pct, chat_pct) == position


def test_chatmix_percentages_to_bar_position_centre_when_both_full():
    from arctis_sound_manager.gui.home_page import chatmix_percentages_to_bar_position

    assert chatmix_percentages_to_bar_position(100, 100) == 50


def test_apply_chatmix_bar_drives_configured_channels_and_chat(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "media"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    calls = []
    fake_self = SimpleNamespace(
        _sink_game="game-sink",
        _sink_media="media-sink",
        _sink_chat="chat-sink",
        _apply_volume=lambda sink, value: calls.append((sink, value)),
    )

    HomePage._apply_chatmix_bar(fake_self, 70, 100)

    assert ("game-sink", 70) in calls
    assert ("media-sink", 70) in calls
    assert ("chat-sink", 100) in calls
    assert len(calls) == 3


def test_apply_chatmix_bar_falls_back_to_game_when_settings_unreadable(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    def _boom():
        raise RuntimeError("no config file")

    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(_boom))

    calls = []
    fake_self = SimpleNamespace(
        _sink_game="game-sink",
        _sink_media="media-sink",
        _sink_chat="chat-sink",
        _apply_volume=lambda sink, value: calls.append((sink, value)),
    )

    HomePage._apply_chatmix_bar(fake_self, 30, 100)

    assert calls == [("game-sink", 30), ("chat-sink", 100)]


# ── HomePage: the ChatMix bar's static, split-in-half track (#269) ──────────

def test_chatmix_bar_track_css_splits_exactly_at_the_middle():
    from arctis_sound_manager.gui.home_page import chatmix_bar_track_css

    css = chatmix_bar_track_css(["#F59E0B"], "#EF4444")

    assert css.startswith("qlineargradient(")
    assert "stop:0.0000 #F59E0B" in css
    assert "stop:0.5000 #EF4444" in css
    assert "stop:1.0000 #EF4444" in css


def test_chatmix_bar_track_css_bands_multiple_channels_within_the_left_half():
    from arctis_sound_manager.gui.home_page import chatmix_bar_track_css

    css = chatmix_bar_track_css(["#F59E0B", "#3b82f6"], "#EF4444")

    assert css.index("#F59E0B") < css.index("#3b82f6") < css.index("#EF4444")
    assert "stop:0.2500 #3b82f6" in css


def test_chatmix_bar_track_css_defaults_to_white_when_no_channel_is_included():
    from arctis_sound_manager.gui.home_page import chatmix_bar_track_css

    css = chatmix_bar_track_css([], "#EF4444")

    assert "#ffffff" in css


# ── HomePage: the ChatMix bar follows the hardware dial (#269) ──────────────

def _fake_bar():
    bar = MagicMock()
    bar.isSliderDown.return_value = False
    bar.value.return_value = -1  # never equal to a real position, forces the write
    return bar


def test_sync_chatmix_bar_follows_the_dial(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    bar = _fake_bar()
    fake_self = SimpleNamespace(_chatmix_bar=bar)

    HomePage._sync_chatmix_bar(fake_self, 40, 100, None, None)

    bar.setValue.assert_called_once_with(80)


def test_sync_chatmix_bar_averages_multiple_configured_channels(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "media"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    bar = _fake_bar()
    fake_self = SimpleNamespace(_chatmix_bar=bar)

    HomePage._sync_chatmix_bar(fake_self, 100, 40, 100, None)

    bar.setValue.assert_called_once_with(20)


def test_sync_chatmix_bar_skips_while_the_user_is_dragging():
    bar = _fake_bar()
    bar.isSliderDown.return_value = True
    fake_self = SimpleNamespace(_chatmix_bar=bar)

    HomePage._sync_chatmix_bar(fake_self, 40, 100, None, None)

    bar.setValue.assert_not_called()


def test_sync_chatmix_bar_skips_when_already_at_that_position(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    bar = _fake_bar()
    bar.value.return_value = 80
    fake_self = SimpleNamespace(_chatmix_bar=bar)

    HomePage._sync_chatmix_bar(fake_self, 40, 100, None, None)

    bar.setValue.assert_not_called()


def test_sync_chatmix_bar_does_nothing_without_a_chat_reading():
    bar = _fake_bar()
    fake_self = SimpleNamespace(_chatmix_bar=bar)

    HomePage._sync_chatmix_bar(fake_self, 40, None, None, None)

    bar.setValue.assert_not_called()
