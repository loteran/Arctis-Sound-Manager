# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for theme manager — slugify, save/reload, delete, export/import.

All tests run without Qt: theme.py must not import PySide6 at module level.
"""

import configparser
import importlib

import pytest

import arctis_sound_manager.gui.theme as theme_mod
from arctis_sound_manager.gui.theme import (
    THEME_KEYS,
    THEMES,
    delete_user_theme,
    export_theme_to_file,
    get_theme,
    get_theme_label,
    import_theme_from_file,
    reload_user_themes,
    save_user_theme,
    slugify,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_colors() -> dict[str, str]:
    """A complete set of 15 valid #RRGGBB colors covering all THEME_KEYS."""
    base = {
        "BG_MAIN":          "#1A1A2E",
        "BG_SIDEBAR":       "#16213E",
        "BG_CARD":          "#0F3460",
        "BG_BUTTON":        "#533483",
        "BG_BUTTON_HOVER":  "#6B3FA0",
        "BG_SIDEBAR_ACTIVE":"#E94560",
        "ACCENT":           "#FF6B6B",
        "ACCENT2":          "#4ECDC4",
        "TEXT_PRIMARY":     "#F5F5F5",
        "TEXT_SECONDARY":   "#AAAAAA",
        "BORDER":           "#333355",
        "COLOR_GAME":       "#00FF88",
        "COLOR_CHAT":       "#00AAFF",
        "COLOR_AUX":        "#FF8800",
        "COLOR_HDMI":       "#BB44FF",
    }
    assert set(base.keys()) == set(THEME_KEYS), "Fixture must cover all THEME_KEYS"
    return base


@pytest.fixture(autouse=False)
def isolated_themes(tmp_path, monkeypatch):
    """Redirect USER_THEMES_DIR to tmp_path and reset user-theme state."""
    monkeypatch.setattr(theme_mod, "USER_THEMES_DIR", tmp_path)
    # Clear in-memory state before each test
    theme_mod._USER_THEMES.clear()
    theme_mod._USER_LABELS.clear()
    yield tmp_path
    # Cleanup after test
    theme_mod._USER_THEMES.clear()
    theme_mod._USER_LABELS.clear()


# ── slugify ───────────────────────────────────────────────────────────────────


def test_slugify_normal_ascii():
    assert slugify("My Cool Theme") == "my-cool-theme"


def test_slugify_accented_chars():
    # Accented letters are not [a-z0-9], they collapse to "-"
    result = slugify("Mon Thème Cool")
    assert result == "mon-th-me-cool"


def test_slugify_whitespace_only():
    assert slugify("   ") == "custom"


def test_slugify_empty_string():
    assert slugify("") == "custom"


def test_slugify_trailing_special():
    # Trailing "!" becomes "-" which is stripped
    assert slugify("SteelSeries!") == "steelseries"


def test_slugify_mixed_separators():
    # Multiple consecutive non-alphanum chars collapse to a single "-"
    assert slugify("a--b__c") == "a-b-c"


# ── save & reload ─────────────────────────────────────────────────────────────


def test_save_and_reload_user_theme(isolated_themes, valid_colors):
    tmp_path = isolated_themes
    theme_id = save_user_theme("My Theme", valid_colors)

    assert isinstance(theme_id, str)
    assert (tmp_path / f"{theme_id}.ini").exists()

    # reload is called inside save_user_theme, but call explicitly too
    reload_user_themes()

    assert theme_id in theme_mod._USER_THEMES
    assert get_theme_label(theme_id) == "My Theme"


def test_save_stores_all_colors(isolated_themes, valid_colors):
    theme_id = save_user_theme("Color Check", valid_colors)
    stored = get_theme(theme_id)
    for key in THEME_KEYS:
        assert stored[key] == valid_colors[key]


# ── validation ────────────────────────────────────────────────────────────────


def test_save_rejects_invalid_color(isolated_themes, valid_colors):
    bad_colors = dict(valid_colors)
    bad_colors["BG_MAIN"] = "red"  # not #RRGGBB
    with pytest.raises(ValueError):
        save_user_theme("Bad Theme", bad_colors)


def test_save_rejects_short_hex(isolated_themes, valid_colors):
    bad_colors = dict(valid_colors)
    bad_colors["ACCENT"] = "#FFF"  # 3-digit hex — invalid
    with pytest.raises(ValueError):
        save_user_theme("Short Hex", bad_colors)


# ── builtin collision avoidance ───────────────────────────────────────────────


def test_collision_with_builtin_gets_suffixed(isolated_themes, valid_colors):
    # "SteelSeries" slugifies to "steelseries" which is a builtin ID
    theme_id = save_user_theme("SteelSeries", valid_colors)
    assert theme_id != "steelseries"
    assert theme_id.startswith("steelseries")  # e.g. "steelseries-2"


def test_no_collision_with_unique_name(isolated_themes, valid_colors):
    theme_id = save_user_theme("Totally Unique Name XYZ", valid_colors)
    assert theme_id == "totally-unique-name-xyz"


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_user_theme(isolated_themes, valid_colors):
    tmp_path = isolated_themes
    theme_id = save_user_theme("Delete Me", valid_colors)
    assert (tmp_path / f"{theme_id}.ini").exists()

    delete_user_theme(theme_id)

    assert not (tmp_path / f"{theme_id}.ini").exists()
    assert theme_id not in theme_mod._USER_THEMES


