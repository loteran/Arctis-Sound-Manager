"""Per-virtual-speaker gain on the HeSuVi chain (Sonar's channel levels)."""
import json
from pathlib import Path

import arctis_sound_manager.sonar_to_pipewire as _s2p


def _spatial_json(home: Path, channel: str = "game") -> Path:
    suffix = "" if channel == "game" else f"_{channel}"
    d = home / ".config" / "arctis_manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"sonar_spatial_audio{suffix}.json"


def _gen(monkeypatch, tmp_path, gains=None, channel="game", immersion=50):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    if gains is not None:
        _spatial_json(home, channel).write_text(json.dumps({"channel_gains": gains}))
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
    monkeypatch.setattr(_s2p, "_hesuvi_boost_db", lambda channel: 0.0)
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: "/usr/lib/ladspa/" + name)
    return _s2p.generate_hesuvi_conf(
        immersion_pct=immersion, distance_pct=0, output_path=Path("/dev/null"), channel=channel,
    )


def test_zero_gains_are_byte_identical_to_no_setting(monkeypatch, tmp_path):
    """All-zero gains must produce exactly the conf an install without the
    setting gets — no header segment, Immersion alone in every gain node."""
    without = _gen(monkeypatch, tmp_path / "a")
    zeros = _gen(monkeypatch, tmp_path / "b", gains={ch: 0.0 for ch in _s2p._HESUVI_CHANNELS})
    assert without == zeros
    assert "Gains:" not in without
    for ch in _s2p._HESUVI_CHANNELS:
        assert f"name = gain{ch}  label = bq_highshelf  control = {{ Freq = 10  Q = 0.7071  Gain = 6.0 }}" in without


def test_gain_adds_to_immersion_on_its_channel_only(monkeypatch, tmp_path):
    text = _gen(monkeypatch, tmp_path, gains={"FC": 3.0, "RL": -4.5})
    assert "name = gainFC  label = bq_highshelf  control = { Freq = 10  Q = 0.7071  Gain = 9.0 }" in text
    assert "name = gainRL  label = bq_highshelf  control = { Freq = 10  Q = 0.7071  Gain = 1.5 }" in text
    assert "name = gainFL  label = bq_highshelf  control = { Freq = 10  Q = 0.7071  Gain = 6.0 }" in text
    assert "Gains: FL=+0.0 FR=+0.0 FC=+3.0 LFE=+0.0 RL=-4.5 RR=+0.0 SL=+0.0 SR=+0.0" in text


