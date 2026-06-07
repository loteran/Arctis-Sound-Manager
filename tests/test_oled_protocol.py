# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused tests for per-device OledProtocol parameterisation.

Validates that:
- The default (no-argument) constructor preserves the original Nova Pro
  Wireless report id (0x06) so the Wireless path is byte-for-byte unchanged.
- Passing report_id=0x01 makes every packet header start with 0x01 (Omni).
- Width/height are forwarded to DISPLAY_WIDTH/DISPLAY_HEIGHT.
- Packet size is always REPORT_SIZE bytes.
"""

import pytest
from arctis_sound_manager.oled_protocol import OledProtocol


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

    def test_brightness_packet_size_is_report_size(self):
        pkt = self.proto.build_brightness_packet(10)
        assert len(pkt) == OledProtocol.REPORT_SIZE

    def test_return_to_ui_packet_size_is_report_size(self):
        pkt = self.proto.build_return_to_ui_packet()
        assert len(pkt) == OledProtocol.REPORT_SIZE


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
