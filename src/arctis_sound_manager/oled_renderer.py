from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


class OledRenderer:
    WIDTH = 128
    HEIGHT = 64

    def __init__(self) -> None:
        self._font = ImageFont.load_default()

    def _image_to_bytes(self, image: Image.Image) -> bytes:
        mono = image.convert("1")
        return mono.tobytes()

    def _draw_battery_icon(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, percent: int, charging: bool, blink_state: bool = True
    ) -> int:
        body_w, body_h = 18, 9
        tip_w, tip_h = 2, 5

        draw.rectangle([x, y, x + body_w - 1, y + body_h - 1], outline=1, fill=0)
        draw.rectangle(
            [x + body_w, y + (body_h - tip_h) // 2, x + body_w + tip_w - 1, y + (body_h + tip_h) // 2 - 1],
            fill=1,
        )

        fill_max = body_w - 4
        fill_w = max(0, int(fill_max * max(0, min(100, percent)) / 100))
        if fill_w > 0:
            draw.rectangle([x + 2, y + 2, x + 2 + fill_w - 1, y + body_h - 3], fill=1)

        if charging and blink_state:
            cx = x + body_w // 2
            cy = y + body_h // 2
            draw.line([(cx + 1, cy - 3), (cx - 1, cy)], fill=1, width=1)
            draw.line([(cx - 1, cy), (cx + 1, cy + 3)], fill=1, width=1)

        return body_w + tip_w + 1

    def _draw_bar(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, percent: int
    ) -> None:
        draw.rectangle([x, y, x + width - 1, y + height - 1], outline=1, fill=0)
        fill_w = max(0, int((width - 2) * max(0, min(100, percent)) / 100))
        if fill_w > 0:
            draw.rectangle([x + 1, y + 1, x + fill_w, y + height - 2], fill=1)

    def render_status(
        self,
        battery_percent: int,
        charging: bool,
        time_str: str,
        active_profile: str,
        sidetone_level: int,
        blink_state: bool = True,
        eq_preset: str = "",
    ) -> bytes:
        image = Image.new("1", (self.WIDTH, self.HEIGHT), color=0)
        draw = ImageDraw.Draw(image)
        font = self._font

        draw.text((1, 1), time_str, font=font, fill=1)

        bat_label = f"{max(0, battery_percent)}%" if battery_percent >= 0 else "?%"
        bat_label_w = int(font.getlength(bat_label))
        icon_w = 22
        bat_total_w = icon_w + 2 + bat_label_w
        bat_x = self.WIDTH - bat_total_w - 1
        self._draw_battery_icon(draw, bat_x, 1, battery_percent if battery_percent >= 0 else 0, charging, blink_state)
        draw.text((bat_x + icon_w + 2, 1), bat_label, font=font, fill=1)

        draw.line([(0, 12), (self.WIDTH - 1, 12)], fill=1)

        draw.text((1, 15), f"Profile: {active_profile}", font=font, fill=1)

        draw.text((1, 26), f"Sidetone: {sidetone_level}%", font=font, fill=1)
        self._draw_bar(draw, 1, 36, self.WIDTH - 2, 7, sidetone_level)

        if eq_preset:
            label = eq_preset if len(eq_preset) <= 18 else eq_preset[:17] + "\u2026"
            draw.text((1, 47), f"EQ: {label}", font=font, fill=1)

        return self._image_to_bytes(image)
