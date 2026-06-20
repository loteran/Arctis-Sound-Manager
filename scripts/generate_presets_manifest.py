# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regenerate presets_manifest.json from the bundled presets directory.

Run from the repo root:
    python scripts/generate_presets_manifest.py
"""
from __future__ import annotations

import json
from pathlib import Path

PRESETS_DIR   = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "gui" / "presets"
MANIFEST_PATH = Path(__file__).parent.parent / "presets_manifest.json"


def main() -> None:
    presets = sorted(p.name for p in PRESETS_DIR.glob("*.json"))
    manifest = {"version": 1, "presets": presets}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"Generated {MANIFEST_PATH} — {len(presets)} presets.")


if __name__ == "__main__":
    main()
