# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""PKG-2 regression.

`.github/workflows/pacman-repo.yaml`'s "Build AUR-only dependencies" step used
to be `continue-on-error: true` for the whole step, and its loop treated
`python-pulsectl` — a hard dependency ASM cannot run without — exactly like
the optional `noise-suppression-for-voice`: a failed build only printed
`::warning::` and the job stayed green. `scripts/verify_release_delivery.py`'s
`check_pacman()` only looked at whether `arctis-sound-manager` itself was in
the repo database, so the daily audit reported green too. Together: a fresh
`pacman -S arctis-sound-manager` could become unresolvable with nothing in CI
ever turning red — the #178 pattern, for the package ASM cannot run without.

Two independent things are tested here:

1. The workflow step's actual bash logic (extracted from the YAML and run for
   real, with `sudo`/`git`/`chown`/`makepkg` stubbed out) must now exit
   non-zero — and the step must carry no `continue-on-error` to swallow that
   — when `python-pulsectl` fails to build, while a `noise-suppression-for-voice`
   failure alone must still only warn.
2. `verify_release_delivery.check_pacman()` must independently catch a repo
   database that has the main package but not `python-pulsectl`, without
   trusting that the workflow above behaved.
"""
from __future__ import annotations

import importlib.util
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pacman-repo.yaml"
VRD_SCRIPT = REPO / "scripts" / "verify_release_delivery.py"

spec = importlib.util.spec_from_file_location("verify_release_delivery", VRD_SCRIPT)
vrd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vrd)


# ---------------------------------------------------------------------------
# 1. The workflow step itself
# ---------------------------------------------------------------------------


def _aur_deps_step() -> dict:
    yaml = YAML(typ="safe")
    with open(WORKFLOW, encoding="utf-8") as f:
        doc = yaml.load(f)
    for step in doc["jobs"]["build"]["steps"]:
        if step.get("name") == "Build AUR-only dependencies":
            return step
    raise AssertionError("'Build AUR-only dependencies' step not found in pacman-repo.yaml")


def test_step_has_no_continue_on_error():
    """A future `continue-on-error: true` on this step would swallow the
    `exit 1` this test exercises below just as effectively as the loop logic
    it replaced — the job would stay green either way."""
    step = _aur_deps_step()
    assert "continue-on-error" not in step, (
        "the 'Build AUR-only dependencies' step must not carry "
        "continue-on-error: it would silence the hard-dependency exit 1"
    )


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_step(tmp_path: Path, fail_pkgs: str) -> subprocess.CompletedProcess:
    """Execute the step's real `run:` script, with the AUR/makepkg/root-only
    parts stubbed out, and *fail_pkgs* (space-separated AUR pkgnames)
    simulated as failing to build.
    """
    script = _aur_deps_step()["run"]

    repo_dir = tmp_path / "repo"
    home_builder = tmp_path / "home_builder"
    work_dir = tmp_path / "work"
    repo_dir.mkdir()
    home_builder.mkdir()
    work_dir.mkdir()

    # The script hardcodes these paths (it runs inside the archlinux:base-devel
    # container in CI) — redirect them into the test's own sandbox without
    # touching the rest of the logic under test.
    script = script.replace("/repo/", f"{repo_dir}/")
    script = script.replace("/home/builder", str(home_builder))
    script = script.replace("/tmp/${pkg}", f"{work_dir}/${{pkg}}")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_stub(bin_dir, "chown", "exit 0")
    _write_stub(
        bin_dir,
        "sudo",
        # Real invocations here are all `sudo -u builder <cmd...>`.
        'if [ "$1" = "-u" ]; then shift 2; fi\nexec "$@"',
    )
    _write_stub(
        bin_dir,
        "git",
        (
            'if [ "$1" = "clone" ]; then\n'
            '  dest="${@: -1}"\n'
            '  rm -rf "$dest"\n'
            '  mkdir -p "$dest"\n'
            "  printf 'epoch=1\\npkgver=1.0\\npkgrel=1\\n' > \"$dest/PKGBUILD\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 1"
        ),
    )
    _write_stub(
        bin_dir,
        "makepkg",
        (
            'pkg="$(basename "$PWD")"\n'
            'for f in $FAIL_PKGS; do\n'
            '  if [ "$f" = "$pkg" ]; then\n'
            '    echo "makepkg: simulated failure for $pkg" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "done\n"
            'touch "${pkg}-1.0-1-x86_64.pkg.tar.zst"\n'
            "exit 0"
        ),
    )

    summary = tmp_path / "step_summary.txt"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    env["FAIL_PKGS"] = fail_pkgs

    return subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hard_dependency_failure_fails_the_step(tmp_path):
    """python-pulsectl is a hard dependency: its build failing must fail the
    job, not just print a warning."""
    result = _run_step(tmp_path, fail_pkgs="python-pulsectl")
    assert result.returncode != 0, (
        f"expected non-zero exit when python-pulsectl fails to build\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "::error::" in result.stdout
    assert "python-pulsectl" in result.stdout


def test_optional_dependency_failure_only_warns(tmp_path):
    """noise-suppression-for-voice is an optdepend: its build failing must
    keep behaving exactly like before — a warning, not a failed job."""
    result = _run_step(tmp_path, fail_pkgs="noise-suppression-for-voice")
    assert result.returncode == 0, (
        f"an optional-dependency failure must not fail the step\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "::warning::" in result.stdout
    assert "noise-suppression-for-voice" in result.stdout
    assert "::error::" not in result.stdout


def test_both_dependencies_building_is_clean(tmp_path):
    result = _run_step(tmp_path, fail_pkgs="")
    assert result.returncode == 0
    assert "::error::" not in result.stdout
    assert "::warning::" not in result.stdout


# ---------------------------------------------------------------------------
# 2. verify_release_delivery.check_pacman()'s own, independent check
# ---------------------------------------------------------------------------


def _fake_repo_db(entries: dict[str, str]) -> bytes:
    """Build a minimal repo-add-shaped .db.tar.gz: one <name>/desc member per
    package, keyed by "<pkgname>-<pkgver>-<pkgrel>" as check_pacman expects.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for member_dir, content in entries.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"{member_dir}/desc")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_check_pacman_delivered_when_hard_dep_present(monkeypatch):
    db = _fake_repo_db(
        {
            "arctis-sound-manager-1.4.4-1": "%NAME%\narctis-sound-manager\n",
            "python-pulsectl-24.12.0-1": "%NAME%\npython-pulsectl\n",
        }
    )
    monkeypatch.setattr(vrd, "_get_bytes", lambda url: db)
    status, detail = vrd.check_pacman("1.4.4")
    assert status == vrd.DELIVERED, detail


