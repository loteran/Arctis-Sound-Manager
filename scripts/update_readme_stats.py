#!/usr/bin/env python3
"""
Update README.md stats tables from telemetry.

Data source priority:
  1. Cloudflare D1 directly (if CF_TOKEN + CF_ACCOUNT_ID + CF_DATABASE_ID are set)
     → same data as the stats page, includes unique users
  2. Public endpoint fallback (https://asm-telemetry.arctis-asm.workers.dev/stats)
     → total reports only, no unique-user count

Usage:
    python3 scripts/update_readme_stats.py
    python3 scripts/update_readme_stats.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

PUBLIC_ENDPOINT = "https://asm-telemetry.arctis-asm.workers.dev/stats"
README          = Path(__file__).parent.parent / "README.md"

PROMOTE_THRESHOLD = 1  # ≥1 confirmed report → promote ⚠️ → ✅

_PID_STRIP_RE = re.compile(r'\$\\color\{[^}]+\}\{\\textbf\{([^}]+)\}\}\$')


# ── data fetching ─────────────────────────────────────────────────────────────

def _d1_query(sql: str, token: str, account_id: str, database_id: str) -> list[dict]:
    url  = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query")
    body = json.dumps({"sql": sql, "params": []}).encode()
    req  = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"D1 query failed: {e} — {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    if not resp.get("success"):
        print(f"D1 error: {resp.get('errors')}", file=sys.stderr)
        sys.exit(1)
    return resp["result"][0]["results"]


def fetch_stats() -> dict:
    """Return normalised stats dict regardless of data source."""
    token      = os.environ.get("CF_TOKEN")
    account_id = os.environ.get("CF_ACCOUNT_ID", "7c6295a096ddf6e771e09fd5d28104c7")
    db_id      = os.environ.get("CF_DATABASE_ID", "564f85d5-de1a-48c0-886c-dfc9de712880")

    if token:
        print("  source: Cloudflare D1 (direct)")
        distros_rows  = _d1_query(
            "SELECT distro AS label, COUNT(*) AS nb FROM stats "
            "GROUP BY distro ORDER BY nb DESC LIMIT 30",
            token, account_id, db_id,
        )
        headsets_rows = _d1_query(
            "SELECT headset AS label, product_id, COUNT(*) AS nb FROM stats "
            "GROUP BY headset, product_id ORDER BY nb DESC LIMIT 30",
            token, account_id, db_id,
        )
        total_row     = _d1_query("SELECT COUNT(*) AS nb FROM stats",
                                  token, account_id, db_id)
        unique_row    = _d1_query("SELECT COUNT(*) AS nb FROM users",
                                  token, account_id, db_id)
        versions_rows = _d1_query(
            "SELECT version AS label, COUNT(*) AS nb FROM users "
            "GROUP BY version ORDER BY nb DESC LIMIT 20",
            token, account_id, db_id,
        )
        return {
            "total":        total_row[0]["nb"]  if total_row  else 0,
            "unique_users": unique_row[0]["nb"] if unique_row else None,
            "distros":      distros_rows,
            "headsets":     headsets_rows,
            "versions":     versions_rows,
        }
    else:
        print("  source: public endpoint (no CF_TOKEN set)")
        req = urllib.request.Request(
            PUBLIC_ENDPOINT, headers={"User-Agent": "asm-readme-updater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())


# ── helpers ───────────────────────────────────────────────────────────────────

def _headset_users(stats: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in stats.get("headsets", []):
        name = row.get("label", "")
        nb   = int(row.get("nb", 0))
        counts[name] = counts.get(name, 0) + nb
    return counts


def _headset_pid_counts(stats: dict) -> dict[str, int]:
    """Map product_id (lowercase hex, no 0x prefix) → total user reports."""
    counts: dict[str, int] = {}
    for row in stats.get("headsets", []):
        pid = str(row.get("product_id") or "").lower().lstrip("0x").strip()
        if pid and pid != "unknown":
            nb = int(row.get("nb", 0))
            counts[pid] = counts.get(pid, 0) + nb
    return counts


def _seen_pids(stats: dict) -> set[str]:
    pids: set[str] = set()
    for row in stats.get("headsets", []):
        pid = str(row.get("product_id") or "").lower().lstrip("0x").strip()
        if pid and pid != "unknown":
            pids.add(pid)
    return pids


def _format_pids(pids_str: str, seen: set[str], force_confirm: bool = False) -> str:
    parts = []
    for p in pids_str.split(", "):
        p = p.strip()
        if p.lower() in seen or force_confirm:
            parts.append(f"$\\color{{royalblue}}{{\\textbf{{{p}}}}}$")
        else:
            parts.append(p)
    return ", ".join(parts)


def _detect_install_method(distro: str) -> str:
    n = distro.lower()
    if any(k in n for k in ("arch", "cachyos", "manjaro", "endeavour", "garuda", "artix")):
        return "🎯 AUR"
    if "fedora" in n or "nobara" in n:
        return "🎯 COPR"
    if any(k in n for k in ("ubuntu", "debian", "mint", "pop!_os", "elementary",
                             "zorin", "kubuntu", "xubuntu")):
        return "🎯 PPA"
    return "📦 Source"


def _replace_block(text: str, marker: str, new_content: str) -> str:
    start = f"<!-- STATS:{marker}:START -->"
    end   = f"<!-- STATS:{marker}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    replacement = f"{start}\n{new_content}\n{end}"
    result, n = pattern.subn(lambda _: replacement, text)
    if n == 0:
        print(f"WARNING: marker STATS:{marker} not found in README", file=sys.stderr)
    return result


# ── section builders ──────────────────────────────────────────────────────────

def _build_devices(block: str, headset_counts: dict[str, int], seen: set[str],
                   pid_counts: dict[str, int] | None = None) -> str:
    lines  = block.strip().splitlines()
    if len(lines) < 2:
        return block
    header, sep, *rows = lines
    pid_counts = pid_counts or {}
    new_rows = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 4:
            new_rows.append(row)
            continue
        device_name, status, _users, pids = cells[:4]
        pids_clean  = _PID_STRIP_RE.sub(r'\1', pids).replace("**", "")
        device_pids = {p.strip().lower() for p in pids_clean.split(",")}

        # Count by PID first (exact match, never ambiguous), fall back to name
        pid_user_count = sum(pid_counts.get(p, 0) for p in device_pids)
        name_user_count = 0
        if pid_user_count == 0:
            dn = device_name.strip("* ").lower()
            for tname, count in headset_counts.items():
                tn = tname.lower()
                if tn in dn or dn in tn:
                    name_user_count += count
        user_count = pid_user_count or name_user_count

        # force_confirm: name-matched users but PID not in seen (unknown exact PID)
        force_confirm = name_user_count > 0 and not device_pids.intersection(seen)
        user_cell     = str(user_count) if user_count else ""
        fmt_pids      = _format_pids(pids_clean, seen, force_confirm=force_confirm)
        if user_count >= PROMOTE_THRESHOLD:
            status = status.replace("⚠️", "✅")
        new_rows.append(
            f"| {device_name} | {status} | {user_cell} | {fmt_pids} |"
        )
    return "\n".join([header, sep] + new_rows)


def _build_tested_distros(stats: dict) -> str:
    distros = stats.get("distros", [])
    if not distros:
        return "_No data yet — stats will appear after the first users opt in._"
    lines = ["| Distribution | Install method | Users |", "|---|---|---|"]
    for row in distros:
        lines.append(
            f"| {row['label']} | {_detect_install_method(row['label'])} | 👥 {row['nb']} |"
        )
    return "\n".join(lines)


def _build_distros(stats: dict) -> str:
    distros = stats.get("distros", [])
    if not distros:
        return "_No data yet._"
    lines = ["| Distribution | Installs |", "|---|---|"]
    for row in distros[:15]:
        lines.append(f"| {row['label']} | {row['nb']} |")
    return "\n".join(lines)


def _build_headsets(stats: dict) -> str:
    counts: dict[str, int] = {}
    for row in stats.get("headsets", []):
        name = row.get("label", "")
        counts[name] = counts.get(name, 0) + int(row.get("nb", 0))
    if not counts:
        return "_No data yet._"
    lines = ["| Headset | Installs |", "|---|---|"]
    for name, nb in sorted(counts.items(), key=lambda x: -x[1])[:20]:
        lines.append(f"| {name} | {nb} |")
    return "\n".join(lines)


def _build_meta(stats: dict) -> str:
    total        = stats.get("total", 0)
    unique_users = stats.get("unique_users")
    updated      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if unique_users is not None:
        return (f"_Based on **{unique_users}** unique users "
                f"(**{total}** anonymous data points) — last updated {updated}_")
    return f"_Based on **{total}** anonymous data points — last updated {updated}_"


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Fetching stats…")
    stats = fetch_stats()
    total   = stats.get("total", 0)
    unique  = stats.get("unique_users")
    print(f"  total={total}, unique_users={unique}")

    headset_counts = _headset_users(stats)
    pid_counts     = _headset_pid_counts(stats)
    seen           = _seen_pids(stats)

    readme = README.read_text()

    # DEVICES block — read dynamically from README, never hardcoded
    m = re.search(
        r"<!-- STATS:DEVICES:START -->\n(.*?)\n<!-- STATS:DEVICES:END -->",
        readme, re.DOTALL,
    )
    if not m:
        print("ERROR: STATS:DEVICES block not found", file=sys.stderr)
        sys.exit(1)
    new_devices = _build_devices(m.group(1), headset_counts, seen, pid_counts)

    new_readme = readme
    new_readme = _replace_block(new_readme, "DEVICES",        new_devices)
    new_readme = _replace_block(new_readme, "TESTED_DISTROS", _build_tested_distros(stats))
    new_readme = _replace_block(new_readme, "DISTROS",        _build_distros(stats))
    new_readme = _replace_block(new_readme, "HEADSETS",       _build_headsets(stats))
    new_readme = _replace_block(new_readme, "META",           _build_meta(stats))

    if args.dry_run:
        import difflib
        diff = difflib.unified_diff(
            readme.splitlines(), new_readme.splitlines(),
            fromfile="README.md (current)", tofile="README.md (updated)", lineterm=""
        )
        print("\n".join(list(diff)[:80]))
    else:
        README.write_text(new_readme)
        print("README.md updated.")


if __name__ == "__main__":
    main()
