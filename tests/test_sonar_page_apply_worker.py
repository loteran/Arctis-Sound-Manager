# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for gui.sonar_page._ApplyWorker's live-apply path (Phase 2, issue
#100/#88): a pure gain/macro/boost value change must be pushed to the running
filter-chain via pw_utils.set_filter_gain instead of restarting the service,
since a SIGTERM to filter-chain while it is processing audio SEGVs it on
PipeWire 1.6.7 (coredump, issue #100)."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arctis_sound_manager.eq_types import EqBand  # noqa: E402
from arctis_sound_manager.gui import sonar_page as sp  # noqa: E402
from arctis_sound_manager import sonar_to_pipewire as stp  # noqa: E402


def _prepare_conf_dir(monkeypatch, tmp_path):
    """Redirect both sonar_to_pipewire's _CONF_DIR and Path.home() (used by
    _ApplyWorker.run() itself to read the old conf) to the same tmp location,
    so the test never touches the real user's ~/.config."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    conf_dir = tmp_path / ".config" / "pipewire" / "filter-chain.conf.d"
    monkeypatch.setattr(stp, "_CONF_DIR", conf_dir)
    return conf_dir


def _stub_settings(monkeypatch):
    """Boost/Smart Volume disabled — keeps the generated conf deterministic
    and independent of any real ~/.config/arctis_manager state."""
    monkeypatch.setattr(sp, "_load_boost", lambda: {"enabled": False, "db": 0.0})
    monkeypatch.setattr(
        sp, "_load_smart_volume",
        lambda: {"enabled": False, "level": 0.0, "loudness": "balanced"},
    )


def test_apply_worker_gain_only_change_skips_restart(monkeypatch, tmp_path):
    """Changing only a macro slider value (basses 0.0 -> 3.0) on an unchanged
    band set must NOT call sc.restart — it must live-apply via
    pw_utils.set_filter_gain instead."""
    _prepare_conf_dir(monkeypatch, tmp_path)
    _stub_settings(monkeypatch)

    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]

    # Seed the "old" conf on disk exactly as generate_sonar_eq_conf would.
    stp.generate_sonar_eq_conf("chat", bands, 0.0, 0.0, 0.0)

    restart_calls = []
    monkeypatch.setattr(sp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)

    set_gain_calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.set_filter_gain",
        lambda node, control, value: set_gain_calls.append((node, control, value)) or True,
    )

    worker = sp._ApplyWorker("chat", bands, 3.0, 0.0, 0.0)
    results = []
    worker.done.connect(lambda ok: results.append(ok))
    worker.run()

    assert results == [True]
    assert restart_calls == [], "a pure gain change must not restart filter-chain"
    assert ("effect_input.sonar-chat-eq", "macro_basses_L:Gain", 3.0) in set_gain_calls
    assert ("effect_input.sonar-chat-eq", "macro_basses_R:Gain", 3.0) in set_gain_calls
    # Only the basses macro changed — voix/aigus/bq0/boost must not be touched.
    touched = {control.split(":")[0] for _, control, _ in set_gain_calls}
    assert touched == {"macro_basses_L", "macro_basses_R"}


def test_apply_worker_micro_gain_only_change_skips_restart(monkeypatch, tmp_path):
    """The mic channel gets the same live-apply treatment: a pure macro/gain
    change on an unchanged band set (no noise-processing toggle) must not
    restart filter-chain either."""
    _prepare_conf_dir(monkeypatch, tmp_path)
    _stub_settings(monkeypatch)

    bands = [EqBand(freq=300, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    stp.generate_sonar_micro_conf(bands, 0.0, 0.0, 0.0)

    restart_calls = []
    monkeypatch.setattr(sp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)
    set_gain_calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.set_filter_gain",
        lambda node, control, value: set_gain_calls.append((node, control, value)) or True,
    )

    worker = sp._ApplyWorker("micro", bands, 0.0, 2.0, 0.0)  # voix 0.0 -> 2.0
    results = []
    worker.done.connect(lambda ok: results.append(ok))
    worker.run()

    assert results == [True]
    assert restart_calls == []
    assert ("effect_input.sonar-micro-eq", "macro_voix:Gain", 2.0) in set_gain_calls


def test_apply_worker_structural_change_still_restarts(monkeypatch, tmp_path):
    """Adding a second band (a real structural change) must still go through
    the full sc.restart() path — diff_filter_conf correctly refuses to
    live-apply a topology change."""
    _prepare_conf_dir(monkeypatch, tmp_path)
    _stub_settings(monkeypatch)

    band_one = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]
    band_two = band_one + [EqBand(freq=2000, gain=1.0, q=0.7, type="peakingEQ", enabled=True)]

    stp.generate_sonar_eq_conf("chat", band_one, 0.0, 0.0, 0.0)

    restart_calls = []
    monkeypatch.setattr(sp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.set_filter_gain",
        lambda *a, **kw: True,
    )
    # The full restart path recreates loopbacks etc. via D-Bus — not under
    # test here; stub it out so run() doesn't need a live daemon/D-Bus.
    monkeypatch.setattr(sp._ApplyWorker, "_wait_for_node", staticmethod(lambda *a, **kw: True))
    monkeypatch.setattr(
        "arctis_sound_manager.gui.dbus_wrapper.DbusWrapper.recreate_loopback_single_sync",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.reapply_routing_overrides",
        lambda *a, **kw: 0,
    )

    worker = sp._ApplyWorker("chat", band_two, 0.0, 0.0, 0.0)
    results = []
    worker.done.connect(lambda ok: results.append(ok))
    worker.run()

    assert results == [True]
    assert len(restart_calls) == 1, "a band-count change must go through a full restart"


def test_apply_worker_spatial_toggle_skips_restart(monkeypatch, tmp_path):
    """Phase 3 (issue #100/#88): a Spatial Audio toggle produces a byte-identical
    game/media conf (channel count & static target no longer depend on the
    toggle), so _ApplyWorker's 'unchanged conf' guard must skip sc.restart and
    instead move the ASM-owned EQ→target link via ensure_spatial_eq_links."""
    _prepare_conf_dir(monkeypatch, tmp_path)
    _stub_settings(monkeypatch)

    bands = [EqBand(freq=100, gain=2.0, q=0.7, type="peakingEQ", enabled=True)]

    # Spatial ON: seed the on-disk conf.
    monkeypatch.setattr(sp, "_load_spatial_audio",
                        lambda ch="game": {"enabled": True, "immersion": 50, "distance": 50})
    stp.generate_sonar_eq_conf("game", bands, 0.0, 0.0, 0.0, spatial_audio=True)
    # HeSuVi generation must not require a real device in this unit test.
    monkeypatch.setattr(stp, "generate_hesuvi_conf", lambda **kw: "")

    restart_calls = []
    monkeypatch.setattr(sp.sc, "restart", lambda *a, **kw: restart_calls.append(a) or True)
    link_calls = []
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.ensure_spatial_eq_links",
        lambda channels, data=None: link_calls.append(tuple(channels)) or {},
    )

    # Now toggle spatial OFF and apply — the regenerated conf is byte-identical.
    monkeypatch.setattr(sp, "_load_spatial_audio",
                        lambda ch="game": {"enabled": False, "immersion": 50, "distance": 50})
    worker = sp._ApplyWorker("game", bands, 0.0, 0.0, 0.0)
    results = []
    worker.done.connect(lambda ok: results.append(ok))
    worker.run()

    assert results == [True]
    assert restart_calls == [], "a Spatial Audio toggle must not restart filter-chain"
    assert ("game",) in link_calls, "toggle must move the ASM-owned EQ→target link"
