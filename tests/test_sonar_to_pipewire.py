# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for sonar_to_pipewire — filter-chain config generation."""

from pathlib import Path
from unittest.mock import patch

from arctis_sound_manager import sonar_to_pipewire as _s2p
from arctis_sound_manager.eq_types import EqBand
from arctis_sound_manager.sonar_to_pipewire import (
    check_and_fix_stale_configs,
    diff_filter_conf,
    generate_sonar_eq_conf,
    generate_sonar_micro_conf,
    generate_virtual_sinks_conf,
)


def test_output_eq_adapts_to_external_sink_channel_count():
    """Output EQ uses the external sink's native channel count (2.0–7.1), so a
    7.1 sink keeps native surround rather than being downmixed (#111)."""
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    with patch.object(_s2p, "_resolve_external_output",
                      return_value=("alsa_output.hdmi-7-1", 8, "FL FR FC LFE RL RR SL SR")):
        text = generate_sonar_eq_conf("output", bands, 0.0, 0.0, 0.0,
                                      output_path=Path("/dev/null"))
    assert "audio.channels = 8" in text
    assert "FL FR FC LFE RL RR SL SR" in text
    assert "alsa_output.hdmi-7-1" in text


def test_output_passthrough_is_copy_at_native_channels():
    """Output passthrough (no bands) = a plain copy at the sink's native channel
    count — no EQ nodes. This is what the Output passthrough toggle emits."""
    with patch.object(_s2p, "_resolve_external_output",
                      return_value=("alsa_output.hdmi-7-1", 8, "FL FR FC LFE RL RR SL SR")):
        text = generate_sonar_eq_conf("output", [], 0.0, 0.0, 0.0,
                                      output_path=Path("/dev/null"))
    assert "label = copy" in text
    assert "bq_peaking" not in text
    assert "audio.channels = 8" in text
    assert "alsa_output.hdmi-7-1" in text


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
    # Phase 1 (issue #100/#88): once the channel is not fully flat (basses/
    # aigus are non-zero here), ALL 3 macro nodes are always emitted — even
    # voix at 0.0 — as a unity-gain bq_peaking passthrough, so the node count
    # stays stable while the user drags a macro slider across zero.
    assert "macro_voix" in text
    assert "Gain = 0.0" in text


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


def test_game_target_has_target_object():
    """Game EQ playback.props must include both node.target and target.object (WP 0.5)."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True)
    assert 'node.target         = "effect_input.virtual-surround-7.1-hesuvi"' in text
    assert 'target.object       = "effect_input.virtual-surround-7.1-hesuvi"' in text


def test_game_8ch_channels():
    """Game EQ uses 8 channels (7.1 surround)."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "audio.channels = 8" in text
    assert "FL FR FC LFE RL RR SL SR" in text


def test_chat_targets_physical_output():
    """Chat EQ targets ALSA physical output directly (2ch stereo)."""
    from arctis_sound_manager import device_state
    device_state.set_current_device(
        physical_out_game="alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.pro-output-1",
        physical_out_chat="alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.pro-output-0",
        physical_in="alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback",
        spatial_engine="hesuvi",
        device_name="SteelSeries Arctis Nova Pro Wireless",
    )
    try:
        text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                      output_path=Path("/dev/null"))
    finally:
        device_state.clear()
    assert "alsa_output.usb-SteelSeries" in text
    assert "audio.channels = 2" in text


def test_chat_target_has_target_object():
    """Chat EQ playback.props must include both node.target and target.object (WP 0.5)."""
    from arctis_sound_manager import device_state
    phys = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.pro-output-0"
    device_state.set_current_device(
        physical_out_game="alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.pro-output-1",
        physical_out_chat=phys,
        physical_in="alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback",
        spatial_engine="hesuvi",
        device_name="SteelSeries Arctis Nova Pro Wireless",
    )
    try:
        text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                      output_path=Path("/dev/null"))
    finally:
        device_state.clear()
    assert f'node.target         = "{phys}"' in text
    assert f'target.object       = "{phys}"' in text


def test_bypass_game_has_target_object():
    """Game EQ bypass config must include target.object when target is non-empty."""
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"),
                                  spatial_audio=True, boost_db=0.0)
    # spatial_audio=True → target = HeSuVi virtual surround
    assert 'target.object       = "effect_input.virtual-surround-7.1-hesuvi"' in text


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
    assert 'node.name             = "effect_output.sonar-micro-eq"' in text


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
    assert "media.class           = Audio/Source" in text
    assert "Audio/Source/Virtual" not in text
    # No Audio/Sink on capture side
    assert "Audio/Sink" not in text


