# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""The language menu offers any language with at least 10 % of en.ini's keys.

At 40 % only German and Turkish were listed, and German was one key away from
dropping out at the next release: every key added to en.ini raises the bar.
Missing keys fall back to English, so a partial translation is still useful.
"""
from __future__ import annotations

from arctis_sound_manager import i18n
from arctis_sound_manager.i18n import I18n


def _write(path, n_keys: int) -> None:
    path.write_text("[ui]\n" + "".join(f"k{i} = v{i}\n" for i in range(n_keys)),
                    encoding="utf-8")


def test_threshold_is_ten_percent(tmp_path, monkeypatch):
    _write(tmp_path / "en.ini", 100)
    _write(tmp_path / "fr.ini", 10)   # exactly 10 %: listed
    _write(tmp_path / "de.ini", 9)    # below: not listed
    monkeypatch.setattr(i18n, "_BUILTIN_LANG_DIR", tmp_path)
    monkeypatch.setattr(i18n, "HOME_LANG_FOLDER", tmp_path / "none")
    codes = [code for code, _ in I18n.available_languages()]
    assert "fr" in codes
    assert "de" not in codes
    assert "en" in codes
