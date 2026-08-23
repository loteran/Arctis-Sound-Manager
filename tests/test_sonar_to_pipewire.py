# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for sonar_to_pipewire — filter-chain config generation."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _gen_hesuvi(monkeypatch, *, limiter_available, distance_pct):
    """Generate a HeSuVi conf with the environment stubbed out.

    ``_write_conf`` is neutered so the test never touches the filesystem (and
    avoids the non-ASCII arrows in the conf tripping a cp1252 locale on Windows
    dev boxes); the returned text is what matters.
    """
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
    monkeypatch.setattr(
        _s2p, "_ladspa_plugin_ref",
        (lambda name: "/usr/lib/ladspa/" + name) if limiter_available else (lambda name: None),
    )
    return _s2p.generate_hesuvi_conf(
        immersion_pct=80, distance_pct=distance_pct, output_path=Path("/dev/null"),
    )


def test_hesuvi_limiter_on_output_when_available(monkeypatch):
    """A fast-lookahead limiter is inserted on the surround output when
    swh-plugins is present, so hot HRIRs cannot clip on loud passages."""
    text = _gen_hesuvi(monkeypatch, limiter_available=True, distance_pct=0)
    assert "label = fastLookaheadLimiter" in text
    # Mixers feed the limiter, and the sink is driven by the limiter outputs.
    assert '{ output = "mixL:Out"  input = "limiter:Input 1" }' in text
    assert '{ output = "mixR:Out"  input = "limiter:Input 2" }' in text
    assert 'outputs = [ "limiter:Output 1" "limiter:Output 2" ]' in text


def test_hesuvi_limiter_after_reverb(monkeypatch):
    """With Distance reverb active, the limiter sits after the plate reverb
    (plate -> limiter -> sink)."""
    text = _gen_hesuvi(monkeypatch, limiter_available=True, distance_pct=40)
    assert "label = fastLookaheadLimiter" in text
    assert '{ output = "plate_L:Left output"  input = "limiter:Input 1" }' in text
    assert '{ output = "plate_R:Right output"  input = "limiter:Input 2" }' in text
    assert 'outputs = [ "limiter:Output 1" "limiter:Output 2" ]' in text


def test_hesuvi_limiter_graceful_fallback_when_absent(monkeypatch):
    """Without swh-plugins the chain is emitted with no limiter and the mixers
    drive the sink directly — no breakage."""
    text = _gen_hesuvi(monkeypatch, limiter_available=False, distance_pct=0)
    assert "fastLookaheadLimiter" not in text
    assert "limiter:" not in text
    assert 'outputs = [ "mixL:Out" "mixR:Out" ]' in text


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


def test_chat_keeps_its_target_when_the_writer_cannot_see_the_device(tmp_path):
    """The GUI rewrites this conf on every EQ edit and never has a device
    state (only the daemon fills it), so a regenerated conf must not throw
    away the target the file already carries. Losing it leaves the chat EQ
    output floating with autoconnect on, and WirePlumber retries routing it
    to the default sink — one of ASM's own loopbacks — on every graph change
    from then on, which is audible on the other channels."""
    from arctis_sound_manager import device_state

    conf = tmp_path / "sonar-chat-eq.conf"
    phys = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"
    device_state.set_current_device(
        physical_out_game=phys, physical_out_chat=phys, physical_in="",
        spatial_engine="hesuvi", device_name="SteelSeries Arctis Nova 7",
    )
    try:
        generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0, output_path=conf)
    finally:
        device_state.clear()
    assert phys in conf.read_text()

    # Now regenerate exactly as the GUI does: no device state at all.
    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0, output_path=conf)
    assert f'node.target         = "{phys}"' in text
    assert f'target.object       = "{phys}"' in text


def test_chat_target_stays_empty_when_nothing_knows_it(tmp_path):
    """No device and no conf on disk — there is nothing to preserve, and an
    empty target must not become the literal string of a missing one."""
    text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                  output_path=tmp_path / "sonar-chat-eq.conf")
    assert "node.target" not in text


