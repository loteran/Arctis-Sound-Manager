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
    ("Spatial Sound Card", ["ssc_dub", "ssc_hù", "ssc_hù+", "ssc_ny", "ssc_ny+", "ssc_syd", "ssc_syd+"]),
    ("None", ["none"]),
]


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
                result.append({"id": hrir_id, "name": catalog[hrir_id], "group": group_name})
                seen.add(hrir_id)
    for hrir_id, desc in catalog.items():
        if hrir_id not in seen:
            result.append({"id": hrir_id, "name": desc, "group": "Other"})
    return result


def list_hrir_options() -> list[dict]:
    """Flat list for D-Bus GetListOptions."""
    return [{"id": o["id"], "name": o["name"]} for o in list_hrir_options_grouped()]


def package_hrir_path(hrir_id: str) -> Path | None:
    """Return absolute path to a bundled WAV, or None if not found."""
    p = _HRIR_DIR / f"{hrir_id}.wav"
    return p if p.exists() else None
