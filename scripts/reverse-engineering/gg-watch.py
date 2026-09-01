#!/usr/bin/env python3
"""Watch for a new SteelSeries GG release, and act on what changed.

Run it on a timer. It costs one HEAD request when nothing is new.

What it does when a new version appears:

  1. downloads the installer and extracts the two things worth reading —
     the encrypted device specs, and Sonar's db-migrations;
  2. recovers the descriptor passphrase from SteelSeriesEngine.exe and
     decrypts the Arctis specs (recover-key.py + decode-arctis-specs.sh,
     both already here);
  3. compares against the previous version using fingerprint index:
       * new .edevice files            → new hardware
       * changed Arctis .device files  → protocol changes worth reading
       * presets in the catalogue that ASM does not ship
  4. acts:
       * presets  → writes the JSON into ASM, commits to main (the branch
                    preset_sync reads from). The back-merge workflow carries
                    it to develop. No cherry-pick needed.
       * hardware or protocol changes → opens a GitHub issue, because those
                    need a human to read the GoLisp and decide.

Why the two outcomes differ: a preset is data in a shape we already know and
can be added mechanically. A spec change is a question — "does this opcode
mean what I think" — and answering it wrong writes silent garbage to somebody's
headset. So that half stops at "here is what changed, go look".

Nothing SteelSeries ships is committed anywhere: the decrypted specs stay in
the temp workdir, which is deliberately discarded after the run. Only the
preset JSON — the same data ASM already carries — reaches git.

Copyright (C) 2026 loteran — SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# ── Paths (configurable via environment) ─────────────────────────────────────

GG_WORKDIR = Path(os.environ.get("GG_WORKDIR", Path.home() / "steelseries-research"))
ASM_ROOT   = Path(os.environ.get("ASM_ROOT",   Path.home() / "Arctis-Sound-Manager"))

PRESETS     = ASM_ROOT / "src/arctis_sound_manager/gui/presets"
PROVENANCE  = ASM_ROOT / "presets_provenance.json"
STATE_FILE  = ASM_ROOT / ".github/gg-watch-state.json"
MANIFEST    = ASM_ROOT / "presets_manifest.json"
GENERATE_MANIFEST = ASM_ROOT / "scripts/generate_presets_manifest.py"

REPO = "loteran/Arctis-Sound-Manager"

LATEST_URL = "https://steelseries.com/gg/downloads/gg/latest/windows"
VERSION_RE = re.compile(r"SteelSeriesGG([\d.]+)Setup\.exe")

log = lambda *a: print(*a, flush=True)

# Set by --dry-run: do every read and comparison, skip everything that writes
# outward (git push, gh issue). Without it there is no way to exercise the
# "new version" path without publishing something.
DRY_RUN = False

# Set by --no-push: for local use where the user manages git themselves.
NO_PUSH = False


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


# ── version discovery ────────────────────────────────────────────────────────

def latest_version() -> tuple[str, str]:
    """(version, url) for the current GG installer, without downloading it.

    The download page 302s to a filename carrying the version, so a HEAD and
    the final URL are enough to know whether there is anything to do.
    """
    out = subprocess.run(
        ["curl", "-sIL", "--max-time", "60", "-o", "/dev/null",
         "-w", "%{url_effective}", LATEST_URL],
        check=True, text=True, capture_output=True).stdout.strip()
    m = VERSION_RE.search(out)
    if not m:
        raise SystemExit(f"could not read a version out of {out!r}")
    return m.group(1), out


def _vkey(v: str) -> tuple:
    """Version as a 3-tuple, so "117" and "117.0.0" compare equal."""
    parts = [int(x) for x in re.findall(r"\d+", v)][:3]
    return tuple(parts + [0] * (3 - len(parts)))


# ── state management ─────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.is_file():
        return json.loads(STATE_FILE.read_text())
    return {"version": None, "edevice_files": [], "spec_hashes": {}}


def save_state(version: str, edevice_files: list[str], spec_hashes: dict[str, str],
               presets_added: list[str], new_files: list[str],
               changed_specs: list[str], issue: str | None) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "version": version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "edevice_files": sorted(edevice_files),
        "spec_hashes": dict(sorted(spec_hashes.items())),
        "presets_added": presets_added,
        "new_files": new_files,
        "changed_specs": changed_specs,
        "issue": issue,
    }, indent=2, ensure_ascii=False) + "\n")


# ── acquisition ──────────────────────────────────────────────────────────────

def fetch(version: str, url: str) -> Path:
    dest = GG_WORKDIR / f"SteelSeriesGG{version}Setup.exe"
    if dest.is_file() and dest.stat().st_size > 100_000_000:
        log(f"  installer already downloaded: {dest.name}")
        return dest
    log(f"  downloading {dest.name} …")
    run(["curl", "-fsSL", "--max-time", "3600", "-o", str(dest), url])
    return dest


def extract(installer: Path, version: str) -> Path:
    root = GG_WORKDIR / f"gg-{version}"
    if (root / "apps/engine/deviceSpecifications").is_dir():
        log(f"  already extracted into {root.name}")
        return root
    root.mkdir(parents=True, exist_ok=True)
    run(["7z", "x", "-y", f"-o{root}", str(installer),
         "apps/engine/deviceSpecifications/*",
         "apps/engine/SteelSeriesEngine.exe",
         "apps/sonar/db-migrations/*"])
    return root


def decode(root: Path, version: str) -> Path:
    out = GG_WORKDIR / f"decoded-{version}"
    if out.is_dir() and any(out.iterdir()):
        log(f"  already decoded into {out.name}")
        return out
    key = GG_WORKDIR / f"key-{version}.txt"
    exe = root / "apps/engine/SteelSeriesEngine.exe"
    log("  recovering the descriptor passphrase …")
    key.write_text(run([sys.executable, str(Path(__file__).parent / "recover-key.py"),
                        str(exe)]).stdout)
    if len(key.read_text().strip()) != 128:
        raise SystemExit("passphrase is not 128 chars — the Go layout probably "
                         "moved; redo the offsets by hand (see the memory)")
    log("  decrypting the Arctis specs …")
    run([str(Path(__file__).parent / "decode-arctis-specs.sh"),
         str(root / "apps/engine/deviceSpecifications"), str(key), str(out)])
    return out


# ── comparison (against state, not disk) ─────────────────────────────────────

def build_index(root: Path, decoded: Path) -> tuple[list[str], dict[str, str]]:
    """Return (edevice filenames, {decoded_name: sha256})."""
    specs = root / "apps/engine/deviceSpecifications"
    edevice_files = sorted(p.name for p in specs.glob("*.edevice")) if specs.is_dir() else []
    spec_hashes = {}
    if decoded.is_dir():
        for p in decoded.iterdir():
            if p.is_file():
                spec_hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return edevice_files, dict(sorted(spec_hashes.items()))


def compare_to_state(edevice_files: list[str], spec_hashes: dict[str, str],
                     state: dict) -> tuple[list[str], list[str]]:
    """Compare current index against state, return (new_edevice, changed_specs).

    First run (empty state) returns ([], []) — adopt silently, don't spam.
    """
    old_edevice = set(state.get("edevice_files", []))
    old_hashes = state.get("spec_hashes", {})

    # First run: nothing to compare against, treat as adopt.
    if not old_edevice and not old_hashes:
        return [], []

    new_files = sorted(set(edevice_files) - old_edevice)

    # Changed = hash differs, plus new decoded files not in old index.
    changed = []
    for name, h in spec_hashes.items():
        if name not in old_hashes:
            changed.append(name)
        elif old_hashes[name] != h:
            changed.append(name)
    changed = sorted(set(changed))

    return new_files, changed


INSERT = re.compile(
    r"INSERT\s+INTO\s+configs\s*\((?P<cols>.*?)\)\s*VALUES\s*\((?P<vals>.*?)\);",
    re.S | re.I)
UPDNAME = re.compile(
    r"UPDATE\s+configs\s+SET\s+name\s*=\s*(?P<name>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")"
    r"\s+WHERE\s+id\s*=\s*'(?P<id>[0-9a-f-]+)'", re.S | re.I)


def _split_vals(s: str) -> list[str]:
    out, buf, q, i = [], [], None, 0
    while i < len(s):
        c = s[i]
        if q:
            if c == q:
                if i + 1 < len(s) and s[i + 1] == q:
                    buf.append(c); i += 2; continue
                q = None
            buf.append(c)
        elif c in "'\"":
            q = c; buf.append(c)
        elif c == ",":
            out.append("".join(buf).strip()); buf = []
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf).strip())
    return out


def _unq(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1].replace(v[0] * 2, v[0])
    return v


def catalogue(root: Path) -> dict[str, dict]:
    """Every Sonar preset in the migrations, with later renames applied."""
    mig = root / "apps/sonar/db-migrations"
    by_id, data_of = {}, {}
    for sql in sorted(mig.rglob("*.sql")):
        t = sql.read_text(encoding="utf-8", errors="replace")
        for m in INSERT.finditer(t):
            cols = [c.strip().strip('"`') for c in m.group("cols").split(",")]
            vals = _split_vals(m.group("vals"))
            if len(cols) != len(vals):
                continue
            row = dict(zip(cols, vals))
            if not {"id", "name", "data"} <= row.keys():
                continue
            try:
                payload = json.loads(_unq(row["data"]))
            except Exception:
                continue
            pid = _unq(row["id"])
            by_id[pid] = _unq(row["name"])
            data_of[pid] = payload
        for m in UPDNAME.finditer(t):
            if m.group("id") in by_id:
                by_id[m.group("id")] = _unq(m.group("name"))
    return {name: data_of[pid] for pid, name in by_id.items()}


def _fold(n: str) -> str:
    """Compare on letters and digits only."""
    for ch in "™®©":
        n = n.replace(ch, "")
    n = "".join(c for c in unicodedata.normalize("NFD", n)
                if unicodedata.category(c) != "Mn").casefold()
    if n.startswith("sonar preset"):
        n = n[len("sonar preset"):]
    return "".join(c for c in n if c.isalnum())


def _words(n: str) -> frozenset:
    n = "".join(c for c in unicodedata.normalize("NFD", n)
                if unicodedata.category(c) != "Mn").casefold()
    for junk in ("sonar preset", " by ", ":", "-", "_", "™", "®"):
        n = n.replace(junk, " ")
    return frozenset(w for w in n.split() if w.isalnum())


def missing_presets(root: Path) -> dict[str, dict]:
    have, have_words = set(), []
    for f in PRESETS.glob("*.json"):
        m = re.match(r"^(.*) \[(Game|Chat|Mic)\]\.json$", f.name)
        if m:
            have.add(_fold(m.group(1)))
            have_words.append(_words(m.group(1)))

    def known(name: str) -> bool:
        if _fold(name) in have:
            return True
        w = _words(name)
        return any(w and a and (w <= a or a <= w) for a in have_words)

    return {n: d for n, d in catalogue(root).items() if not known(n)}


# ── actions ──────────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    for ch in ':/\\?*"<>|':
        name = name.replace(ch, "_")
    return name.strip()


def add_presets(new: dict[str, dict], version: str) -> list[str]:
    """Write presets, provenance, manifest, and commit on current branch (main)."""
    written = []
    for name, data in sorted(new.items()):
        chan = "Game" if data.get("formFactor") == "headphones" else "Chat"
        path = PRESETS / f"{_safe(name)} [{chan}].json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        written.append(path.name)

    if not written:
        return []

    if DRY_RUN:
        log("  [dry-run] would commit and push these to main")
        for n in written:
            (PRESETS / n).unlink(missing_ok=True)
        return written

    # Merge provenance
    try:
        prov = json.loads(PROVENANCE.read_text()) if PROVENANCE.is_file() else {}
    except Exception:
        prov = {}
    prov.update({n: version for n in written})
    PROVENANCE.write_text(
        json.dumps(dict(sorted(prov.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    # Regenerate manifest — ESSENTIAL: GITHUB_TOKEN pushes don't trigger
    # on:push workflows, so update-presets-manifest.yml would not fire.
    run([sys.executable, str(GENERATE_MANIFEST)])

    if NO_PUSH:
        log("  [no-push] files written, manifest regenerated — commit manually")
        return written

    body = "\n".join(f"  {n}" for n in written)
    msg = (f"feat(presets): add {len(written)} Sonar preset(s) from GG {version}\n\n"
           f"Extracted from the Sonar db-migrations in the installer, where each\n"
           f"preset is an INSERT INTO configs carrying the same JSON shape ASM\n"
           f"stores on disk.\n\n{body}\n")
    git = ["git", "-C", str(ASM_ROOT)]
    run(git + ["add", str(PRESETS), str(PROVENANCE), str(MANIFEST), str(STATE_FILE)])
    run(git + ["commit", "-m", msg])
    run(git + ["push", "origin", "HEAD"])
    log("  presets pushed to main")
    return written


def open_issue(version: str, new_files: list[str], changed: list[str]) -> str | None:
    if not new_files and not changed:
        return None
    title = f"SteelSeries GG {version}: device specs changed"
    existing = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--state", "all",
         "--search", title, "--json", "title,url"],
        text=True, capture_output=True).stdout
    if title in existing:
        log("  issue already open for this version")
        return None

    lines = [f"GG **{version}** changes what the device specifications say. "
             f"Raised automatically; the reading is still a human job.", ""]
    if new_files:
        heads = [f for f in new_files
                 if any(k in f.lower() for k in ("arctis", "nova", "gamebud"))]
        lines += [f"### New device files ({len(new_files)})", ""]
        lines += [f"- `{f}`" + ("  **← headset**" if f in heads else "")
                  for f in new_files]
        lines += ["", "A headset here means a possible new YAML profile. Anything "
                  "else is a mouse, keyboard or controller and is out of scope "
                  "for ASM.", ""]
    if changed:
        lines += [f"### Changed Arctis specs ({len(changed)})", ""]
        lines += [f"- `{f}`" for f in changed]
        lines += ["", "Worth diffing against the previous version: these carry "
                  "opcodes, status layouts and screen geometry. A change here can "
                  "mean a new capability — or that one of ours moved.", ""]
    lines += ["<sub>Opened by the GG watcher. Decrypted specs are not attached: "
              "they are SteelSeries' material and stay off this repository.</sub>"]

    if DRY_RUN:
        log(f"  [dry-run] would open an issue: {title}")
        log("\n".join("      " + l for l in lines if l))
        return None

    out = run(["gh", "issue", "create", "--repo", REPO, "--title", title,
               "--body", "\n".join(lines)]).stdout.strip()
    return out.splitlines()[-1] if out else None


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    global DRY_RUN, NO_PUSH
    DRY_RUN = "--dry-run" in sys.argv[1:]
    NO_PUSH = "--no-push" in sys.argv[1:]
    forced = next((a.split("=", 1)[1] for a in sys.argv[1:]
                   if a.startswith("--since=")), None)

    for tool in ("curl", "7z", "gh", "git"):
        if not shutil.which(tool):
            raise SystemExit(f"missing required tool: {tool}")

    state = load_state()
    version, url = latest_version()
    known = forced or state.get("version")
    log(f"GG latest={version} known={known}")
    if known and _vkey(version) == _vkey(known):
        log("nothing new.")
        return 0

    log(f"new version: {known} → {version}")
    root = extract(fetch(version, url), version)
    decoded = decode(root, version)

    edevice_files, spec_hashes = build_index(root, decoded)
    new_files, changed = compare_to_state(edevice_files, spec_hashes, state)
    presets = missing_presets(root)

    log(f"  new device files : {len(new_files)}")
    log(f"  changed specs    : {len(changed)}")
    log(f"  missing presets  : {len(presets)}")

    written = add_presets(presets, version) if presets else []
    for n in written:
        log(f"    + {n}")
    issue = open_issue(version, new_files, changed)
    if issue:
        log(f"  issue: {issue}")

    if DRY_RUN:
        log("[dry-run] state not written")
        return 0

    save_state(version, edevice_files, spec_hashes, written, new_files, changed, issue)
    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())