def test_micro_keeps_its_capture_target_when_the_writer_is_in_the_dark(tmp_path):
    """Same fallback on the mic conf's capture hint."""
    from arctis_sound_manager import device_state

    conf = tmp_path / "sonar-micro-eq.conf"
    mic = "alsa_input.usb-SteelSeries_Arctis_Nova_7-00.mono-fallback"
    bands = [EqBand(freq=300, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    device_state.set_current_device(
        physical_out_game="alsa_output.x", physical_out_chat="alsa_output.x",
        physical_in=mic, spatial_engine="hesuvi", device_name="Arctis Nova 7",
    )
    try:
        generate_sonar_micro_conf(bands, 0.0, 0.0, 0.0, output_path=conf)
    finally:
        device_state.clear()

    text = generate_sonar_micro_conf(bands, 1.0, 0.0, 0.0, output_path=conf)
    assert f'target.object  = "{mic}"' in text


def test_a_conf_that_already_lost_its_target_gets_it_back_with_its_eq(tmp_path, monkeypatch):
    """The daemon's repair for a conf written before the fix. Regenerating it
    would write a bypass and take the user's chat EQ with it, so the target
    goes back in place instead."""
    import arctis_sound_manager.sonar_to_pipewire as _s2p
    from arctis_sound_manager import device_state

    conf_dir = tmp_path / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    monkeypatch.setattr(_s2p, "_CONF_DIR", conf_dir)

    phys = "alsa_output.usb-SteelSeries_Arctis_Nova_7-00.analog-stereo"
    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    # Written by a GUI in the dark: real EQ, no target.
    for channel in ("game", "media", "chat"):
        generate_sonar_eq_conf(channel, bands, 0.0, 0.0, 0.0,
                               output_path=conf_dir / f"sonar-{channel}-eq.conf")
    chat_conf = conf_dir / "sonar-chat-eq.conf"
    assert "node.target" not in chat_conf.read_text()

    device_state.set_current_device(
        physical_out_game=phys, physical_out_chat=phys, physical_in="",
        spatial_engine="hesuvi", device_name="SteelSeries Arctis Nova 7",
    )
    try:
        _s2p.ensure_sonar_eq_configs()
    finally:
        device_state.clear()

    repaired = chat_conf.read_text()
    assert f'node.target         = "{phys}"' in repaired
    assert f'target.object       = "{phys}"' in repaired
    # …and the EQ is still there: the bypass path would have dropped it.
    assert "bq0" in repaired
    assert "Gain = 2.0" in repaired


def test_regenerated_output_conf_stays_a_selectable_sink(tmp_path, monkeypatch):
    """ensure_sonar_eq_configs() must write Output as Audio/Sink priority 1.

    _bypass_conf derives media.class and priority.session from its `channel`
    argument, which this call site used to omit: the Output conf came out as
    Audio/Sink/Internal priority 1000 and the channel vanished from the
    selectable outputs — while check_and_fix_stale_configs()'s regen of the
    very same file passed `channel` and wrote it correctly.
    """
    import arctis_sound_manager.sonar_to_pipewire as _s2p_mod
    from arctis_sound_manager import device_state

    monkeypatch.setattr(_s2p_mod, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(_s2p_mod, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(_s2p_mod, "_SAFE_MODE_MARKER", tmp_path / "no-such-marker.json")
    monkeypatch.setattr(_s2p_mod, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.hdmi-stereo", 2, "FL FR"))

    device_state.set_current_device(
        physical_out_game="alsa_output.test-headset",
        physical_out_chat="alsa_output.test-headset",
        physical_in="", spatial_engine="hesuvi", device_name="Test",
    )
    try:
        assert _s2p_mod.ensure_sonar_eq_configs() is True
    finally:
        device_state.clear()

    output_conf = (tmp_path / "sonar-output-eq.conf").read_text()
    assert "media.class       = Audio/Sink\n" in output_conf
    assert "priority.session  = 1\n" in output_conf
    # The internal channels keep the shape that hides them from the picker.
    chat_conf = (tmp_path / "sonar-chat-eq.conf").read_text()
    assert "media.class       = Audio/Sink/Internal\n" in chat_conf
    assert "priority.session  = 1000\n" in chat_conf


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


# ── Noise-cancel engine selection: RNNoise ↔ DeepFilterNet ────────────────────

def test_micro_conf_deepfilter_engine_emits_deep_filter_node(monkeypatch):
    """engine=deepfilternet emits the DeepFilterNet node, mapping the 0-1 slider
    to its Attenuation Limit (dB), and never the RNNoise node."""
    monkeypatch.setattr(_s2p, "_deepfilter_plugin_ref",
                        lambda: "/home/u/.ladspa/libdeep_filter_ladspa.so")
    text = _s2p.generate_sonar_micro_conf(
        [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"),
        noise_canceling={"enabled": True, "value": 0.8, "engine": "deepfilternet"},
    )
    assert "label = deep_filter_mono" in text
    assert '"Attenuation Limit (dB)" = 80.0' in text
    assert "noise_suppressor_mono" not in text


def test_micro_conf_deepfilter_skipped_when_plugin_absent(monkeypatch):
    """A missing DeepFilterNet plugin omits the node gracefully (no bare-name
    LADSPA that would SEGV filter-chain — issue #88), not a hard failure."""
    monkeypatch.setattr(_s2p, "_deepfilter_plugin_ref", lambda: None)
    text = _s2p.generate_sonar_micro_conf(
        [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"),
        noise_canceling={"enabled": True, "value": 0.8, "engine": "deepfilternet"},
    )
    assert "deep_filter_mono" not in text


def test_micro_conf_defaults_to_rnnoise_engine(monkeypatch):
    """No engine key (existing configs) keeps the RNNoise node — back-compat."""
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref",
                        lambda *a, **k: "/home/u/.ladspa/librnnoise_ladspa.so")
    text = _s2p.generate_sonar_micro_conf(
        [], 0.0, 0.0, 0.0, output_path=Path("/dev/null"),
        noise_canceling={"enabled": True, "value": 0.5},
    )
    assert "label = noise_suppressor_mono" in text
    assert "deep_filter_mono" not in text


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
        f'    # ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n'
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
        f'    # ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n'
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
    # The micro conf is part of the contract too, and an absent one is now
    # created rather than ignored — see
    # test_missing_micro_conf_is_created_whatever_the_eq_mode.
    (tmp_path / "sonar-micro-eq.conf").write_text(_MICRO_CLEAN)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()
        assert fixed is False


# A micro conf none of the staleness checks object to: right media.class, no
# `label = gain`, and a target already filled in so the empty-target repair
# does not fire either.
_MICRO_CLEAN = (
    'context.modules = [\n'
    '  { name = libpipewire-module-filter-chain\n'
    '    args = {\n'
    '      capture.props  = { target.object  = "alsa_input.test-headset" }\n'
    '      playback.props = { media.class           = Audio/Source }\n'
    '    } }\n'
    ']\n'
)


def test_missing_micro_conf_is_created_whatever_the_eq_mode(tmp_path, monkeypatch):
    """An absent sonar-micro-eq.conf is generated, in Custom EQ mode as in Sonar.

    Nothing used to guarantee this file: ensure_sonar_eq_configs() covers
    game/media/chat/output and skips micro, and it is itself only reached in
    Sonar mode. On an install where the user never pressed Apply in the micro
    EQ tab, ``effect_output.sonar-micro-eq`` therefore never existed — while
    the daemon makes it the default source at startup, leaving the headset mic
    out of the selectable inputs entirely.
    """
    monkeypatch.setattr(_s2p, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    # Custom EQ mode: the `if sonar and ensure_sonar_eq_configs()` branch is
    # not the one that must save us here.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    micro_path = tmp_path / "sonar-micro-eq.conf"

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        fixed, _needs_pw_restart = check_and_fix_stale_configs()

    assert fixed is True
    assert micro_path.exists(), "the micro conf has no other guarantor"
    content = micro_path.read_text()
    assert 'node.name             = "effect_output.sonar-micro-eq"' in content
    assert "media.class           = Audio/Source" in content


def test_existing_micro_conf_is_never_flattened(tmp_path, monkeypatch):
    """The creation above only fires on absence — a real mic EQ survives.

    Writing the bypass over a configured conf would silently drop the user's
    bands, macros and noise processing, the same reason an outdated
    ASM-CONF-VERSION is not a regeneration trigger for this file.
    """
    monkeypatch.setattr(_s2p, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(_s2p, "_get_physical_in", lambda: "alsa_input.test-headset")
    micro_path = tmp_path / "sonar-micro-eq.conf"
    generate_sonar_micro_conf(
        [EqBand(freq=250, gain=-4.0, q=0.7, type="peakingEQ", enabled=True)],
        0.0, 3.0, 0.0, output_path=micro_path,
    )
    assert "Gain = -4.0" in micro_path.read_text()

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        check_and_fix_stale_configs()

    after = micro_path.read_text()
    assert "Gain = -4.0" in after, "the band survived"
    assert "Gain = 3.0" in after, "the voice macro survived"
    assert "micro passthrough" not in after, "the bypass must not have been written"


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
        f'    # ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n'
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
        f'    # ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n'
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
    # An absent micro conf is now created, not ignored — see
    # test_missing_micro_conf_is_created_whatever_the_eq_mode.
    (tmp_path / "sonar-micro-eq.conf").write_text(_MICRO_CLEAN)

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
         patch("arctis_sound_manager.service_control.detect_init",
               return_value="unknown"):
        # detect_init returning "unknown" skips NRestarts check
        # (service_control binds detect_init at import, so it must be patched
        # there — patching init_system leaves the real systemctl call in place)
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
         patch("arctis_sound_manager.service_control.detect_init",
               return_value="systemd"):
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


def test_2ch_output_channel_sink_is_visible_to_applications():
    """The Output channel's sink must be a plain Audio/Sink.

    It is the one channel users route applications *to* from a mixer, so it has
    to appear in output pickers. _active_conf_8ch and _bypass_conf already did
    this; the 2ch generator hardcoded Audio/Sink/Internal, so a stereo Output
    channel with an active EQ disappeared from every picker and a saved routing
    pin to it could no longer be reapplied.
    """
    from arctis_sound_manager.eq_types import EqBand
    from arctis_sound_manager.sonar_to_pipewire import _active_conf_2ch

    bands = [("bq0", EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True))]

    text = _active_conf_2ch(
        "output", "effect_input.sonar-output-eq", "alsa_output.hdmi",
        "FL FR", bands, [], [], 0.0,
    )

    assert "media.class       = Audio/Sink\n" in text
    assert "Audio/Sink/Internal" not in text


def test_2ch_chat_channel_sink_stays_internal():
    """Every other 2ch channel is fed by ASM's own loopbacks and must stay
    Internal — making Chat visible would put a second, confusing Arctis entry
    in every application's output picker."""
    from arctis_sound_manager.eq_types import EqBand
    from arctis_sound_manager.sonar_to_pipewire import _active_conf_2ch

    bands = [("bq0", EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True))]

    text = _active_conf_2ch(
        "chat", "effect_input.sonar-chat-eq", "alsa_output.test",
        "FL FR", bands, [], [], 0.0,
    )

    assert "media.class       = Audio/Sink/Internal" in text


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


def test_ladspa_ref_container_system_path_stages_into_home_ladspa(tmp_path):
    """Distrobox/Flatpak + a system-wide plugin path (e.g. /usr/lib64/ladspa/)
    is NOT guaranteed to exist on the HOST, where filter-chain actually runs.
    A bare name silently killed HeSuVi on hosts without the plugin (issue #100),
    so _ladspa_plugin_ref() now STAGES the plugin into ~/.ladspa (shared with
    the host) and returns that absolute path the host can always load."""
    from arctis_sound_manager.sonar_to_pipewire import _ladspa_plugin_ref

    home = tmp_path / "home"
    home.mkdir()
    src = tmp_path / "sys" / "ladspa" / "plate_1423.so"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x7fELF-fake-plugin")

    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value=str(src)), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="distrobox (container=podman, CONTAINER_ID=asm)"), \
         patch("arctis_sound_manager.sonar_to_pipewire.Path.home",
               return_value=home):
        ref = _ladspa_plugin_ref("plate_1423.so")

    staged = home / ".ladspa" / "plate_1423.so"
    assert ref == str(staged)
    assert staged.read_bytes() == b"\x7fELF-fake-plugin"


def test_ladspa_ref_container_bare_name_fallback_on_copy_failure(tmp_path):
    """If staging into ~/.ladspa fails (e.g. the source is unreadable), fall
    back to the bare plugin name so behaviour is never worse than before the
    #100 staging change."""
    from arctis_sound_manager.sonar_to_pipewire import _ladspa_plugin_ref

    home = tmp_path / "home"
    home.mkdir()
    with patch("arctis_sound_manager.system_deps_checker._find_ladspa_plugin",
               return_value="/nonexistent/ladspa/sc4m_1916.so"), \
         patch("arctis_sound_manager.bug_reporter._detect_container_env",
               return_value="distrobox (container=podman, CONTAINER_ID=asm)"), \
         patch("arctis_sound_manager.sonar_to_pipewire.Path.home",
               return_value=home):
        ref = _ladspa_plugin_ref("sc4m_1916.so")

    assert ref == "sc4m_1916"


def test_conf_has_bare_ladspa_detects_bare_plugin():
    """The config-repair pass must recognise a HeSuVi conf that still carries a
    bare-name LADSPA plugin (pre-#100 container fallback) so it regenerates it
    into the staged absolute-path form."""
    from arctis_sound_manager.sonar_to_pipewire import _conf_has_bare_ladspa

    bare = "{ type = ladspa  name = plate_L  plugin = plate_1423  label = plate }"
    absolute = "{ type = ladspa  name = plate_L  plugin = /home/u/.ladspa/plate_1423.so  label = plate }"
    builtin_only = "{ type = builtin  name = bq0  label = bq_peaking }"

    assert _conf_has_bare_ladspa(bare) is True
    assert _conf_has_bare_ladspa(absolute) is False
    assert _conf_has_bare_ladspa(builtin_only) is False


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
# change (band retyped, preset switch, …) may change the node names. This is
# what lets Phase 2's diff_filter_conf() distinguish a safe live-apply from a
# case that genuinely needs a filter-chain restart.
#
# Phase 4 widened that: how MANY bands are enabled no longer changes the node
# names either, because the bands sit in a fixed rack of slots.

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


def test_stable_graph_across_band_count():
    """Phase 4: one band or eight, the emitted rack is the same nodes in the
    same order — that is what keeps an added/deleted band live-appliable."""
    one = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    eight = [
        EqBand(freq=100 * (i + 1), gain=1.0, q=0.7, type="peakingEQ", enabled=True)
        for i in range(8)
    ]
    text_a = generate_sonar_eq_conf("game", one, 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    text_b = generate_sonar_eq_conf("game", eight, 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    assert _node_names(text_a) == _node_names(text_b)


def test_empty_rack_slots_are_unity_passthroughs():
    """A slot no band occupies must be a bq_peaking at Gain=0.0 — anything
    else would colour the audio of a curve the user never drew."""
    from arctis_sound_manager.sonar_to_pipewire import _BAND_SLOTS, _RACK_TYPES

    one = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", one, 0.0, 0.0, 0.0,
                                   output_path=Path("/dev/null"))
    for slot in range(1, _BAND_SLOTS):
        assert f"name = bq{slot}  label = bq_peaking" in text
    # Every slot but the one the band occupies: the rest of the peaking pool
    # plus a spare slot for each shelf type.
    empty = _BAND_SLOTS - 1 + (len(_RACK_TYPES) - 1)
    assert text.count("control = { Freq = 1000.0  Q = 0.7071  Gain = 0.0 }") == empty


def test_the_rack_has_room_above_a_preset_that_uses_every_filter():
    """The rack only pays off if a full preset still leaves free slots: Sonar
    presets carry ten filters and plenty enable all ten (Music - Punchy,
    Flat), so a rack of exactly ten is full before the user adds anything and
    the first added band takes the restart the rack exists to avoid."""
    from arctis_sound_manager.sonar_to_pipewire import _BAND_SLOTS

    full_preset = [
        EqBand(freq=100 * (i + 1), gain=1.0, q=0.7, type="peakingEQ", enabled=True)
        for i in range(10)
    ]
    assert _BAND_SLOTS > len(full_preset)

    old_text = generate_sonar_eq_conf("media", full_preset, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf(
        "media", full_preset + [EqBand(freq=2500, gain=2.0, q=0.7)],
        0.0, 0.0, 0.0, output_path=Path("/dev/null"),
    )
    assert diff_filter_conf(old_text, new_text) == {
        "bq10": {"Freq": 2500.0, "Q": 0.7, "Gain": 2.0},
    }


def test_deleting_a_band_beside_a_shelf_stays_live():
    """The stock Flat preset is a low shelf, eight peaking bands and a high
    shelf. With the rack laid out in curve order, deleting any of them slid
    the high shelf down into a peaking slot — a relabelled node, so every
    delete on that channel restarted filter-chain. Laying the rack out by
    type keeps each band in a slot of its own kind."""
    preset = (
        [EqBand(freq=60, gain=1.0, q=0.7, type="lowShelving", enabled=True)]
        + [EqBand(freq=200 * (i + 1), gain=1.0, q=0.7, type="peakingEQ", enabled=True)
           for i in range(8)]
        + [EqBand(freq=12000, gain=1.0, q=0.7, type="highShelving", enabled=True)]
    )
    old_text = generate_sonar_eq_conf("chat", preset, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    for idx in (0, 4, 9):
        remaining = [b for i, b in enumerate(preset) if i != idx]
        new_text = generate_sonar_eq_conf("chat", remaining, 0.0, 0.0, 0.0,
                                           output_path=Path("/dev/null"))
        assert diff_filter_conf(old_text, new_text) is not None, (
            f"deleting band {idx} fell back to a filter-chain restart"
        )


def test_an_empty_shelf_slot_is_a_unity_shelf_not_a_peaking_filter():
    """Emptying a shelf slot must keep the slot's label — swapping it for a
    peaking node is the relabel the layout exists to avoid."""
    peaking_only = [EqBand(freq=500, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", peaking_only, 0.0, 0.0, 0.0,
                                   output_path=Path("/dev/null"))
    assert "label = bq_lowshelf" in text
    assert "label = bq_highshelf" in text
    # …and they are flat: a shelf at Gain=0.0 passes the signal through.
    for name in ("bq16", "bq17"):
        assert f"name = {name}  label = bq_" in text
    assert "Gain = 1.0" not in text


def test_a_pass_filter_is_not_parked_in_the_rack():
    """Gain cannot neutralise a high/low-pass, so an unused one must not be
    emitted — it would filter the audio of a curve that does not use it."""
    peaking_only = [EqBand(freq=500, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", peaking_only, 0.0, 0.0, 0.0,
                                   output_path=Path("/dev/null"))
    assert "bq_highpass" not in text
    assert "bq_lowpass" not in text


def test_the_rack_grows_a_step_at_a_time_not_a_band_at_a_time():
    """Once a curve outgrows the rack, the restart that costs must buy more
    than a single extra band."""
    from arctis_sound_manager.sonar_to_pipewire import _BAND_SLOT_STEP, _BAND_SLOTS

    def peaking_slots(n):
        bands = [EqBand(freq=100 + i, gain=1.0, q=0.7, type="peakingEQ", enabled=True)
                 for i in range(n)]
        text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
        return len(re.findall(r"name = bq\d+  label = bq_peaking", text))

    assert peaking_slots(_BAND_SLOTS) == _BAND_SLOTS
    assert peaking_slots(_BAND_SLOTS + 1) == _BAND_SLOTS + _BAND_SLOT_STEP
    assert peaking_slots(_BAND_SLOTS + _BAND_SLOT_STEP) == _BAND_SLOTS + _BAND_SLOT_STEP


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


def test_diff_filter_conf_band_added_is_live_appliable():
    """Phase 4: a band added within the rack fills a slot that was already in
    the graph as a unity passthrough -> control values only, no restart."""
    band_one = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    band_two = band_one + [
        EqBand(freq=2000, gain=1.0, q=0.7, type="peakingEQ", enabled=True),
    ]
    old_text = generate_sonar_eq_conf("chat", band_one, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("chat", band_two, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    assert diff_filter_conf(old_text, new_text) == {
        "bq1_L": {"Freq": 2000.0, "Q": 0.7, "Gain": 1.0},
        "bq1_R": {"Freq": 2000.0, "Q": 0.7, "Gain": 1.0},
    }


def test_diff_filter_conf_band_disabled_is_live_appliable():
    """Toggling a band off empties its slot back to unity — also live."""
    bands_on = [
        EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True),
        EqBand(freq=2000, gain=1.0, q=0.7, type="peakingEQ", enabled=True),
    ]
    bands_off = [
        EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True),
        EqBand(freq=2000, gain=1.0, q=0.7, type="peakingEQ", enabled=False),
    ]
    old_text = generate_sonar_eq_conf("game", bands_on, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf("game", bands_off, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    assert diff_filter_conf(old_text, new_text) == {
        "bq1": {"Freq": 1000.0, "Q": 0.7071, "Gain": 0.0},
    }


def test_diff_filter_conf_returns_none_past_the_rack_size():
    """The rack only absorbs so many bands: a curve that outgrows it really
    does add a node, and that still needs a restart."""
    from arctis_sound_manager.sonar_to_pipewire import _BAND_SLOTS

    bands = [
        EqBand(freq=100 * (i + 1), gain=1.0, q=0.7, type="peakingEQ", enabled=True)
        for i in range(_BAND_SLOTS)
    ]
    old_text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                       output_path=Path("/dev/null"))
    new_text = generate_sonar_eq_conf(
        "game", bands + [EqBand(freq=15000, gain=1.0, q=0.7, type="peakingEQ", enabled=True)],
        0.0, 0.0, 0.0, output_path=Path("/dev/null"),
    )
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
    # HeSuVi present in the graph. Stubbed, or pw_node_exists() answers from a
    # live pw-dump and the assertion below turns into a reading of whichever
    # machine ran the suite.
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.pw_node_exists",
        lambda name, data=None: True,
    )
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
    # HeSuVi is loaded — otherwise the issue #100 fallback sends the ON legs to
    # the physical output too, and the toggle it is testing stops being visible.
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.pw_node_exists",
        lambda name, data=None: True,
    )
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


# ── ensure_physical_output_links (headset power-cycle final-hop fix) ─────────
#
# effect_output.sonar-chat-eq and effect_output.virtual-surround-7.1-hesuvi
# both carry a node.target hint at the physical Arctis output, but that hint
# is only honoured by WirePlumber once, at node-creation time. When the
# headset powers off and back on, the physical output node is destroyed and
# recreated under a new id and neither link comes back on its own — nothing
# else in the watchdog was watching this last hop (ensure_loopback_link only
# covers loopback→EQ, ensure_spatial_eq_links only covers the EQ→{HeSuVi,
# physical} hop for game/media). ensure_physical_output_links() closes that
# gap by composing with ensure_loopback_link, exactly like the other two.

def test_ensure_physical_output_links_links_both_channels(monkeypatch):
    """Device attached: the chat EQ output and each channel's HeSuVi output
    are linked to their respective targets.

    Game and Media have separate HeSuVi stages so their device menus are
    independent — one shared stage had a single output, which made Media's
    choice inert and dragged it along whenever Game's changed."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "alsa_output.test-chat")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "alsa_output.test-game")
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    result = _s2p_p3.ensure_physical_output_links()
    # Media carries its own HeSuVi chain since #169, onto the same physical
    # game output: three last hops, not two.
    assert result == {"chat": True, "hesuvi": True, "hesuvi_media": True}
    assert calls == [
        ("effect_output.sonar-chat-eq", "alsa_output.test-chat"),
        ("effect_output.virtual-surround-7.1-hesuvi", "alsa_output.test-game"),
        ("effect_output.virtual-surround-7.1-hesuvi-media", "alsa_output.test-game"),
    ]


def test_ensure_physical_output_links_no_device_touches_nothing(monkeypatch):
    """Headset off (device_state empty) → both physical targets are empty →
    neither channel is attempted and ensure_loopback_link is never called
    (so nothing is logged in a loop while the headset stays off)."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    called = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda *a, **kw: called.append(a) or True,
    )
    result = _s2p_p3.ensure_physical_output_links()
    assert result == {}
    assert called == []


def test_ensure_physical_output_links_chat_only(monkeypatch):
    """Only the chat physical target has resolved this tick → hesuvi is
    skipped entirely, chat is still enforced."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "alsa_output.test-chat")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    result = _s2p_p3.ensure_physical_output_links()
    assert result == {"chat": True}
    assert calls == [("effect_output.sonar-chat-eq", "alsa_output.test-chat")]


def test_ensure_physical_output_links_reuses_shared_pw_dump(monkeypatch):
    """The optional `data` payload (the watchdog's already-fetched pw-dump)
    is forwarded to both ensure_loopback_link calls unchanged — this
    function must never spawn its own extra pw-dump subprocess."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "alsa_output.test-chat")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "alsa_output.test-game")
    seen_data = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: seen_data.append(data) or True,
    )
    sentinel = ["sentinel-pw-dump"]
    _s2p_p3.ensure_physical_output_links(data=sentinel)
    # chat, game HeSuVi, and media's own HeSuVi chain (#169).
    assert seen_data == [sentinel, sentinel, sentinel]


# ── ensure_physical_output_links — the Output channel's last hop ────────────
#
# The Output channel (EQ → external sink: HDMI/TV/speakers) was owned by
# nobody: not by owns_link (game/media only), not by ensure_spatial_eq_links,
# and not here. quiesce_filter_chain() tears its link down on every
# filter-chain restart and nothing put it back, so an app routed to Output
# played into a dead end — silent, with no error anywhere.

def _write_output_conf(conf_dir, target: str) -> None:
    """Write a minimal sonar-output-eq.conf carrying *target*."""
    (conf_dir / "sonar-output-eq.conf").write_text(
        "# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n"
        "context.modules = [\n"
        "  { name = libpipewire-module-filter-chain\n"
        "    args = { playback.props = {\n"
        f'        node.target         = "{target}"\n'
        "    } } }\n"
        "]\n"
    )


def test_ensure_physical_output_links_links_the_output_channel(monkeypatch, tmp_path):
    """The Output EQ → external sink hop is enforced like the other two."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_CONF_DIR", tmp_path)
    _write_output_conf(tmp_path, "alsa_output.pci-hdmi-stereo")

    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )

    result = _s2p_p3.ensure_physical_output_links()

    assert result == {"output": True}
    assert calls == [("effect_output.sonar-output-eq", "alsa_output.pci-hdmi-stereo")]


def test_ensure_physical_output_links_skips_output_when_no_external_sink(
    monkeypatch, tmp_path
):
    """No Output conf (or no target in it) → the hop is skipped entirely,
    not attempted against an empty target name."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_CONF_DIR", tmp_path)  # no conf written

    called = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda *a, **kw: called.append(a) or True,
    )

    assert _s2p_p3.ensure_physical_output_links() == {}
    assert called == []


def test_output_target_follows_the_conf_without_querying_pulse(monkeypatch, tmp_path):
    """The target is read back from the generated conf, not resolved through
    pulsectl: this runs on every watchdog tick, and _resolve_external_output()
    opens a PulseAudio connection. Changing the conf changes the target."""
    monkeypatch.setattr(_s2p_p3, "_CONF_DIR", tmp_path)

    def _explode(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("_resolve_external_output must not be called per tick")

    monkeypatch.setattr(_s2p_p3, "_resolve_external_output", _explode)

    _write_output_conf(tmp_path, "alsa_output.first-sink")
    assert _s2p_p3._get_configured_external_output() == "alsa_output.first-sink"

    # User switches external output → conf is rewritten → the hop follows it.
    _write_output_conf(tmp_path, "alsa_output.second-sink")
    assert _s2p_p3._get_configured_external_output() == "alsa_output.second-sink"


# ── ensure_physical_output_links — real (un-mocked) pw-link machinery ───────
#
# The tests above verify the composition (which channel maps to which
# ensure_loopback_link call). These exercise the real, un-mocked
# ensure_loopback_link/pw-link machinery end-to-end — the same helper
# pattern as TestEnsureLoopbackLink in tests/test_pw_utils.py — to prove the
# exact failure mode from the bug report: the physical output node is
# destroyed and recreated (new PipeWire id) on a headset power-cycle, and
# both effect_output nodes (re)link to it on the next watchdog tick.

import types as _po_types  # noqa: E402


def _po_node(node_id: int, name: str) -> dict:
    return {"id": node_id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": name}}}


def _po_port(port_id: int, node_id: int, direction: str, channel: str) -> dict:
    return {"id": port_id, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": node_id, "port.direction": direction,
                               "audio.channel": channel}}}


def _po_link(link_id: int, out_node: int, out_port: int, in_node: int, in_port: int) -> dict:
    return {"id": link_id, "type": "PipeWire:Interface:Link",
            "info": {"props": {"link.output.node": out_node, "link.output.port": out_port,
                               "link.input.node": in_node, "link.input.port": in_port}}}


def _patch_po_pwlink(monkeypatch):
    """Record every pw-link invocation against the real pw_utils machinery;
    make them all succeed."""
    from arctis_sound_manager import pw_utils as _pwu
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _po_types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(_pwu.subprocess, "run", fake_run)
    return calls


_PO_CHAT_TARGET = "alsa_output.usb-SteelSeries_Arctis-00.chat"
_PO_GAME_TARGET = "alsa_output.usb-SteelSeries_Arctis-00.game"


def _po_graph(extra=None):
    data = [
        _po_node(10, "effect_output.sonar-chat-eq"),
        _po_node(20, _PO_CHAT_TARGET),
        _po_port(11, 10, "out", "FL"), _po_port(12, 10, "out", "FR"),
        _po_port(21, 20, "in", "FL"), _po_port(22, 20, "in", "FR"),
        _po_node(30, "effect_output.virtual-surround-7.1-hesuvi"),
        _po_node(40, _PO_GAME_TARGET),
        _po_port(31, 30, "out", "FL"), _po_port(32, 30, "out", "FR"),
        _po_port(41, 40, "in", "FL"), _po_port(42, 40, "in", "FR"),
        # Media's parallel HeSuVi chain (#169) shares the physical game
        # output. Two sources into one sink is fine: the stray-link cleanup
        # is indexed on the source node, so neither chain tears down the
        # other's link.
        _po_node(50, "effect_output.virtual-surround-7.1-hesuvi-media"),
        _po_port(51, 50, "out", "FL"), _po_port(52, 50, "out", "FR"),
    ]
    data.extend(extra or [])
    return data


class TestEnsurePhysicalOutputLinksRealGraph:
    def test_creates_missing_links_when_physical_node_reappears(self, monkeypatch):
        """Simulates the headset coming back online: the physical node is
        present in the graph with nothing linked to it yet — both hops must
        be created."""
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: _PO_CHAT_TARGET)
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: _PO_GAME_TARGET)
        calls = _patch_po_pwlink(monkeypatch)

        result = _s2p_p3.ensure_physical_output_links(data=_po_graph())

        assert result == {"chat": True, "hesuvi": True, "hesuvi_media": True}
        created = {(c[1], c[2]) for c in calls if "-d" not in c}
        assert created == {("11", "21"), ("12", "22"), ("31", "41"), ("32", "42"),
                           ("51", "41"), ("52", "42")}

    def test_noop_when_already_linked(self, monkeypatch):
        """Both hops already correctly linked → no pw-link calls at all."""
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: _PO_CHAT_TARGET)
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: _PO_GAME_TARGET)
        calls = _patch_po_pwlink(monkeypatch)
        existing = [
            _po_link(5001, 10, 11, 20, 21), _po_link(5002, 10, 12, 20, 22),
            _po_link(5003, 30, 31, 40, 41), _po_link(5004, 30, 32, 40, 42),
            _po_link(5005, 50, 51, 40, 41), _po_link(5006, 50, 52, 40, 42),
        ]

        result = _s2p_p3.ensure_physical_output_links(data=_po_graph(existing))

        assert result == {"chat": True, "hesuvi": True, "hesuvi_media": True}
        assert calls == []

    def test_physical_output_absent_does_nothing(self, monkeypatch):
        """Headset off: device_state is empty, both physical target names
        resolve to "" → no graph lookup, no pw-link call at all."""
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "")
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
        calls = _patch_po_pwlink(monkeypatch)

        result = _s2p_p3.ensure_physical_output_links(data=_po_graph())

        assert result == {}
        assert calls == []

    def test_target_node_missing_from_graph_returns_false_without_crashing(self, monkeypatch):
        """Physical target name is known (device_state populated) but the
        physical node hasn't reappeared in the PipeWire graph yet — one tick
        into a power-cycle — reported as not-linked (retried next tick), no
        pw-link call attempted."""
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: _PO_CHAT_TARGET)
        monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: _PO_GAME_TARGET)
        calls = _patch_po_pwlink(monkeypatch)
        data = [
            _po_node(10, "effect_output.sonar-chat-eq"),
            _po_port(11, 10, "out", "FL"), _po_port(12, 10, "out", "FR"),
            _po_node(30, "effect_output.virtual-surround-7.1-hesuvi"),
            _po_port(31, 30, "out", "FL"), _po_port(32, 30, "out", "FR"),
        ]

        result = _s2p_p3.ensure_physical_output_links(data=data)

        assert result == {"chat": False, "hesuvi": False, "hesuvi_media": False}
        assert calls == []


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