def test_micro_capture_owns_its_link():
    """Issue #127: the micro EQ capture must run with node.autoconnect=false
    and state.restore-target=false, exactly like the loopback/EQ-output
    links (issue #100), so WirePlumber never links or moves it and a
    filter-chain restart cannot let it get stolen by a competing mic. Checked
    on both the active (banded) and bypass paths."""
    active = generate_sonar_micro_conf(
        [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)],
        0.0, 0.0, 0.0, output_path=Path("/dev/null"),
    )
    bypass = generate_sonar_micro_conf([], 0.0, 0.0, 0.0, output_path=Path("/dev/null"))
    for text in (active, bypass):
        assert "node.autoconnect     = false" in text
        assert "state.restore-target = false" in text
        # target.object is retained as a documentary/pre-link hint only.
        assert "target.object" in text


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
    # check_and_fix_stale_configs() validates that the game, media AND chat
    # configs exist with the right target+channels — the fixture must mirror
    # the full contract for the noop assertion to hold. Use stable test values
    # for the physical-out helpers so the expected node.target is known
    # (no real Arctis is plugged into CI runners). Spatial audio defaults to
    # enabled, so game and media are 8ch routed through the HeSuVi surround.
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out",
        lambda: "alsa_output.test-headset",
    )
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out_game",
        lambda: "alsa_output.test-headset",
    )
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out_chat",
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
    # Media mirrors game: 8ch routed through the HeSuVi surround when spatial
    # audio is enabled (the default).
    media_clean = game_clean
    (tmp_path / "sonar-game-eq.conf").write_text(game_clean)
    (tmp_path / "sonar-media-eq.conf").write_text(media_clean)
    (tmp_path / "sonar-chat-eq.conf").write_text(chat_clean)
    # The Output channel is checked too, and its expected shape comes from
    # _resolve_external_output() — which asks PipeWire what is actually plugged
    # in, so the answer differs from one machine to the next. Pin it to the
    # documented "no external sink" fallback and provide the matching conf,
    # otherwise the config is seen as missing/stale and `fixed` comes back True.
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._resolve_external_output",
        lambda *a, **kw: ("", 2, "FL FR"),
    )
    (tmp_path / "sonar-output-eq.conf").write_text(chat_clean)

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
    assert "media.class           = Audio/Source" in fixed


# ── generate_virtual_sinks_conf — deprecated shim behaviour ──────────────────

def test_generate_virtual_sinks_conf_returns_empty_string(tmp_path):
    """The deprecated shim must return '' regardless of sonar mode."""
    sinks_conf_dir = tmp_path / "pipewire.conf.d"
    sinks_conf_dir.mkdir()

    with patch("arctis_sound_manager.sonar_to_pipewire._SINKS_CONF_DIR", sinks_conf_dir):
        result_sonar = generate_virtual_sinks_conf(sonar=True)
        result_simple = generate_virtual_sinks_conf(sonar=False)

    assert result_sonar == ""
    assert result_simple == ""


def test_generate_virtual_sinks_conf_removes_static_file(tmp_path):
    """The deprecated shim must delete 10-arctis-virtual-sinks.conf if present."""
    sinks_conf_dir = tmp_path / "pipewire.conf.d"
    sinks_conf_dir.mkdir()
    static_file = sinks_conf_dir / "10-arctis-virtual-sinks.conf"
    static_file.write_text("context.modules = []")

    with patch("arctis_sound_manager.sonar_to_pipewire._SINKS_CONF_DIR", sinks_conf_dir):
        generate_virtual_sinks_conf(sonar=True)

    assert not static_file.exists(), "Legacy static loopback config should have been removed"


def test_generate_virtual_sinks_conf_noop_when_no_file(tmp_path):
    """The shim must not crash when the static file does not exist."""
    sinks_conf_dir = tmp_path / "pipewire.conf.d"
    sinks_conf_dir.mkdir()

    with patch("arctis_sound_manager.sonar_to_pipewire._SINKS_CONF_DIR", sinks_conf_dir):
        result = generate_virtual_sinks_conf(sonar=False)

    assert result == ""


# ── check_and_fix_stale_configs — static loopback file migration ──────────────

def test_check_and_fix_removes_static_sinks_and_signals_pw_restart(tmp_path):
    """When 10-arctis-virtual-sinks.conf exists, it must be removed and
    needs_pw_restart must be True (one-shot migration to dynamic loopbacks)."""
    sinks_conf_dir = tmp_path / "pipewire.conf.d"
    sinks_conf_dir.mkdir()
    static_file = sinks_conf_dir / "10-arctis-virtual-sinks.conf"
    static_file.write_text("context.modules = []")

    with (
        patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path),
        patch("arctis_sound_manager.sonar_to_pipewire._SINKS_CONF_DIR", sinks_conf_dir),
    ):
        fixed, needs_pw_restart = check_and_fix_stale_configs()

    assert fixed is True
    assert needs_pw_restart is True
    assert not static_file.exists(), "Legacy static loopback config should have been deleted"


def test_check_and_fix_noop_when_no_static_sinks_file(tmp_path, monkeypatch):
    """When no 10-arctis-virtual-sinks.conf exists, fixed must be False
    (no migration needed)."""
    sinks_conf_dir = tmp_path / "pipewire.conf.d"
    sinks_conf_dir.mkdir()

    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out",
        lambda: "alsa_output.test-headset",
    )
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out_game",
        lambda: "alsa_output.test-headset",
    )
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out_chat",
        lambda: "alsa_output.test-headset",
    )

    # check_and_fix also ensures the per-channel EQ confs exist; provide clean
    # ones (8ch game/media via HeSuVi, 2ch chat) so that part is a no-op and we
    # isolate the "no static sinks file → no migration" assertion.
    eq_8ch = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy  label = copy }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 8 }\n'
        '    playback.props = { node.target         = "effect_input.virtual-surround-7.1-hesuvi" } } }\n'
        ']\n'
    )
    eq_chat = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy  label = copy }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 2 }\n'
        '    playback.props = { node.target         = "alsa_output.test-headset" } } }\n'
        ']\n'
    )
    (tmp_path / "sonar-game-eq.conf").write_text(eq_8ch)
    (tmp_path / "sonar-media-eq.conf").write_text(eq_8ch)
    (tmp_path / "sonar-chat-eq.conf").write_text(eq_chat)
    # Same as above: the Output channel's expected shape is probed from the live
    # PipeWire graph, so pin it to the "no external sink" fallback and ship the
    # matching conf, or this test depends on the machine it runs on.
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._resolve_external_output",
        lambda *a, **kw: ("", 2, "FL FR"),
    )
    (tmp_path / "sonar-output-eq.conf").write_text(eq_chat)

    with (
        patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path),
        patch("arctis_sound_manager.sonar_to_pipewire._SINKS_CONF_DIR", sinks_conf_dir),
    ):
        fixed, needs_pw_restart = check_and_fix_stale_configs()

    assert fixed is False
    assert needs_pw_restart is False


