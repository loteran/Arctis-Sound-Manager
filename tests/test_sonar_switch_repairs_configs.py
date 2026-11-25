"""Switching to Sonar must let the daemon write the Sonar-only confs.

A daemon that connected in Custom mode never wrote Media's HeSuVi chain
(sink-virtual-surround-7.1-hesuvi-media.conf): Media's EQ was linked into a
node that did not exist and Media was silent in Sonar until a replug.
"""
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager import core as core_mod
from arctis_sound_manager.core import CoreEngine


@pytest.fixture()
def engine(monkeypatch):
    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = MagicMock()
    eng._last_recreate_loopbacks = -1e9
    eng.loopback_manager = MagicMock()
    eng._snapshot_sink_streams = MagicMock(return_value={})
    eng._restore_sink_streams = MagicMock()
    eng._link_loopbacks = MagicMock()
    monkeypatch.setattr(core_mod.device_state, "is_device_set", lambda: True)
    monkeypatch.setattr(core_mod, "make_specs", lambda **_: [])
    eng._repair_sonar_configs = MagicMock()
    return eng


@pytest.mark.parametrize("sonar", [True, False])
def test_recreate_after_mode_switch_repairs_only_in_sonar(engine, sonar):
    engine._read_eq_mode_is_sonar = lambda: sonar
    engine.recreate_loopbacks_game_media()
    assert engine._repair_sonar_configs.called is sonar


def test_repair_restarts_filter_chain_only_when_something_was_written(monkeypatch):
    from arctis_sound_manager import service_control as sc
    from arctis_sound_manager import sonar_to_pipewire as stp

    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = MagicMock()
    restart = MagicMock()
    monkeypatch.setattr(sc, "restart", restart)

    monkeypatch.setattr(stp, "check_and_fix_stale_configs", lambda: (False, False))
    eng._repair_sonar_configs()
    restart.assert_not_called()

    monkeypatch.setattr(stp, "check_and_fix_stale_configs", lambda: (True, False))
    eng._repair_sonar_configs()
    restart.assert_called_once_with("filter-chain", timeout=15, force=True)


def test_forced_restart_is_not_absorbed_by_a_recent_one(monkeypatch):
    from arctis_sound_manager import service_control as sc

    monkeypatch.setattr(sc, "manager_available", lambda: True)
    monkeypatch.setattr(sc, "_should_restart_filter_chain_now", lambda: False)
    action = MagicMock(return_value=True)
    monkeypatch.setattr(sc, "_action", action)
    monkeypatch.setattr("arctis_sound_manager.pw_utils.quiesce_filter_chain", lambda: None)
    # restart() opens the router's reconfiguration window, a real file in
    # XDG_RUNTIME_DIR the live system reads.
    monkeypatch.setattr("arctis_sound_manager.audio_reconfig.begin", lambda *a, **k: None)

    sc.restart("filter-chain")
    action.assert_not_called()

    sc.restart("filter-chain", force=True)
    action.assert_called_once()


@pytest.mark.parametrize("restarted", [True, False])
def test_spatial_toggle_moves_the_link_in_the_daemon(monkeypatch, restarted):
    """The GUI cannot resolve the stereo-downmix target (no device in its
    process), so turning Spatial off only took effect on the next watchdog
    tick. The daemon relinks right away when no restart covers it."""
    import threading

    from arctis_sound_manager import sonar_to_pipewire as stp

    eng = CoreEngine.__new__(CoreEngine)
    eng.logger = MagicMock()
    eng._spatial_apply_lock = threading.Lock()
    monkeypatch.setattr(core_mod.device_state, "is_device_set", lambda: True)
    monkeypatch.setattr(stp, "apply_spatial_audio_change", lambda: restarted)
    relink = MagicMock(return_value={})
    monkeypatch.setattr(stp, "ensure_spatial_eq_links", relink)

    eng.apply_spatial_audio()
    assert relink.called is not restarted
