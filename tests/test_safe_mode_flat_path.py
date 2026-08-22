# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #203 knock-on: safe mode promised a flat path and delivered silence.

_enter_filter_chain_safe_mode() moves the ASM confs out of
filter-chain.conf.d/ to break a filter-chain crash-loop, and logs "Audio will
be flat but stable". But the loopback targets are chosen by make_specs() from
_read_eq_mode_is_sonar(), which only ever read ~/.config/arctis_manager/.eq_mode
— so with Sonar mode stored, every loopback kept pointing at
effect_input.sonar-<channel>-eq, a node safe mode had just removed. Unlinkable
loopbacks, no sound, from the mechanism meant to guarantee sound.

Safe mode now overrides the stored mode while it is armed. The stored choice is
not rewritten: Sonar comes back on its own when safe mode is cleared.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from arctis_sound_manager.core import CoreEngine


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = tmp_path / ".config" / "arctis_manager"
    cfg.mkdir(parents=True)
    (cfg / ".eq_mode").write_text("sonar")
    return cfg


def _armed(monkeypatch, value: bool):
    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.is_filter_chain_safe_mode_armed",
        lambda: value,
    )


def test_sonar_mode_is_honoured_normally(home, monkeypatch):
    _armed(monkeypatch, False)
    assert CoreEngine._read_eq_mode_is_sonar() is True


def test_safe_mode_forces_the_flat_path(home, monkeypatch):
    """The Sonar EQ nodes do not exist while safe mode is armed, so aiming the
    loopbacks at them is aiming them at nothing."""
    _armed(monkeypatch, True)
    assert CoreEngine._read_eq_mode_is_sonar() is False


def test_the_stored_choice_is_not_rewritten(home, monkeypatch):
    """A crash-loop must not silently cost the user their EQ mode."""
    _armed(monkeypatch, True)
    CoreEngine._read_eq_mode_is_sonar()
    assert (home / ".eq_mode").read_text() == "sonar"


def test_sonar_returns_once_safe_mode_clears(home, monkeypatch):
    _armed(monkeypatch, True)
    assert CoreEngine._read_eq_mode_is_sonar() is False
    _armed(monkeypatch, False)
    assert CoreEngine._read_eq_mode_is_sonar() is True


def test_a_broken_safe_mode_check_never_blocks_startup(home, monkeypatch):
    """If the check itself raises, the stored mode must still be readable."""
    def _boom():
        raise RuntimeError("marker unreadable")

    monkeypatch.setattr(
        "arctis_sound_manager.sonar_to_pipewire.is_filter_chain_safe_mode_armed",
        _boom,
    )
    assert CoreEngine._read_eq_mode_is_sonar() is True
