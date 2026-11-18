# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#391: a "/" in a preset name was read as a folder and saving crashed."""
from __future__ import annotations

from types import SimpleNamespace

import arctis_sound_manager.gui.sonar_page as sp


def test_preset_with_slashes_saves_finds_and_deletes(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "_PRESETS_DIR", tmp_path)
    band = SimpleNamespace(enabled=True, q=0.7, freq=100.0, gain=1.0, type="peakingEQ")
    path = sp._save_custom_preset("game", "Content/Media/Talking", [band])
    assert path.parent == tmp_path and path.is_file()
    assert sp._is_custom_preset("game", "Content/Media/Talking")
    assert sp._delete_custom_preset("game", "Content/Media/Talking")
    assert not path.exists()
