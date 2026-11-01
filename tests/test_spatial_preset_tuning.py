"""Spatial tuning saved per preset or for all presets."""
import json
from pathlib import Path

import pytest

sp = pytest.importorskip("arctis_sound_manager.gui.sonar_page")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfg = home / ".config" / "arctis_manager"
    cfg.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(sp, "_CFG", cfg)
    monkeypatch.setattr(sp, "_PRESETS_DIR", cfg / "sonar_presets")
    monkeypatch.setattr(sp, "_list_presets", lambda ch: {
        p.name[: -len(" [Game].json")]: p
        for p in (cfg / "sonar_presets").glob("* [Game].json")
    } if (cfg / "sonar_presets").exists() else {})
    return cfg


def _tuning(imm, dist, **gains):
    return {"immersion": imm, "distance": dist, "channel_gains": gains}


def test_tuning_saved_before_levels_resets_them():
    """A common tuning saved without channel_gains still zeroes the levels."""
    store = {"default": {"immersion": 50, "distance": 50}, "presets": {}}
    assert sp._resolve_spatial_tuning(store, "game/X", None)[0]["channel_gains"] == {}


def test_resolution_order():
    store = {"default": _tuning(50, 50), "presets": {"game/A": _tuning(10, 20, FC=3.0)}}
    assert sp._resolve_spatial_tuning(store, "game/A", {"immersion": 99})[1] == "preset"
    t, src = sp._resolve_spatial_tuning(store, "game/B", {"enabled": True, "immersion": 70})
    assert src == "legacy" and t == _tuning(70, 50)
    assert sp._resolve_spatial_tuning(store, "game/B", None) == (_tuning(50, 50), "default")
    # A legacy block holding only the on/off switch carries no tuning.
    assert sp._resolve_spatial_tuning(store, "game/B", {"enabled": False})[1] == "default"
    assert sp._resolve_spatial_tuning({"default": None, "presets": {}}, "game/B", None) == (None, "none")


def test_tuning_excludes_listening_choices():
    state = {"enabled": True, "auto": True, "mode": "headphones",
             "immersion": 30, "distance": 40, "channel_gains": {"RR": 2.0}}
    assert sp._spatial_tuning(state) == _tuning(30, 40, RR=2.0)


def test_store_roundtrip_and_corrupt_file(cfg):
    assert sp._load_spatial_tuning_store("game") == {"default": None, "presets": {}}
    sp._save_spatial_tuning_store({"default": _tuning(1, 2), "presets": {"game/X": _tuning(3, 4)}}, "game")
    assert sp._load_spatial_tuning_store("game")["presets"]["game/X"] == _tuning(3, 4)
    (cfg / "sonar_spatial_tuning.json").write_text("{nope")
    assert sp._load_spatial_tuning_store("game") == {"default": None, "presets": {}}


def test_strip_preset_spatial_tuning_keeps_the_rest(cfg):
    d = cfg / "sonar_presets"
    d.mkdir()
    f = d / "Mine [Game].json"
    f.write_text(json.dumps({"parametricEQ": {}, "settings": {
        "spatial": {"enabled": True, "immersion": 70, "distance": 10, "channel_gains": {}},
        "boost": {"db": 3}}}))
    sp._strip_preset_spatial_tuning("game", "Mine")
    data = json.loads(f.read_text())
    assert data["settings"]["spatial"] == {"enabled": True}
    assert data["settings"]["boost"] == {"db": 3}


def test_apply_preset_spatial_tuning_writes_state(cfg):
    sp._save_spatial_audio({**sp._SPATIAL_DEFAULTS, "enabled": False}, "game")
    sp._save_spatial_tuning_store(
        {"default": _tuning(50, 50), "presets": {"game/CS2": _tuning(20, 0, RL=-6.0)}}, "game")
    assert sp.apply_preset_spatial_tuning("game", "CS2") is True
    state = sp._load_spatial_audio("game")
    assert state["immersion"] == 20 and state["channel_gains"] == {"RL": -6.0}
    assert state["enabled"] is False          # the switch is not part of a preset
    assert sp.apply_preset_spatial_tuning("game", "CS2") is False   # already there
    assert sp.apply_preset_spatial_tuning("game", "Other") is True  # back to common
    assert sp._load_spatial_audio("game")["immersion"] == 50
    assert sp.apply_preset_spatial_tuning("chat", "CS2") is False   # no spatial on chat


def test_widget_save_for_preset_all_and_forget(cfg, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = sp.SpatialAudioWidget("game")
    active = {"name": "CS2"}
    w.bind_presets("game", lambda: active["name"])
    # First construction made the current tuning the common one.
    assert sp._load_spatial_tuning_store("game")["default"]["immersion"] == 50

    w.set_state({"immersion": 20, "channel_gains": {"RR": 3.0}}, emit=False)
    w.save_tuning_for_preset()
    assert sp._load_spatial_tuning_store("game")["presets"]["game/CS2"]["immersion"] == 20

    # Another preset without its own gets the common tuning back.
    w.load_preset_tuning("Dota", None)
    assert w.get_state()["immersion"] == 50 and w.get_state()["channel_gains"] == {}
    w.load_preset_tuning("CS2", None)
    assert w.get_state()["immersion"] == 20

    # Forget: CS2 falls back to the common tuning.
    w.forget_preset_tuning()
    assert "game/CS2" not in sp._load_spatial_tuning_store("game")["presets"]
    assert w.get_state()["immersion"] == 50

    # Save for all: becomes the common one, per-preset entries cleared.
    w.save_tuning_for_preset()
    w.set_state({"immersion": 80}, emit=False)
    w.save_tuning_for_all()
    store = sp._load_spatial_tuning_store("game")
    assert store["default"]["immersion"] == 80 and store["presets"] == {}
    app.processEvents()