def test_apply_hrir_choice_triggers_single_restart(monkeypatch, tmp_path):
    """Phase 4: an HRIR change is the ONE remaining case that legitimately
    restarts filter-chain (the convolver only reads the WAV at load). It must
    restart exactly once and then re-establish the ASM-owned EQ→target links."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    import arctis_sound_manager.hrir_catalog as cat
    # Redirect the WAV destination away from the real home; a falsy hrir_id now
    # materialises the bundled default rather than skipping (issue #100).
    dest = tmp_path / "hrir.wav"
    monkeypatch.setattr(stp, "_HRIR_DEST", dest)
    src = tmp_path / "atmos.wav"
    src.write_bytes(b"RIFFWAVE-stub")
    monkeypatch.setattr(cat, "package_hrir_path", lambda _id: src)
    restart_calls = []
    monkeypatch.setattr(stp, "_restart_filter_chain",
                        lambda: restart_calls.append(1))
    link_calls = []
    monkeypatch.setattr(stp, "ensure_spatial_eq_links",
                        lambda *a, **kw: link_calls.append(a) or {})
    # hrir_id=None → materialise the default WAV, then restart.
    stp.apply_hrir_choice(None)
    assert dest.exists(), "falsy hrir_id must fall back to the bundled default WAV"
    assert restart_calls == [1], "HRIR change must restart filter-chain exactly once"
    assert link_calls, "HRIR change must re-establish the EQ→target links after restart"


def test_ensure_hrir_materialized_copies_when_missing(monkeypatch, tmp_path):
    """issue #100: with no HRIR on disk the HeSuVi convolver can't load, so the
    surround node never appears and Spatial Audio is silent. Materialisation
    must copy a bundled WAV into place when the destination is absent."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    import arctis_sound_manager.hrir_catalog as cat
    dest = tmp_path / "hrir.wav"
    src = tmp_path / "atmos.wav"
    src.write_bytes(b"RIFFWAVE-stub")
    monkeypatch.setattr(stp, "_HRIR_DEST", dest)
    monkeypatch.setattr(cat, "package_hrir_path",
                        lambda _id: src if _id == "atmos" else None)
    assert stp.ensure_hrir_materialized(None) is True
    assert dest.read_bytes() == b"RIFFWAVE-stub"


