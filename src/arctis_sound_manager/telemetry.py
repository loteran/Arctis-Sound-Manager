"""
Telemetry — anonymous usage stats (distro + headset + version).

Consent is stored in ~/.config/arctis_manager/telemetry.yaml.
  consent: true | false | null   (null = never asked)
  last_sent: ISO date string

Data sent (POST JSON):
  { "distro": "...", "headset": "...", "version": "..." }

No personal data, no IP stored server-side.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

log = logging.getLogger(__name__)

_TELEMETRY_FILE = Path.home() / ".config" / "arctis_manager" / "telemetry.yaml"

# Replace with your actual Cloudflare Worker URL after deployment
TELEMETRY_ENDPOINT = "https://asm-telemetry.arctis-asm.workers.dev/collect"

_yaml = YAML(typ="safe")


# ── Consent helpers ────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        if _TELEMETRY_FILE.exists():
            data = _yaml.load(_TELEMETRY_FILE)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    try:
        _TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _yaml.dump(data, _TELEMETRY_FILE)
    except Exception as exc:
        log.debug("telemetry: could not save state: %s", exc)


def get_consent() -> bool | None:
    """Return True / False / None (never asked)."""
    data = _load()
    v = data.get("consent")
    if v is None:
        return None
    return bool(v)


def set_consent(value: bool) -> None:
    data = _load()
    data["consent"] = value
    _save(data)


# ── System info ────────────────────────────────────────────────────────────────

def _get_distro() -> str:
    """Return a human-readable distro name + version, e.g. 'Ubuntu 24.04.2 LTS'."""
    # Parse /etc/os-release — most reliable source
    fields: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                fields[k.strip()] = v.strip().strip('"')
    except Exception:
        pass

    if fields.get("PRETTY_NAME"):
        return fields["PRETTY_NAME"]          # e.g. "Ubuntu 24.04.2 LTS"

    # Build from NAME + VERSION if PRETTY_NAME is missing
    name = fields.get("NAME", "")             # e.g. "Ubuntu"
    version = fields.get("VERSION") or fields.get("VERSION_ID", "")  # e.g. "24.04.2 LTS"
    if name and version:
        return f"{name} {version}"
    if name:
        return name

    return "Unknown"


# ── Send logic ─────────────────────────────────────────────────────────────────

def _do_send(headset: str) -> None:
    """Blocking send — must be called in a background thread."""
    from arctis_sound_manager.utils import project_version

    payload = json.dumps({
        "distro":  _get_distro(),
        "headset": headset or "Unknown",
        "version": project_version(),
    }).encode()

    req = urllib.request.Request(
        TELEMETRY_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5):
        pass

    # Record today's date so we don't resend until tomorrow
    data = _load()
    data["last_sent"] = date.today().isoformat()
    _save(data)
    log.debug("telemetry: sent (headset=%s)", headset)


def maybe_send(headset: str) -> None:
    """
    Fire-and-forget telemetry send.

    Silently skipped if:
    - consent is False or None (never asked)
    - already sent today
    - endpoint unreachable
    """
    if get_consent() is not True:
        return

    # Rate-limit: once per day
    data = _load()
    last = data.get("last_sent")
    if last:
        try:
            if date.fromisoformat(str(last)) >= date.today():
                return
        except ValueError:
            pass

    def _worker():
        try:
            _do_send(headset)
        except Exception as exc:
            log.debug("telemetry: send failed (non-blocking): %s", exc)

    threading.Thread(target=_worker, daemon=True, name="asm-telemetry").start()
