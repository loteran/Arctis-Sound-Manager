from __future__ import annotations

import math


class OledProtocol:
    DISPLAY_WIDTH = 128
    DISPLAY_HEIGHT = 64
    REPORT_SIZE = 1024
    OLED_INTERFACE = 4

    REPORT_ID = 0x06
    CMD_SCREEN = 0x93
    CMD_BRIGHTNESS = 0x85
    CMD_RETURN_UI = 0x95
    HEADER_SIZE = 6
    MAX_STRIP_WIDTH = 64
    MAX_BRIGHTNESS = 10

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
                self.REPORT_ID,
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
        header = [self.REPORT_ID, self.CMD_BRIGHTNESS, clamped, 0, 0, 0]
        return self._build_packet(header, [])

    def build_return_to_ui_packet(self) -> list[int]:
        header = [self.REPORT_ID, self.CMD_RETURN_UI, 0, 0, 0, 0]
        return self._build_packet(header, [])

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

    def _build_packet(self, header: list[int], body: list[int]) -> list[int]:
        payload = header + body
        padding = self.REPORT_SIZE - len(payload)
        if padding > 0:
            payload.extend([0] * padding)
        return payload[: self.REPORT_SIZE]