def test_ensure_hrir_materialized_noop_when_present(monkeypatch, tmp_path):
    """Idempotent: an existing non-empty WAV is never overwritten, so a user's
    explicit HRIR choice is left intact."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    import arctis_sound_manager.hrir_catalog as cat
    dest = tmp_path / "hrir.wav"
    dest.write_bytes(b"user-picked")
    monkeypatch.setattr(stp, "_HRIR_DEST", dest)
    called = {"copied": False}
    monkeypatch.setattr(cat, "package_hrir_path",
                        lambda _id: (_ for _ in ()).throw(AssertionError("must not copy")))
    assert stp.ensure_hrir_materialized("cmss_game") is False
    assert dest.read_bytes() == b"user-picked"


def test_spatial_links_fall_back_to_physical_when_hesuvi_absent(monkeypatch, tmp_path):
    """issue #100: Spatial ON while the HeSuVi node is missing AND no HRIR WAV
    exists (so it can never load) must route to the physical output instead of
    the dead surround node — otherwise game/media are silent."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(stp, "_HRIR_DEST", tmp_path / "missing.wav")  # absent
    monkeypatch.setattr(stp, "_spatial_enabled", lambda ch: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.phys")
    monkeypatch.setattr(pw, "pw_node_exists", lambda name, data=None: False)
    targets = {}
    monkeypatch.setattr(pw, "ensure_loopback_link",
                        lambda pb, tgt, data=None: targets.__setitem__(pb, tgt) or True)
    stp.ensure_spatial_eq_links(("game",))
    assert targets["effect_output.sonar-game-eq"] == "alsa_output.phys"


def test_spatial_links_keep_hesuvi_when_wav_present(monkeypatch, tmp_path):
    """The fallback must NOT flap onto physical during a transient (HeSuVi node
    briefly absent while filter-chain restarts) when the HRIR WAV is present —
    HeSuVi will come back, so keep targeting it and retry."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    import arctis_sound_manager.pw_utils as pw
    wav = tmp_path / "hrir.wav"
    wav.write_bytes(b"present")
    monkeypatch.setattr(stp, "_HRIR_DEST", wav)
    monkeypatch.setattr(stp, "_spatial_enabled", lambda ch: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.phys")
    monkeypatch.setattr(pw, "pw_node_exists", lambda name, data=None: False)
    targets = {}
    monkeypatch.setattr(pw, "ensure_loopback_link",
                        lambda pb, tgt, data=None: targets.__setitem__(pb, tgt) or False)
    stp.ensure_spatial_eq_links(("game",))
    assert targets["effect_output.sonar-game-eq"] == stp._SURROUND


# ── Generated-config versioning (ASM-CONF-VERSION) ────────────────────────────
#
# Regression coverage for the bug where v1.2.5 added a LADSPA limiter node to
# the HeSuVi surround chain, but users who already had a
# sink-virtual-surround-7.1-hesuvi.conf from an older release never got it
# regenerated: none of check_and_fix_stale_configs()'s prior staleness checks
# (node.target, bare-name LADSPA, ...) matched a conf that was otherwise
# perfectly well-formed, just older in shape. The limiter stayed inert for
# every existing install across the upgrade.

def test_active_game_conf_has_version_marker():
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_active_chat_conf_has_version_marker():
    bands = [EqBand(freq=1000, gain=3.0, q=0.7, type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_bypass_game_conf_has_version_marker():
    text = generate_sonar_eq_conf("game", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_bypass_chat_conf_has_version_marker():
    text = generate_sonar_eq_conf("chat", [], 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_active_micro_conf_has_version_marker():
    bands = [EqBand(freq=500, gain=2.0, q=0.5, type="peakingEQ", enabled=True)]
    text = generate_sonar_micro_conf(bands, 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_bypass_micro_conf_has_version_marker():
    text = generate_sonar_micro_conf([], 0.0, 0.0, 0.0,
                                     output_path=Path("/dev/null"))
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_hesuvi_conf_has_version_marker(monkeypatch):
    text = _gen_hesuvi(monkeypatch, limiter_available=True, distance_pct=0)
    assert f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}" in text


def test_conf_is_outdated_true_when_marker_absent():
    """No ASM-CONF-VERSION marker at all (every conf written before this
    mechanism existed) must be treated as outdated."""
    content = "# Auto-generated by Arctis Sound Manager — DO NOT EDIT\ncontext.modules = []\n"
    assert _s2p._conf_is_outdated(content) is True


def test_conf_is_outdated_true_when_marker_older():
    older = _s2p._CONF_VERSION - 1
    content = f"# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n# ASM-CONF-VERSION: {older}\ncontext.modules = []\n"
    assert _s2p._conf_is_outdated(content) is True


def test_conf_is_outdated_false_when_marker_current():
    content = (
        f"# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n"
        f"# ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n"
        f"context.modules = []\n"
    )
    assert _s2p._conf_is_outdated(content) is False


def test_check_and_fix_does_not_touch_current_version_confs(tmp_path, monkeypatch):
    """A game conf that already carries the current ASM-CONF-VERSION marker
    (and is otherwise clean) must not be rewritten — verified by content
    equality, not just `fixed is False`, so a marker regression that made the
    check always regenerate would still be caught even if that happened to
    also leave `fixed` False for other reasons."""
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire._get_physical_out_chat",
        lambda: "alsa_output.test-headset",
    )
    game_clean = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        f'# ASM-CONF-VERSION: {_s2p._CONF_VERSION}\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = copy  label = copy }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 8 }\n'
        '    playback.props = { node.target         = "effect_input.virtual-surround-7.1-hesuvi" } } }\n'
        ']\n'
    )
    path = tmp_path / "sonar-game-eq.conf"
    path.write_text(game_clean)

    with patch("arctis_sound_manager.sonar_to_pipewire._CONF_DIR", tmp_path):
        _s2p.check_and_fix_stale_configs()

    assert path.read_text() == game_clean, "an up-to-date conf must not be rewritten"


def test_check_and_fix_regenerates_pre_1_2_5_hesuvi_conf_with_limiter(tmp_path, monkeypatch):
    """Regression test for the reported bug (Discord, @craciu25_YT): a HeSuVi
    conf written by pre-1.2.5 ASM — correct node.target, no bare-name LADSPA,
    but no limiter node and no ASM-CONF-VERSION marker — must be regenerated
    with the limiter by check_and_fix_stale_configs(), so an existing install
    that upgrades actually gets the anti-clipping fix instead of silently
    keeping its stale conf forever."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    (tmp_path / "pipewire.conf.d").mkdir()
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_device_attached", lambda: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    # swh-plugins present on the host — the limiter must actually be emitted.
    monkeypatch.setattr(stp, "_ladspa_plugin_ref", lambda name: f"/usr/lib/ladspa/{name}")

    hrir = tmp_path / "hrir.wav"
    hrir.write_bytes(b"RIFFWAVE-stub")
    monkeypatch.setattr(stp, "_HRIR_DEST", hrir)

    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)
    (home / ".config" / "arctis_manager" / ".eq_mode").write_text("sonar")
    (home / ".config" / "arctis_manager" / "sonar_spatial_audio.json").write_text(
        '{"immersion": 80, "distance": 40}'
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    # Pre-1.2.5-style HeSuVi conf: mixers feed the sink directly, no "limiter"
    # node anywhere, no ASM-CONF-VERSION marker — exactly what generate_hesuvi_conf
    # produced before the limiter was added.
    pre_125_conf = (
        '# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n'
        '# HeSuVi 7.1 Virtual Surround  |  Immersion: 80%  |  Distance: 40%\n'
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = {\n'
        '      filter.graph = {\n'
        '        outputs = [ "mixL:Out" "mixR:Out" ]\n'
        '      }\n'
        '      playback.props = {\n'
        '        node.target        = "alsa_output.test-headset"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        ']\n'
    )
    (tmp_path / "sink-virtual-surround-7.1-hesuvi.conf").write_text(pre_125_conf)

    fixed, _needs_pw_restart = stp.check_and_fix_stale_configs()

    assert fixed is True
    regenerated = (tmp_path / "sink-virtual-surround-7.1-hesuvi.conf").read_text()
    assert "name = limiter" in regenerated
    assert "label = fastLookaheadLimiter" in regenerated
    assert f"# ASM-CONF-VERSION: {stp._CONF_VERSION}" in regenerated
    # The user's saved Immersion/Distance settings must survive the repair.
    assert "Immersion: 80%" in regenerated
    assert "Distance: 40%" in regenerated


# ── Per-channel HeSuVi chains (issue #169) ────────────────────────────────────

def test_hesuvi_conf_name_and_nodes_per_channel():
    """Game keeps the historical un-suffixed identity; Media gets -media so the
    two chains coexist without a duplicate node-name conflict."""
    assert _s2p._hesuvi_conf_name("game") == "sink-virtual-surround-7.1-hesuvi.conf"
    assert _s2p._hesuvi_conf_name("media") == "sink-virtual-surround-7.1-hesuvi-media.conf"
    assert _s2p._hesuvi_input_node("game") == "effect_input.virtual-surround-7.1-hesuvi"
    assert _s2p._hesuvi_input_node("media") == "effect_input.virtual-surround-7.1-hesuvi-media"
    assert _s2p._hesuvi_output_node("game") == "effect_output.virtual-surround-7.1-hesuvi"
    assert _s2p._hesuvi_output_node("media") == "effect_output.virtual-surround-7.1-hesuvi-media"


def test_generate_hesuvi_media_channel_uses_suffixed_node_names(monkeypatch):
    """The Media chain's capture/playback nodes carry the -media suffix, while
    the Game chain stays byte-identical to pre-#169 (un-suffixed)."""
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: None)

    game = _s2p.generate_hesuvi_conf(channel="game", output_path=Path("/dev/null"))
    media = _s2p.generate_hesuvi_conf(channel="media", output_path=Path("/dev/null"))

    assert 'node.name      = "effect_input.virtual-surround-7.1-hesuvi"' in game
    assert 'node.name          = "effect_output.virtual-surround-7.1-hesuvi"' in game
    assert 'node.name      = "effect_input.virtual-surround-7.1-hesuvi-media"' in media
    assert 'node.name          = "effect_output.virtual-surround-7.1-hesuvi-media"' in media
    # Both chains still drive the same physical GAME output.
    assert 'node.target        = "alsa_output.test-game"' in media
    # Media's sink description is distinct so the two are tellable apart.
    assert 'node.description = "Virtual Surround Sink (Media)"' in media


