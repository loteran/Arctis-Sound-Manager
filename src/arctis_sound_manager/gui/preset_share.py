# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
preset_share.py — Encode/decode EQ preset deep links (no Qt dependency).

ASM deep link format:
    arctis-asm://import?data=<base64url(json)>
    JSON: {"name": "...", "virtualAudioDevice": "game", "data": {<preset>}}

SteelSeries deep link format:
    https://www.steelseries.com/deeplink/gg/sonar/config/v1/import?url=<base64(cdn_url)>
    CDN returns: {"data": {"data": {<preset>}, "schemaVersion": N,
                           "virtualAudioDevice": "game"},
                  "metadata": {"name": "...", "author": "..."}}
"""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import parse_qs, urlparse

_ASM_SCHEME = "arctis-asm"
_SS_DEEPLINK_HOST = "www.steelseries.com"
_SS_DEEPLINK_PATH = "/deeplink/gg/sonar/config/v1/import"
_SS_CDN_HOST = "community-configs.steelseriescdn.com"

_DEVICE_TO_TAG: dict[str, str] = {
    "game":   "[Game]",
    "media":  "[Game]",
    "output": "[Game]",
    "chat":   "[Chat]",
    "micro":  "[Mic]",
    "mic":    "[Mic]",
}

_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


class PresetImportError(Exception):
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


def _validate_preset_data(data: dict) -> None:
    """Raise PresetImportError if the preset payload looks invalid."""
    if not isinstance(data, dict):
        raise PresetImportError("preset data must be a JSON object")
    eq = data.get("parametricEQ")
    if not eq or not isinstance(eq, dict):
        raise PresetImportError("preset is missing parametricEQ")
    if "filter1" not in eq:
        raise PresetImportError("parametricEQ must contain at least filter1")


def sanitize_filename(name: str) -> str:
    """Remove filesystem-unsafe characters from a preset name."""
    name = _UNSAFE_CHARS.sub("", name).strip()
    return name or "Imported Preset"


def virtual_device_to_tag(device: str) -> str:
    return _DEVICE_TO_TAG.get(device.lower(), "[Game]")


# ── Link detection ────────────────────────────────────────────────────────────

def is_asm_link(url: str) -> bool:
    try:
        return urlparse(url).scheme == _ASM_SCHEME
    except Exception:
        return False


def is_steelseries_link(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("https", "http") and p.hostname == _SS_DEEPLINK_HOST
    except Exception:
        return False


# ── ASM deep link ─────────────────────────────────────────────────────────────

def decode_asm_link(url: str) -> dict:
    """Parse an arctis-asm://import?data=... link.

    Returns {"name": str, "virtualAudioDevice": str, "data": dict}.
    Raises PresetImportError on any malformed input.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise PresetImportError(f"invalid URL: {e}") from e

    if parsed.scheme != _ASM_SCHEME:
        raise PresetImportError(f"expected scheme '{_ASM_SCHEME}', got '{parsed.scheme}'")

    qs = parse_qs(parsed.query)
    data_b64 = qs.get("data", [None])[0]
    if not data_b64:
        raise PresetImportError("missing 'data' query parameter")

    try:
        raw = _b64_decode(data_b64)
        payload = json.loads(raw)
    except Exception as e:
        raise PresetImportError(f"could not decode payload: {e}") from e

    if not isinstance(payload, dict):
        raise PresetImportError("payload must be a JSON object")
    if "name" not in payload:
        raise PresetImportError("payload missing 'name'")
    if "data" not in payload:
        raise PresetImportError("payload missing 'data'")

    _validate_preset_data(payload["data"])

    return {
        "name":               str(payload["name"]),
        "virtualAudioDevice": str(payload.get("virtualAudioDevice", "game")),
        "data":               payload["data"],
    }


def build_asm_link(name: str, virtual_audio_device: str, preset_data: dict) -> str:
    """Build an arctis-asm://import?data=... link from a preset."""
    payload = {
        "name":               name,
        "virtualAudioDevice": virtual_audio_device,
        "data":               preset_data,
    }
    encoded = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{_ASM_SCHEME}://import?data={encoded}"


# ── SteelSeries deep link ─────────────────────────────────────────────────────

def decode_steelseries_link(url: str) -> str:
    """Decode a SteelSeries deep link and return the CDN URL to fetch.

    Raises PresetImportError on invalid input.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise PresetImportError(f"invalid URL: {e}") from e

    qs = parse_qs(parsed.query)
    url_b64 = qs.get("url", [None])[0]
    if not url_b64:
        raise PresetImportError("missing 'url' query parameter in SteelSeries link")

    try:
        cdn_url = _b64_decode(url_b64).decode()
    except Exception as e:
        raise PresetImportError(f"could not decode CDN URL: {e}") from e

    if not cdn_url.startswith("https://"):
        raise PresetImportError(f"decoded CDN URL does not start with https://: {cdn_url!r}")

    return cdn_url


def parse_steelseries_cdn_payload(payload: dict) -> dict:
    """Extract preset info from a SteelSeries CDN JSON response.

    Returns {"name": str, "virtualAudioDevice": str, "data": dict}.
    Raises PresetImportError on missing/invalid fields.
    """
    if not isinstance(payload, dict):
        raise PresetImportError("CDN response must be a JSON object")

    outer = payload.get("data")
    if not isinstance(outer, dict):
        raise PresetImportError("CDN response missing 'data' object")

    preset_data = outer.get("data")
    if not isinstance(preset_data, dict):
        raise PresetImportError("CDN response missing 'data.data' preset object")

    metadata = payload.get("metadata", {})
    name = metadata.get("name") or "Imported Preset"
    virtual_device = outer.get("virtualAudioDevice", "game")

    _validate_preset_data(preset_data)

    return {
        "name":               str(name),
        "virtualAudioDevice": str(virtual_device),
        "data":               preset_data,
    }
