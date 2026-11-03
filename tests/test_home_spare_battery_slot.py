# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#367: the Arctis Pro Wireless reports only whether a spare battery is in
the base (charge_slot_battery, #363), and the Channels page only read the
percentage key, so the DAC pill vanished."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


@pytest.fixture
def page():
    QApplication.instance() or QApplication([])
    from arctis_sound_manager.gui.home_page import HomePage
    widget = HomePage()
    yield widget
    widget.deleteLater()


def _status(slot):
    return {"headset": {"headset_power_status": {"type": "label", "value": "online"},
                        "headset_battery_charge": {"type": "percentage", "value": 50}},
            "gamedac": {"charge_slot_battery": {"type": "label", "value": slot}}}


@pytest.mark.parametrize("slot", ["battery_in_base", "no_battery"])
def test_spare_slot_state_shows_on_the_channels_page(page, slot):
    page.update_status(_status(slot))
    pill = page._status_bar._dac_bat_pill
    assert not pill.isHidden()
    assert pill._text.text().startswith("DAC") and "%" not in pill._text.text()


def test_a_percentage_still_wins(page):
    status = _status("battery_in_base")
    status["gamedac"]["charge_slot_battery_charge"] = {"type": "percentage", "value": 75}
    page.update_status(status)
    assert page._status_bar._dac_bat_pill._text.text() == "DAC  75%"