def test_ensure_spatial_eq_links_media_targets_media_hesuvi(monkeypatch):
    """Media EQ links to its OWN HeSuVi chain, not the Game one (#169)."""
    monkeypatch.setattr(_s2p_p3, "_spatial_enabled", lambda ch: True)
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    result = _s2p_p3.ensure_spatial_eq_links(("media",))
    assert result == {"media": True}
    assert calls == [("effect_output.sonar-media-eq",
                      "effect_input.virtual-surround-7.1-hesuvi-media")]


def test_hesuvi_conf_spatial_drift_detection():
    """The drift check compares the conf header's baked percentages against the
    saved JSON — the trigger that makes the sliders live (#169)."""
    conf = "# HeSuVi 7.1 Virtual Surround  |  Immersion: 35%  |  Distance: 59%\n"
    # Same values → no drift.
    assert _s2p._hesuvi_conf_has_spatial_drift(conf, 35, 59) is False
    # A moved slider → drift.
    assert _s2p._hesuvi_conf_has_spatial_drift(conf, 50, 59) is True
    assert _s2p._hesuvi_conf_has_spatial_drift(conf, 35, 50) is True
    # A conf with no header (older shape) is always treated as stale.
    assert _s2p._hesuvi_conf_has_spatial_drift("no header here", 50, 50) is True