# ── CoreEngine._read_eq_mode_is_sonar ─────────────────────────────────────────
# CoreEngine imports USB deps at module level, so we test the helper logic
# directly here rather than importing CoreEngine and risking import errors on CI
# machines without a USB stack.  The logic in _read_eq_mode_is_sonar is a
# one-liner; these tests verify the three distinct cases.

def _eq_mode_is_sonar(path: Path) -> bool:
    """Mirror of CoreEngine._read_eq_mode_is_sonar for isolated testing."""
    try:
        return path.exists() and path.read_text().strip() == "sonar"
    except OSError:
        return False


def test_read_eq_mode_is_sonar_returns_true_when_file_contains_sonar(tmp_path):
    """Logic returns True when .eq_mode contains 'sonar'."""
    eq_mode_file = tmp_path / ".eq_mode"
    eq_mode_file.write_text("sonar")
    assert _eq_mode_is_sonar(eq_mode_file) is True


def test_read_eq_mode_is_sonar_returns_false_when_file_missing(tmp_path):
    """Logic returns False when .eq_mode does not exist."""
    eq_mode_file = tmp_path / ".eq_mode"
    assert not eq_mode_file.exists()
    assert _eq_mode_is_sonar(eq_mode_file) is False


def test_read_eq_mode_is_sonar_returns_false_when_file_contains_custom(tmp_path):
    """Logic returns False when .eq_mode contains anything other than 'sonar'."""
    eq_mode_file = tmp_path / ".eq_mode"
    eq_mode_file.write_text("custom")
    assert _eq_mode_is_sonar(eq_mode_file) is False


# ── _restart_filter_chain crash-loop safe mode (issue #88) ────────────────────


def test_restart_filter_chain_stable_no_safe_mode(monkeypatch):
    """When the filter-chain stays up, safe mode is NOT entered."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)

    restart_calls = []
    with patch("arctis_sound_manager.service_control.restart",
               side_effect=lambda *a, **kw: restart_calls.append(a) or True), \
         patch("arctis_sound_manager.service_control.is_active", return_value=True), \
         patch("time.sleep"):
        stp._restart_filter_chain()

    assert len(restart_calls) == 1
    assert stp._filter_chain_safe_mode is False


def test_restart_filter_chain_crash_loop_enters_safe_mode(tmp_path, monkeypatch):
    """A persistent crash-loop triggers safe mode and moves ASM configs aside."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "disabled")
    # Patch marker so we don't write to the real home dir during tests
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "safe_mode_marker.json")

    (tmp_path / "sonar-game-eq.conf").write_text("game")
    (tmp_path / "sonar-chat-eq.conf").write_text("chat")
    (tmp_path / "unrelated.conf").write_text("should stay")

    with patch("arctis_sound_manager.service_control.restart", return_value=True), \
         patch("arctis_sound_manager.service_control.is_active", return_value=False), \
         patch("time.sleep"):
        stp._restart_filter_chain()

    assert stp._filter_chain_safe_mode is True
    disabled = tmp_path.parent / "disabled"
    assert (disabled / "sonar-game-eq.conf").exists()
    assert (disabled / "sonar-chat-eq.conf").exists()
    assert not (tmp_path / "sonar-game-eq.conf").exists()
    assert (tmp_path / "unrelated.conf").exists()  # non-ASM file untouched


def test_restart_filter_chain_noop_when_already_safe_mode(monkeypatch):
    """Calling _restart_filter_chain while already in safe mode is a no-op."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)

    restart_calls = []
    with patch("arctis_sound_manager.service_control.restart",
               side_effect=lambda *a, **kw: restart_calls.append(a) or True):
        stp._restart_filter_chain()

    assert restart_calls == []


def test_safe_mode_moves_only_asm_files(tmp_path, monkeypatch):
    """_enter_filter_chain_safe_mode moves only filenames in _ASM_CONF_NAMES."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    disabled_dir = tmp_path.parent / "fc_disabled"
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", disabled_dir)
    # Patch marker path so we don't write to the real home dir during tests
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "safe_mode_marker.json")

    for name in stp._ASM_CONF_NAMES:
        (tmp_path / name).write_text(f"# {name}")
    (tmp_path / "user-custom.conf").write_text("# user-managed")
    (tmp_path / "10-system.conf").write_text("# system-managed")

    with patch("arctis_sound_manager.service_control.restart", return_value=True):
        stp._enter_filter_chain_safe_mode()

    for name in stp._ASM_CONF_NAMES:
        assert (disabled_dir / name).exists(), f"{name} should have moved"
        assert not (tmp_path / name).exists(), f"{name} should not remain"
    assert (tmp_path / "user-custom.conf").exists()
    assert (tmp_path / "10-system.conf").exists()


def test_reset_filter_chain_safe_mode_clears_flag(tmp_path, monkeypatch):
    """reset_filter_chain_safe_mode() clears the module-level flag."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    stp.reset_filter_chain_safe_mode()
    assert stp._filter_chain_safe_mode is False


# ── Correctif 1 — _poll_filter_chain_stable / ensure_filter_chain_healthy ─────


def test_poll_filter_chain_stable_returns_true_when_active(monkeypatch):
    """_poll_filter_chain_stable() returns True when is_active() sees the service up."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    with patch("arctis_sound_manager.service_control.is_active", return_value=True), \
         patch("time.sleep"):
        result = stp._poll_filter_chain_stable()

    assert result is True