def test_gains_are_clamped_and_garbage_ignored(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    _spatial_json(home).write_text(json.dumps(
        {"channel_gains": {"FL": 40, "FR": -99, "FC": "loud", "XX": 3}}
    ))
    gains = _s2p._load_channel_gains("game")
    assert gains["FL"] == _s2p.CHANNEL_GAIN_MAX_DB
    assert gains["FR"] == _s2p.CHANNEL_GAIN_MIN_DB
    assert gains["FC"] == 0.0
    assert set(gains) == set(_s2p._HESUVI_CHANNELS)


def test_missing_or_corrupt_file_reads_as_zero(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    assert all(v == 0.0 for v in _s2p._load_channel_gains("game").values())
    _spatial_json(home).write_text("{not json")
    assert all(v == 0.0 for v in _s2p._load_channel_gains("game").values())


def test_media_channel_reads_its_own_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    _spatial_json(home, "game").write_text(json.dumps({"channel_gains": {"FC": 2.0}}))
    _spatial_json(home, "media").write_text(json.dumps({"channel_gains": {"FC": -2.0}}))
    assert _s2p._load_channel_gains("game")["FC"] == 2.0
    assert _s2p._load_channel_gains("media")["FC"] == -2.0


def test_gain_drift_detection(monkeypatch, tmp_path):
    zeros = {ch: 0.0 for ch in _s2p._HESUVI_CHANNELS}
    some = dict(zeros, FC=3.0)
    header = "# HeSuVi 7.1 Virtual Surround  |  Immersion: 50%  |  Distance: 50%"
    # No segment, all zero → current.
    assert _s2p._hesuvi_conf_has_gain_drift(header + "\n", zeros) is False
    # No segment but a gain was set → stale.
    assert _s2p._hesuvi_conf_has_gain_drift(header + "\n", some) is True
    # Matching segment, also after a Boost segment.
    baked = header + "  |  Boost: 3.0 dB" + _s2p._hesuvi_gains_segment(some) + "\n"
    assert _s2p._hesuvi_conf_has_gain_drift(baked, some) is False
    # Segment present but the user reset everything → stale.
    assert _s2p._hesuvi_conf_has_gain_drift(baked, zeros) is True
    # Segment present, other value → stale.
    assert _s2p._hesuvi_conf_has_gain_drift(baked, dict(some, FC=1.0)) is True
    # Boost parsing is not disturbed by the trailing segment.
    assert _s2p._hesuvi_conf_has_boost_drift(baked, 3.0) is False


def test_generated_conf_has_no_drift_against_its_own_gains(monkeypatch, tmp_path):
    gains = {"FL": 1.5, "LFE": -6.0}
    text = _gen(monkeypatch, tmp_path, gains=gains)
    assert _s2p._hesuvi_conf_has_gain_drift(text, _s2p._load_channel_gains("game")) is False


# ── Live apply (no filter-chain restart) ─────────────────────────────────────

def test_diff_controls_gain_only(monkeypatch, tmp_path):
    old = _gen(monkeypatch, tmp_path / "a")
    new = _gen(monkeypatch, tmp_path / "b", gains={"FC": 3.0, "LFE": -6.0})
    # The HRTF gain stage and the 5.1/7.1-output levels stage both follow.
    assert _s2p.diff_hesuvi_controls(old, new) == {
        "gainFC:Gain": 9.0, "gainLFE:Gain": 0.0, "lvlFC:Gain": 3.0, "lvlLFE:Gain": -6.0,
    }


def test_diff_controls_immersion_moves_every_gain_stage(monkeypatch, tmp_path):
    old = _gen(monkeypatch, tmp_path / "a", immersion=50)
    new = _gen(monkeypatch, tmp_path / "b", immersion=25)
    assert _s2p.diff_hesuvi_controls(old, new) == {
        f"gain{ch}:Gain": 3.0 for ch in _s2p._HESUVI_CHANNELS
    }


def _gen_distance(monkeypatch, tmp_path, distance):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
    monkeypatch.setattr(_s2p, "_hesuvi_boost_db", lambda channel: 0.0)
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: "/usr/lib/ladspa/" + name)
    return _s2p.generate_hesuvi_conf(distance_pct=distance, output_path=Path("/dev/null"))


def test_diff_controls_distance_wet_mix(monkeypatch, tmp_path):
    old = _gen_distance(monkeypatch, tmp_path, 50)
    new = _gen_distance(monkeypatch, tmp_path, 30)
    assert _s2p.diff_hesuvi_controls(old, new) == {
        "plate_L:Dry/wet mix": 0.3, "plate_R:Dry/wet mix": 0.3,
    }


def test_diff_controls_structural_change_needs_restart(monkeypatch, tmp_path):
    # Distance to 0 drops the plate nodes: the graph changes shape.
    old = _gen_distance(monkeypatch, tmp_path, 50)
    new = _gen_distance(monkeypatch, tmp_path, 0)
    assert _s2p.diff_hesuvi_controls(old, new) is None
    # Any other line touched (here the HRIR file) is structural too.
    base = _gen(monkeypatch, tmp_path / "c")
    assert _s2p.diff_hesuvi_controls(base, base.replace("channel =  6", "channel =  7", 1)) is None


def _regen_setup(monkeypatch, tmp_path, live_ok=True):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    conf_dir = tmp_path / "confs"
    conf_dir.mkdir()
    monkeypatch.setattr(_s2p, "_CONF_DIR", conf_dir)
    monkeypatch.setattr(_s2p.device_state, "is_device_set", lambda: True)
    monkeypatch.setattr(_s2p, "ensure_hrir_materialized", lambda: False)
    monkeypatch.setattr(_s2p, "spatial_channels", lambda: ("game",))
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_hesuvi_boost_db", lambda channel: 0.0)
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: "/usr/lib/ladspa/" + name)
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: Path(path).write_text(text))
    calls = []
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, "set_filter_controls",
                        lambda node, controls: calls.append((node, controls)) or live_ok)
    _s2p.regenerate_hesuvi_if_changed()  # first write
    _spatial_json(home).write_text(json.dumps({"channel_gains": {"RR": 2.0}}))
    return calls


def test_live_apply_avoids_restart(monkeypatch, tmp_path):
    calls = _regen_setup(monkeypatch, tmp_path, live_ok=True)
    restarts = []
    monkeypatch.setattr(_s2p, "_restart_filter_chain", lambda *a, **k: restarts.append(1))
    assert _s2p.apply_spatial_audio_change() is False
    assert restarts == []
    assert calls == [("effect_input.virtual-surround-7.1-hesuvi", {"gainRR:Gain": 8.0}),
                     ("effect_input.sonar-game-surround-levels", {"lvlRR:Gain": 2.0})]
    # The conf on disk carries the new value, so the next start loads it.
    conf = (tmp_path / "confs" / _s2p._hesuvi_conf_name("game")).read_text()
    assert "Gains: " in conf and "RR=+2.0" in conf