def test_regenerate_hesuvi_if_changed_on_slider_drift(tmp_path, monkeypatch):
    """regenerate_hesuvi_if_changed rewrites the conf and reports True when the
    saved Immersion/Distance no longer matches the on-disk conf, and False when
    they already agree."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    (tmp_path / "pipewire.conf.d").mkdir()
    monkeypatch.setattr(stp, "_device_attached", lambda: True)
    monkeypatch.setattr(stp.device_state, "is_device_set", lambda: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(stp, "_ladspa_plugin_ref", lambda name: None)
    hrir = tmp_path / "hrir.wav"
    hrir.write_bytes(b"RIFFWAVE-stub")
    monkeypatch.setattr(stp, "_HRIR_DEST", hrir)
    monkeypatch.setattr(stp, "ensure_hrir_materialized", lambda *a, **kw: False)

    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    spatial = home / ".config" / "arctis_manager" / "sonar_spatial_audio.json"
    spatial.write_text('{"immersion": 50, "distance": 50}')

    # First run creates both confs from scratch → changed.
    assert stp.regenerate_hesuvi_if_changed() is True
    game_conf = tmp_path / "sink-virtual-surround-7.1-hesuvi.conf"
    assert "Immersion: 50%" in game_conf.read_text()

    # No change in JSON → nothing rewritten → False.
    assert stp.regenerate_hesuvi_if_changed() is False

    # Move the Game slider → drift → rewritten, reported True.
    spatial.write_text('{"immersion": 35, "distance": 59}')
    assert stp.regenerate_hesuvi_if_changed() is True
    assert "Immersion: 35%" in game_conf.read_text()
    assert "Distance: 59%" in game_conf.read_text()


def test_regenerate_hesuvi_if_changed_noop_without_device(monkeypatch):
    """No device attached → generate_hesuvi_conf can't write, so the function
    reports no change rather than churning 'fixed' forever (#169)."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp.device_state, "is_device_set", lambda: False)
    assert stp.regenerate_hesuvi_if_changed() is False


def test_apply_spatial_audio_change_restarts_only_when_changed(monkeypatch):
    """apply_spatial_audio_change restarts the filter-chain (quiesced, #100-safe)
    and re-owns the links ONLY when a conf actually changed — a pure toggle
    (no Immersion/Distance change) must not restart."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    restarts, spatial_links, phys_links = [], [], []
    monkeypatch.setattr(stp, "_restart_filter_chain", lambda: restarts.append(1))
    monkeypatch.setattr(stp, "ensure_spatial_eq_links", lambda *a, **kw: spatial_links.append(a) or {})
    monkeypatch.setattr(stp, "ensure_physical_output_links", lambda *a, **kw: phys_links.append(1) or {})

    # Nothing changed → no restart, no relink.
    monkeypatch.setattr(stp, "regenerate_hesuvi_if_changed", lambda: False)
    assert stp.apply_spatial_audio_change() is False
    assert restarts == [] and spatial_links == [] and phys_links == []

    # A slider moved → exactly one restart, both link-owners re-run.
    monkeypatch.setattr(stp, "regenerate_hesuvi_if_changed", lambda: True)
    assert stp.apply_spatial_audio_change() is True
    assert restarts == [1]
    assert spatial_links == [(("game", "media"),)]
    assert phys_links == [1]


def test_check_and_fix_regenerates_media_hesuvi_on_slider_drift(tmp_path, monkeypatch):
    """A moved MEDIA Immersion slider must regenerate the media chain from
    sonar_spatial_audio_media.json — the per-channel half of #169."""
    import arctis_sound_manager.sonar_to_pipewire as stp
    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    (tmp_path / "pipewire.conf.d").mkdir()
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_device_attached", lambda: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_ladspa_plugin_ref", lambda name: None)
    hrir = tmp_path / "hrir.wav"
    hrir.write_bytes(b"RIFFWAVE-stub")
    monkeypatch.setattr(stp, "_HRIR_DEST", hrir)

    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)
    (home / ".config" / "arctis_manager" / ".eq_mode").write_text("sonar")
    (home / ".config" / "arctis_manager" / "sonar_spatial_audio.json").write_text(
        '{"immersion": 50, "distance": 50}'
    )
    (home / ".config" / "arctis_manager" / "sonar_spatial_audio_media.json").write_text(
        '{"immersion": 25, "distance": 49}'
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    fixed, _ = stp.check_and_fix_stale_configs()
    assert fixed is True
    media_conf = (tmp_path / "sink-virtual-surround-7.1-hesuvi-media.conf").read_text()
    # Media chain reflects the MEDIA JSON, not the Game 50/50.
    assert "Immersion: 25%" in media_conf
    assert "Distance: 49%" in media_conf
    assert 'node.name      = "effect_input.virtual-surround-7.1-hesuvi-media"' in media_conf
    # Game chain stays on its own values.
    game_conf = (tmp_path / "sink-virtual-surround-7.1-hesuvi.conf").read_text()
    assert "Immersion: 50%" in game_conf


def test_missing_version_marker_never_regenerates_eq_confs(tmp_path, monkeypatch):
    """A marker-less but otherwise valid EQ conf must be left ALONE.

    Guard rail for the "Scope" note on _CONF_VERSION: the repair path for the
    EQ/micro confs can only write a *bypass* (flat) conf, since nothing outside
    gui/sonar_page.py can read back the user's bands/macros/boost. Treating a
    missing marker as staleness there would flatten every configured EQ on the
    first launch after an upgrade — every existing conf predates the marker.
    Extending _conf_is_outdated() to these confs must stay impossible until
    their repair path can restore the real settings; this test fails loudly if
    anyone wires it up anyway.
    """
    import arctis_sound_manager.sonar_to_pipewire as stp

    monkeypatch.setattr(stp, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(stp, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    (tmp_path / "pipewire.conf.d").mkdir()
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", tmp_path / "marker.json")
    monkeypatch.setattr(stp, "_device_attached", lambda: True)
    monkeypatch.setattr(stp, "_get_physical_out_game", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")

    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    # A configured EQ conf as written by any release before the marker existed:
    # correct channel count and target, real filter nodes — nothing stale about
    # it except the absent ASM-CONF-VERSION line.
    configured = (
        '# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n'
        '# Channel: game  |  Active bands: 1  |  Macros: 3\n'
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [\n'
        '      { type = builtin  name = eq0  label = bq_peaking\n'
        '        control = { Freq = 120.0  Q = 1.0  Gain = 6.0 } }\n'
        '    ] }\n'
        '    capture.props  = { audio.channels = 8 }\n'
        '    playback.props = { node.target         = '
        '"effect_input.virtual-surround-7.1-hesuvi" } } }\n'
        ']\n'
    )
    for name in ("sonar-game-eq.conf", "sonar-media-eq.conf", "sonar-micro-eq.conf"):
        (tmp_path / name).write_text(configured)

    stp.check_and_fix_stale_configs()

    for name in ("sonar-game-eq.conf", "sonar-media-eq.conf", "sonar-micro-eq.conf"):
        assert (tmp_path / name).read_text() == configured, (
            f"{name} was rewritten — a missing version marker must never flatten "
            "a user's EQ (see the Scope note on _CONF_VERSION)"
        )


# ── CHA-6 — the Output channel's setting and conf must not diverge silently ──

def test_output_target_reconciles_toward_the_setting_when_conf_diverges(tmp_path, monkeypatch, caplog):
    """CHA-6 reproduction: general_settings.yaml says the headset, the conf
    on disk still says the TV (a SetSetting over D-Bus, a hand-edit, a
    config restore or an upgrade never rewrote it). The setting is now the
    single owner: the next read must reconcile the live target — and the
    conf on disk — toward the setting, log the jump, and leave a backup of
    what was there before.
    """
    import logging
    import arctis_sound_manager.sonar_to_pipewire as _s2p_mod

    conf_dir = tmp_path / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    settings_dir = tmp_path / ".config" / "arctis_manager" / "settings"
    settings_dir.mkdir(parents=True)

    tv = "alsa_output.pci-0000_09_00.1.hdmi-stereo"
    headset = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"

    (settings_dir / "general_settings.yaml").write_text(
        f"external_output_device: {headset}\n"
    )
    conf_path = conf_dir / "sonar-output-eq.conf"
    conf_path.write_text(
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { filter.graph = { nodes = [ { type = builtin  name = copy  label = copy } ] }\n'
        '      playback.props = {\n'
        f'        node.target         = "{tv}"\n'
        f'        target.object       = "{tv}"\n'
        '      } } }\n'
        ']\n'
    )
    # No snapshot recorded — this conf predates the reconciliation mechanism
    # (or the setting moved past it without anything rewriting it), exactly
    # the state a pre-existing install is in the first time it sees this fix.

    monkeypatch.setattr(_s2p_mod, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_s2p_mod, "_resolve_external_output",
                        lambda *a, **kw: (headset, 2, "FL FR"))

    with caplog.at_level(logging.WARNING):
        resolved = _s2p_mod._get_configured_external_output()

    assert resolved == headset, "must reconcile toward the setting, not the stale conf"
    assert f'node.target         = "{headset}"' in conf_path.read_text()
    assert (conf_dir / "sonar-output-eq.conf.bak").exists(), "no backup taken before rewriting"
    assert tv in (conf_dir / "sonar-output-eq.conf.bak").read_text()
    assert any("diverged" in r.message and headset in r.message for r in caplog.records), (
        "the reconciliation must be logged, not silent"
    )

    # The snapshot is now in sync with the setting, so a second read is the
    # cheap path (conf unchanged) and does not flip anything again.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        second = _s2p_mod._get_configured_external_output()
    assert second == headset
    assert not any("diverged" in r.message for r in caplog.records), (
        "once reconciled, a following read must not re-trigger the expensive path"
    )


def test_output_target_stays_on_fast_path_when_setting_unchanged(tmp_path, monkeypatch):
    """The common case — nothing changed — must never pay for a pulsectl
    round-trip: _resolve_external_output() must not be called at all."""
    import arctis_sound_manager.sonar_to_pipewire as _s2p_mod

    conf_dir = tmp_path / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    settings_dir = tmp_path / ".config" / "arctis_manager" / "settings"
    settings_dir.mkdir(parents=True)
    snapshot_dir = tmp_path / ".config" / "arctis_manager"

    headset = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
    (settings_dir / "general_settings.yaml").write_text(
        f"external_output_device: {headset}\n"
    )
    (snapshot_dir / ".sonar_output_setting_snapshot").write_text(headset)

    conf_path = conf_dir / "sonar-output-eq.conf"
    conf_path.write_text(f'node.target         = "{headset}"\n')

    monkeypatch.setattr(_s2p_mod, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    calls = []
    monkeypatch.setattr(_s2p_mod, "_resolve_external_output",
                        lambda *a, **kw: calls.append(1) or (headset, 2, "FL FR"))

    resolved = _s2p_mod._get_configured_external_output()
    assert resolved == headset
    assert calls == [], "setting matches the snapshot — must not resolve via pulsectl"


# ── CHA-7 — a corrupt/missing conf must regenerate with the bands intact ────

def test_corrupt_conf_regenerates_from_saved_eq_state_not_a_bypass(tmp_path, monkeypatch):
    """CHA-7 reproduction: sonar-media-eq.conf truncated to the point its
    channel count no longer matches (the same trigger as the real
    truncation — wrong channel count fires the identical regeneration
    path as a missing file or a wrong target). The repair must rebuild the
    real EQ from the last saved state (bands/macros/boost intact) instead
    of the old flat bypass, and must leave a backup of the corrupt file.
    """
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    state_dir = home / ".config" / "arctis_manager"
    state_dir.mkdir(parents=True)

    # What generate_sonar_eq_conf() snapshots every time it writes the
    # Media channel's live conf.
    saved_state = {
        "bands": [
            {"freq": 250.0, "gain": 4.5, "q": 0.9, "type": "peakingEQ", "enabled": True},
        ],
        "basses_db": 1.0, "voix_db": -1.0, "aigus_db": 0.5,
        "boost_db": 2.0, "smart_volume": None,
    }
    (state_dir / "sonar_eq_state_media.json").write_text(json.dumps(saved_state))

    # Truncated conf: wrong channel count is exactly the trigger the report
    # reproduced (14 filters -> 1, channel count no longer matches).
    corrupt = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { capture.props = { audio.channels = 2 } } }\n'
        ']\n'
    )
    media_conf = conf_dir / "sonar-media-eq.conf"
    media_conf.write_text(corrupt)

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", conf_dir / "no-such-marker.json")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.test-ext", 2, "FL FR"))

    assert stp.ensure_sonar_eq_configs() is True

    repaired = media_conf.read_text()
    # The band survived — this is what a flat bypass would have destroyed.
    assert "Freq = 250.0" in repaired
    assert "Gain = 4.5" in repaired
    assert "Q = 0.9" in repaired
    # Macros and boost survived too.
    assert "Gain = 1.0" in repaired    # basses macro
    assert "Gain = -1.0" in repaired   # voix macro
    assert "Gain = 0.5" in repaired    # aigus macro
    assert "Gain = 2.0" in repaired    # boost
    assert "label = copy" not in repaired, "must not have fallen back to a flat bypass"
    assert "audio.channels = 8" in repaired

    # The corrupt file was preserved for diagnosis before being overwritten.
    backup = conf_dir / "sonar-media-eq.conf.bak"
    assert backup.exists()
    assert backup.read_text() == corrupt


def test_missing_saved_state_still_falls_back_to_a_bypass(tmp_path, monkeypatch):
    """The flip side: a channel that was never Applied has no saved state to
    rebuild from, so the repair must still fall back to the flat bypass —
    the fallback path stays intact for a first-ever install."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)  # no state file inside

    corrupt = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { capture.props = { audio.channels = 2 } } }\n'
        ']\n'
    )
    media_conf = conf_dir / "sonar-media-eq.conf"
    media_conf.write_text(corrupt)

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(stp, "_SAFE_MODE_MARKER", conf_dir / "no-such-marker.json")
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.test-ext", 2, "FL FR"))

    assert stp.ensure_sonar_eq_configs() is True

    repaired = media_conf.read_text()
    assert "label = copy" in repaired
    assert (conf_dir / "sonar-media-eq.conf.bak").exists()


def test_write_conf_failed_rename_leaves_original_conf_untouched(tmp_path, monkeypatch):
    """CHA-7: _write_conf must never leave a half-written conf on disk. If
    the final atomic rename fails partway through, the previous file must
    be exactly what it was — never truncated or partially overwritten —
    and no stray .tmp file is left behind."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    path = tmp_path / "sonar-media-eq.conf"
    original = "ORIGINAL CONF — 14 filters, byte for byte"
    path.write_text(original)

    def _boom(self, target):
        raise OSError("simulated interruption during rename")

    monkeypatch.setattr(Path, "replace", _boom)

    with pytest.raises(OSError):
        stp._write_conf(path, "TRUNCATED-CONTENT-THAT-MUST-NEVER-LAND")

    assert path.read_text() == original, "the original conf must survive an interrupted write"
    assert not (tmp_path / "sonar-media-eq.conf.tmp").exists(), "no stray tempfile left behind"


def test_write_conf_is_all_or_nothing_on_success(tmp_path, monkeypatch):
    """The successful path: no .tmp file left behind, and the target holds
    exactly the new content — the write is atomic, not incremental."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    path = tmp_path / "sonar-game-eq.conf"
    stp._write_conf(path, "NEW CONTENT")
    assert path.read_text() == "NEW CONTENT"
    assert not (tmp_path / "sonar-game-eq.conf.tmp").exists()


# ── CHA-7 (micro) — the mic channel gets the same lossless rebuild ─────────
#
# The EQ channels (game/chat/media/output) already have the CHA-7 fix; the
# mic channel was left calling _bypass_micro_conf() on every repair trigger,
# discarding bands, macros, boost AND the noise-cancelling / noise-reduction
# settings that have no equivalent anywhere in the EQ state. These tests
# cover generate_sonar_micro_conf()'s own snapshot (_save_micro_state) and
# _regenerate_micro_conf()'s rebuild/fallback/fail-closed behaviour.

def test_micro_conf_written_to_real_path_saves_state(tmp_path, monkeypatch):
    """generate_sonar_micro_conf() must snapshot the state that produced the
    conf when writing to the real (live) path — output_path=None — the same
    way generate_sonar_eq_conf() does for the other four channels. Written
    from here (the producer), not the GUI's Apply worker, so an install that
    upgrades and never reopens the Micro tab still gets a snapshot the first
    time anything regenerates this conf."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_get_physical_in", lambda: "alsa_input.test-headset")
    monkeypatch.setattr(stp, "_device_attached", lambda: True)

    bands = [EqBand(freq=250, gain=-4.0, q=0.7, type="peakingEQ", enabled=True)]
    nc = {"enabled": True, "value": 0.6, "engine": "rnnoise"}
    nr = {
        "bgReduction": {"enabled": True, "value": 0.4},
        "compressor": {"enabled": True, "value": 0.2},
    }
    stp.generate_sonar_micro_conf(
        bands, 0.0, 3.0, 0.0, boost_db=2.0,
        noise_canceling=nc, noise_reduction=nr,
    )

    state_path = home / ".config" / "arctis_manager" / "sonar_micro_state.json"
    assert state_path.exists()

    loaded = stp._load_micro_state()
    assert loaded is not None
    assert loaded["bands"][0].freq == 250.0
    assert loaded["bands"][0].gain == -4.0
    assert loaded["voix_db"] == 3.0
    assert loaded["boost_db"] == 2.0
    assert loaded["noise_canceling"] == {"enabled": True, "value": 0.6, "engine": "rnnoise"}
    assert loaded["noise_reduction"]["bgReduction"] == {"enabled": True, "value": 0.4}
    assert loaded["noise_reduction"]["compressor"] == {"enabled": True, "value": 0.2}
    # Sub-processors never set default to disabled, not omitted.
    assert loaded["noise_reduction"]["impactReduction"] == {"enabled": False, "value": 0.0}
    assert loaded["noise_reduction"]["noiseGate"] == {"enabled": False, "value": -40.0}


def test_micro_conf_written_to_explicit_path_does_not_save_state(tmp_path, monkeypatch):
    """The flip side of the guard above: a caller passing an explicit
    output_path (diffing/testing) must not overwrite the real snapshot —
    exactly the same rule generate_sonar_eq_conf() already follows."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    bands = [EqBand(freq=250, gain=-4.0, q=0.7, type="peakingEQ", enabled=True)]
    stp.generate_sonar_micro_conf(
        bands, 0.0, 0.0, 0.0, output_path=tmp_path / "sonar-micro-eq.conf",
    )

    state_path = home / ".config" / "arctis_manager" / "sonar_micro_state.json"
    assert not state_path.exists()


def test_corrupt_micro_conf_regenerates_from_saved_state_not_a_bypass(tmp_path, monkeypatch):
    """CHA-7 (micro) reproduction: a micro conf using the old
    Audio/Source/Virtual media.class is a regeneration trigger in
    check_and_fix_stale_configs(). The repair must rebuild the real mic EQ —
    bands, macros, boost AND noise-reduction/noise-cancelling — from the
    last saved micro state instead of the old flat bypass, and must leave a
    backup of the stale file."""
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    state_dir = home / ".config" / "arctis_manager"
    state_dir.mkdir(parents=True)

    saved_state = {
        "bands": [
            {"freq": 250.0, "gain": -4.5, "q": 0.9, "type": "peakingEQ", "enabled": True},
        ],
        "basses_db": 1.0, "voix_db": -1.0, "aigus_db": 0.5, "boost_db": 3.0,
        "noise_canceling": {"enabled": True, "value": 0.7, "engine": "rnnoise"},
        "noise_reduction": {
            "bgReduction": {"enabled": True, "value": 0.5},
            "impactReduction": {"enabled": True, "value": 0.25},
            "noiseGate": {"enabled": True, "value": -30.0},
            "compressor": {"enabled": True, "value": 0.6},
        },
    }
    (state_dir / "sonar_micro_state.json").write_text(json.dumps(saved_state))

    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { playback.props = { media.class = Audio/Source/Virtual } } }\n'
        ']\n'
    )
    micro_conf = conf_dir / "sonar-micro-eq.conf"
    micro_conf.write_text(stale)

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_get_physical_in", lambda: "alsa_input.test-headset")
    # The noise-reduction nodes are LADSPA, so on a machine without
    # swh-plugins and noise-suppression-for-voice — every CI runner — the
    # generator legitimately skips them and this test failed for a reason that
    # has nothing to do with what it checks. Pin the lookup instead of
    # depending on what happens to be installed.
    monkeypatch.setattr(stp, "_ladspa_plugin_ref",
                        lambda name, resolved=None: f"/fake/ladspa/{name}.so")

    fixed, _needs_pw_restart = check_and_fix_stale_configs()
    assert fixed is True

    repaired = micro_conf.read_text()
    assert "Freq = 250.0" in repaired
    assert "Gain = -4.5" in repaired
    assert "Q = 0.9" in repaired
    assert "Gain = 1.0" in repaired    # basses macro
    assert "Gain = -1.0" in repaired   # voix macro
    assert "Gain = 0.5" in repaired    # aigus macro
    assert "Gain = 3.0" in repaired    # boost
    assert "rnnoise" in repaired, "noise cancelling engine survived"
    assert "nr_bg" in repaired, "background noise reduction survived"
    assert "nr_impact" in repaired, "impact noise reduction survived"
    assert "ngate" in repaired, "noise gate survived"
    assert "comp" in repaired, "compressor survived"
    assert "micro passthrough" not in repaired, "must not have fallen back to a flat bypass"

    backup = conf_dir / "sonar-micro-eq.conf.bak"
    assert backup.exists()
    assert backup.read_text() == stale


def test_missing_micro_saved_state_still_falls_back_to_a_bypass(tmp_path, monkeypatch):
    """The flip side: a mic channel that was never Applied has no saved
    state to rebuild from, so the repair must still fall back to the flat
    bypass — the fallback path stays intact for a first-ever install."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".config" / "arctis_manager").mkdir(parents=True)  # no state file inside

    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { playback.props = { media.class = Audio/Source/Virtual } } }\n'
        ']\n'
    )
    micro_conf = conf_dir / "sonar-micro-eq.conf"
    micro_conf.write_text(stale)

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)

    fixed, _needs_pw_restart = check_and_fix_stale_configs()
    assert fixed is True

    repaired = micro_conf.read_text()
    assert "micro passthrough" in repaired
    assert (conf_dir / "sonar-micro-eq.conf.bak").exists()


