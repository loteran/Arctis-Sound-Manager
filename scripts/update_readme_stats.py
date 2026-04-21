#!/usr/bin/env python3
"""
Update the README.md stats tables from the live telemetry endpoint.

Usage:
    python3 scripts/update_readme_stats.py
    python3 scripts/update_readme_stats.py --dry-run   # print diff without writing

Replaces the content between <!-- STATS:DEVICES:START --> / <!-- STATS:DEVICES:END -->
and <!-- STATS:TESTED_DISTROS:START --> / <!-- STATS:TESTED_DISTROS:END --> markers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ENDPOINT = "https://asm-telemetry.arctis-asm.workers.dev/stats"
README   = Path(__file__).parent.parent / "README.md"

DEVICES_START = "<!-- STATS:DEVICES:START -->"
DEVICES_END   = "<!-- STATS:DEVICES:END -->"
DISTROS_START = "<!-- STATS:TESTED_DISTROS:START -->"
DISTROS_END   = "<!-- STATS:TESTED_DISTROS:END -->"

PROMOTE_THRESHOLD = 5  # ⚠️ becomes ✅ above this many unique users


def fetch_stats() -> dict:
    req = urllib.request.Request(ENDPOINT, headers={"User-Agent": "asm-readme-updater/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _headset_users(stats: dict) -> dict[str, int]:
    """Return {headset_name: unique_user_count} from the /stats response."""
    counts: dict[str, int] = {}
    for row in stats.get("headsets", []):
        name = row.get("label", "")
        nb   = int(row.get("nb", 0))
        counts[name] = counts.get(name, 0) + nb
    return counts


def _update_devices_block(block: str, headset_counts: dict[str, int]) -> str:
    lines = block.strip().splitlines()
    if len(lines) < 2:
        return block

    header = lines[0]  # | Device | Mixer | ...
    sep    = lines[1]  # |---|---|...
    rows   = lines[2:]

    new_rows = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 5:
            new_rows.append(row)
            continue

        device_name, mixer, advanced, _users, pids = cells[:5]

        # Sum counts for all telemetry headsets that match this row
        user_count = 0
        for telemetry_name, count in headset_counts.items():
            # Match: telemetry name is contained in the README row name (case-insensitive)
            # or the README row name contains the telemetry name
            dn_lower = device_name.strip("* ").lower()
            tn_lower = telemetry_name.lower()
            if tn_lower in dn_lower or dn_lower in tn_lower:
                user_count += count

        user_cell = str(user_count) if user_count else ""

        # Auto-promote ⚠️ → ✅ at threshold
        if user_count >= PROMOTE_THRESHOLD:
            mixer    = mixer.replace("⚠️", "✅")
            advanced = advanced.replace("⚠️", "✅")

        new_rows.append(
            f"| {device_name} | {mixer} | {advanced} | {user_cell} | {pids} |"
        )

    return "\n".join([header, sep] + new_rows)


def _update_distros_block(stats: dict) -> str:
    distros = stats.get("distros", [])
    if not distros:
        return "_No data yet — stats will appear after the first users opt in._"

    lines = ["| Distribution | Reports |", "|---|---|"]
    for row in distros:
        lines.append(f"| {row['label']} | {row['nb']} |")
    return "\n".join(lines)


def _replace_block(text: str, start_marker: str, end_marker: str, new_content: str) -> str:
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    replacement = f"{start_marker}\n{new_content}\n{end_marker}"
    result, n = pattern.subn(replacement, text)
    if n == 0:
        print(f"WARNING: marker not found: {start_marker}", file=sys.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description="Update README.md stats from telemetry")
    parser.add_argument("--dry-run", action="store_true", help="Print new content without writing")
    args = parser.parse_args()

    print("Fetching stats from telemetry endpoint…")
    stats = fetch_stats()
    print(f"  total={stats.get('total')}, unique_users={stats.get('unique_users')}")

    headset_counts = _headset_users(stats)

    readme = README.read_text()

    # Extract current devices block and update it
    m = re.search(
        re.escape(DEVICES_START) + r"\n(.*?)\n" + re.escape(DEVICES_END),
        readme, re.DOTALL,
    )
    if not m:
        print("ERROR: devices block not found in README", file=sys.stderr)
        sys.exit(1)

    updated_devices = _update_devices_block(m.group(1), headset_counts)
    updated_distros = _update_distros_block(stats)

    new_readme = _replace_block(readme, DEVICES_START, DEVICES_END, updated_devices)
    new_readme = _replace_block(new_readme, DISTROS_START, DISTROS_END, updated_distros)

    if args.dry_run:
        print("\n--- Updated README (devices block) ---")
        print(updated_devices)
        print("\n--- Updated README (distros block) ---")
        print(updated_distros)
    else:
        README.write_text(new_readme)
        print(f"README.md updated ({README})")


if __name__ == "__main__":
    main()