def test_check_pacman_missing_when_hard_dep_absent(monkeypatch):
    """This is the PKG-2 audit gap: the main package is right there at the
    right version, but a fresh install still can't resolve — the database
    used to be read as green anyway."""
    db = _fake_repo_db(
        {
            "arctis-sound-manager-1.4.4-1": "%NAME%\narctis-sound-manager\n",
            # python-pulsectl missing entirely.
        }
    )
    monkeypatch.setattr(vrd, "_get_bytes", lambda url: db)
    status, detail = vrd.check_pacman("1.4.4")
    assert status == vrd.MISSING
    assert "python-pulsectl" in detail


def test_check_pacman_still_missing_when_project_itself_absent(monkeypatch):
    """Sanity: the pre-existing behavior (no arctis-sound-manager entry at
    all) must still be MISSING."""
    db = _fake_repo_db({"python-pulsectl-24.12.0-1": "%NAME%\npython-pulsectl\n"})
    monkeypatch.setattr(vrd, "_get_bytes", lambda url: db)
    status, detail = vrd.check_pacman("1.4.4")
    assert status == vrd.MISSING


def test_aur_only_hard_deps_still_lists_pulsectl():
    """Guard against the constant silently losing the one entry that matters."""
    assert "python-pulsectl" in vrd.AUR_ONLY_HARD_DEPS
