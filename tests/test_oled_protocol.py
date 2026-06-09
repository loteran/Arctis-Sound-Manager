# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused tests for per-device OledProtocol parameterisation.

Validates that:
- The default (no-argument) constructor preserves the original Nova Pro
  Wireless report id (0x06) so the Wireless path is byte-for-byte unchanged.
- Passing report_id=0x01 makes every packet header start with 0x01 (Omni).
- Width/height are forwarded to DISPLAY_WIDTH/DISPLAY_HEIGHT.
- Frame packets are REPORT_SIZE (1024) bytes.
- Brightness and return-to-ui packets are CONTROL_REPORT_SIZE (64) bytes,
  matching ggoled behaviour required by the Wired GameDAC Gen 2 (issue #76).
"""

import pytest
from arctis_sound_manager.oled_protocol import OledProtocol
from arctis_sound_manager.oled_manager import (
    _compute_wvalue,
    _OLED_REPORT_TYPE_FEATURE,
    _OLED_REPORT_TYPE_OUTPUT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solid_frame(width: int, height: int) -> bytes:
    """Return a frame where every pixel is ON (all bits set)."""
    row_bytes = (width + 7) // 8
    return bytes([0xFF] * (row_bytes * height))


# ---------------------------------------------------------------------------
# Default constructor — Wireless path unchanged
# ---------------------------------------------------------------------------

class TestDefaultConstructor:
    def setup_method(self):
        self.proto = OledProtocol()

    def test_default_report_id_is_0x06(self):
        assert self.proto.report_id == 0x06

    def test_default_display_width_128(self):
        assert self.proto.DISPLAY_WIDTH == 128

    def test_default_display_height_64(self):
        assert self.proto.DISPLAY_HEIGHT == 64

    def test_frame_packets_start_with_0x06(self):
        frame = _solid_frame(128, 64)
        packets = self.proto.build_frame_packets(frame, 128, 64)
        assert len(packets) > 0
        for pkt in packets:
            assert pkt[0] == 0x06, (
                f"Expected report_id 0x06, got 0x{pkt[0]:02x}"
            )

    def test_brightness_packet_starts_with_0x06(self):
        pkt = self.proto.build_brightness_packet(5)
        assert pkt[0] == 0x06

    def test_return_to_ui_packet_starts_with_0x06(self):
        pkt = self.proto.build_return_to_ui_packet()
        assert pkt[0] == 0x06

    def test_frame_packet_size_is_report_size(self):
        frame = _solid_frame(128, 64)
        for pkt in self.proto.build_frame_packets(frame, 128, 64):
            assert len(pkt) == OledProtocol.REPORT_SIZE

    def test_brightness_packet_size_is_control_report_size(self):
        # Brightness uses a 64-byte Output report (ggoled-derived, issue #76).
        pkt = self.proto.build_brightness_packet(10)
        assert len(pkt) == OledProtocol.CONTROL_REPORT_SIZE

    def test_return_to_ui_packet_size_is_control_report_size(self):
        # Return-to-UI uses a 64-byte Output report (ggoled-derived, issue #76).
        pkt = self.proto.build_return_to_ui_packet()
        assert len(pkt) == OledProtocol.CONTROL_REPORT_SIZE


# ---------------------------------------------------------------------------
# Omni constructor — report_id=0x01
# ---------------------------------------------------------------------------

class TestOmniConstructor:
    def setup_method(self):
        self.proto = OledProtocol(report_id=0x01)

    def test_report_id_is_0x01(self):
        assert self.proto.report_id == 0x01

    def test_frame_packets_start_with_0x01(self):
        frame = _solid_frame(128, 64)
        packets = self.proto.build_frame_packets(frame, 128, 64)
        assert len(packets) > 0
        for pkt in packets:
            assert pkt[0] == 0x01, (
                f"Expected report_id 0x01, got 0x{pkt[0]:02x}"
            )

    def test_brightness_packet_starts_with_0x01(self):
        pkt = self.proto.build_brightness_packet(5)
        assert pkt[0] == 0x01

    def test_return_to_ui_packet_starts_with_0x01(self):
        pkt = self.proto.build_return_to_ui_packet()
        assert pkt[0] == 0x01

    def test_default_dimensions_unchanged(self):
        assert self.proto.DISPLAY_WIDTH == 128
        assert self.proto.DISPLAY_HEIGHT == 64


# ---------------------------------------------------------------------------
# Width/height forwarding
# ---------------------------------------------------------------------------

class TestCustomDimensions:
    def test_custom_width_height_stored(self):
        proto = OledProtocol(report_id=0x01, width=96, height=32)
        assert proto.DISPLAY_WIDTH == 96
        assert proto.DISPLAY_HEIGHT == 32

    def test_custom_dimensions_do_not_leak_to_other_instances(self):
        """Instances are independent — no shared mutable class state."""
        proto_a = OledProtocol(report_id=0x06, width=128, height=64)
        proto_b = OledProtocol(report_id=0x01, width=96, height=32)
        assert proto_a.DISPLAY_WIDTH == 128
        assert proto_b.DISPLAY_WIDTH == 96
        assert proto_a.report_id == 0x06
        assert proto_b.report_id == 0x01


# ---------------------------------------------------------------------------
# Class-level fallback (REPORT_ID class attribute kept for legacy access)
# ---------------------------------------------------------------------------

class TestClassLevelFallback:
    def test_class_attribute_report_id_is_0x06(self):
        """OledProtocol.REPORT_ID class attribute retains 0x06 for legacy reads."""
        assert OledProtocol.REPORT_ID == 0x06

    def test_instance_report_id_takes_precedence_over_class_attr(self):
        proto = OledProtocol(report_id=0x01)
        assert proto.report_id == 0x01
        # Class attribute is still the default
        assert OledProtocol.REPORT_ID == 0x06


# ---------------------------------------------------------------------------
# wValue computation helper — issue #76 (Wired GameDAC Gen 2 fix)
# ---------------------------------------------------------------------------

class TestComputeWvalue:
    """Unit-tests for _compute_wvalue without requiring USB hardware.

    The Wired GameDAC Gen 2 requires wValue = (report_type << 8) | report_id.
    The Wireless firmware accepted the wrong 0x0300 for any report_id; the
    Wired firmware does not — it stalls the control transfer → Errno 110.
    """

    def test_feature_report_wvalue(self):
        # report_type=0x03, report_id=0x06 → 0x0306
        assert _compute_wvalue(_OLED_REPORT_TYPE_FEATURE, 0x06) == 0x0306

    def test_output_report_wvalue(self):
        # report_type=0x02, report_id=0x06 → 0x0206
        assert _compute_wvalue(_OLED_REPORT_TYPE_OUTPUT, 0x06) == 0x0206

    def test_report_id_masked_to_byte(self):
        # High bits of report_id must not bleed into the type nibble.
        assert _compute_wvalue(_OLED_REPORT_TYPE_FEATURE, 0x106) == 0x0306

    def test_wvalue_0x0300_base_gives_0x0306_for_report_id_0x06(self):
        # Simulates what __init__ does: derive frame_report_type from wvalue YAML field.
        wvalue_yaml = 0x0300
        frame_report_type = (wvalue_yaml >> 8) & 0xFF  # == 0x03
        assert _compute_wvalue(frame_report_type, 0x06) == 0x0306

    def test_constants_have_expected_values(self):
        assert _OLED_REPORT_TYPE_FEATURE == 0x03
        assert _OLED_REPORT_TYPE_OUTPUT == 0x02
