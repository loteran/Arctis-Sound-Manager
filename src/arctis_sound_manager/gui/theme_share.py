# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
theme_share.py — Encode/decode theme deep links (no Qt dependency).

ASM deep link format:
    arctis-asm://import-theme?data=<base64url(json)>
    JSON: {"v": 1, "name": "...", "colors": {<15 THEME_KEYS>: "#rrggbb", ...}}
"""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

from arctis_sound_manager.gui.theme import THEME_KEYS

_ASM_SCHEME = "arctis-asm"
THEME_SHARE_VERSION = 1

_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


class ThemeImportError(Exception):
    pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64_decode(s: str) -> bytes:
    """Decode base64 / base64url, tolerant of missing padding."""
    s = s.strip().replace("-", "+").replace("_", "/")
    pad = (4 - len(s) % 4) % 4
    return base64.b64decode(s + "=" * pad)


def _b64url_encode(data: bytes) -> str:
    """Encode to URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _validate_colors(colors: dict) -> dict[str, str]:
    """Raise ThemeImportError unless every THEME_KEYS entry is a valid color."""
    if not isinstance(colors, dict):
        raise ThemeImportError("'colors' must be a JSON object")
    result: dict[str, str] = {}
    for key in THEME_KEYS:
        if key not in colors:
            raise ThemeImportError(f"colors missing key: {key}")
        val = colors[key]
        if not isinstance(val, str) or not _COLOR_RE.match(val):
            raise ThemeImportError(f"invalid color for {key}: {val!r}")
        result[key] = val
    return result


# ── Link detection ────────────────────────────────────────────────────────────

def is_theme_link(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme == _ASM_SCHEME and parsed.netloc == "import-theme"
    except Exception:
        return False


# ── ASM theme deep link ───────────────────────────────────────────────────────

def build_theme_link(name: str, colors: dict) -> str:
    """Build an arctis-asm://import-theme?data=... link from a theme.

    Raises ThemeImportError if `colors` is missing or has invalid THEME_KEYS.
    """
    validated = _validate_colors(colors)
    payload = {
        "v":      THEME_SHARE_VERSION,
        "name":   name,
        "colors": {k: validated[k] for k in THEME_KEYS},
    }
    encoded = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{_ASM_SCHEME}://import-theme?data={encoded}"


def decode_theme_link(url: str) -> dict:
    """Parse an arctis-asm://import-theme?data=... link.

    Returns {"name": str, "colors": {<15 THEME_KEYS>: "#rrggbb", ...}}.
    Raises ThemeImportError on any malformed input.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ThemeImportError(f"invalid URL: {e}") from e

    if parsed.scheme != _ASM_SCHEME:
        raise ThemeImportError(f"expected scheme '{_ASM_SCHEME}', got '{parsed.scheme}'")
    if parsed.netloc != "import-theme":
        raise ThemeImportError(f"expected host 'import-theme', got '{parsed.netloc}'")

    qs = parse_qs(parsed.query)
    data_b64 = qs.get("data", [None])[0]
    if not data_b64:
        raise ThemeImportError("missing 'data' query parameter")

    try:
        raw = _b64_decode(data_b64)
        payload = json.loads(raw)
    except Exception as e:
        raise ThemeImportError(f"could not decode payload: {e}") from e

    if not isinstance(payload, dict):
        raise ThemeImportError("payload must be a JSON object")
    if "name" not in payload:
        raise ThemeImportError("payload missing 'name'")
    if "colors" not in payload:
        raise ThemeImportError("payload missing 'colors'")

    colors = _validate_colors(payload["colors"])

    return {
        "name":   str(payload["name"]),
        "colors": colors,
    }