def test_poll_filter_chain_stable_returns_false_in_crash_loop(monkeypatch):
    """_poll_filter_chain_stable() returns False when is_active() stays False (crash-loop)."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    with patch("arctis_sound_manager.service_control.is_active", return_value=False), \
         patch("time.sleep"):
        result = stp._poll_filter_chain_stable()

    assert result is False


def test_ensure_filter_chain_healthy_no_asm_conf_returns_true_without_action(tmp_path, monkeypatch):
    """No ASM config exists on disk → ASM cannot have caused a crash loop.
    Returns True immediately without calling is_active/start/restart at all
    (adapted from PR #104's early-return, kept from the original review)."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)  # empty dir — no ASM conf files
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")

    with patch("arctis_sound_manager.service_control.is_active") as mock_active, \
         patch("arctis_sound_manager.service_control.start") as mock_start, \
         patch("arctis_sound_manager.service_control.restart") as mock_restart:
        result = stp.ensure_filter_chain_healthy()

    assert result is True
    assert stp._filter_chain_safe_mode is False
    mock_active.assert_not_called()
    mock_start.assert_not_called()
    mock_restart.assert_not_called()


def test_ensure_filter_chain_healthy_inactive_starts_and_recovers(tmp_path, monkeypatch):
    """Inactive filter-chain that comes back up after sc.start() (e.g. a boot
    ordering race rather than a real crash-loop) recovers without entering
    safe mode — the start-then-poll behaviour adapted from PR #104."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    with patch("arctis_sound_manager.service_control.is_active", return_value=False), \
         patch("arctis_sound_manager.service_control.start", return_value=True) as mock_start, \
         patch("arctis_sound_manager.service_control.restart") as mock_restart, \
         patch("arctis_sound_manager.sonar_to_pipewire._poll_filter_chain_stable", return_value=True):
        result = stp.ensure_filter_chain_healthy()

    assert result is True
    assert stp._filter_chain_safe_mode is False
    mock_start.assert_called_once()
    mock_restart.assert_not_called()  # safe mode never entered → no restart


def test_ensure_filter_chain_healthy_inactive_stays_down_enters_safe_mode(tmp_path, monkeypatch):
    """Inactive filter-chain that a start-then-poll fails to bring up is a real
    crash-loop — still enters safe mode. The #88 protection must not be
    weakened by the start-then-poll adaptation."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    with patch("arctis_sound_manager.service_control.is_active", return_value=False), \
         patch("arctis_sound_manager.service_control.start", return_value=True) as mock_start, \
         patch("arctis_sound_manager.service_control.restart", return_value=True), \
         patch("arctis_sound_manager.sonar_to_pipewire._poll_filter_chain_stable", return_value=False):
        result = stp.ensure_filter_chain_healthy()

    assert result is False
    assert stp._filter_chain_safe_mode is True
    mock_start.assert_called_once()


def test_ensure_filter_chain_healthy_returns_true_when_healthy(tmp_path, monkeypatch):
    """ensure_filter_chain_healthy() returns True when is_active() is True and
    NRestarts is below threshold."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    with patch("arctis_sound_manager.service_control.is_active", return_value=True), \
         patch("arctis_sound_manager.init_system.detect_init", return_value="unknown"):
        # detect_init returning "unknown" skips NRestarts check
        result = stp.ensure_filter_chain_healthy()

    assert result is True
    assert stp._filter_chain_safe_mode is False


def test_ensure_filter_chain_healthy_enters_safe_mode_on_high_nrestarts(tmp_path, monkeypatch):
    """ensure_filter_chain_healthy() enters safe mode when NRestarts >= 3 (systemd)."""
    import subprocess
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")
    (tmp_path / "sonar-game-eq.conf").write_text("# dummy ASM conf")

    mock_result = type("R", (), {"stdout": "NRestarts=5\n", "returncode": 0})()

    with patch("arctis_sound_manager.service_control.is_active", return_value=True), \
         patch("arctis_sound_manager.service_control.restart", return_value=True), \
         patch("subprocess.run", return_value=mock_result), \
         patch("arctis_sound_manager.init_system.detect_init", return_value="systemd"):
        result = stp.ensure_filter_chain_healthy()

    assert result is False
    assert stp._filter_chain_safe_mode is True


# ── Correctif 2 — safe-mode disk marker persistence ───────────────────────────


def test_enter_safe_mode_writes_marker(tmp_path, monkeypatch):
    """_enter_filter_chain_safe_mode() writes a JSON marker to disk."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    marker = tmp_path / "marker.json"
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", marker)
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_CONF_DIR_DISABLED", tmp_path.parent / "fc_disabled")

    with patch("arctis_sound_manager.service_control.restart", return_value=True):
        stp._enter_filter_chain_safe_mode()

    assert marker.exists(), "marker should be written to disk"
    import json
    data = json.loads(marker.read_text())
    assert "timestamp" in data
    assert "reason" in data


def test_reset_safe_mode_removes_marker(tmp_path, monkeypatch):
    """reset_filter_chain_safe_mode() removes the disk marker."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    marker = tmp_path / "marker.json"
    marker.write_text('{"timestamp": "x", "reason": "test"}')
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", marker)

    stp.reset_filter_chain_safe_mode()

    assert not marker.exists(), "marker should be deleted on reset"
    assert stp._filter_chain_safe_mode is False


def test_check_and_fix_stale_configs_skips_in_safe_mode(tmp_path, monkeypatch):
    """check_and_fix_stale_configs() is a no-op when safe mode is active."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    marker = tmp_path / "marker.json"
    marker.write_text('{"timestamp": "x", "reason": "test"}')
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", marker)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, needs_restart = stp.check_and_fix_stale_configs()

    assert fixed is False
    assert needs_restart is False


def test_ensure_sonar_eq_configs_skips_in_safe_mode(tmp_path, monkeypatch):
    """ensure_sonar_eq_configs() returns False without regenerating when safe mode is active."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    marker = tmp_path / "marker.json"
    marker.write_text('{"timestamp": "x", "reason": "test"}')
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", True)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", marker)

    # If the function tries to regenerate it would need device_state set — the
    # fact that it returns without error proves the early-return is working.
    result = stp.ensure_sonar_eq_configs()
    assert result is False


# ── Correctif 3 — anti-flap window ────────────────────────────────────────────


def test_flap_window_is_60_seconds():
    """_FLAP_WINDOW constant documents the fix: raised from 30 → 60 s so that 3
    orphan recreations spaced ~15 s apart all fall within the observation window
    and correctly trigger the anti-flap cooldown (issue #88 Correctif 3)."""
    # This is validated by reading core.py at import time: the constant is local
    # to the coroutine and not directly importable.  The test verifies the
    # documented intent via a regression note (change is detectable via grep).
    import re
    from pathlib import Path
    core_text = (Path(__file__).parent.parent /
                 "src" / "arctis_sound_manager" / "core.py").read_text()
    # Should find "_FLAP_WINDOW: float = 60.0"
    assert re.search(r"_FLAP_WINDOW\s*:\s*float\s*=\s*60\.0", core_text), (
        "_FLAP_WINDOW should be 60.0 (raised from 30.0 for issue #88 Correctif 3)"
    )


# ── Correctif 4 — LADSPA guards ───────────────────────────────────────────────
#
# _ladspa_plugin_available() is now a boolean wrapper around
# _ladspa_plugin_ref(), which itself resolves through
# system_deps_checker._find_ladspa_plugin() (the single source of truth,
# v1.1.89) — so tests patch that function (and, for container-path tests,
# bug_reporter._detect_container_env), not a plugin-generator-local stub.


def test_ladspa_sc4m_absent_skips_smart_volume_8ch():
    """When sc4m_1916.so is missing, smart volume node is omitted from 8ch config."""
    from arctis_sound_manager.eq_types import EqBand
    from arctis_sound_manager.sonar_to_pipewire import _active_conf_8ch

    bands = [("bq0", EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True))]
    smart_volume = {"enabled": True, "loudness": "balanced", "level": 50}

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=None):
        text = _active_conf_8ch(
            "game", "effect_input.sonar-game-eq", "effect_input.virtual-surround",
            "FL FR FC LFE RL RR SL SR", bands, [], [], 0.0, smart_volume,
        )

    # Smart volume LADSPA node must be absent
    assert "sc4m" not in text
    assert "compressor" not in text