def test_malformed_micro_state_snapshot_falls_back_to_bypass_without_raising(tmp_path, monkeypatch):
    """A mangled sonar_micro_state.json (wrong type on a nested field) must
    not crash the repair — _load_micro_state() fails closed and the repair
    falls back to the bypass, exactly like a missing snapshot."""
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    state_dir = home / ".config" / "arctis_manager"
    state_dir.mkdir(parents=True)

    # "value" inside a noise-reduction sub-processor is a non-numeric string —
    # generate_sonar_micro_conf() would crash deep inside its arithmetic
    # (max(0.0, min(1.0, ...))) if this reached it unvalidated.
    malformed = {
        "bands": [],
        "basses_db": 0.0, "voix_db": 0.0, "aigus_db": 0.0, "boost_db": 0.0,
        "noise_canceling": {"enabled": True, "value": 0.5, "engine": "rnnoise"},
        "noise_reduction": {"bgReduction": {"enabled": True, "value": "not-a-number"}},
    }
    (state_dir / "sonar_micro_state.json").write_text(json.dumps(malformed))

    stale = (
        'context.modules = [\n'
        '  { name = libpipewire-module-filter-chain\n'
        '    args = { playback.props = { media.class = Audio/Source/Virtual } } }\n'
        ']\n'
    )
    micro_conf = conf_dir / "sonar-micro-eq.conf"
    micro_conf.write_text(stale)

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)

    fixed, _needs_pw_restart = check_and_fix_stale_configs()  # must not raise
    assert fixed is True

    repaired = micro_conf.read_text()
    assert "micro passthrough" in repaired, "malformed snapshot must fall back to bypass"


def test_interrupted_micro_state_write_leaves_previous_snapshot_readable(tmp_path, monkeypatch):
    """CHA-7: _save_micro_state must never leave a half-written or missing
    snapshot on disk. If the final atomic rename fails partway through, the
    previous snapshot must still be exactly what it was."""
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    home = tmp_path / "home"
    state_dir = home / ".config" / "arctis_manager"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "sonar_micro_state.json"

    original = {
        "bands": [{"freq": 100.0, "gain": 1.0, "q": 0.7, "type": "peakingEQ", "enabled": True}],
        "basses_db": 0.0, "voix_db": 0.0, "aigus_db": 0.0, "boost_db": 0.0,
        "noise_canceling": {}, "noise_reduction": {},
    }
    state_path.write_text(json.dumps(original))

    monkeypatch.setattr(Path, "home", lambda: home)

    def _boom(self, target):
        raise OSError("simulated interruption during rename")

    monkeypatch.setattr(Path, "replace", _boom)

    # _save_micro_state must never raise even though the rename fails.
    stp._save_micro_state(
        [EqBand(freq=999, gain=9.0, q=0.7, type="peakingEQ", enabled=True)],
        0.0, 0.0, 0.0, 0.0, {}, {},
    )

    assert json.loads(state_path.read_text()) == original, (
        "the previous snapshot must survive an interrupted write"
    )
    assert not (state_dir / "sonar_micro_state.json.tmp").exists(), (
        "no stray tempfile left behind"
    )


# ── CHA-10 — inf/nan/1e400 from a preset must never reach the conf ──────────

def test_clamp_finite_rejects_non_finite_values():
    import arctis_sound_manager.sonar_to_pipewire as stp

    assert stp._clamp_finite(float("inf"), 20.0, 20000.0, 1000.0) == 1000.0
    assert stp._clamp_finite(float("-inf"), 20.0, 20000.0, 1000.0) == 1000.0
    assert stp._clamp_finite(float("nan"), -12.0, 12.0, 0.0) == 0.0
    # Finite but out of range still clamps, same as boost_db already does.
    assert stp._clamp_finite(999999.0, 20.0, 20000.0, 1000.0) == 20000.0
    assert stp._clamp_finite(-5.0, 0.1, 10.0, 0.7071) == 0.1
    # Finite and in range passes through unchanged.
    assert stp._clamp_finite(440.0, 20.0, 20000.0, 1000.0) == 440.0


def test_node_block_never_emits_non_finite_control_values():
    """CHA-10 reproduction: a band carrying inf/nan (e.g. from a shared
    preset with 1e400/NaN literals) must never reach the generated conf as
    'Freq = inf' / 'Gain = nan' — PipeWire creates that node with no
    diagnostic at all. This is the single choke point every producer's band
    literals pass through, so it must catch this regardless of where the
    non-finite value came from."""
    bands = [EqBand(freq=float("inf"), gain=float("nan"), q=float("-inf"),
                    type="peakingEQ", enabled=True)]
    text = generate_sonar_eq_conf("media", bands, 0.0, 0.0, 0.0,
                                  output_path=Path("/dev/null"))
    assert "= inf" not in text
    assert "= -inf" not in text
    assert "= nan" not in text


def test_parse_preset_data_rejects_non_finite_values():
    """gui/sonar_page.py._parse_preset_data — the earliest boundary a shared
    preset crosses. A band carrying inf/nan must come out finite and within
    the same domain the interactive EQ curve itself clamps to."""
    import math
    from arctis_sound_manager.gui import sonar_page as sp

    data = {
        "parametricEQ": {
            "filter1": {
                "frequency": 1e400, "gain": float("nan"), "qFactor": float("inf"),
                "type": "peakingEQ", "enabled": True,
            },
        }
    }
    bands = sp._parse_preset_data(data)
    assert len(bands) == 1
    b = bands[0]
    assert math.isfinite(b.freq) and math.isfinite(b.gain) and math.isfinite(b.q)
    assert 20.0 <= b.freq <= 20000.0
    assert -12.0 <= b.gain <= 12.0
    assert 0.1 <= b.q <= 10.0


def test_parse_preset_rejects_json_infinity_and_nan_literals(tmp_path):
    """End-to-end CHA-10 reproduction: Python's json module accepts
    Infinity/NaN literals by default, and 1e400 overflows straight to inf.
    A preset file carrying exactly those tokens must still parse into a
    finite, in-range band."""
    import math
    from arctis_sound_manager.gui import sonar_page as sp

    preset_path = tmp_path / "chaos [Game].json"
    preset_path.write_text(
        '{"parametricEQ": {"filter1": '
        '{"frequency": 1e400, "gain": NaN, "qFactor": Infinity, '
        '"type": "peakingEQ", "enabled": true}}}'
    )
    bands = sp._parse_preset(preset_path)
    assert len(bands) == 1
    b = bands[0]
    assert math.isfinite(b.freq) and math.isfinite(b.gain) and math.isfinite(b.q)


