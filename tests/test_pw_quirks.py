# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for pw_quirks — per-device WirePlumber quirk fragments (issue #105)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import arctis_sound_manager.pw_quirks as pw_quirks


def _device_config(alsa_headroom=None, name="Test Device"):
    return SimpleNamespace(alsa_headroom=alsa_headroom, name=name)


@pytest.fixture(autouse=True)
def _isolated_conf_dir(tmp_path, monkeypatch):
    """Redirect the module's WirePlumber conf.d dir to a temp dir for every test."""
    monkeypatch.setattr(pw_quirks, "_WP_CONF_DIR", tmp_path / "wireplumber.conf.d")
    return tmp_path / "wireplumber.conf.d"


# ── Content generation ──────────────────────────────────────────────────────


def test_render_headroom_conf_contains_value_and_matcher():
    text = pw_quirks._render_headroom_conf(4096)
    assert "monitor.alsa.rules" in text
    assert "api.alsa.headroom = 4096" in text
    assert "SteelSeries" in text


# ── Write path ───────────────────────────────────────────────────────────────


def test_apply_writes_fragment_when_headroom_set_and_wp_supported():
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME

    with patch("arctis_sound_manager.pw_quirks._wireplumber_version", return_value=(0, 5)), \
         patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=4096))

    assert result is True
    assert conf_path.exists()
    assert "api.alsa.headroom = 4096" in conf_path.read_text()
    mock_restart.assert_called_once_with("wireplumber", timeout=15)


def test_apply_is_no_op_when_content_unchanged():
    """No write and no restart if the fragment on disk already matches."""
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(pw_quirks._render_headroom_conf(4096))
    mtime_before = conf_path.stat().st_mtime_ns

    with patch("arctis_sound_manager.pw_quirks._wireplumber_version", return_value=(0, 5)), \
         patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=4096))

    assert result is False
    mock_restart.assert_not_called()
    assert conf_path.stat().st_mtime_ns == mtime_before


def test_apply_rewrites_and_restarts_when_headroom_value_changes():
    """A changed headroom value must be written and must trigger a restart."""
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(pw_quirks._render_headroom_conf(2048))

    with patch("arctis_sound_manager.pw_quirks._wireplumber_version", return_value=(0, 5)), \
         patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=4096))

    assert result is True
    assert "api.alsa.headroom = 4096" in conf_path.read_text()
    mock_restart.assert_called_once_with("wireplumber", timeout=15)


# ── Removal path ─────────────────────────────────────────────────────────────


def test_apply_removes_fragment_when_quirk_absent():
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(pw_quirks._render_headroom_conf(4096))

    with patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=None))

    assert result is True
    assert not conf_path.exists()
    mock_restart.assert_called_once_with("wireplumber", timeout=15)


def test_apply_no_op_when_quirk_absent_and_no_fragment_on_disk():
    with patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=None))

    assert result is False
    mock_restart.assert_not_called()


# ── WirePlumber version gate ──────────────────────────────────────────────────


def test_apply_skips_when_wireplumber_below_0_5():
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME

    with patch("arctis_sound_manager.pw_quirks._wireplumber_version", return_value=(0, 4)), \
         patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=4096))

    assert result is False
    assert not conf_path.exists()
    mock_restart.assert_not_called()


def test_apply_skips_when_wireplumber_version_unknown():
    conf_path = pw_quirks._WP_CONF_DIR / pw_quirks._HEADROOM_CONF_NAME

    with patch("arctis_sound_manager.pw_quirks._wireplumber_version", return_value=None), \
         patch("arctis_sound_manager.service_control.restart", return_value=True) as mock_restart:
        result = pw_quirks.apply_alsa_headroom_quirk(_device_config(alsa_headroom=4096))

    assert result is False
    assert not conf_path.exists()
    mock_restart.assert_not_called()


def test_wireplumber_version_parses_dotted_output():
    fake_result = SimpleNamespace(stdout="wireplumber 0.5.3\n", stderr="")
    with patch("subprocess.run", return_value=fake_result):
        assert pw_quirks._wireplumber_version() == (0, 5)


def test_wireplumber_version_returns_none_when_binary_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert pw_quirks._wireplumber_version() is None