def test_ladspa_sc4m_absent_skips_smart_volume_2ch():
    """When sc4m_1916.so is missing, smart volume nodes are omitted from 2ch config
    and the output port uses builtin 'Out' (not LADSPA 'Output')."""
    from arctis_sound_manager.eq_types import EqBand
    from arctis_sound_manager.sonar_to_pipewire import _active_conf_2ch

    bands = [("bq0", EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True))]
    smart_volume = {"enabled": True, "loudness": "balanced", "level": 50}

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=None):
        text = _active_conf_2ch(
            "chat", "effect_input.sonar-chat-eq", "alsa_output.test",
            "FL FR", bands, [], [], 0.0, smart_volume,
        )

    assert "sc4m" not in text
    assert "comp_L" not in text
    # Output port must use builtin "Out", not LADSPA "Output"
    assert ":Out\"" in text
    assert ":Output\"" not in text


def test_ladspa_gate_absent_skips_noise_gate():
    """When gate_1410.so is missing, noise gate node is omitted from micro config."""
    from arctis_sound_manager.sonar_to_pipewire import generate_sonar_micro_conf

    noise_reduction = {"noiseGate": {"enabled": True, "value": -40.0}}

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=None):
        text = generate_sonar_micro_conf(
            [], 0.0, 0.0, 0.0,
            output_path=Path("/dev/null"),
            noise_reduction=noise_reduction,
        )

    assert "gate_1410" not in text
    assert "ngate" not in text


def test_ladspa_rnnoise_absent_skips_noise_cancellation():
    """When librnnoise_ladspa.so is missing, rnnoise node is omitted."""
    from arctis_sound_manager.sonar_to_pipewire import generate_sonar_micro_conf

    noise_canceling = {"enabled": True, "value": 0.5}

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=None):
        text = generate_sonar_micro_conf(
            [], 0.0, 0.0, 0.0,
            output_path=Path("/dev/null"),
            noise_canceling=noise_canceling,
        )

    assert "librnnoise_ladspa" not in text
    assert "rnnoise" not in text


def test_ladspa_all_available_includes_nodes_with_absolute_path():
    """When all LADSPA plugins are available natively, smart volume and micro
    processing nodes ARE included in the generated configs, using the
    absolute path resolved by _find_ladspa_plugin (not the bare name) —
    issue #88-adjacent Fedora LADSPA_PATH fix, adapted from PR #104."""
    from arctis_sound_manager.eq_types import EqBand
    from arctis_sound_manager.sonar_to_pipewire import _active_conf_8ch

    bands = [("bq0", EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True))]
    smart_volume = {"enabled": True, "loudness": "balanced", "level": 50}

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value="/usr/lib64/ladspa/sc4m_1916.so"), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="native"):
        text = _active_conf_8ch(
            "game", "effect_input.sonar-game-eq", "effect_input.virtual-surround",
            "FL FR FC LFE RL RR SL SR", bands, [], [], 0.0, smart_volume,
        )

    assert "/usr/lib64/ladspa/sc4m_1916.so" in text
    assert "compressor" in text


