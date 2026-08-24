# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for theme_share — build/decode deep links for community theme sharing.

All tests run without Qt: theme_share.py must not import PySide6 at module level.
"""

import base64
import json

import pytest

from arctis_sound_manager.gui.theme import THEME_KEYS
from arctis_sound_manager.gui.theme_share import (
    ThemeImportError,
    build_theme_link,
    decode_theme_link,
    is_theme_link,
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
        "BG_SIDEBAR_ACTIVE": "#241640",
        "ACCENT":           "#E94560",
        "ACCENT2":          "#00D4FF",
        "TEXT_PRIMARY":     "#F0F0F0",
        "TEXT_SECONDARY":   "#A0A0C0",
        "BORDER":           "#2A2A50",
        "COLOR_GAME":       "#04C5A8",
        "COLOR_CHAT":       "#2791CE",
        "COLOR_AUX":        "#FB4A00",
        "COLOR_AUX2":       "#E4B53C",
        "COLOR_HDMI":       "#9B59B6",
    }
    assert set(base) == set(THEME_KEYS)
    return base


# ── Round-trip ────────────────────────────────────────────────────────────────


def test_round_trip_ascii_name(valid_colors):
    link = build_theme_link("My Custom Theme", valid_colors)
    parsed = decode_theme_link(link)
    assert parsed["name"] == "My Custom Theme"
    assert parsed["colors"] == valid_colors


def test_round_trip_unicode_name(valid_colors):
    name = "Thème Néon 音楽"
    link = build_theme_link(name, valid_colors)
    parsed = decode_theme_link(link)
    assert parsed["name"] == name
    assert parsed["colors"] == valid_colors


def test_round_trip_3digit_hex(valid_colors):
    colors = dict(valid_colors)
    colors["ACCENT"] = "#f00"
    colors["ACCENT2"] = "#0A3"
    link = build_theme_link("Short Hex", colors)
    parsed = decode_theme_link(link)
    assert parsed["colors"]["ACCENT"] == "#f00"
    assert parsed["colors"]["ACCENT2"] == "#0A3"


def test_link_format(valid_colors):
    link = build_theme_link("Foo", valid_colors)
    assert link.startswith("arctis-asm://import-theme?data=")
    # No padding characters in the base64url payload.
    data = link.split("data=", 1)[1]
    assert "=" not in data
    assert "+" not in data
    assert "/" not in data

    pad = (4 - len(data) % 4) % 4
    raw = base64.urlsafe_b64decode(data + "=" * pad)
    payload = json.loads(raw)
    assert payload["v"] == 1
    assert payload["name"] == "Foo"
    assert set(payload["colors"]) == set(THEME_KEYS)


# ── is_theme_link ─────────────────────────────────────────────────────────────


def test_is_theme_link_true(valid_colors):
    link = build_theme_link("Foo", valid_colors)
    assert is_theme_link(link) is True


@pytest.mark.parametrize("url", [
    "arctis-asm://import?data=xxx",           # preset link, wrong netloc
    "https://example.com/import-theme",       # wrong scheme
    "not-a-url",
    "",
])
def test_is_theme_link_false(url):
    assert is_theme_link(url) is False


# ── Rejections ────────────────────────────────────────────────────────────────


def test_decode_rejects_wrong_scheme():
    with pytest.raises(ThemeImportError):
        decode_theme_link("https://import-theme?data=xxx")


def test_decode_rejects_wrong_netloc():
    with pytest.raises(ThemeImportError):
        decode_theme_link("arctis-asm://import?data=xxx")


def test_decode_rejects_missing_data():
    with pytest.raises(ThemeImportError):
        decode_theme_link("arctis-asm://import-theme")


def test_build_rejects_missing_key(valid_colors):
    colors = dict(valid_colors)
    del colors["ACCENT"]
    with pytest.raises(ThemeImportError):
        build_theme_link("Missing Key", colors)


def test_decode_rejects_missing_key(valid_colors):
    colors = dict(valid_colors)
    del colors["ACCENT"]
    payload = {"v": 1, "name": "Incomplete", "colors": colors}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    url = f"arctis-asm://import-theme?data={encoded}"
    with pytest.raises(ThemeImportError):
        decode_theme_link(url)


def test_build_rejects_invalid_color(valid_colors):
    colors = dict(valid_colors)
    colors["ACCENT"] = "not-a-color"
    with pytest.raises(ThemeImportError):
        build_theme_link("Bad Color", colors)


def test_decode_rejects_invalid_color(valid_colors):
    colors = dict(valid_colors)
    colors["ACCENT"] = "not-a-color"
    payload = {"v": 1, "name": "Bad Color", "colors": colors}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    url = f"arctis-asm://import-theme?data={encoded}"
    with pytest.raises(ThemeImportError):
        decode_theme_link(url)


def test_decode_rejects_malformed_base64():
    with pytest.raises(ThemeImportError):
        decode_theme_link("arctis-asm://import-theme?data=***not-base64***")


def test_decode_rejects_missing_name(valid_colors):
    payload = {"v": 1, "colors": valid_colors}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    url = f"arctis-asm://import-theme?data={encoded}"
    with pytest.raises(ThemeImportError):
        decode_theme_link(url)


# ── colours added after the format shipped (#209) ────────────────────────────


def _link_without(*omit: str) -> str:
    """A share link carrying every key except *omit*."""
    import base64, json
    from arctis_sound_manager.gui.theme import THEMES, THEME_KEYS

    colors = {k: THEMES["aurora"][k] for k in THEME_KEYS if k not in omit}
    blob = json.dumps({"v": 1, "name": "Older theme", "colors": colors})
    data = base64.urlsafe_b64encode(blob.encode()).rstrip(b"=").decode()
    return "arctis-asm://import-theme?data=" + data


def test_a_link_made_before_a_colour_existed_still_imports():
    """The reason the Aux channel's colour sat outside the share format until
    now: adding a key to THEME_KEYS rejected every link already in circulation,
    because they cannot carry a colour that did not exist when they were made."""
    from arctis_sound_manager.gui.theme_share import decode_theme_link

    out = decode_theme_link(_link_without("COLOR_AUX2"))

    assert out["name"] == "Older theme"
    assert out["colors"]["COLOR_AUX2"], "the missing colour must be filled in"


def test_the_filled_colour_comes_from_the_default_palette():
    from arctis_sound_manager.gui.theme import THEMES
    from arctis_sound_manager.gui.theme_share import decode_theme_link

    out = decode_theme_link(_link_without("COLOR_AUX2"))

    assert out["colors"]["COLOR_AUX2"] == THEMES["steelseries"]["COLOR_AUX2"]


def test_a_truncated_link_is_still_refused():
    """Tolerance is for colours added later, not for a corrupt payload: a link
    missing an original key would paint half a theme."""
    import pytest
    from arctis_sound_manager.gui.theme_share import ThemeImportError, decode_theme_link

    with pytest.raises(ThemeImportError):
        decode_theme_link(_link_without("BG_MAIN"))


def test_every_optional_key_is_a_real_key():
    """A typo here would silently make a required colour optional."""
    from arctis_sound_manager.gui.theme import THEME_KEYS, THEME_KEYS_OPTIONAL

    assert THEME_KEYS_OPTIONAL <= set(THEME_KEYS)