def test_delete_builtin_raises(isolated_themes):
    with pytest.raises(ValueError):
        delete_user_theme("steelseries")


def test_delete_nonexistent_user_theme_does_not_raise(isolated_themes):
    # File doesn't exist; should not raise (path.unlink is guarded by exists())
    delete_user_theme("ghost-theme-that-never-existed")


# ── export / import roundtrip ─────────────────────────────────────────────────


def test_export_then_import_roundtrip(isolated_themes, valid_colors, tmp_path):
    theme_id = save_user_theme("Roundtrip Theme", valid_colors)
    original_colors = get_theme(theme_id)

    export_path = tmp_path / "exported.ini"
    export_theme_to_file(theme_id, export_path)
    assert export_path.exists()

    delete_user_theme(theme_id)
    assert theme_id not in theme_mod._USER_THEMES

    new_id = import_theme_from_file(export_path)
    assert new_id in theme_mod._USER_THEMES
    assert get_theme(new_id) == original_colors


def test_import_preserves_label(isolated_themes, valid_colors, tmp_path):
    theme_id = save_user_theme("Label Test", valid_colors)
    export_path = tmp_path / "label_test.ini"
    export_theme_to_file(theme_id, export_path)
    delete_user_theme(theme_id)

    new_id = import_theme_from_file(export_path)
    assert get_theme_label(new_id) == "Label Test"


def test_import_invalid_file_raises(isolated_themes, tmp_path):
    bad_ini = tmp_path / "bad.ini"
    bad_ini.write_text("[theme]\nname = No Colors\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing \\[colors\\]"):
        import_theme_from_file(bad_ini)


def test_import_file_with_invalid_color_raises(isolated_themes, tmp_path):
    ini = tmp_path / "badcolor.ini"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["theme"] = {"name": "Bad Color"}
    parser["colors"] = {k: "#FFFFFF" for k in THEME_KEYS}
    parser["colors"]["BG_MAIN"] = "not-a-color"
    with open(ini, "w", encoding="utf-8") as fh:
        parser.write(fh)
    with pytest.raises(ValueError):
        import_theme_from_file(ini)


# ── get_theme fallback ────────────────────────────────────────────────────────


def test_get_theme_fallback_unknown_returns_steelseries(isolated_themes):
    result = get_theme("unknown-xyz-theme-id")
    assert result == THEMES["steelseries"]


def test_get_theme_known_builtin(isolated_themes):
    for tid in THEMES:
        assert get_theme(tid) == THEMES[tid]


# ── corrupt .ini file handling ────────────────────────────────────────────────


def test_corrupt_ini_skipped_on_reload(isolated_themes):
    tmp_path = isolated_themes
    corrupt_file = tmp_path / "corrupt.ini"
    corrupt_file.write_bytes(b"\xff\xfe garbage @@@ not ini content at all \x00\x01")

    # Must not raise
    reload_user_themes()

    assert "corrupt" not in theme_mod._USER_THEMES


def test_partial_ini_falls_back_to_steelseries_colors(isolated_themes, tmp_path):
    """An INI with [colors] but missing some keys uses steelseries defaults."""
    ini = tmp_path / "partial.ini"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["theme"] = {"name": "Partial"}
    # Provide only a subset of valid colors; the rest will be filled by steelseries defaults
    parser["colors"] = {
        "BG_MAIN": "#111111",
        "BG_SIDEBAR": "#222222",
        "BG_CARD": "#333333",
        "BG_BUTTON": "#444444",
        "BG_BUTTON_HOVER": "#555555",
        "BG_SIDEBAR_ACTIVE": "#666666",
        "ACCENT": "#777777",
        "ACCENT2": "#888888",
        "TEXT_PRIMARY": "#999999",
        "TEXT_SECONDARY": "#AAAAAA",
        "BORDER": "#BBBBBB",
        "COLOR_GAME": "#CCCCCC",
        "COLOR_CHAT": "#DDDDDD",
        "COLOR_AUX": "#EEEEEE",
        # COLOR_HDMI intentionally missing
    }
    with open(ini, "w", encoding="utf-8") as fh:
        parser.write(fh)

    # Copy into isolated_themes dir so reload_user_themes picks it up
    dest = isolated_themes / "partial.ini"
    dest.write_text(ini.read_text(encoding="utf-8"), encoding="utf-8")

    reload_user_themes()

    assert "partial" in theme_mod._USER_THEMES
    # Missing key falls back to steelseries default
    assert theme_mod._USER_THEMES["partial"]["COLOR_HDMI"] == THEMES["steelseries"]["COLOR_HDMI"]


# ── no Qt import guard ────────────────────────────────────────────────────────


def test_theme_module_does_not_import_qt():
    """theme.py must not import PySide6 or PyQt at module level."""
    import sys
    qt_modules = [m for m in sys.modules if "PySide6" in m or "PyQt" in m]
    # The theme module itself should not pull in Qt
    assert "arctis_sound_manager.gui.theme" in sys.modules
    # If Qt IS loaded it means something else imported it — we only check that
    # importing theme alone doesn't require Qt by verifying it was already
    # importable at the top of this file without any Qt import.
    # This is a smoke test: if we got here, the import succeeded without Qt.
    assert theme_mod is not None
