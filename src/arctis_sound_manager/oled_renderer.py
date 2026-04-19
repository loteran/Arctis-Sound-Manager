from __future__ import annotations

from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from arctis_sound_manager.weather_service import WeatherData

_LINE_H = 11
_BAR_H = 9
_FONT_BIG_SIZE = 20
_FONT_MED_SIZE = 16
_ICON_SIZE = 20
_WEATHER_H = _ICON_SIZE + 2


class OledRenderer:
    WIDTH = 128
    HEIGHT = 64

    def __init__(self) -> None:
        self._font = ImageFont.load_default()
        self._font_big = ImageFont.load_default(size=_FONT_BIG_SIZE)
        self._font_med = ImageFont.load_default(size=_FONT_MED_SIZE)

    def _image_to_bytes(self, image: Image.Image) -> bytes:
        mono = image.convert("1")
        return mono.tobytes()

    def _draw_battery_icon(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, percent: int, charging: bool, blink_state: bool = True
    ) -> int:
        body_w, body_h = 22, 12
        tip_w, tip_h = 3, 7

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
            draw.line([(cx + 2, cy - 4), (cx - 2, cy)], fill=1, width=2)
            draw.line([(cx - 2, cy), (cx + 2, cy + 4)], fill=1, width=2)
        return body_w + tip_w + 1

    def _draw_bar(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, percent: int
    ) -> None:
        draw.rectangle([x, y, x + width - 1, y + height - 1], outline=1, fill=0)
        fill_w = max(0, int((width - 2) * max(0, min(100, percent)) / 100))
        if fill_w > 0:
            draw.rectangle([x + 1, y + 1, x + fill_w, y + height - 2], fill=1)

    def _draw_weather_icon_id(self, draw: ImageDraw.ImageDraw, x: int, y: int, icon_id: int) -> None:
        s = _ICON_SIZE
        cx, cy = x + s // 2, y + s // 2
        if icon_id == 0:  # sun
            r = 5
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=1)
            for dx, dy in [(0, -(r+4)), (0, r+4), (-(r+4), 0), (r+4, 0),
                           (-(r+3), -(r+3)), (r+3, -(r+3)), (-(r+3), r+3), (r+3, r+3)]:
                draw.line([(cx + dx//2, cy + dy//2), (cx + dx, cy + dy)], fill=1, width=2)
        elif icon_id == 1:  # cloud
            draw.ellipse([x + 2, cy - 2, x + 10, cy + 6], fill=1)
            draw.ellipse([x + 7, cy - 5, x + 17, cy + 5], fill=1)
            draw.rectangle([x + 2, cy + 2, x + 17, y + s - 2], fill=1)
        elif icon_id == 2:  # rain
            draw.ellipse([x + 2, cy - 5, x + 10, cy + 3], fill=1)
            draw.ellipse([x + 8, cy - 8, x + 18, cy + 2], fill=1)
            draw.rectangle([x + 2, cy - 1, x + 18, cy + 4], fill=1)
            for i in range(4):
                px = x + 3 + i * 4
                draw.line([(px, cy + 6), (px - 2, y + s - 1)], fill=1, width=2)
        elif icon_id == 3:  # snow
            draw.line([(cx, y + 1), (cx, y + s - 2)], fill=1, width=2)
            draw.line([(x + 1, cy), (x + s - 2, cy)], fill=1, width=2)
            draw.line([(x + 3, y + 3), (x + s - 4, y + s - 4)], fill=1, width=2)
            draw.line([(x + s - 4, y + 3), (x + 3, y + s - 4)], fill=1, width=2)
            for px, py in [(cx, y + 1), (cx, y + s - 2),
                           (x + 1, cy), (x + s - 2, cy)]:
                draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=1)
        elif icon_id == 4:  # fog
            for row in [y + 3, y + 7, y + 11, y + 15]:
                draw.rounded_rectangle([x + 1, row, x + s - 2, row + 2], radius=1, fill=1)
        elif icon_id == 5:  # storm
            draw.ellipse([x + 2, cy - 5, x + 10, cy + 3], fill=1)
            draw.ellipse([x + 8, cy - 8, x + 18, cy + 2], fill=1)
            draw.rectangle([x + 2, cy - 1, x + 18, cy + 3], fill=1)
            pts = [(cx + 2, cy + 3), (cx - 3, cy + 9), (cx + 1, cy + 9), (cx - 4, y + s - 1)]
            draw.line(pts, fill=1, width=3)

    _DEFAULT_DISPLAY_ORDER = ['profile', 'eq', 'weather']

    def render_status_image(
        self,
        battery_percent: int,
        charging: bool,
        time_str: str,
        active_profile: str,
        connected: bool = True,
        blink_state: bool = True,
        eq_preset: str = "",
        weather: "WeatherData | None" = None,
        show_time: bool = True,
        show_battery: bool = True,
        show_profile: bool = True,
        show_eq: bool = True,
        display_order: "list[str] | None" = None,
    ) -> Image.Image:
        """Render all content at natural height, no clipping."""
        order = display_order if display_order is not None else self._DEFAULT_DISPLAY_ORDER

        natural_h = self._natural_height(show_time, show_battery, show_profile, show_eq, weather)
        buf_h = max(self.HEIGHT, natural_h)

        image = Image.new("1", (self.WIDTH, buf_h), color=0)
        draw = ImageDraw.Draw(image)
        font = self._font
        y = 1

        # Fixed top row — time (left) + battery/status (right)
        if show_time or show_battery:
            if show_battery:
                if not connected:
                    offline_label = "Offline"
                    offline_w = int(self._font_med.getlength(offline_label))
                    draw.text((self.WIDTH - offline_w - 2, y), offline_label, font=self._font_med, fill=1)
                else:
                    bat_label = f"{max(0, battery_percent)}%" if battery_percent >= 0 else "?%"
                    bat_label_w = int(self._font_med.getlength(bat_label))
                    icon_w = 26
                    bat_x = self.WIDTH - icon_w - bat_label_w - 7
                    icon_y = y + (_FONT_BIG_SIZE - 12) // 2
                    self._draw_battery_icon(draw, bat_x, icon_y, battery_percent if battery_percent >= 0 else 0, charging, blink_state)
                    draw.text((bat_x + icon_w + 4, y), bat_label, font=self._font_med, fill=1)
            if show_time:
                draw.text((1, y - 1), time_str, font=self._font_big, fill=1)
                y += _FONT_BIG_SIZE + 2
            else:
                y += _LINE_H
            draw.line([(0, y), (self.WIDTH - 1, y)], fill=1)
            y += 3

        # Orderable elements
        for element in order:
            if element == 'profile' and show_profile:
                draw.text((1, y), f"Profile: {active_profile}", font=font, fill=1)
                y += _LINE_H
            elif element == 'eq' and show_eq and eq_preset:
                label = eq_preset if len(eq_preset) <= 18 else eq_preset[:17] + "\u2026"
                draw.text((1, y), f"EQ: {label}", font=font, fill=1)
                y += _LINE_H
            elif element == 'weather' and weather is not None:
                y += 2
                self._draw_weather_icon_id(draw, 1, y, weather.icon_id)
                temp_str = f"{weather.temp:g}{weather.unit_label}"
                draw.text((_ICON_SIZE + 4, y), temp_str, font=self._font_big, fill=1)
                city = weather.city[:10] if len(weather.city) > 10 else weather.city
                temp_w = int(self._font_big.getlength(temp_str))
                city_x = _ICON_SIZE + 4 + temp_w + 4
                if city_x + int(font.getlength(city)) <= self.WIDTH:
                    draw.text((city_x, y + 6), city, font=font, fill=1)
                else:
                    draw.text((_ICON_SIZE + 4, y + _FONT_BIG_SIZE - 6), city, font=font, fill=1)
                y += _WEATHER_H + 2

        return image

    def _natural_height(
        self,
        show_time: bool, show_battery: bool, show_profile: bool,
        show_eq: bool, weather: "WeatherData | None",
    ) -> int:
        y = 1
        if show_time or show_battery:
            y += (_FONT_BIG_SIZE + 2 if show_time else _LINE_H) + 1 + 3
        if show_profile:
            y += _LINE_H
        if show_eq:
            y += _LINE_H
        if weather is not None:
            y += 2 + _WEATHER_H
        return y

    def crop_frame(self, image: Image.Image, offset: int, x_offset: int = 0) -> bytes:
        """Crop HEIGHT rows starting at offset, return as OLED bytes."""
        cropped = image.crop((x_offset, offset, x_offset + self.WIDTH, offset + self.HEIGHT))
        return self._image_to_bytes(cropped)

    def render_status(
        self,
        battery_percent: int,
        charging: bool,
        time_str: str,
        active_profile: str,
        connected: bool = True,
        blink_state: bool = True,
        eq_preset: str = "",
        weather: "WeatherData | None" = None,
        show_time: bool = True,
        show_battery: bool = True,
        show_profile: bool = True,
        show_eq: bool = True,
        scroll_offset: int = 0,
    ) -> bytes:
        image = self.render_status_image(
            battery_percent=battery_percent, charging=charging, connected=connected,
            time_str=time_str, active_profile=active_profile, blink_state=blink_state,
            eq_preset=eq_preset, weather=weather, show_time=show_time,
            show_battery=show_battery, show_profile=show_profile, show_eq=show_eq,
        )
        return self.crop_frame(image, scroll_offset)
