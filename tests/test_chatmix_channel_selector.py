# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the ChatMix channel selector GUI (#249, #269).

Each of Game, Media and Aux can be included in (or excluded from) the
ChatMix crossfade via a small checkbox on its card ("Include in ChatMix").
Game ships on by default, and comes back automatically whenever unchecking
a card would otherwise leave the selection empty — the bar/dial must always
drive something. Chat never shows the checkbox: it is the crossfade's fixed
other half.

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

from arctis_sound_manager.gui.home_page import AudioCard, HomePage


# ── AudioCard: the checkbox itself ──────────────────────────────────────────

class _FakeCheckbox:
    """Stands in for the real QCheckBox: records blockSignals/setChecked calls
    in order, so the blockSignals-during-population guard can be verified
    without a real Qt event loop."""

    def __init__(self):
        self._checked = False
        self.calls: list[tuple[str, object]] = []

    def blockSignals(self, value):
        self.calls.append(("blockSignals", value))

    def setChecked(self, value):
        self.calls.append(("setChecked", value))
        self._checked = value

    def isChecked(self):
        return self._checked


def test_set_chatmix_checked_blocks_signals_around_the_write():
    """Same guard as set_device_options(): without it, setting the initial
    state during population would fire the toggle callback and re-write the
    setting right back to what it already was."""
    fake_self = SimpleNamespace(_chatmix_checkbox=_FakeCheckbox())

    AudioCard.set_chatmix_checked(fake_self, True)

    assert fake_self._chatmix_checkbox.calls == [
        ("blockSignals", True),
        ("setChecked", True),
        ("blockSignals", False),
    ]
    assert fake_self._chatmix_checkbox.isChecked() is True


def test_set_chatmix_checked_reflects_false_too():
    fake_self = SimpleNamespace(_chatmix_checkbox=_FakeCheckbox())
    fake_self._chatmix_checkbox.setChecked(True)  # pre-existing state

    AudioCard.set_chatmix_checked(fake_self, False)

    assert fake_self._chatmix_checkbox.isChecked() is False


def test_chatmix_toggled_invokes_the_registered_callback():
    calls = []
    fake_self = SimpleNamespace(_chatmix_toggle_cb=lambda enabled: calls.append(enabled))

    AudioCard._on_chatmix_toggled(fake_self, True)

    assert calls == [True]


def test_chatmix_toggled_is_a_noop_without_a_registered_callback():
    fake_self = SimpleNamespace(_chatmix_toggle_cb=None)

    AudioCard._on_chatmix_toggled(fake_self, True)  # must not raise


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

    game_card = _fake_card()
    media_card = _fake_card()
    aux_card = _fake_card()
    fake_self = SimpleNamespace(
        _game_card=game_card,
        _media_card=media_card,
        _aux_card=aux_card,
        _refresh_chatmix_bar_label=MagicMock(),
    )

    HomePage._refresh_chatmix_toggles(fake_self)

    game_card.set_chatmix_toggle_visible.assert_called_once_with(True)
    game_card.set_chatmix_checked.assert_called_once_with(True)
    media_card.set_chatmix_toggle_visible.assert_called_once_with(True)
    media_card.set_chatmix_checked.assert_called_once_with(True)
    # Aux card is not hidden in this fake, so its toggle is relevant too.
    aux_card.set_chatmix_toggle_visible.assert_called_once_with(True)
    aux_card.set_chatmix_checked.assert_called_once_with(False)
    fake_self._refresh_chatmix_bar_label.assert_called_once_with({"game", "media"})


def test_refresh_chatmix_toggles_hides_aux_toggle_when_aux_card_is_hidden(monkeypatch):
    from arctis_sound_manager import settings as settings_mod

    saved = settings_mod.GeneralSettings()
    saved.chatmix_channels = ["game", "aux"]
    monkeypatch.setattr(settings_mod.GeneralSettings, "read_from_file", staticmethod(lambda: saved))

    game_card = _fake_card()
    media_card = _fake_card()
    aux_card = _fake_card()
    aux_card.isHidden.return_value = True  # Aux channel itself is off
    fake_self = SimpleNamespace(
        _game_card=game_card,
        _media_card=media_card,
        _aux_card=aux_card,
        _refresh_chatmix_bar_label=MagicMock(),
    )

    HomePage._refresh_chatmix_toggles(fake_self)

    aux_card.set_chatmix_toggle_visible.assert_called_once_with(False)


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

    game_card = _fake_card()
    fake_self = SimpleNamespace(_game_card=game_card, _refresh_chatmix_bar_label=MagicMock())
    HomePage._on_chatmix_toggle(fake_self, "game", False)

    assert saved.chatmix_channels == ["game"]
    game_card.set_chatmix_checked.assert_called_once_with(True)
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

    game_card = _fake_card()
    fake_self = SimpleNamespace(_game_card=game_card, _refresh_chatmix_bar_label=MagicMock())
    HomePage._on_chatmix_toggle(fake_self, "media", False)

    assert saved.chatmix_channels == ["game"]
    game_card.set_chatmix_checked.assert_called_once_with(True)
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


def test_chatmix_bar_to_percentages_full_left_is_chat_only():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(0) == (0, 100)


def test_chatmix_bar_to_percentages_full_right_is_channel_only():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(100) == (100, 0)


def test_chatmix_bar_to_percentages_clamps_out_of_range_input():
    from arctis_sound_manager.gui.home_page import chatmix_bar_to_percentages

    assert chatmix_bar_to_percentages(-10) == chatmix_bar_to_percentages(0)
    assert chatmix_bar_to_percentages(150) == chatmix_bar_to_percentages(100)


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


# ── HomePage: the ChatMix bar's channel-side colour (#269) ──────────────────

def test_chatmix_bar_channels_css_color_defaults_to_white_when_empty():
    from arctis_sound_manager.gui.home_page import chatmix_bar_channels_css_color

    assert chatmix_bar_channels_css_color([]) == "#ffffff"


def test_chatmix_bar_channels_css_color_is_flat_for_a_single_channel():
    from arctis_sound_manager.gui.home_page import chatmix_bar_channels_css_color

    assert chatmix_bar_channels_css_color(["#F59E0B"]) == "#F59E0B"


def test_chatmix_bar_channels_css_color_bands_each_included_channel():
    from arctis_sound_manager.gui.home_page import chatmix_bar_channels_css_color

    css = chatmix_bar_channels_css_color(["#F59E0B", "#3b82f6"])

    assert css.startswith("qlineargradient(")
    assert "#F59E0B" in css
    assert "#3b82f6" in css
    assert "stop:0.0000 #F59E0B" in css
    assert "stop:1.0000 #3b82f6" in css


def test_chatmix_bar_channels_css_color_bands_three_channels_in_order():
    from arctis_sound_manager.gui.home_page import chatmix_bar_channels_css_color

    css = chatmix_bar_channels_css_color(["#F59E0B", "#3b82f6", "#10b981"])

    assert css.index("#F59E0B") < css.index("#3b82f6") < css.index("#10b981")
