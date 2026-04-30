"""Tests for sonar_to_pipewire — filter-chain config generation."""

from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager.gui.eq_curve_widget import EqBand
from arctis_sound_manager.sonar_to_pipewire import (
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


def test_boost_game_uses_bq_highshelf_single_node():
    """Game EQ (8ch): single boost node (PipeWire auto-dups per channel)."""
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  boost_db=6.0)
    assert "bq_highshelf" in text
    assert "label = gain" not in text
    assert "name = boost" in text
    # 8ch: no L/R duplicates
    assert "boost_L" not in text
    assert "boost_R" not in text


def test_boost_chat_uses_bq_highshelf_lr_nodes():
    """Chat EQ (2ch): L/R boost nodes."""
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  boost_db=6.0)
    assert "bq_highshelf" in text
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


def test_macro_sliders_game_single_nodes():
    """Game EQ (8ch): macro filters are single nodes (auto-dup)."""
    text = generate_sonar_eq_conf("game", [], basses_db=3.0, voix_db=0.0, aigus_db=-2.0,
                                  output_path=Path("/dev/null"))
    assert "macro_basses" in text
    assert "macro_aigus" in text
    # 8ch: no L/R suffixes
    assert "macro_basses_L" not in text
    assert "macro_aigus_L" not in text
    # voix is 0.0, should not generate a filter
    assert "macro_voix" not in text


def test_macro_sliders_chat_lr_nodes():
    """Chat EQ (2ch): macro filters have L/R pairs."""
    text = generate_sonar_eq_conf("chat", [], basses_db=3.0, voix_db=0.0, aigus_db=-2.0,
                                  output_path=Path("/dev/null"))
    assert "macro_basses_L" in text
    assert "macro_basses_R" in text
    assert "macro_aigus_L" in text


def test_game_targets_hesuvi_virtual_surround():
    """Game EQ targets HeSuVi virtual surround for 7.1 virtualisation."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True)
    assert "virtual-surround-7.1-hesuvi" in text


def test_game_8ch_channels():
    """Game EQ uses 8 channels (7.1 surround)."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "audio.channels = 8" in text
    assert "FL FR FC LFE RL RR SL SR" in text


def test_chat_targets_physical_output():
    """Chat EQ targets ALSA physical output directly (2ch stereo)."""
    text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "alsa_output.usb-SteelSeries" in text
    assert "audio.channels = 2" in text


def test_bypass_game_has_node_name_in_playback():
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True, boost_db=0.0)
    assert 'node.name           = "effect_output.sonar-game-eq"' in text


def test_bypass_chat_has_node_name_in_playback():
    text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  boost_db=0.0)
    assert 'node.name           = "effect_output.sonar-chat-eq"' in text


def test_bypass_micro_has_node_name_in_playback():
    text = generate_sonar_micro_conf([], 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"),
                                     boost_db=0.0)
    assert 'node.name      = "effect_output.sonar-micro-eq"' in text


def test_active_game_has_node_name_in_playback():
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True)
    assert 'node.name           = "effect_output.sonar-game-eq"' in text


def test_micro_capture_uses_unique_name():
    """Micro capture must NOT reuse the physical ALSA device name."""
    text = generate_sonar_micro_conf([], 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"),
                                     boost_db=0.0)
    assert 'node.name      = "effect_input.sonar-micro-eq"' in text
    # Must use target.object for the physical device, not node.name
    assert "target.object" in text


def test_micro_source_pattern():
    """Micro uses correct source pattern: passive capture, Audio/Source playback."""
    text = generate_sonar_micro_conf([], 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    # Capture side: passive, no media.class
    assert "node.passive   = true" in text
    # Playback side: Audio/Source (not Audio/Source/Virtual)
    assert "media.class    = Audio/Source" in text
    assert "Audio/Source/Virtual" not in text
    # No Audio/Sink on capture side
    assert "Audio/Sink" not in text


def test_game_bypass_no_explicit_inputs_outputs():
    """Game bypass (8ch) relies on PipeWire auto-dup, no inputs/outputs arrays."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "inputs" not in text
    assert "outputs" not in text


def test_chat_bypass_has_explicit_inputs_outputs():
    """Chat bypass (2ch) has explicit inputs/outputs for L/R."""
    text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "inputs" in text
    assert "outputs" in text
    assert "copy_L" in text
    assert "copy_R" in text


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

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()
        assert fixed is True

    fixed = (tmp_path / "sonar-game-eq.conf").read_text()
    assert "label = gain" not in fixed
    assert "label = copy" in fixed


def test_check_and_fix_stale_configs_fixes_2ch_game(tmp_path):
    """A game config with 2ch is stale — should be regenerated as 8ch."""
    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { capture.props = { audio.channels = 2 } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-game-eq.conf").write_text(stale)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()
        assert fixed is True

    fixed = (tmp_path / "sonar-game-eq.conf").read_text()
    assert "audio.channels = 8" in fixed


def test_check_and_fix_stale_configs_noop_when_clean(tmp_path, monkeypatch):
    # ensure_sonar_eq_configs() validates that BOTH game and chat configs
    # exist with the right target+channels — the fixture must mirror the
    # full contract for the noop assertion to hold. Use stable test values
    # for both physical-out helpers so the expected node.target is known
    # (no real Arctis is plugged into CI runners).
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out",
        lambda: "alsa_output.test-headset",
    )

    game_clean = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy  label = copy }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 8 }\n'
        '    playback.props = { node.target         = "effect_input.virtual-surround-7.1-hesuvi" } } }\n'
        ']\n'
    )
    chat_clean = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy  label = copy }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 2 }\n'
        '    playback.props = { node.target         = "alsa_output.test-headset" } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-game-eq.conf").write_text(game_clean)
    (tmp_path / "sonar-chat-eq.conf").write_text(chat_clean)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()
        assert fixed is False


def test_check_and_fix_stale_configs_fixes_micro_source_virtual(tmp_path):
    """A micro config with Audio/Source/Virtual is stale — should be Audio/Source."""
    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { playback.props = { media.class = Audio/Source/Virtual } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-micro-eq.conf").write_text(stale)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()
        assert fixed is True

    fixed = (tmp_path / "sonar-micro-eq.conf").read_text()
    assert "Audio/Source/Virtual" not in fixed
    assert "media.class    = Audio/Source" in fixed
