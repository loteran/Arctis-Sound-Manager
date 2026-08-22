#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify that a release version has actually been delivered to every
distribution channel (signed pacman repo, COPR, Launchpad PPA, PyPI — plus the
AUR and Terra's Nobara spec, both informational).

Each channel is queried through its public API and classified as:
  - delivered : the version is published/available
  - pending   : the version is known but still building/propagating
  - missing   : the version is absent (or the channel errored)

Exit status:
  - audit/strict mode  : non-zero unless every HARD channel is `delivered`
  - post-release mode   : non-zero only if a HARD channel is `missing`
                          (a still-`pending` slow build is tolerated; the daily
                          audit is the safety net)

PyPI went live with 1.1.51 once its Trusted Publisher was configured; set a
channel's flag to False in CHANNELS to make it soft (reported, never fatal).

The AUR is SOFT here, which is a statement about the audit and not about the
channel: every release still publishes to it (release.yaml), and when it cannot
be reached that release arms aur-retry.yaml, which polls and pushes as soon as
it can. It is soft because delivery is not ours to guarantee — pushing depends
on aur.archlinux.org's git being reachable, and during an upstream maintenance
window it answers "The AUR is down due to maintenance" to git-upload-pack while
its read-only RPC keeps serving the stale version. Nothing on our side can
unblock that, so gating the release audit on it only buried a real regression
under recurring noise (issues #176, #178).

What the audit gates on for Arch is the signed binary pacman repository
(pacman-repo.yaml): the channel `pacman -Syu` actually reads, the one the
Distrobox installers register, and the only one a PackageKit software centre
can see at all.

Terra is SOFT for a different reason than the AUR: it is a third-party
downstream (terrapkg/packages) this project does not maintain, build for, or
have any way to push a fix to — Nobara ships it enabled by default through
Terra's own spec. Gating the release on Terra catching up would fail this
project's releases over someone else's backlog, so it can only ever be
reported (PKG-4), never fatal.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
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

# A channel we could not reach a verdict on — the API timed out, returned a 5xx,
# or answered something unparseable. Deliberately NOT `missing`: "the release is
# not there" and "I could not find out" call for different reactions, and
# collapsing them is how a transient Launchpad timeout files an issue announcing
# an undelivered release. That is the same class of false positive that buried a
# real regression under recurring noise in #176/#178, in a different disguise.
UNKNOWN = "unknown"


PACMAN_DB_URL = (
    f"https://github.com/{OWNER}/Arctis-Sound-Manager/releases/download"
    f"/pacman-repo/{PROJECT}.db.tar.gz"
)

# AUR-only packages pacman-repo.yaml rebuilds and ships alongside the main
# package because pacman cannot resolve them from the official repositories
# at all (see that workflow's module comment). A fresh install is
# unresolvable without these, even when arctis-sound-manager itself is
# present in the database — the #178 pattern, applied to the package ASM
# cannot run without (PKG-2).
AUR_ONLY_HARD_DEPS = ("python-pulsectl",)

# Nobara ships ASM enabled by default through a Terra-maintained spec this
# project does not control and cannot push to. Terra has no package-search
# API (no Copr-style JSON endpoint, no documented repodata mirror); the
# closest honest, queryable signal is the Version: field of the spec file
# Terra maintains in its own monorepo (terrapkg/packages), read straight off
# GitHub. "frawhide" is that repo's actual default branch name (confirmed via
# `gh repo view terrapkg/packages`), not a typo.
TERRA_SPEC_URL = (
    "https://raw.githubusercontent.com/terrapkg/packages/frawhide/anda/apps/"
    "Arctis-Sound-Manager/Arctis-Sound-Manager.spec"
)


def _fetch(url: str, timeout: int, attempts: int = 3) -> bytes:
    """GET *url*, retrying transient failures before giving up.

    These are public APIs behind caches and load balancers; a single timeout or
    5xx says nothing about whether the release landed. Retrying here keeps the
    difference between "not delivered" and "could not check" from being decided
    by one unlucky packet. A 404 is not retried — that is an answer, not a
    failure.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "asm-release-verify"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or e.code < 500:
                raise
            last = e
        except Exception as e:  # timeout, DNS, connection reset, malformed TLS…
            last = e
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError(f"could not fetch {url}")


def _get(url: str):
    return json.loads(_fetch(url, timeout=30))


def _get_bytes(url: str) -> bytes:
    return _fetch(url, timeout=60)


def _base(version: str) -> str:
    """Strip the packaging release/suffix: 1.1.51-1~noble1 -> 1.1.51."""
    return version.split("-")[0].split("~")[0]


def check_pypi(v: str):
    try:
        d = _get(f"https://pypi.org/pypi/{PROJECT}/json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return MISSING, "package not on PyPI (404 — Trusted Publisher not set up?)"
        # Any other HTTP status is PyPI having a bad day, not evidence about
        # our release.
        return UNKNOWN, f"HTTP {e.code}"
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


def check_pacman(v: str):
    """The signed binary pacman repository — what Arch/CachyOS users actually
    install from, and the only Arch channel a PackageKit software centre sees.

    Read the repository database itself rather than the release's asset list:
    that is the exact file `pacman -Sy` downloads, so a package uploaded but
    missing from the database (repo-add never ran, a partial upload) reads as
    missing here too, which is what the user would experience.

    Beyond the main package, also check every name in AUR_ONLY_HARD_DEPS: a
    fresh `pacman -S arctis-sound-manager` is only actually resolvable if
    those are in the database too (PKG-2) — pacman-repo.yaml is meant to fail
    its job when one fails to build (see that workflow), but this is the
    audit's own, independent check that the artifact it is meant to verify
    really is complete, not just a claim that the upstream job behaved.
    """
    raw = _get_bytes(PACMAN_DB_URL)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        # repo-add lays the database out as one <pkgname>-<pkgver>-<pkgrel>/
        # directory per package, each holding a `desc` file.
        entries = {n.split("/", 1)[0] for n in tf.getnames() if "/" in n}
    by_name = {}
    for entry in entries:
        parts = entry.rsplit("-", 2)
        if len(parts) == 3:
            by_name.setdefault(parts[0], f"{parts[1]}-{parts[2]}")

    project_entry = by_name.get(PROJECT)
    if not project_entry or not project_entry.startswith(f"{v}-"):
        found = project_entry or f"no {PROJECT} entry"
        return MISSING, f"repo database has {found}"

    missing_deps = [dep for dep in AUR_ONLY_HARD_DEPS if dep not in by_name]
    if missing_deps:
        return MISSING, (
            f"{project_entry} in the signed repo database, but a fresh "
            f"install is unresolvable — missing hard dependency: "
            f"{', '.join(missing_deps)}"
        )
    return DELIVERED, f"{project_entry} in the signed repo database"


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


def check_terra(v: str):
    """Nobara ships ASM through a Terra-maintained spec (terrapkg/packages)
    this project does not control and cannot push to — PKG-4. There is no
    package-search API for Terra to poll the way COPR or the PPA offer one;
    the closest honest, queryable signal is the Version: field of the spec
    file Terra maintains, read straight off GitHub (TERRA_SPEC_URL).

    That only says what Terra's spec currently targets, not whether a build
    from it actually succeeded — Terra's own build pipeline is not something
    this project can query. But a spec still pinned to an old version is
    exactly what leaves a hard-dependency promotion (e.g. ladspa-swh-plugins
    Recommends -> Requires) unreachable on Nobara until Terra catches up, so
    it is the thing worth surfacing.

    Purely informational (see CHANNELS: always soft). Any failure to reach
    GitHub, or a spec whose Version: field cannot be found, propagates as an
    exception and is turned into UNKNOWN by run_once() — same as every other
    channel — so a network hiccup here never claims a release is undelivered.
    """
    text = _fetch(TERRA_SPEC_URL, timeout=30).decode("utf-8", errors="replace")
    m = re.search(r"^Version:\s*(\S+)", text, re.MULTILINE)
    if not m:
        raise RuntimeError("Version: field not found in Terra's spec")
    terra_v = _base(m.group(1))
    if terra_v == v:
        return DELIVERED, f"spec pinned to {terra_v}, matches this release"
    return PENDING, (
        f"spec pinned to {terra_v}, this release is {v} — "
        "Terra has not caught up yet"
    )


# name -> (function, hard?)
CHANNELS = {
    "Pacman": (check_pacman, True),  # the real Arch channel (see module docstring)
    "AUR": (check_aur, False),       # convenience mirror, gated on upstream AUR git
    "COPR": (check_copr, True),
    "PPA": (check_ppa, True),
    "PyPI": (check_pypi, True),  # live since 1.1.51 (Trusted Publisher configured)
    "Terra": (check_terra, False),  # Nobara's downstream spec — informational only, see check_terra
}

ICON = {DELIVERED: "✅", PENDING: "⏳", MISSING: "❌", UNKNOWN: "❓"}


def run_once(v: str):
    results = {}
    for name, (fn, hard) in CHANNELS.items():
        try:
            status, detail = fn(v)
        except Exception as e:
            # Already retried inside _fetch, so this is a persistent failure —
            # but still a failure to *check*, not a missing release.
            status, detail = UNKNOWN, f"check error: {e}"
        results[name] = (status, detail, hard)
    return results


def all_settled(results, strict: bool) -> bool:
    """Has every hard channel reached the verdict this mode requires?

    Strict (audit): every hard channel delivered. Lenient (post-release): a hard
    channel may still be pending — Launchpad routinely is — but not missing.

    UNKNOWN never counts as settled either way, so polling keeps trying: given a
    transient outage, the next pass usually resolves it into a real answer.
    """
    for status, _detail, hard in results.values():
        if not hard:
            continue
        if status == UNKNOWN:
            return False
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
            print(f"  {ICON[status]} {name:6}{tag}: {status} — {detail}")
        if all_settled(results, args.strict) or time.time() >= deadline:
            break
        remaining = int(deadline - time.time())
        print(f"  … not all settled, retrying in {args.interval}s ({remaining}s left)")
        time.sleep(min(args.interval, max(1, remaining)))

    ok = all_settled(results, args.strict)
    unknown = [n for n, (s, _d, h) in results.items() if h and s == UNKNOWN]
    hard_bad = [n for n, (s, _d, h) in results.items()
                if h and s != UNKNOWN and (s == MISSING or (args.strict and s != DELIVERED))]

    if ok:
        print("\n✅ All hard channels OK.")
        return 0
    if hard_bad:
        # A real verdict: the release is not on these channels.
        print(f"\n❌ Channels not delivered: {', '.join(hard_bad)}")
        if unknown:
            print(f"   (also could not be verified: {', '.join(unknown)})")
        return 1
    # Nothing is known to be missing — we just could not finish checking. Exit 2
    # so the caller can tell this apart and avoid announcing a failed delivery
    # that never happened. The daily audit re-runs anyway.
    print(f"\n❓ Could not verify: {', '.join(unknown)} — "
          f"no channel is known to be missing.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
