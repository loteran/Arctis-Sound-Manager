"""Tests for sonar_to_pipewire — filter-chain config generation."""

from pathlib import Path
from unittest.mock import patch

from linux_arctis_manager.gui.eq_curve_widget import EqBand
from linux_arctis_manager.sonar_to_pipewire import (
    check_and_fix_stale_configs,
    generate_sonar_eq_conf,
    generate_sonar_micro_conf,
)


def test_bypass_game_uses_copy_not_gain():
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True, boost_db=0.0)
    assert "label = copy" in text
    assert "label = gain" not in text


def test_bypass_micro_uses_copy_not_gain():
    text = generate_sonar_micro_conf([], 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"),
                                     boost_db=0.0)
    assert "label = copy" in text
    assert "label = gain" not in text


def test_boost_uses_bq_highshelf():
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  boost_db=6.0)
    assert "bq_highshelf" in text
    assert "label = gain" not in text
    assert "boost_L" in text
    assert "boost_R" in text


def test_micro_boost_uses_bq_highshelf():
    bands = [EqBand(freq=500, gain=2.0, q=0.5, type="peakingEQ", enabled=True)]
    text = generate_sonar_micro_conf(bands, 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"),
                                     boost_db=3.0)
    assert "bq_highshelf" in text
    assert "label = gain" not in text


def test_boost_clamped_to_12db():
    bands = [EqBand(freq=1000, gain=1.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  boost_db=50.0)
    # Should be clamped to 12.0
    assert "Gain = 12.0" in text


def test_macro_sliders_generate_filters():
    text = generate_sonar_eq_conf("game", [], basses_db=3.0, voix_db=0.0, aigus_db=-2.0,
                                  output_path=Path("/dev/null"))
    assert "macro_basses_L" in text
    assert "macro_aigus_L" in text
    # voix is 0.0, should not generate a filter
    assert "macro_voix" not in text


def test_spatial_audio_off_targets_physical():
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=False)
    assert "alsa_output.usb-SteelSeries" in text
    assert "virtual-surround" not in text


def test_spatial_audio_on_targets_hesuvi():
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True)
    assert "virtual-surround-7.1-hesuvi" in text


def test_check_and_fix_stale_configs_fixes_gain(tmp_path):
    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = boost_L  label = gain\n'
        '        control = { Gain = 1.2 } }\n'
        '    ] } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-game-eq.conf").write_text(stale)

    with patch("linux_arctis_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        assert check_and_fix_stale_configs() is True

    fixed = (tmp_path / "sonar-game-eq.conf").read_text()
    assert "label = gain" not in fixed
    assert "label = copy" in fixed


def test_check_and_fix_stale_configs_noop_when_clean(tmp_path):
    clean = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy_L  label = copy }\n'
        '    ] } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-game-eq.conf").write_text(clean)

    with patch("linux_arctis_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        assert check_and_fix_stale_configs() is False
