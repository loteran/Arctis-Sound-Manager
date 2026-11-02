# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#362: a usb_device event without udev's ID_VENDOR_ID / ID_MODEL_ID was
dropped, so a removal could go unnoticed. The kernel's PRODUCT is the
fallback."""
from __future__ import annotations

from arctis_sound_manager.usb_devices_monitor import _vid_pid


def test_udev_ids_win():
    assert _vid_pid({'ID_VENDOR_ID': '1038', 'ID_MODEL_ID': '12ad',
                     'PRODUCT': '1038/12ad/119'}) == (0x1038, 0x12ad)


def test_product_fallback_without_udev_ids():
    assert _vid_pid({'PRODUCT': '1038/12ad/119'}) == (0x1038, 0x12ad)
    assert _vid_pid({'ID_VENDOR_ID': '', 'PRODUCT': '1038/2202/100'}) == (0x1038, 0x2202)


def test_nothing_usable():
    assert _vid_pid({}) is None
    assert _vid_pid({'PRODUCT': 'garbage'}) is None
    assert _vid_pid({'PRODUCT': '11038/12ad/1'}) is None
    assert _vid_pid({'ID_VENDOR_ID': 'zz', 'ID_MODEL_ID': '12ad'}) is None
