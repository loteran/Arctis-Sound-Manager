# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math

# Default OLED parameters (Nova Pro Wireless values, kept as module-level
# constants so external code that reads them directly is not broken).
_DEFAULT_REPORT_ID = 0x06
_DEFAULT_DISPLAY_WIDTH = 128
_DEFAULT_DISPLAY_HEIGHT = 64


class OledProtocol:
    # Class-level constants that are device-independent or purely structural.
    REPORT_SIZE = 1024
    # Brightness and return-to-ui commands are sent as Output reports (64 bytes)
    # matching ggoled behaviour — the Wired GameDAC Gen 2 rejects 1024-byte
    # control packets for these commands (issue #76).
    CONTROL_REPORT_SIZE = 64
    CMD_SCREEN = 0x93
    CMD_BRIGHTNESS = 0x85
    CMD_RETURN_UI = 0x95
    HEADER_SIZE = 6
    MAX_STRIP_WIDTH = 64
    MAX_BRIGHTNESS = 10

    def __init__(
        self,
        report_id: int = _DEFAULT_REPORT_ID,
        width: int = _DEFAULT_DISPLAY_WIDTH,
        height: int = _DEFAULT_DISPLAY_HEIGHT,
    ) -> None:
        """Initialise the protocol with per-device parameters.

        Args:
            report_id: HID report identifier prepended to every packet.
                       Nova Pro Wireless = 0x06, Nova Pro Omni = 0x01.
            width:     OLED panel width in pixels (default 128).
            height:    OLED panel height in pixels (default 64).
        """
        self.report_id = report_id
        self.DISPLAY_WIDTH = width
        self.DISPLAY_HEIGHT = height

    # ------------------------------------------------------------------
    # Legacy class-attribute aliases so callers that access REPORT_ID as
    # a class attribute (e.g. OledProtocol.REPORT_ID) still get the
    # default value and don't break.  Instance attribute takes precedence
    # for instantiated objects.
    REPORT_ID = _DEFAULT_REPORT_ID

    def build_frame_packets(
        self, pixel_data: bytes, width: int, height: int
    ) -> list[list[int]]:
        padded_height = self._pad_height(height)
        packets: list[list[int]] = []

        src_x = 0
        while src_x < width:
            strip_width = min(self.MAX_STRIP_WIDTH, width - src_x)
            body = self._row_major_msb_to_column_major_lsb(
                pixel_data, width, height, padded_height, src_x, strip_width
            )
            header = [
                self.report_id,
                self.CMD_SCREEN,
                src_x,
                0,
                strip_width,
                padded_height,
            ]
            packets.append(self._build_packet(header, body))
            src_x += strip_width

        return packets

    def build_brightness_packet(self, level: int) -> list[int]:
        clamped = max(0, min(self.MAX_BRIGHTNESS, level))
        header = [self.report_id, self.CMD_BRIGHTNESS, clamped, 0, 0, 0]
        # 64-byte Output report — matches ggoled; Wired GameDAC rejects 1024 bytes here.
        return self._build_packet(header, [], size=self.CONTROL_REPORT_SIZE)

    def build_return_to_ui_packet(self) -> list[int]:
        header = [self.report_id, self.CMD_RETURN_UI, 0, 0, 0, 0]
        # 64-byte Output report — same rationale as build_brightness_packet.
        return self._build_packet(header, [], size=self.CONTROL_REPORT_SIZE)

    def _pad_height(self, height: int) -> int:
        return math.ceil(height / 8) * 8

    def _row_major_msb_to_column_major_lsb(
        self,
        pixel_data: bytes,
        width: int,
        height: int,
        padded_height: int,
        src_x: int,
        strip_width: int,
    ) -> list[int]:
        pages = padded_height // 8
        body_size = strip_width * pages
        body = [0] * body_size

        for row in range(height):
            page = row // 8
            bit_pos = row % 8

            for local_col in range(strip_width):
                global_col = src_x + local_col
                pixel_byte_index = row * ((width + 7) // 8) + global_col // 8
                pixel_bit = 7 - (global_col % 8)

                if pixel_byte_index >= len(pixel_data):
                    continue

                pixel_on = (pixel_data[pixel_byte_index] >> pixel_bit) & 1
                if not pixel_on:
                    continue

                body_index = local_col * pages + page
                body[body_index] |= 1 << bit_pos

        return body

    def _build_packet(
        self, header: list[int], body: list[int], size: int | None = None
    ) -> list[int]:
        target = size if size is not None else self.REPORT_SIZE
        payload = header + body
        padding = target - len(payload)
        if padding > 0:
            payload.extend([0] * padding)
        return payload[:target]
