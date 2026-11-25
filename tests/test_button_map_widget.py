"""Button mappings as a side × gesture grid on the Headset page."""
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.gui import button_map_widget as B


def test_fits_only_side_gesture_names():
    assert B.fits(['right_1_tap', 'right_2_tap', 'left_1_tap', 'left_2_tap'])
    assert B.fits(['left_hold'])
    assert not B.fits(['right_1_tap', 'volume_limiter'])
    assert not B.fits(['right_1_tap_call'])
    assert not B.fits([])


@pytest.fixture()
def grid(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(B.QSettingsWidget, 'on_settings_updated', MagicMock())
    names = ['right_1_tap', 'right_2_tap', 'left_1_tap']
    widget = B.QButtonMapWidget(names)
    mapping = {'1': 'play_pause', '2': 'next_track'}
    widget.update_settings({
        'settings_config': {n: {'type': 'button_group', 'default_value': 1, 'values_mapping': mapping}
                            for n in names},
        'device': {n: 1 for n in names},
    })
    return widget


def test_sides_are_columns_and_gestures_rows(grid):
    layout = grid._grid
    def cell(name):
        return layout.getItemPosition(layout.indexOf(grid._settings_widgets[name]))[:2]
    assert cell('left_1_tap') == (1, 1)
    assert cell('right_1_tap') == (1, 2)
    assert cell('right_2_tap') == (2, 2)          # no left double tap: an empty cell


def test_earbuds_get_bud_icons_facing_each_other(grid):
    from arctis_sound_manager.gui.components import SvgIconWidget
    def icons():
        return [(w._svg_path.endswith('earbud_icon.svg'), w._mirrored)
                for header in grid._frame[:2] for w in header.findChildren(SvgIconWidget)]
    assert icons() == [(False, False), (False, False)]      # headphones until known
    grid.set_available_status_keys(frozenset({'left_bud_battery'}))
    assert icons() == [(True, False), (True, True)]         # right bud mirrored
