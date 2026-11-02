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
        frame_report_size: int = REPORT_SIZE,
        control_report_size: int = CONTROL_REPORT_SIZE,
        min_brightness: int = 0,
        transpose: bool = False,
        y_offset: int = 0,
    ) -> None:
        """Initialise the protocol with per-device parameters.

        Args:
            report_id: HID report identifier prepended to every packet.
                       Nova Pro Wireless = 0x06, Nova Pro Omni = 0x01.
            width:     OLED panel width in pixels (default 128).
            height:    OLED panel height in pixels (default 64).
            frame_report_size / control_report_size:
                       HID chunk sizes from the device's spec, report id byte
                       included (Nova Elite/Omni: 1036 for draw_bitmap).
                       Report id 0x00 means unnumbered reports: that byte is
                       dropped from what goes on the wire.
            min_brightness: lowest level the firmware accepts; the Elite
                       family's range is 1-10, so 0 cannot darken it.
            transpose: the controller addresses the panel rotated 90°
                       (Arctis Pro GameDAC: a 128x52 panel drawn as 128
                       rows of 52 pixels, bottom pixel first).
            y_offset:  panel row the bitmap's top edge lands on (Nova Pro
                       Wired: 12, under the DAC's own status strip).
        """
        self.report_id = report_id
        self.DISPLAY_WIDTH = width
        self.DISPLAY_HEIGHT = height
        self.REPORT_SIZE = frame_report_size
        self.CONTROL_REPORT_SIZE = control_report_size
        self.min_brightness = min_brightness
        self.transpose = transpose
        self.y_offset = y_offset

    @property
    def can_go_dark(self) -> bool:
        """Whether brightness 0 is a level the panel accepts."""
        return self.min_brightness == 0

    # ------------------------------------------------------------------
    # Legacy class-attribute aliases so callers that access REPORT_ID as
    # a class attribute (e.g. OledProtocol.REPORT_ID) still get the
    # default value and don't break.  Instance attribute takes precedence
    # for instantiated objects.
    REPORT_ID = _DEFAULT_REPORT_ID

    def build_frame_packets(
        self, pixel_data: bytes, width: int, height: int
    ) -> list[list[int]]:
        if self.transpose:
            # One frame, header in the controller's axes (width = panel height).
            header = [self.report_id, self.CMD_SCREEN, 0, 0, height, width]
            body = self._row_major_msb_to_rotated_lsb(pixel_data, width, height)
            return [self._build_packet(header, body)]
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
                self.y_offset,
                strip_width,
                padded_height,
            ]
            packets.append(self._build_packet(header, body))
            src_x += strip_width

        return packets

    def build_brightness_packet(self, level: int) -> list[int]:
        clamped = max(self.min_brightness, min(self.MAX_BRIGHTNESS, level))
        header = [self.report_id, self.CMD_BRIGHTNESS, clamped, 0, 0, 0]
        # Short Output report — matches ggoled; Wired GameDAC rejects 1024 bytes here.
        return self._build_packet(header, [], size=self.CONTROL_REPORT_SIZE)

    def build_return_to_ui_packet(self) -> list[int]:
        header = [self.report_id, self.CMD_RETURN_UI, 0, 0, 0, 0]
        # 64-byte Output report — same rationale as build_brightness_packet.
        return self._build_packet(header, [], size=self.CONTROL_REPORT_SIZE)

    def build_preamble_packets(self) -> list[list[int]]:
        """Control reports a frame needs sent before it. None for this family."""
        return []

    def build_blank_frame_packets(self) -> list[list[int]]:
        """An all-dark frame, for panels that cannot be dimmed to 0."""
        size = ((self.DISPLAY_WIDTH + 7) // 8) * self.DISPLAY_HEIGHT
        return self.build_frame_packets(
            bytes(size), self.DISPLAY_WIDTH, self.DISPLAY_HEIGHT
        )

    @staticmethod
    def _row_major_msb_to_rotated_lsb(
        pixel_data: bytes, width: int, height: int
    ) -> list[int]:
        """GG's convert-to-column-packed-byte-format-with-LSB, read from its
        disassembly (GG 121): panel column x is a run of whole bytes, bottom
        pixel in bit 0 — bit x * padded_height + (height - 1 - y)."""
        src_stride = (width + 7) // 8
        padded_height = math.ceil(height / 8) * 8
        body = [0] * (width * padded_height // 8)
        for y in range(height):
            for x in range(width):
                idx = y * src_stride + x // 8
                if idx < len(pixel_data) and (pixel_data[idx] >> (7 - x % 8)) & 1:
                    bit = x * padded_height + (height - 1 - y)
                    body[bit // 8] |= 1 << (bit % 8)
        return body

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
        payload = payload[:target]
        # Report id 0x00 = the device uses no report ids. The spec still
        # counts that byte in its chunk size (hidapi's convention), but it is
        # not part of the report: the device would read 0x93 at offset 1.
        if self.report_id == 0x00:
            return payload[1:]
        return payload


class SiberiaOledProtocol(OledProtocol):
    """The Siberia 840 screen protocol, which the Arctis Pro Wireless base
    inherits (its spec `include`s siberia-840).

    From the siberia-840 spec: set_custom_write_mode [0xD0, 0x01, 0x0F] and
    set_custom_write_offset [0xD1, 0x00, 0x10], each followed by 250 ms, take
    the screen; oled_display [0xD2, 0x00, data] then draws a whole 128x48
    frame in one 1025-byte Feature report; reset_screen [0xD0, 0x00, 0x00]
    hands it back. Brightness is the base's own oled_brightness,
    [0x85, 0xAA, 0-10].

    GG packs the frame with `convert-to-column-packed-byte-format`, a builtin
    the spec does not define — unlike its `-with-LSB` variants, which ASM
    already speaks. `packing` names the layout: column_* puts 8 vertical
    pixels in a byte, columns left to right; row_* 8 horizontal ones, rows
    top to bottom; _msb/_lsb is which bit holds the first pixel.
    scripts/reverse-engineering/oled_packing_probe.py tells them apart on
    the hardware.
    """

    CMD_WRITE_MODE = 0xD0
    CMD_WRITE_OFFSET = 0xD1
    CMD_DISPLAY = 0xD2
    CHECK_BYTE = 0xAA
    #: page_lsb is GG's own (utils.defConvertToColumnPackedByteFormat,
    #: disassembled from GG 118): byte (y // 8) * width + x, bit y % 8, bit
    #: 0 = top. Confirmed on a base by oled_page_probe.py (#363, for #305).
    PACKINGS = ("column_msb", "column_lsb", "row_msb", "row_lsb", "page_lsb")
    #: The 0x0F in set_custom_write_mode reads as seconds. Re-taking the
    #: screen well inside that keeps it ours whichever way the firmware
    #: counts them (from the mode switch, or from the last frame).
    PREAMBLE_REFRESH_S = 10.0
    PREAMBLE_GAP_S = 0.25

    def __init__(self, *args, packing: str = "column_msb", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if packing not in self.PACKINGS:
            raise ValueError(f"Unknown OLED packing {packing!r}")
        self.packing = packing

    def build_frame_packets(
        self, pixel_data: bytes, width: int, height: int
    ) -> list[list[int]]:
        body = self.pack(pixel_data, width, height, self.packing)
        return [self._build_packet([self.report_id, self.CMD_DISPLAY, 0x00], body)]

    def build_preamble_packets(self) -> list[list[int]]:
        return [
            self._build_packet([self.report_id, self.CMD_WRITE_MODE, 0x01, 0x0F], [],
                               size=self.CONTROL_REPORT_SIZE),
            self._build_packet([self.report_id, self.CMD_WRITE_OFFSET, 0x00, 0x10], [],
                               size=self.CONTROL_REPORT_SIZE),
        ]

    def build_brightness_packet(self, level: int) -> list[int]:
        clamped = max(self.min_brightness, min(self.MAX_BRIGHTNESS, level))
        return self._build_packet([self.report_id, self.CMD_BRIGHTNESS, self.CHECK_BYTE, clamped], [],
                                  size=self.CONTROL_REPORT_SIZE)

    def build_return_to_ui_packet(self) -> list[int]:
        return self._build_packet([self.report_id, self.CMD_WRITE_MODE, 0x00, 0x00], [],
                                  size=self.CONTROL_REPORT_SIZE)

    @staticmethod
    def pack(pixel_data: bytes, width: int, height: int, packing: str) -> list[int]:
        """Repack a row-major MSB-first 1-bpp image into `packing`."""
        src_stride = (width + 7) // 8
        column = packing.startswith("column")
        page = packing.startswith("page")
        lsb = packing.endswith("lsb")
        pages = (height + 7) // 8
        out = [0] * (width * pages if column or page else src_stride * height)
        for y in range(height):
            for x in range(width):
                idx = y * src_stride + x // 8
                if idx >= len(pixel_data) or not (pixel_data[idx] >> (7 - x % 8)) & 1:
                    continue
                if page:
                    index, pos = (y // 8) * width + x, y % 8
                elif column:
                    index, pos = x * pages + y // 8, y % 8
                else:
                    index, pos = y * src_stride + x // 8, x % 8
                out[index] |= 1 << (pos if lsb else 7 - pos)
        return out