def test_live_apply_failure_falls_back_to_restart(monkeypatch, tmp_path):
    _regen_setup(monkeypatch, tmp_path, live_ok=False)
    restarts = []
    monkeypatch.setattr(_s2p, "_restart_filter_chain", lambda *a, **k: restarts.append(1))
    monkeypatch.setattr(_s2p, "ensure_spatial_eq_links", lambda *a, **k: None)
    monkeypatch.setattr(_s2p, "ensure_physical_output_links", lambda *a, **k: None)
    assert _s2p.apply_spatial_audio_change() is True
    assert restarts == [1]


def test_apply_all_path_never_live_applies(monkeypatch, tmp_path):
    """regenerate_hesuvi (Apply-All) restarts right after: no set-param there."""
    calls = _regen_setup(monkeypatch, tmp_path)
    assert _s2p.regenerate_hesuvi_if_changed() is True
    assert calls == []


# ── Speakers mode ────────────────────────────────────────────────────────────

def _spatial_state(tmp_path, **state):
    home = tmp_path / "home"
    _spatial_json(home).write_text(json.dumps(state))
    return home


def test_speaker_mode_gains_only_when_on_in_speakers_mode(monkeypatch, tmp_path):
    home = _spatial_state(tmp_path, mode="speakers", enabled=True, channel_gains={"RL": -6.0})
    monkeypatch.setattr(Path, "home", lambda: home)
    assert _s2p._speaker_mode_gains("game")["RL"] == -6.0
    _spatial_json(home).write_text(json.dumps({"mode": "speakers", "enabled": False,
                                               "channel_gains": {"RL": -6.0}}))
    assert _s2p._speaker_mode_gains("game") is None
    _spatial_json(home).write_text(json.dumps({"mode": "headphones", "enabled": True,
                                               "channel_gains": {"RL": -6.0}}))
    assert _s2p._speaker_mode_gains("game") is None
    _spatial_json(home).write_text(json.dumps({"mode": "speakers", "enabled": True}))
    assert _s2p._speaker_mode_gains("game") is None


def test_downmix_scales_fold_coefficients_by_speaker_level():
    plain = _s2p._downmix_module("game", "alsa_output.x")
    assert '"Gain 1" = 1.0  "Gain 2" = 0.7071  "Gain 3" = 0.7071  "Gain 4" = 0.7071' in plain
    assert _s2p._downmix_module("game", "alsa_output.x", {}) == plain
    scaled = _s2p._downmix_module("game", "alsa_output.x", {"FC": -6.0, "FL": 6.0})
    # FL 1.0 * 10^(6/20) = 1.9953 ; FC 0.7071 * 10^(-6/20) = 0.3544
    assert '"Gain 1" = 1.9953  "Gain 2" = 0.3544' in scaled
    assert _s2p.diff_hesuvi_controls(plain, scaled) == {
        "dmixL:Gain 1": 1.9953, "dmixL:Gain 2": 0.3544, "dmixR:Gain 2": 0.3544,
    }


def test_speakers_mode_routes_to_downmix(monkeypatch):
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, "node_input_channel_count", lambda name, data=None: 2)
    monkeypatch.setattr(pw, "pw_node_exists", lambda name, data=None: True)
    monkeypatch.setattr(pw, "sink_has_multichannel_stream", lambda *a, **k: True)
    monkeypatch.setattr(_s2p, "spatial_channels", lambda: ("game",))
    monkeypatch.setattr(_s2p, "channel_destination", lambda ch, data=None: "alsa_output.x")
    monkeypatch.setattr(_s2p, "_spatial_enabled", lambda ch: True)
    monkeypatch.setattr(_s2p, "_spatial_auto", lambda ch: False)
    links = []
    monkeypatch.setattr(pw, "ensure_loopback_link",
                        lambda src, dst, data=None: links.append(dst) or True)
    monkeypatch.setattr(_s2p, "_spatial_mode", lambda ch: "speakers")
    _s2p.ensure_spatial_eq_links(("game",), data=[])
    monkeypatch.setattr(_s2p, "_spatial_mode", lambda ch: "headphones")
    _s2p.ensure_spatial_eq_links(("game",), data=[])
    assert links == ["effect_input.sonar-game-downmix", "effect_input.virtual-surround-7.1-hesuvi"]


