# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for CoreEngine.is_device_online() — specifically the on/off ↔ online/offline aliasing
that fixes the silent-bug affecting 8 devices (Nova 5, Nova 7, Arctis 7+, Arctis 9, Arctis 1 W)."""

from unittest.mock import MagicMock, patch

import pytest


def make_engine_stub(actual_value, expected_value):
    """Return a minimal CoreEngine-like object with the attributes is_device_online() reads."""
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)

    online_status = MagicMock()
    online_status.status_variable = 'headset_power_status'
    online_status.online_value = expected_value

    device_config = MagicMock()
    device_config.online_status = online_status

    engine.device_status = {'headset_power_status': actual_value}
    engine.device_config = device_config

    return engine


@pytest.mark.parametrize("actual, expected, result", [
    # Primary fix: on_off parser ('on'/'off') vs YAML online_value ('online'/'offline')
    ('on',      'online',  True),
    ('off',     'online',  False),
    ('on',      'offline', False),
    ('off',     'offline', True),
    # Already-working devices (int_str_mapping, returns 'online'/'offline')
    ('online',  'online',  True),
    ('offline', 'online',  False),
    ('online',  'offline', False),
    ('offline', 'offline', True),
    # Case-insensitivity
    ('ON',      'online',  True),
    ('Off',     'offline', True),
])
def test_is_device_online_aliasing(actual, expected, result):
    engine = make_engine_stub(actual, expected)
    with patch('arctis_sound_manager.core.parsed_status', return_value={'headset_power_status': actual}):
        assert engine.is_device_online() is result


def test_is_device_online_missing_key():
    engine = make_engine_stub('on', 'online')
    with patch('arctis_sound_manager.core.parsed_status', return_value={}):
        assert engine.is_device_online() is False


def test_is_device_online_no_online_status_block():
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    device_config = MagicMock()
    device_config.online_status = None
    engine.device_status = {}
    engine.device_config = device_config

    assert engine.is_device_online() is True


def test_is_device_online_no_device_status():
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.device_status = None
    engine.device_config = MagicMock()

    assert engine.is_device_online() is False
