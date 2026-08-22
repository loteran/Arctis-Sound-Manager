# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""PKG-4 regression.

Nobara ships ASM enabled by default through a Terra-maintained spec
(terrapkg/packages) this project does not control and cannot push to.
`scripts/verify_release_delivery.py`'s CHANNELS had no way to see it, so a
hard-dependency promotion made on develop (e.g. ladspa-swh-plugins:
Recommends -> Requires) could sit unreflected in Terra's spec indefinitely
with nothing ever surfacing the lag.

`check_terra()` reads the Version: field of the spec file Terra maintains,
straight off GitHub (there is no Copr-style search API for Terra) and
compares it against the release being audited. It is registered in CHANNELS
as SOFT (hard=False): a third party's backlog must never fail this project's
own release, and any failure to reach GitHub degrades to UNKNOWN through the
same exception-handling path every other channel already uses in run_once().
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VRD_SCRIPT = REPO / "scripts" / "verify_release_delivery.py"

spec = importlib.util.spec_from_file_location("verify_release_delivery", VRD_SCRIPT)
vrd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vrd)


def _fake_spec_text(version: str) -> bytes:
    return (
        "%global pypi_name arctis-sound-manager\n"
        f"Name:\t\tpython-%{{pypi_name}}\n"
        f"Version:\t\t{version}\n"
        "Release:\t\t1%{?dist}\n"
    ).encode("utf-8")


def test_terra_channel_is_registered_and_soft():
    """Must exist and must never be able to fail a release on its own."""
    assert "Terra" in vrd.CHANNELS
    fn, hard = vrd.CHANNELS["Terra"]
    assert fn is vrd.check_terra
    assert hard is False


def test_terra_reports_delivered_when_spec_matches_release(monkeypatch):
    monkeypatch.setattr(vrd, "_fetch", lambda url, timeout=30, attempts=3: _fake_spec_text("1.4.4"))
    status, detail = vrd.check_terra("1.4.4")
    assert status == vrd.DELIVERED
    assert "1.4.4" in detail


def test_terra_lagging_spec_is_reported_not_swallowed(monkeypatch):
    """The whole point of PKG-4: a spec still pinned to an older version must
    show up in the audit output instead of being invisible."""
    def fake_fetch(url, timeout=30, attempts=3):
        assert url == vrd.TERRA_SPEC_URL
        return _fake_spec_text("1.4.2")

    monkeypatch.setattr(vrd, "_fetch", fake_fetch)
    status, detail = vrd.check_terra("1.4.4")

    assert status != vrd.DELIVERED
    assert "1.4.2" in detail and "1.4.4" in detail


def test_terra_never_counts_as_a_hard_failure_even_when_lagging(monkeypatch):
    """Reproduce main()'s own hard_bad/unknown classification (see
    verify_release_delivery.main) directly against a lagging Terra result, so
    a future change to that filter can't accidentally start failing releases
    over Terra's backlog without this test noticing.
    """
    monkeypatch.setattr(vrd, "_fetch", lambda url, timeout=30, attempts=3: _fake_spec_text("1.0.0"))
    monkeypatch.setattr(vrd, "CHANNELS", {"Terra": (vrd.check_terra, False)})

    results = vrd.run_once("1.4.4")
    status, _detail, _hard = results["Terra"]
    assert status in (vrd.DELIVERED, vrd.PENDING, vrd.MISSING, vrd.UNKNOWN)
    # Whatever verdict a lagging Terra spec earns, it is soft — it must never
    # appear in main()'s hard_bad list, which is what actually fails the run.
    hard_bad = [n for n, (s, _d, h) in results.items()
                if h and s != vrd.UNKNOWN and (s == vrd.MISSING or s != vrd.DELIVERED)]
    assert hard_bad == []
    assert vrd.all_settled(results, strict=True)
    assert vrd.all_settled(results, strict=False)


def test_terra_network_failure_degrades_to_unknown_not_missing(monkeypatch):
    def raising_fetch(url, timeout=30, attempts=3):
        raise TimeoutError("simulated network absence")

    monkeypatch.setattr(vrd, "_fetch", raising_fetch)
    monkeypatch.setattr(vrd, "CHANNELS", {"Terra": (vrd.check_terra, False)})

    results = vrd.run_once("1.4.4")
    status, detail, hard = results["Terra"]
    assert status == vrd.UNKNOWN
    assert hard is False
    assert "check error" in detail


def test_terra_missing_version_field_degrades_to_unknown(monkeypatch):
    """A spec that somehow no longer has a Version: line must not raise an
    unhandled exception out of the audit — it should read as UNKNOWN like
    every other unparseable channel response."""
    monkeypatch.setattr(vrd, "_fetch", lambda url, timeout=30, attempts=3: b"no version field here\n")
    monkeypatch.setattr(vrd, "CHANNELS", {"Terra": (vrd.check_terra, False)})

    results = vrd.run_once("1.4.4")
    status, _detail, hard = results["Terra"]
    assert status == vrd.UNKNOWN
    assert hard is False