def test_speakers_drift_follows_mode_and_switch(monkeypatch, tmp_path):
    home = _spatial_state(tmp_path, mode="speakers", enabled=True, channel_gains={"RR": 3.0})
    monkeypatch.setattr(Path, "home", lambda: home)
    plain = "# HeSuVi 7.1 Virtual Surround  |  Immersion: 50%  |  Distance: 50%  |  Gains: x\n"
    marked = plain.rstrip("\n") + "  |  Speakers  |  Levels\n"
    assert _s2p._hesuvi_conf_has_speakers_drift(plain, "game") is True
    assert _s2p._hesuvi_conf_has_speakers_drift(marked, "game") is False
    _spatial_json(home).write_text(json.dumps({"mode": "headphones", "channel_gains": {"RR": 3.0}}))
    assert _s2p._hesuvi_conf_has_speakers_drift(marked, "game") is True
    assert _s2p._hesuvi_conf_has_speakers_drift(plain.rstrip("\n") + "  |  Levels\n", "game") is False


def test_live_apply_sends_downmix_controls_to_downmix_node(monkeypatch):
    calls = []
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, "set_filter_controls", lambda node, ctl: calls.append((node, ctl)) or True)
    plain = _s2p._downmix_module("game", "alsa_output.x")
    scaled = _s2p._downmix_module("game", "alsa_output.x", {"FR": -6.0})
    assert _s2p._live_apply_hesuvi("game", plain, scaled) is True
    assert calls == [("effect_input.sonar-game-downmix", {"dmixR:Gain 1": 0.5012})]


# ── Speaker levels on a 5.1/7.1 output ───────────────────────────────────────

def test_levels_module_shape_and_live_diff():
    zero = _s2p._levels_module("game", "alsa_output.hdmi")
    assert 'node.name      = "effect_input.sonar-game-surround-levels"' in zero
    assert 'node.target        = "alsa_output.hdmi"' in zero
    assert "name = lvlFC  label = bq_highshelf  control = { Freq = 10  Q = 0.7071  Gain = 0.0 }" in zero
    some = _s2p._levels_module("game", "alsa_output.hdmi", {"RL": -6.0, "FC": 3.0})
    assert _s2p.diff_hesuvi_controls(zero, some) == {"lvlFC:Gain": 3.0, "lvlRL:Gain": -6.0}


def test_multichannel_output_goes_through_levels_only_when_set(monkeypatch):
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, "node_input_channel_count", lambda name, data=None: 8)
    monkeypatch.setattr(pw, "pw_node_exists", lambda name, data=None: True)
    monkeypatch.setattr(_s2p, "_passthrough_level_gains", lambda ch: {"RL": -6.0})
    assert _s2p._spatial_eq_target("game", True, "alsa_output.hdmi", []) == (
        "effect_input.sonar-game-surround-levels", False)
    monkeypatch.setattr(_s2p, "_passthrough_level_gains", lambda ch: None)
    assert _s2p._spatial_eq_target("game", True, "alsa_output.hdmi", []) == ("alsa_output.hdmi", False)
    # Stage not up yet: straight out, never silence.
    monkeypatch.setattr(_s2p, "_passthrough_level_gains", lambda ch: {"RL": -6.0})
    monkeypatch.setattr(pw, "pw_node_exists", lambda name, data=None: False)
    assert _s2p._spatial_eq_target("game", True, "alsa_output.hdmi", []) == ("alsa_output.hdmi", False)


def test_passthrough_level_gains_follow_switch(monkeypatch, tmp_path):
    home = _spatial_state(tmp_path, enabled=True, channel_gains={"FC": 2.0})
    monkeypatch.setattr(Path, "home", lambda: home)
    assert _s2p._passthrough_level_gains("game")["FC"] == 2.0
    _spatial_json(home).write_text(json.dumps({"enabled": False, "channel_gains": {"FC": 2.0}}))
    assert _s2p._passthrough_level_gains("game") is None


def test_live_apply_sends_level_controls_to_levels_node(monkeypatch):
    calls = []
    import arctis_sound_manager.pw_utils as pw
    monkeypatch.setattr(pw, "set_filter_controls", lambda node, ctl: calls.append((node, ctl)) or True)
    a = _s2p._levels_module("media", "alsa_output.hdmi")
    b = _s2p._levels_module("media", "alsa_output.hdmi", {"SR": 1.5})
    assert _s2p._live_apply_hesuvi("media", a, b) is True
    assert calls == [("effect_input.sonar-media-surround-levels", {"lvlSR:Gain": 1.5})]
