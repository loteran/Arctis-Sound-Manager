# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A published PPA source is not an installable package.

Launchpad accepts an upload, publishes the source, and *then* builds it. That
build can fail on its own: 1.4.8-1~noble1 did, on 2026-08-23, two and a half
minutes in — while the release workflow reported success, because it only ever
measured the upload. Ubuntu users had no package for a day and nothing said so.

check_ppa() looked at the source's publication status alone, so it would have
called that release delivered. Same shape as PKG-2 on the AUR side: the audit
reports green while a fresh install is impossible. check_copr() next to it has
always refused to call a build delivered until it succeeded; this is that
discipline, applied to the channel that lacked it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vrd", Path(__file__).resolve().parents[1] / "scripts" / "verify_release_delivery.py")
vrd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vrd)


def _sources(*, status="Published", version="1.4.8-1~noble1", series="noble"):
    return {"entries": [{
        "source_package_version": version,
        "status": status,
        "distro_series_link": f"https://api.launchpad.net/1.0/ubuntu/{series}",
        "self_link": "https://api.launchpad.net/1.0/fake/source",
    }]}


def _patch(monkeypatch, sources, builds):
    def fake_get(url):
        return builds if "getBuilds" in url else sources
    monkeypatch.setattr(vrd, "_get", fake_get)


def test_a_failed_build_is_not_delivered(monkeypatch):
    """The 1.4.8 case, verbatim."""
    _patch(monkeypatch, _sources(), {"entries": [
        {"buildstate": "Failed to build", "arch_tag": "amd64",
         "web_link": "https://launchpad.net/build/33535007"}]})

    status, note = vrd.check_ppa("1.4.8")

    assert status == vrd.MISSING
    assert "FAILED" in note
    assert "33535007" in note, "the log link is what makes the report actionable"


def test_a_queued_build_is_pending_not_delivered(monkeypatch):
    """Minutes after a tag: source accepted, nothing installable yet."""
    _patch(monkeypatch, _sources(version="1.4.10-1~noble1"), {"entries": [
        {"buildstate": "Needs building", "arch_tag": "amd64"}]})

    status, note = vrd.check_ppa("1.4.10")

    assert status == vrd.PENDING
    assert "Needs building" in note


def test_a_successful_build_is_delivered(monkeypatch):
    _patch(monkeypatch, _sources(version="1.4.9-1~noble1"), {"entries": [
        {"buildstate": "Successfully built", "arch_tag": "amd64"}]})

    status, note = vrd.check_ppa("1.4.9")

    assert status == vrd.DELIVERED
    assert "noble/amd64" in note


def test_one_failed_series_fails_the_whole_check(monkeypatch):
    """Two series are published; one built, one did not. Users of the broken
    series have nothing, so the answer is not "delivered"."""
    sources = {"entries": [
        {"source_package_version": "1.4.10-1~noble1", "status": "Published",
         "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/noble",
         "self_link": "https://api.launchpad.net/1.0/fake/noble"},
        {"source_package_version": "1.4.10-1~resolute1", "status": "Published",
         "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/resolute",
         "self_link": "https://api.launchpad.net/1.0/fake/resolute"},
    ]}

    def fake_get(url):
        if "getBuilds" not in url:
            return sources
        if "resolute" in url:
            return {"entries": [{"buildstate": "Failed to build", "arch_tag": "amd64"}]}
        return {"entries": [{"buildstate": "Successfully built", "arch_tag": "amd64"}]}

    monkeypatch.setattr(vrd, "_get", fake_get)

    status, _ = vrd.check_ppa("1.4.10")

    assert status == vrd.MISSING


def test_an_unreachable_build_api_never_claims_delivered(monkeypatch):
    """The audit's own network trouble must not read as a working release —
    nor fail one. It reports what it could not check."""
    def fake_get(url):
        if "getBuilds" in url:
            raise OSError("launchpad unreachable")
        return _sources(version="1.4.10-1~noble1")

    monkeypatch.setattr(vrd, "_get", fake_get)

    status, note = vrd.check_ppa("1.4.10")

    assert status == vrd.PENDING
    assert "unavailable" in note


def test_no_source_at_all_is_still_missing(monkeypatch):
    _patch(monkeypatch, {"entries": []}, {"entries": []})
    assert vrd.check_ppa("1.4.10")[0] == vrd.MISSING
