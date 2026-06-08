# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for OledRenderer text measurement."""
import math
import pytest
from PIL import ImageFont

from arctis_sound_manager.oled_renderer import OledRenderer


@pytest.fixture
def renderer():
    return OledRenderer()


@pytest.mark.parametrize("preset,sz", [
    ("Crimson Desert", 8),
    ("Crimson Desert", 16),
    ("Crimson Desert", 10),
    ("Flat", 8),
    ("Bass Boost Heavy", 8),
])
def test_measure_eq_text_ge_actual_pixels(renderer, preset, sz):
    """measure_eq_text must return >= the true last rendered pixel + 1."""
    from PIL import Image as _Image, ImageDraw as _IDraw, ImageFont as _IFont
    font = _IFont.load_default(size=max(7, min(30, sz)))
    text = f"EQ: {preset}"
    wide = math.ceil(font.getlength(text)) + 32
    h = font.getbbox(text)[3] + 2
    img = _Image.new("1", (wide, h), color=0)
    _IDraw.Draw(img).text((0, 0), text, font=font, fill=1)
    last_x = max(
        (x for x in range(wide) for y in range(h) if img.getpixel((x, y))),
        default=-1,
    )
    true_width = last_x + 1
    result = renderer.measure_eq_text(preset, sz)
    assert result >= true_width, (
        f"measure_eq_text({preset!r}, {sz}) = {result} < true pixel width = {true_width}"
    )


@pytest.mark.parametrize("profile,sz", [
    ("Nova Pro Default", 8),
    ("Nova Pro Default", 16),
    ("Gaming", 8),
])
def test_measure_profile_text_ge_actual_pixels(renderer, profile, sz):
    """measure_profile_text must return >= the true last rendered pixel + 1."""
    from PIL import Image as _Image, ImageDraw as _IDraw, ImageFont as _IFont
    font = _IFont.load_default(size=max(7, min(30, sz)))
    text = f"Profile: {profile}"
    wide = math.ceil(font.getlength(text)) + 32
    h = font.getbbox(text)[3] + 2
    img = _Image.new("1", (wide, h), color=0)
    _IDraw.Draw(img).text((0, 0), text, font=font, fill=1)
    last_x = max(
        (x for x in range(wide) for y in range(h) if img.getpixel((x, y))),
        default=-1,
    )
    true_width = last_x + 1
    result = renderer.measure_profile_text(profile, sz)
    assert result >= true_width


def test_measure_eq_crimson_desert_scroll_reaches_end(renderer):
    """At max_offset the last glyph pixel must be within the 128px canvas (x <= 127)."""
    preset = "Crimson Desert"
    sz = 8
    text_w = renderer.measure_eq_text(preset, sz)
    max_offset = text_w - (renderer.WIDTH - 2)   # formula from oled_manager

    if max_offset <= 0:
        pytest.skip("preset fits without scrolling at this font size")

    # At max scroll: draw origin = 1 - max_offset
    draw_origin_x = 1 - max_offset
    font = ImageFont.load_default(size=sz)
    bbox = font.getbbox(f"EQ: {preset}")
    last_glyph_pixel_x = draw_origin_x + bbox[2] - 1
    assert last_glyph_pixel_x <= renderer.WIDTH - 1, (
        f"Last glyph pixel at x={last_glyph_pixel_x}, expected <= {renderer.WIDTH - 1}"
    )
