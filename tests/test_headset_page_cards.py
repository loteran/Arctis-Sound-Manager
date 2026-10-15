# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_headset_page_cards.py — the Headset page groups the headset's settings
into one card per section of the device profile.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arctis_sound_manager.gui.headset_cards import StatusTiles  # noqa: E402
from arctis_sound_manager.gui.headset_page import HeadsetPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _config(section: str | None, **extra) -> dict:
    config = {"type": "toggle", "default_value": 0,
              "values": {"off": 0, "on": 1, "off_label": "off", "on_label": "on"}}
    if section is not None:
        config["section"] = section
    return {**config, **extra}


def _payload(configs: dict[str, dict]) -> dict:
    return {"settings_config": configs, "device": {name: 0 for name in configs}}


def _groups(page: HeadsetPage) -> list[tuple[str | None, tuple[str, ...]]]:
    return page._card_groups


def test_one_card_per_section_in_profile_order(qapp):
    page = HeadsetPage()
    page.update_settings(_payload({
        "mic_volume": _config("microphone"),
        "pm_shutdown": _config("power_management"),
        "mic_side_tone": _config("microphone"),
    }))
    assert _groups(page) == [
        ("microphone", ("mic_volume", "mic_side_tone")),
        ("power_management", ("pm_shutdown",)),
    ]
    assert [card.title.text() for card in page._setting_cards] == ["Microphone", "Power"]


def test_station_and_hidden_settings_stay_off_the_page(qapp):
    """The base station has the DAC page; hidden settings have their own UI."""
    page = HeadsetPage()
    page.update_settings(_payload({
        "line_out": _config("station"),
        "noise_cancelling": _config("audio", hidden=True),
        "mic_volume": _config("microphone"),
    }))
    assert _groups(page) == [("microphone", ("mic_volume",))]


def test_an_older_daemon_gets_everything_in_one_card(qapp):
    page = HeadsetPage()
    page.update_settings(_payload({"a": _config(None), "b": _config(None)}))
    assert _groups(page) == [(None, ("a", "b"))]
    assert page._setting_cards[0].title.text() == "Device Settings"


def test_cards_are_not_rebuilt_while_the_sections_stay_the_same(qapp):
    """Settings arrive every second; rebuilding would reset a slider mid-drag."""
    page = HeadsetPage()
    payload = _payload({"mic_volume": _config("microphone")})
    page.update_settings(payload)
    first = list(page._setting_cards)
    page.update_settings(payload)
    assert page._setting_cards == first


def test_status_tiles_follow_the_reported_values(qapp):
    tiles = StatusTiles()
    tiles.update_status({
        "headset": {"headset_battery_charge": {"value": 67, "type": "percentage"},
                    "headset_power_status": {"value": "on", "type": "label"}},
        "mic": {"mic_status": {"value": "muted", "type": "label"}},
    })
    shown = [t for t in tiles._tiles if not t.isHidden()]
    assert [t.value.text() for t in shown][0] == "67%"
    assert not shown[0].bar.isHidden() and shown[0].bar.value() == 67
    assert shown[1].bar.isHidden()

    tiles.update_status({"headset": {"headset_power_status": {"value": "off", "type": "label"}}})
    assert len([t for t in tiles._tiles if not t.isHidden()]) == 1


@pytest.mark.parametrize("count,columns", [(1, 1), (4, 4), (6, 6), (7, 4), (8, 4), (9, 5), (12, 6)])
def test_tile_rows_are_balanced(count, columns):
    """Six tiles are one row, never four over two; more split evenly."""
    from arctis_sound_manager.gui.headset_cards import tile_columns
    assert tile_columns(count) == columns


def test_the_dial_is_a_chatmix_bar_not_two_percentages(qapp):
    tiles = StatusTiles()
    tiles.update_status({"headset": {
        "headset_power_status": {"value": "on", "type": "label"},
        "media_mix": {"value": 100, "type": "percentage"},
        "chat_mix": {"value": 40, "type": "percentage"},
    }})
    shown = [t.caption.text() for t in tiles._tiles if not t.isHidden()]
    assert shown == ["Power"]
    assert not tiles.chatmix.isHidden()
    assert tiles.chatmix.bar.value() == 20  # chat pulled down: left of centre


def test_a_long_choice_becomes_a_drop_down_in_a_compact_card(qapp):
    """Ten auto power-off delays as buttons pushed the page sideways."""
    from PySide6.QtWidgets import QPushButton

    from arctis_sound_manager.gui.qt_widgets.no_wheel_combo import NoWheelComboBox
    from arctis_sound_manager.gui.settings_widget import QSettingsWidget

    def group(n: int) -> dict:
        return {"type": "button_group", "default_value": 0,
                "values_mapping": {str(i): f"{i}_minutes" for i in range(n)}}

    widget = QSettingsWidget(None, None, "device", compact=True)
    widget.update_settings({
        "settings_config": {"pm_shutdown": group(10), "mic_side_tone": group(4)},
        "device": {"pm_shutdown": 5, "mic_side_tone": 1},
    })
    long_row = widget._settings_widgets["pm_shutdown"]
    short_row = widget._settings_widgets["mic_side_tone"]
    combo = long_row.findChild(NoWheelComboBox)
    assert combo is not None and combo.currentIndex() == 5
    assert not long_row.findChildren(QPushButton)
    assert len(short_row.findChildren(QPushButton)) == 4