def test_ladspa_ref_native_keeps_absolute_path():
    """Native (no container) — _ladspa_plugin_ref() always keeps the absolute
    path; there is no host/container filesystem mismatch to worry about."""
    from arctis_sound_manager.sonar_to_pipewire import _ladspa_plugin_ref

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value="/usr/lib64/ladspa/sc4m_1916.so"), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="native"):
        ref = _ladspa_plugin_ref("sc4m_1916.so")

    assert ref == "/usr/lib64/ladspa/sc4m_1916.so"


def test_ladspa_ref_container_system_path_falls_back_to_bare_name():
    """Distrobox/Flatpak + a system-wide plugin path (e.g. /usr/lib64/ladspa/)
    is NOT guaranteed to exist on the HOST, where filter-chain actually runs
    — _ladspa_plugin_ref() must fall back to the bare plugin name so the
    host's own filter-chain resolves it via its own search path (issue #88
    distrobox risk identified in PR #104 review)."""
    from arctis_sound_manager.sonar_to_pipewire import _ladspa_plugin_ref

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value="/usr/lib64/ladspa/sc4m_1916.so"), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="distrobox (container=distrobox, CONTAINER_ID=?)"):
        ref = _ladspa_plugin_ref("sc4m_1916.so")

    assert ref == "sc4m_1916"


def test_ladspa_ref_container_home_path_keeps_absolute():
    """Distrobox/Flatpak + a plugin under ~/.ladspa is safe to keep as an
    absolute path: HOME is bind-mounted into the container, so the host sees
    the exact same file at the exact same path."""
    from arctis_sound_manager.sonar_to_pipewire import _ladspa_plugin_ref

    home_plugin = str(Path.home() / ".ladspa" / "sc4m_1916.so")
    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=home_plugin), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="distrobox (container=distrobox, CONTAINER_ID=?)"):
        ref = _ladspa_plugin_ref("sc4m_1916.so")

    assert ref == home_plugin


# ── Phase 1 — stable graph across macro/boost/gain edits (issue #100/#88) ────
#
# generate_sonar_eq_conf() must emit the SAME set of node names, in the same
# order, for a given active band set — regardless of macro/boost values, and
# regardless of the exact Freq/Gain/Q of those bands. Only a real topology
# change (band added/removed/retyped, preset switch, …) may change the node
# names. This is what lets Phase 2's diff_filter_conf() distinguish a safe
# live-apply from a case that genuinely needs a filter-chain restart.

def _node_names(text: str) -> list[str]:
    import re
    return re.findall(r"\bname = (\S+)", text)


def test_stable_graph_across_macro_and_boost_values():
    """Same active bands, wildly different macro/boost values (crossing
    zero in both directions) -> identical node names/order."""
    bands = [
        EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True),
        EqBand(freq=1000, gain=-1.0, q=0.7, type="peakingEQ", enabled=True),
    ]
    text_a = generate_sonar_eq_conf("game", bands, basses_db=0.0, voix_db=0.0,
                                     aigus_db=0.0, output_path=Path("/dev/null"),
                                     boost_db=0.0)
    text_b = generate_sonar_eq_conf("game", bands, basses_db=4.0, voix_db=-2.0,
                                     aigus_db=1.5, output_path=Path("/dev/null"),
                                     boost_db=5.0)
    assert _node_names(text_a) == _node_names(text_b)


def test_stable_graph_across_macro_values_chat_2ch():
    """Same property holds for the 2ch (L/R) code path."""
    bands = [EqBand(freq=250, gain=1.0, q=0.7, type="peakingEQ", enabled=True)]
    text_a = generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    text_b = generate_sonar_eq_conf("chat", bands, 3.0, -3.0, 2.0,
                                     output_path=Path("/dev/null"), boost_db=6.0)
    assert _node_names(text_a) == _node_names(text_b)


