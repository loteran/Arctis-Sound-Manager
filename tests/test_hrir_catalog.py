# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for hrir_catalog — bundled HeSuVi HRIR preset listing/lookup."""

from arctis_sound_manager import hrir_catalog


def test_shanghai_presets_use_ascii_ids():
    # issue #132: the Shanghai ("Hù") presets used to carry a non-ASCII id,
    # which was also the on-disk WAV filename — renamed to ASCII (ssc_hu /
    # ssc_hu+) to stop bsdtar from silently dropping them on extraction.
    options = hrir_catalog.list_hrir_options()
    ids = {o["id"] for o in options}
    assert "ssc_hu" in ids
    assert "ssc_hu+" in ids
    assert not any("ù" in o["id"] for o in options)


def test_shanghai_presets_grouped_under_spatial_sound_card():
    grouped = hrir_catalog.list_hrir_options_grouped()
    by_id = {o["id"]: o for o in grouped}
    assert by_id["ssc_hu"]["group"] == "Spatial Sound Card"
    assert by_id["ssc_hu+"]["group"] == "Spatial Sound Card"


def test_package_hrir_path_resolves_renamed_shanghai_presets():
    assert hrir_catalog.package_hrir_path("ssc_hu") is not None
    assert hrir_catalog.package_hrir_path("ssc_hu+") is not None


def test_package_hrir_path_no_longer_resolves_old_non_ascii_ids():
    assert hrir_catalog.package_hrir_path("ssc_hù") is None
    assert hrir_catalog.package_hrir_path("ssc_hù+") is None


# ── CHA-12: hrir_id must be checked against the catalogue, not just isinstance(str) ──
#
# package_hrir_path() used to build _HRIR_DIR / f"{hrir_id}.wav" with no
# resolve(), no is_relative_to(_HRIR_DIR) and no membership check. A "../"
# id let it read an arbitrary file outside hrir_assets/, which the convolver
# then failed to load — silencing Spatial Audio for Game and Media (issue
# #100's failure mode).

def test_package_hrir_path_refuses_directory_traversal():
    assert hrir_catalog.package_hrir_path("../../../../../../etc/passwd") is None


def test_package_hrir_path_refuses_traversal_even_onto_a_real_file(tmp_path):
    # Prove the guard isn't just "the target file happens not to exist":
    # point the traversal at a file that *does* exist and confirm it is
    # still refused, because "atmos" (adjusted by the "../" segments) is
    # simply not a catalogue id.
    decoy = tmp_path / "sine.wav"
    decoy.write_bytes(b"RIFF....WAVEfmt ")
    rel = "../" * 20 + str(decoy).lstrip("/")
    assert hrir_catalog.package_hrir_path(rel) is None


def test_package_hrir_path_refuses_absolute_path_disguised_as_id():
    assert hrir_catalog.package_hrir_path("/etc/passwd") is None


def test_is_valid_hrir_id_accepts_only_catalogue_members():
    assert hrir_catalog.is_valid_hrir_id("atmos") is True
    assert hrir_catalog.is_valid_hrir_id("../../../../etc/passwd") is False
    assert hrir_catalog.is_valid_hrir_id("not-a-real-id") is False
    assert hrir_catalog.is_valid_hrir_id(None) is False
