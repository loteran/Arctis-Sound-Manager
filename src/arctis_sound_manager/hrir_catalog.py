# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
import functools
from pathlib import Path

_HRIR_DIR = Path(__file__).parent / "hrir_assets"

_GROUPS: list[tuple[str, list[str]]] = [
    ("Dolby", ["atmos", "atmos-", "dh-", "dh+", "dh++", "dht", "dht-", "dvs", "dvs+"]),
    ("CMSS-3D", ["cmss_game", "cmss_game-", "cmss_ent", "cmss_ent-", "cmss_rx", "cmss_rx+"]),
    ("SBX Pro Studio", ["sbx100", "sbx100-", "sbx67", "sbx67-", "sbx33", "sbx33-"]),
    ("Sennheiser GSX", ["gsx", "gsx-", "gsx+", "gsx++"]),
    ("DTS Headphone:X", ["dtshx", "dtshx-"]),
    ("Windows Sonic", ["sonic", "sonic-", "sonic+"]),
    ("Razer Surround", ["razer", "razer_fix"]),
    ("Out Of Your Head", ["ooyh0", "ooyh1"]),
    ("Waves NX", ["waves", "waves-"]),
    ("Flux HEar", ["hear"]),
    ("OpenAL / DirectSound3D", ["oal_dflt", "oal_cia0", "oal_cia1", "oal+", "oal++", "oal+++", "ds3d", "ds3d+", "ds3d++", "ds3d+++"]),
    ("Nahimic", ["nahimic", "nahimic-"]),
    ("Spatial Sound Card", ["ssc_dub", "ssc_hu", "ssc_hu+", "ssc_ny", "ssc_ny+", "ssc_syd", "ssc_syd+"]),
    ("None", ["none"]),
]


# Convolution cost scales with the length of the impulse response, and this
# catalog spans roughly 1 KB to 918 KB — a wide enough range that the choice
# materially changes CPU load on the surround chain (14 convolvers per sink).
# The reporter in #183 hit xruns with a ~288 KB profile, just above the 262 KB
# median, so "large" here is not a theoretical concern: about half the catalog
# sits in the range that can glitch on a busy machine.
#
# Thresholds are deliberately coarse. The point is to make an invisible
# trade-off visible when picking, not to imply precision we don't have — actual
# cost depends on the machine, the quantum and what else is running.
_HRIR_COST_MEDIUM_BYTES = 128 * 1024
_HRIR_COST_HIGH_BYTES = 512 * 1024


def hrir_cpu_cost(hrir_id: str) -> str | None:
    """Rough CPU cost of convolving with this HRIR: 'low' | 'medium' | 'high'.

    Returns None when the file is missing or unreadable — callers show nothing
    rather than guessing, since a wrong hint is worse than no hint.
    """
    try:
        size = (_HRIR_DIR / f"{hrir_id}.wav").stat().st_size
    except OSError:
        return None
    if size >= _HRIR_COST_HIGH_BYTES:
        return "high"
    if size >= _HRIR_COST_MEDIUM_BYTES:
        return "medium"
    return "low"


@functools.lru_cache(maxsize=None)
def _parse_csv() -> dict[str, str]:
    result: dict[str, str] = {}
    csv_path = _HRIR_DIR / "info.csv"
    if not csv_path.exists():
        return result
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        for row in reader:
            if len(row) < 2 or row[0].startswith("*"):
                continue
            hrir_id = row[0].strip()
            description = row[1].strip()
            if (_HRIR_DIR / f"{hrir_id}.wav").exists():
                result[hrir_id] = description
    return result


def list_hrir_options_grouped() -> list[dict]:
    """Return options with group info for grouped QComboBox display."""
    catalog = _parse_csv()
    result: list[dict] = []
    seen: set[str] = set()
    for group_name, ids in _GROUPS:
        for hrir_id in ids:
            if hrir_id in catalog:
                result.append({"id": hrir_id, "name": catalog[hrir_id], "group": group_name,
                               "cpu_cost": hrir_cpu_cost(hrir_id)})
                seen.add(hrir_id)
    for hrir_id, desc in catalog.items():
        if hrir_id not in seen:
            result.append({"id": hrir_id, "name": desc, "group": "Other",
                           "cpu_cost": hrir_cpu_cost(hrir_id)})
    return result


def list_hrir_options() -> list[dict]:
    """Flat list for D-Bus GetListOptions."""
    return [{"id": o["id"], "name": o["name"], "cpu_cost": o.get("cpu_cost")}
            for o in list_hrir_options_grouped()]


def is_valid_hrir_id(hrir_id: str) -> bool:
    """True if *hrir_id* is a real entry in the bundled catalogue.

    ``list_hrir_options()`` only ever lists ids matched against an actual
    ``<id>.wav`` file already sitting in ``_HRIR_DIR``, so this doubles as
    the boundary check for any hrir_id coming from outside the process — a
    D-Bus call or a hand-edited/restored settings file (CHA-12).
    """
    if not isinstance(hrir_id, str):
        return False
    return hrir_id in {o["id"] for o in list_hrir_options()}


def package_hrir_path(hrir_id: str) -> Path | None:
    """Return absolute path to a bundled WAV, or None if not found.

    Only ids present in the catalogue are accepted (see is_valid_hrir_id).
    Building the path straight from hrir_id used to let a value like
    "../../../../../../tmp/x/sine" escape _HRIR_DIR entirely (CHA-12): an
    arbitrary file became "the HRIR", the convolver failed to load, and
    Spatial Audio went silent for Game and Media — issue #100's failure
    mode. resolve()/is_relative_to() is kept as a second, independent guard
    in case the catalogue itself ever grows a bad entry.
    """
    if not is_valid_hrir_id(hrir_id):
        return None

    p = (_HRIR_DIR / f"{hrir_id}.wav").resolve()
    try:
        if not p.is_relative_to(_HRIR_DIR.resolve()):
            return None
    except OSError:
        return None

    return p if p.exists() else None