def test_stable_graph_across_band_freq_gain_q_edits():
    """Editing Freq/Gain/Q of an already-active band (curve drag) without
    changing which bands are enabled must not change the node names."""
    bands_a = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    bands_b = [EqBand(freq=120, gain=5.0, q=1.2, type="peakingEQ", enabled=True)]
    text_a = generate_sonar_eq_conf("chat", bands_a, 1.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    text_b = generate_sonar_eq_conf("chat", bands_b, 1.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    assert _node_names(text_a) == _node_names(text_b)


def test_stable_graph_micro_across_macro_values():
    """Same stability property for the microphone EQ config."""
    bands = [EqBand(freq=300, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text_a = generate_sonar_micro_conf(bands, 0.0, 0.0, 0.0,
                                        output_path=Path("/dev/null"))
    text_b = generate_sonar_micro_conf(bands, 4.0, -1.0, 2.0,
                                        output_path=Path("/dev/null"), boost_db=3.0)
    assert _node_names(text_a) == _node_names(text_b)


# ── Phase 2 — diff_filter_conf (issue #100/#88) ───────────────────────────────

def test_diff_filter_conf_identical_text_returns_empty_dict():
    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 1.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert diff_filter_conf(text, text) == {}


def test_diff_filter_conf_detects_gain_only_change():
    """Only the basses macro changed -> diff reports exactly that node."""
    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    old_text = generate_sonar_eq_conf("game", bands, basses_db=0.0, voix_db=0.0,
                                       aigus_db=0.0, output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("game", bands, basses_db=3.0, voix_db=0.0,
                                       aigus_db=0.0, output_path=Path("/dev/null"))
    diff = diff_filter_conf(old_text, new_text)
    assert diff == {"macro_basses": {"Gain": 3.0}}


def test_diff_filter_conf_detects_band_freq_and_gain_change():
    """A curve-drag edit (Freq + Gain both changed on the same band) is
    reported per-field, and remains live-appliable (not None)."""
    bands_a = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    bands_b = [EqBand(freq=150, gain=5.0, q=0.7, type="peakingEQ", enabled=True)]
    old_text = generate_sonar_eq_conf("chat", bands_a, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("chat", bands_b, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    diff = diff_filter_conf(old_text, new_text)
    assert diff == {
        "bq0_L": {"Freq": 150.0, "Gain": 5.0},
        "bq0_R": {"Freq": 150.0, "Gain": 5.0},
    }


def test_diff_filter_conf_returns_none_on_band_count_change():
    """A band added/removed is a real topology change -> must restart."""
    band_one = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    band_two = band_one + [
        EqBand(freq=2000, gain=1.0, q=0.7, type="peakingEQ", enabled=True),
    ]
    old_text = generate_sonar_eq_conf("chat", band_one, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("chat", band_two, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    assert diff_filter_conf(old_text, new_text) is None


def test_diff_filter_conf_returns_none_on_band_type_change():
    """A band changing filter type (e.g. peakingEQ -> highPass) changes the
    node's label, not just its control literals -> must restart."""
    band_peaking = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    band_highpass = [EqBand(freq=100, gain=2.0, q=0.7, type="highPass", enabled=True)]
    old_text = generate_sonar_eq_conf("chat", band_peaking, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("chat", band_highpass, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    assert diff_filter_conf(old_text, new_text) is None


def test_diff_filter_conf_returns_none_on_flat_to_active_transition():
    """Going from the fully-flat bypass ("copy" node) to an active graph is
    a structural change -> must restart."""
    old_text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    new_text = generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    assert diff_filter_conf(old_text, new_text) is None

# ── Phase 3 — Spatial Audio toggle without a filter-chain restart (#100/#88) ──
#
# The Spatial Audio toggle no longer changes the game/media EQ's channel count
# or static target, so a toggle produces a byte-identical conf and needs no
# filter-chain restart. The live routing decision (HeSuVi vs. physical) is made
# by ensure_spatial_eq_links(), which moves ASM's own EQ→target link.

import arctis_sound_manager.sonar_to_pipewire as _s2p_p3  # noqa: E402


def test_game_eq_always_8ch_regardless_of_spatial():
    """Phase 3: game EQ is 8ch and targets HeSuVi whether spatial is on OR off
    (the toggle no longer changes channel count — that is what makes it
    restart-free)."""
    on = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                output_path=Path("/dev/null"), spatial_audio=True)
    off = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                 output_path=Path("/dev/null"), spatial_audio=False)
    for text in (on, off):
        assert "audio.channels = 8" in text
        assert 'node.target         = "effect_input.virtual-surround-7.1-hesuvi"' in text
    # The two are byte-identical → a toggle changes nothing on disk.
    assert on == off


def test_media_eq_always_8ch_regardless_of_spatial():
    """Same as game for the media channel (independent Spatial Audio toggle)."""
    on = generate_sonar_eq_conf("media", [], 0.0, 0.0, 0.0,
                                output_path=Path("/dev/null"), media_spatial_audio=True)
    off = generate_sonar_eq_conf("media", [], 0.0, 0.0, 0.0,
                                 output_path=Path("/dev/null"), media_spatial_audio=False)
    for text in (on, off):
        assert "audio.channels = 8" in text
        assert 'node.target         = "effect_input.virtual-surround-7.1-hesuvi"' in text
    assert on == off


def test_game_media_eq_own_their_output_link():
    """Phase 3: game/media EQ playback runs with node.autoconnect=false +
    state.restore-target=false so ASM owns the EQ→target link (issue #100
    pattern) and can move it live on a Spatial toggle. Chat (physical target,
    never toggled) does NOT get autoconnect=false."""
    game = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"))
    media = generate_sonar_eq_conf("media", [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"))
    for text in (game, media):
        assert "node.autoconnect     = false" in text
        assert "state.restore-target = false" in text
    chat = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"))
    assert "node.autoconnect     = false" not in chat


def test_active_game_eq_owns_link_with_bands():
    """The autoconnect=false hint is present on the active (non-bypass) 8ch
    path too, not only the bypass copy path."""
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0, output_path=Path("/dev/null"))
    assert "node.autoconnect     = false" in text
    assert "state.restore-target = false" in text


def test_spatial_toggle_produces_identical_conf():
    """The core Phase 3 property: flipping the spatial flag on the SAME EQ
    state yields a byte-identical conf, so _ApplyWorker's 'unchanged conf'
    guard skips the restart entirely."""
    bands = [EqBand(freq=250, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    a = generate_sonar_eq_conf("game", bands, 1.0, 0.0, 0.0,
                               output_path=Path("/dev/null"), spatial_audio=True)
    b = generate_sonar_eq_conf("game", bands, 1.0, 0.0, 0.0,
                               output_path=Path("/dev/null"), spatial_audio=False)
    assert a == b
    assert diff_filter_conf(a, b) == {}


# ── ensure_spatial_eq_links — live EQ→target reroute ──────────────────────────

def test_ensure_spatial_eq_links_targets_hesuvi_when_enabled(monkeypatch):
    """Spatial ON → EQ output is linked to the HeSuVi virtual-surround sink."""
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: True)
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    result = _s2p_p3.ensure_spatial_eq_links(("game",))
    assert result == {"game": True}
    assert calls == [("effect_output.sonar-game-eq",
                      "effect_input.virtual-surround-7.1-hesuvi")]


def test_ensure_spatial_eq_links_targets_physical_when_disabled(monkeypatch):
    """Spatial OFF → EQ output is linked (channel-matched, FL/FR only) to the
    physical output instead of HeSuVi. This is what a toggle-OFF does live,
    with no filter-chain restart."""
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: False)
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    result = _s2p_p3.ensure_spatial_eq_links(("game",))
    assert result == {"game": True}
    assert calls == [("effect_output.sonar-game-eq", "alsa_output.test-headset")]


def test_ensure_spatial_eq_links_moves_link_on_toggle(monkeypatch):
    """Toggling ON↔OFF moves the same EQ output link between HeSuVi and the
    physical output (mock pw-link layer)."""
    state = {"game": True}
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: state[ch])
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    targets = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: targets.append(target) or True,
    )
    _s2p_p3.ensure_spatial_eq_links(("game",))            # ON
    state["game"] = False
    _s2p_p3.ensure_spatial_eq_links(("game",))            # OFF
    state["game"] = True
    _s2p_p3.ensure_spatial_eq_links(("game",))            # ON again
    assert targets == [
        "effect_input.virtual-surround-7.1-hesuvi",
        "alsa_output.test-headset",
        "effect_input.virtual-surround-7.1-hesuvi",
    ]


def test_ensure_spatial_eq_links_no_target_when_no_device(monkeypatch):
    """Spatial OFF and no device attached → no physical target → reported as
    not-linked (retry later), and ensure_loopback_link is never called."""
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: False)
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    called = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda *a, **kw: called.append(a) or True,
    )
    result = _s2p_p3.ensure_spatial_eq_links(("game",))
    assert result == {"game": False}
    assert called == []


def test_ensure_spatial_eq_links_ignores_non_toggle_channels(monkeypatch):
    """chat/output are not spatial-toggled channels → silently ignored."""
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: True)
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda *a, **kw: True,
    )
    result = _s2p_p3.ensure_spatial_eq_links(("chat", "output"))
    assert result == {}


def test_spatial_enabled_defaults_to_true(monkeypatch, tmp_path):
    """Missing spatial-state file → treated as enabled (on-by-default)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _s2p_p3._spatial_enabled("game") is True
    assert _s2p_p3._spatial_enabled("media") is True


def test_spatial_enabled_reads_disabled_state(monkeypatch, tmp_path):
    """A saved {'enabled': false} is read back as disabled, for the right file
    per channel (game → sonar_spatial_audio.json, media → *_media.json)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = tmp_path / ".config" / "arctis_manager"
    cfg.mkdir(parents=True)
    (cfg / "sonar_spatial_audio.json").write_text('{"enabled": false}')
    (cfg / "sonar_spatial_audio_media.json").write_text('{"enabled": true}')
    assert _s2p_p3._spatial_enabled("game") is False
    assert _s2p_p3._spatial_enabled("media") is True


# ── ensure_micro_capture_link (issue #127) ────────────────────────────────────

def test_ensure_micro_capture_link_links_arctis_to_capture(monkeypatch):
    """When a device is attached, the capture link is established between the
    physical Arctis mic and the micro-EQ capture node."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_in", lambda: "alsa_input.test-mic")
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_capture_link",
        lambda source, capture, data=None: calls.append((source, capture, data)) or True,
    )
    result = _s2p_p3.ensure_micro_capture_link(data=["sentinel"])
    assert result is True
    assert calls == [("alsa_input.test-mic", "effect_input.sonar-micro-eq", ["sentinel"])]


def test_ensure_micro_capture_link_skips_when_no_device(monkeypatch):
    """No device attached (empty physical_in) → skip entirely, never call
    ensure_capture_link, retry on a later watchdog tick instead."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_in", lambda: "")
    called = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_capture_link",
        lambda *a, **kw: called.append(a) or True,
    )
    result = _s2p_p3.ensure_micro_capture_link()
    assert result is False
    assert called == []


# ── HeSuVi always present (Phase 3) ──────────────────────────────────────────

def test_check_and_fix_generates_hesuvi_even_when_spatial_disabled(tmp_path, monkeypatch):
    """Phase 3: HeSuVi is generated unconditionally so it is always ready for a
    live toggle-ON — even while Spatial Audio is currently DISABLED. Previously
    it was only written when spatial was enabled."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    (tmp_path / "pipewire.conf.d").mkdir()
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_device_attached", lambda: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    # Sonar mode active, spatial DISABLED for both channels.
    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)
    (home / ".config" / "arctis_manager" / ".eq_mode").write_text("sonar")
    (home / ".config" / "arctis_manager" / "sonar_spatial_audio.json").write_text('{"enabled": false}')
    monkeypatch.setattr(Path, "home", lambda: home)

    # Track whether HeSuVi was generated (device attached, so it writes).
    generated = {}
    real_gen = stp.generate_hesuvi_conf

    def _spy(*a, **kw):
        generated["called"] = True
        # Write a stub file so the "exists" branch is satisfied afterwards.
        (tmp_path / "sink-virtual-surround-7.1-hesuvi.conf").write_text("stub")
        return "stub"
    monkeypatch.setattr(stp, "generate_hesuvi_conf", _spy)

    stp.check_and_fix_stale_configs()
    assert generated.get("called"), "HeSuVi must be generated even when spatial is disabled"


def test_apply_hrir_choice_triggers_single_restart(monkeypatch):
    """Phase 4: an HRIR change is the ONE remaining case that legitimately
    restarts filter-chain (the convolver only reads the WAV at load). It must
    restart exactly once and then re-establish the ASM-owned EQ→target links."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    restart_calls = []
    monkeypatch.setattr(stp, "_restart_filter_chain",
                        lambda: restart_calls.append(1))
    link_calls = []
    monkeypatch.setattr(stp, "ensure_spatial_eq_links",
                        lambda *a, **kw: link_calls.append(a) or {})
    # hrir_id=None → skip the WAV copy, go straight to restart.
    stp.apply_hrir_choice(None)
    assert restart_calls == [1], "HRIR change must restart filter-chain exactly once"
    assert link_calls, "HRIR change must re-establish the EQ→target links after restart"
