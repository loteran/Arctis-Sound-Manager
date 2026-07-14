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
