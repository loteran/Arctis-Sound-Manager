#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify that a release version has actually been delivered to every
distribution channel (AUR, COPR, Launchpad PPA, PyPI).

Each channel is queried through its public API and classified as:
  - delivered : the version is published/available
  - pending   : the version is known but still building/propagating
  - missing   : the version is absent (or the channel errored)

Exit status:
  - audit/strict mode  : non-zero unless every HARD channel is `delivered`
  - post-release mode   : non-zero only if a HARD channel is `missing`
                          (a still-`pending` slow build is tolerated; the daily
                          audit is the safety net)

PyPI is a SOFT channel: its result is reported but never fails the run, because
the Trusted Publisher may not be configured yet. Flip SOFT below once it is live.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Emoji status markers below need a UTF-8 stdout; Windows consoles default to
# cp1252 and would crash. Harmless on the Linux CI runners (already UTF-8).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OWNER = "loteran"
PROJECT = "arctis-sound-manager"

DELIVERED, PENDING, MISSING = "delivered", "pending", "missing"


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "asm-release-verify"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _base(version: str) -> str:
    """Strip the packaging release/suffix: 1.1.51-1~noble1 -> 1.1.51."""
    return version.split("-")[0].split("~")[0]


def check_pypi(v: str):
    try:
        d = _get(f"https://pypi.org/pypi/{PROJECT}/json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return MISSING, "package not on PyPI (404 — Trusted Publisher not set up?)"
        return MISSING, f"HTTP {e.code}"
    if v in d.get("releases", {}):
        return DELIVERED, f"{v} present (latest: {d['info']['version']})"
    return MISSING, f"absent (latest on PyPI: {d['info']['version']})"


def check_aur(v: str):
    d = _get(f"https://aur.archlinux.org/rpc/?v=5&type=info&arg={PROJECT}")
    results = d.get("results") or []
    if not results:
        return MISSING, "package not found in AUR"
    ver = results[0].get("Version", "")
    return (DELIVERED if _base(ver) == v else MISSING), ver


def check_copr(v: str):
    d = _get(
        "https://copr.fedorainfracloud.org/api_3/package"
        f"?ownername={OWNER}&projectname={PROJECT}&packagename={PROJECT}"
        "&with_latest_succeeded_build=true"
    )
    builds = d.get("builds") or {}
    running = builds.get("latest")
    succeeded = builds.get("latest_succeeded")
    succ_ver = (succeeded or {}).get("source_package", {}).get("version", "")
    if succeeded and _base(succ_ver) == v and succeeded.get("state") == "succeeded":
        return DELIVERED, f"{succ_ver} (succeeded)"
    # A build for v exists but hasn't succeeded yet.
    run_ver = (running or {}).get("source_package", {}).get("version", "")
    if running and _base(run_ver) == v and running.get("state") not in ("failed", "canceled"):
        return PENDING, f"{run_ver} ({running.get('state')})"
    return MISSING, f"last succeeded: {succ_ver or 'none'}"


def check_ppa(v: str):
    url = (
        f"https://api.launchpad.net/1.0/~{OWNER}/+archive/ubuntu/{PROJECT}"
        f"?ws.op=getPublishedSources&source_name={PROJECT}&exact_match=true&order_by_date=true"
    )
    d = _get(url)
    entries = [e for e in d.get("entries", []) if _base(e.get("source_package_version", "")) == v]
    if not entries:
        return MISSING, "no source published for this version"
    published = [e for e in entries if e.get("status") == "Published"]
    pending = [e for e in entries if e.get("status") in ("Pending",)]
    if published:
        series = ", ".join(sorted(e.get("distro_series_link", "").rsplit("/", 1)[-1] for e in published))
        note = f"Published [{series}]"
        if pending:
            note += f" — {len(pending)} series still Pending"
        return DELIVERED, note
    if pending:
        return PENDING, f"{len(pending)} series Pending ({pending[0].get('source_package_version')})"
    return MISSING, f"only {entries[0].get('status')}"


# name -> (function, hard?)
CHANNELS = {
    "AUR": (check_aur, True),
    "COPR": (check_copr, True),
    "PPA": (check_ppa, True),
    "PyPI": (check_pypi, False),  # SOFT until the PyPI Trusted Publisher is live
}

ICON = {DELIVERED: "✅", PENDING: "⏳", MISSING: "❌"}


def run_once(v: str):
    results = {}
    for name, (fn, hard) in CHANNELS.items():
        try:
            status, detail = fn(v)
        except Exception as e:  # network/parse — treat as missing this pass
            status, detail = MISSING, f"check error: {e}"
        results[name] = (status, detail, hard)
    return results


def all_settled(results, strict: bool) -> bool:
    """In strict (audit) mode every hard channel must be delivered. In lenient
    (post-release) mode, hard channels may be pending but not missing."""
    for status, _detail, hard in results.values():
        if not hard:
            continue
        if strict and status != DELIVERED:
            return False
        if not strict and status == MISSING:
            return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Verify release delivery across distro channels.")
    p.add_argument("version", help="Version to verify, e.g. 1.1.51 (no leading v)")
    p.add_argument("--timeout", type=int, default=0, help="Max seconds to poll (0 = single pass)")
    p.add_argument("--interval", type=int, default=60, help="Seconds between polls")
    p.add_argument("--strict", action="store_true",
                   help="Require every hard channel delivered (audit mode); else pending is tolerated")
    args = p.parse_args()
    v = args.version.lstrip("v")

    deadline = time.time() + args.timeout
    while True:
        results = run_once(v)
        print(f"\n=== Release delivery for v{v} "
              f"({'audit/strict' if args.strict else 'post-release'} mode) ===")
        for name, (status, detail, hard) in results.items():
            tag = "" if hard else " (soft)"
            print(f"  {ICON[status]} {name:5}{tag}: {status} — {detail}")
        if all_settled(results, args.strict) or time.time() >= deadline:
            break
        remaining = int(deadline - time.time())
        print(f"  … not all settled, retrying in {args.interval}s ({remaining}s left)")
        time.sleep(min(args.interval, max(1, remaining)))

    ok = all_settled(results, args.strict)
    hard_bad = [n for n, (s, _d, h) in results.items()
                if h and (s == MISSING or (args.strict and s != DELIVERED))]
    print("\n" + ("✅ All hard channels OK." if ok
                  else f"❌ Channels not delivered: {', '.join(hard_bad)}"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