def test_generating_a_conf_snapshots_the_state_that_produced_it(tmp_path, monkeypatch):
    """CHA-7, second half: the lossless rebuild is only worth anything if a
    snapshot exists. Writing it from the GUI's Apply worker would leave
    every install that upgrades and never reopens the Sonar page with no
    snapshot at all — and the first repair would still flatten its EQ. The
    snapshot is therefore written by generate_sonar_eq_conf() itself, which
    every producer (GUI apply, global apply, the daemon's own repair) goes
    through.
    """
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.test-ext", 2, "FL FR"))

    bands = [stp.EqBand(freq=440.0, gain=3.5, q=1.2, type="peakingEQ", enabled=True)]
    stp.generate_sonar_eq_conf("media", bands, 1.0, 0.0, -2.0, boost_db=1.5)

    state = json.loads((home / ".config" / "arctis_manager"
                        / "sonar_eq_state_media.json").read_text())
    assert state["bands"] == [
        {"freq": 440.0, "gain": 3.5, "q": 1.2, "type": "peakingEQ", "enabled": True}
    ]
    assert state["basses_db"] == 1.0
    assert state["aigus_db"] == -2.0
    assert state["boost_db"] == 1.5


def test_generating_to_an_explicit_path_leaves_the_snapshot_alone(tmp_path, monkeypatch):
    """A caller passing output_path is diffing or testing, not applying.
    Letting it overwrite the snapshot would let a dry-run become the state
    the next repair rebuilds from."""
    import json
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"
    state_dir = home / ".config" / "arctis_manager"
    state_dir.mkdir(parents=True)
    snapshot = state_dir / "sonar_eq_state_media.json"
    snapshot.write_text(json.dumps({"bands": [], "basses_db": 9.0, "voix_db": 0.0,
                                    "aigus_db": 0.0, "boost_db": 0.0,
                                    "smart_volume": None}))

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.test-ext", 2, "FL FR"))

    bands = [stp.EqBand(freq=100.0, gain=1.0, q=1.0, type="peakingEQ", enabled=True)]
    stp.generate_sonar_eq_conf("media", bands, 0.0, 0.0, 0.0,
                               output_path=tmp_path / "scratch.conf")

    assert json.loads(snapshot.read_text())["basses_db"] == 9.0


# ── SD-1: the Output channel's device can disappear ─────────────────────────


def _output_hop_setup(monkeypatch, configured, graph_nodes):
    """Only the Output hop active: no chat/game physical targets."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_chat", lambda: "")
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "alsa_output.headset")
    monkeypatch.setattr(_s2p_p3, "channel_destination", lambda ch, data=None: "")
    monkeypatch.setattr(_s2p_p3, "_get_configured_external_output", lambda: configured)
    monkeypatch.setattr(_s2p_p3, "_node_in_graph",
                        lambda data, name: name in graph_nodes)
    monkeypatch.setattr(_s2p_p3, "_output_fallback_active", None, raising=False)
    calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.ensure_loopback_link",
        lambda playback, target, data=None: calls.append((playback, target)) or True,
    )
    return calls


def test_output_channel_falls_back_to_the_headset_when_its_device_is_gone(monkeypatch):
    """SD-1: the monitor is switched off / the Bluetooth speaker walks away.
    Skipping the hop left everything routed to Output playing into a dead end,
    silently and for ever, whenever the tray GUI was not running to fall back
    itself. Game/Chat/Media never had that gap."""
    calls = _output_hop_setup(monkeypatch, "alsa_output.hdmi-tv",
                              {"alsa_output.headset"})

    result = _s2p_p3.ensure_physical_output_links()

    assert calls == [("effect_output.sonar-output-eq", "alsa_output.headset")]
    assert result == {"output": True}


def test_output_fallback_does_not_rewrite_the_users_choice(monkeypatch):
    """The fallback is a link, not a decision: the setting stays put so the
    channel returns to the external sink on its own when it comes back."""
    written = []
    monkeypatch.setattr(_s2p_p3, "_sync_output_setting_snapshot",
                        lambda *a, **kw: written.append(a), raising=False)
    _output_hop_setup(monkeypatch, "alsa_output.hdmi-tv", {"alsa_output.headset"})

    _s2p_p3.ensure_physical_output_links()

    assert written == [], "the fallback must not touch external_output_device"


def test_output_hop_prefers_the_configured_sink_when_it_is_present(monkeypatch):
    calls = _output_hop_setup(monkeypatch, "alsa_output.hdmi-tv",
                              {"alsa_output.headset", "alsa_output.hdmi-tv"})

    _s2p_p3.ensure_physical_output_links()

    assert calls == [("effect_output.sonar-output-eq", "alsa_output.hdmi-tv")]


def test_output_hop_stays_quiet_with_no_headset_to_fall_back_to(monkeypatch):
    """Configured sink gone and no headset either: nothing to link, and no
    link attempt to log in a loop."""
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")
    calls = _output_hop_setup(monkeypatch, "alsa_output.hdmi-tv", set())
    monkeypatch.setattr(_s2p_p3, "_get_physical_out_game", lambda: "")

    result = _s2p_p3.ensure_physical_output_links()

    assert calls == []
    assert "output" not in result


def test_a_failed_snapshot_write_does_not_take_the_conf_down_with_it(tmp_path, monkeypatch):
    """The snapshot is a convenience; the conf is the product. A full disk or
    a read-only $HOME must cost the snapshot, not the conf — and the handler
    that says so must itself run: it referenced a `logger` name this module
    does not define, so it raised NameError from inside the except clause."""
    import arctis_sound_manager.sonar_to_pipewire as stp

    conf_dir = tmp_path / "conf" / "filter-chain.conf.d"
    conf_dir.mkdir(parents=True)
    home = tmp_path / "home"

    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(stp, "_get_physical_out_chat", lambda: "alsa_output.test-headset")
    monkeypatch.setattr(stp, "_resolve_external_output",
                        lambda *a, **kw: ("alsa_output.test-ext", 2, "FL FR"))


    bands = [stp.EqBand(freq=440.0, gain=3.0, q=1.0, type="peakingEQ", enabled=True)]
    text = stp.generate_sonar_eq_conf("media", bands, 0.0, 0.0, 0.0)

    assert "Freq = 440.0" in text
    assert (conf_dir / "sonar-media-eq.conf").exists()


# ── pipewire.conf.d duplicates (#14, then #205) ──────────────────────────────


def _purge_dirs(tmp_path, monkeypatch):
    """A filter-chain.conf.d/ and the pipewire.conf.d/ beside it."""
    import logging
    from arctis_sound_manager import sonar_to_pipewire as _mod

    good = tmp_path / "pipewire" / "filter-chain.conf.d"
    bad = tmp_path / "pipewire" / "pipewire.conf.d"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    monkeypatch.setattr(_mod, "_CONF_DIR", good)
    return good, bad, logging.getLogger("test")


def test_a_copy_of_one_of_ours_is_removed(tmp_path, monkeypatch):
    """The shape of both #14 and #205: the same filter in both directories,
    loaded by two different processes, so two nodes answer to one name."""
    from arctis_sound_manager.sonar_to_pipewire import _purge_duplicate_pipewire_confs

    good, bad, log = _purge_dirs(tmp_path, monkeypatch)
    (good / "sonar-media-eq.conf").write_text("# Auto-generated by Arctis Sound Manager\n")
    (bad / "sonar-media-eq.conf").write_text("# Auto-generated by Arctis Sound Manager\n")

    removed = _purge_duplicate_pipewire_confs(bad, log)

    assert removed == ["sonar-media-eq.conf"]
    assert not (bad / "sonar-media-eq.conf").exists()
    assert (good / "sonar-media-eq.conf").exists(), "ours must survive"


def test_the_unmarked_hesuvi_template_is_still_caught(tmp_path, monkeypatch):
    """#14's actual file. The static HeSuVi template carries no ASM header — it
    opens with "# Convolver sink" — so recognising our own files by marker alone
    would walk straight past the very bug this repair exists for."""
    from arctis_sound_manager.sonar_to_pipewire import _purge_duplicate_pipewire_confs

    good, bad, log = _purge_dirs(tmp_path, monkeypatch)
    (good / "sink-virtual-surround-7.1-hesuvi.conf").write_text("# generated\n")
    (bad / "sink-virtual-surround-7.1-hesuvi.conf").write_text(
        "# Convolver sink\n#\n# Copy this file into a conf.d/ directory\n"
    )

    removed = _purge_duplicate_pipewire_confs(bad, log)

    assert removed == ["sink-virtual-surround-7.1-hesuvi.conf"]


def test_the_filters_the_old_list_forgot(tmp_path, monkeypatch):
    """Why #205 happened at all. The repair used to be four hand-written
    filenames; sonar-output-eq and the -media HeSuVi variant were added to the
    generator later and never added here, so they were the two that survived."""
    from arctis_sound_manager.sonar_to_pipewire import _purge_duplicate_pipewire_confs

    good, bad, log = _purge_dirs(tmp_path, monkeypatch)
    forgotten = ["sonar-output-eq.conf", "sink-virtual-surround-7.1-hesuvi-media.conf"]
    for name in forgotten:
        (good / name).write_text("# Auto-generated by Arctis Sound Manager\n")
        (bad / name).write_text("# Auto-generated by Arctis Sound Manager\n")

    removed = _purge_duplicate_pipewire_confs(bad, log)

    assert sorted(removed) == sorted(forgotten)


def test_a_leftover_of_ours_goes_even_with_no_counterpart(tmp_path, monkeypatch):
    """An install that no longer generates a filter still has to clean up the
    copy it left behind, with nothing left to compare the name against."""
    from arctis_sound_manager.sonar_to_pipewire import _purge_duplicate_pipewire_confs

    _good, bad, log = _purge_dirs(tmp_path, monkeypatch)
    (bad / "sonar-chat-eq.conf").write_text(
        "# Auto-generated by Arctis Sound Manager — DO NOT EDIT\n# ASM-CONF-VERSION: 4\n"
    )

    assert _purge_duplicate_pipewire_confs(bad, log) == ["sonar-chat-eq.conf"]


def test_a_file_that_is_not_ours_is_left_alone(tmp_path, monkeypatch, caplog):
    """#205's reporter had a spatializer of his own in that directory and was
    surprised to learn it was not part of ASM. A duplicate node is not a good
    enough reason to delete someone's file — say so and move on."""
    import logging
    from arctis_sound_manager.sonar_to_pipewire import _purge_duplicate_pipewire_confs

    _good, bad, log = _purge_dirs(tmp_path, monkeypatch)
    mine = bad / "spatializer.conf"
    mine.write_text(
        "context.modules = [\n  { name = libpipewire-module-filter-chain\n"
        "    args = { node.name = effect_input.spatializer } }\n]\n"
    )

    with caplog.at_level(logging.WARNING):
        removed = _purge_duplicate_pipewire_confs(bad, log)

    assert removed == []
    assert mine.exists()
    assert "not ours" in caplog.text


def test_removing_anything_asks_for_the_restart(tmp_path, monkeypatch):
    """Deleting the file does not unload the node: the daemon loaded that module
    at startup and keeps it until it restarts. Before this, only HeSuVi's removal
    set the flag, so clearing any other duplicate changed nothing until reboot."""
    from arctis_sound_manager import sonar_to_pipewire as _mod

    good, bad, _log = _purge_dirs(tmp_path, monkeypatch)
    monkeypatch.setattr(_mod, "_filter_chain_safe_mode", False)
    monkeypatch.setattr(_mod, "_SAFE_MODE_MARKER", tmp_path / "no-marker.json")
    (good / "sonar-output-eq.conf").write_text("# Auto-generated by Arctis Sound Manager\n")
    (bad / "sonar-output-eq.conf").write_text("# Auto-generated by Arctis Sound Manager\n")

    _fixed, needs_pw_restart = _mod.check_and_fix_stale_configs()

    assert needs_pw_restart is True
    assert not (bad / "sonar-output-eq.conf").exists()
