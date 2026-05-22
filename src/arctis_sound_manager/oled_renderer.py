from __future__ import annotations

import math
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
        body_w, body_h = 8, 12
        tip_w, tip_h = 4, 2
        tip_x = x + (body_w - tip_w) // 2
        body_y = y + tip_h
        # Tip
        draw.rectangle([tip_x, y, tip_x + tip_w - 1, y + tip_h - 1], fill=1)
        # Body outline
        draw.rectangle([x, body_y, x + body_w - 1, body_y + body_h - 1], outline=1, fill=0)
        # Fill from bottom upward
        fill_max = body_h - 4
        fill_h = max(0, int(fill_max * max(0, min(100, percent)) / 100))
        if fill_h > 0:
            fill_bottom = body_y + body_h - 3
            draw.rectangle([x + 2, fill_bottom - fill_h + 1, x + body_w - 3, fill_bottom], fill=1)
        # Charging bolt
        if charging and blink_state:
            cx = x + body_w // 2
            cy = body_y + body_h // 2
            draw.line([(cx + 1, cy - 3), (cx - 1, cy)], fill=1, width=2)
            draw.line([(cx - 1, cy), (cx + 1, cy + 3)], fill=1, width=2)
        return body_w  # total width

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

    _DEFAULT_DISPLAY_ORDER = ['mic_status', 'sonar_mode', 'profile', 'eq', 'eq_chat', 'weather']

    @staticmethod
    def _measure_text_pixels(font: "ImageFont.FreeTypeFont", text: str) -> int:
        """Return the actual rightmost lit pixel + 1 by rendering on an oversized canvas.

        PIL's getbbox/getlength can under-report the true glyph extent (e.g. the
        'trailing-pixel' of characters like 't' at certain sizes).  Rendering to a
        canvas wider than needed and scanning the result gives the true value.
        """
        estimate = math.ceil(font.getlength(text)) + 32   # +32px safety margin
        h = font.getbbox(text)[3] + 2                     # canvas height = glyph bottom + margin
        img = Image.new("1", (estimate, h), color=0)
        ImageDraw.Draw(img).text((0, 0), text, font=font, fill=1)
        last_x = -1
        for x in range(estimate - 1, -1, -1):
            if any(img.getpixel((x, y)) for y in range(h)):
                last_x = x
                break
        return last_x + 1 if last_x >= 0 else estimate

    def measure_eq_text(self, eq_preset: str, sz_eq: int) -> int:
        """Return pixel width of 'EQ: <eq_preset>' at the given font size."""
        font = ImageFont.load_default(size=max(7, min(30, sz_eq)))
        return self._measure_text_pixels(font, f"EQ: {eq_preset}")

    def measure_profile_text(self, active_profile: str, sz_profile: int) -> int:
        """Return pixel width of 'Profile: <active_profile>' at the given font size."""
        font = ImageFont.load_default(size=max(7, min(30, sz_profile)))
        return self._measure_text_pixels(font, f"Profile: {active_profile}")

    def measure_eq_chat_text(self, eq_chat_preset: str, sz_eq_chat: int) -> int:
        """Return pixel width of 'Chat: <eq_chat_preset>' at the given font size."""
        font = ImageFont.load_default(size=max(7, min(30, sz_eq_chat)))
        return self._measure_text_pixels(font, f"Chat: {eq_chat_preset}")

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
        show_mic_status: bool = True,
        show_sonar_mode: bool = True,
        show_eq_chat: bool = False,
        show_weather_city: bool = True,
        mic_status: str = "",
        eq_mode: str = "custom",
        eq_chat_preset: str = "",
        eq_chat_scroll_offset: int = 0,
        display_order: "list[str] | None" = None,
        font_sizes: "dict[str, int] | None" = None,
        eq_scroll_offset: int = 0,
        profile_scroll_offset: int = 0,
    ) -> Image.Image:
        """Render all content at natural height, no clipping."""
        order = display_order if display_order is not None else self._DEFAULT_DISPLAY_ORDER
        fs = font_sizes or {}

        sz_time        = max(7, min(30, fs.get('time', _FONT_BIG_SIZE)))
        sz_battery     = max(7, min(30, fs.get('battery', _FONT_MED_SIZE)))
        sz_profile     = max(7, min(30, fs.get('profile', 8)))
        sz_eq          = max(7, min(30, fs.get('eq', 8)))
        sz_weather_tmp = max(7, min(30, fs.get('weather_temp', _FONT_BIG_SIZE)))
        sz_eq_chat    = max(7, min(30, fs.get('eq_chat',      8)))
        sz_mic        = max(7, min(30, fs.get('mic_status',    8)))
        sz_sonar_mode = max(7, min(30, fs.get('sonar_mode',    8)))

        font_time    = ImageFont.load_default(size=sz_time)
        font_battery = ImageFont.load_default(size=sz_battery)
        font_profile = ImageFont.load_default(size=sz_profile)
        font_eq      = ImageFont.load_default(size=sz_eq)
        font_wtmp    = ImageFont.load_default(size=sz_weather_tmp)
        font_eq_chat  = ImageFont.load_default(size=sz_eq_chat)
        font_mic      = ImageFont.load_default(size=sz_mic)
        font_sonar    = ImageFont.load_default(size=sz_sonar_mode)
        font_small   = self._font  # city / labels always small

        natural_h = self._natural_height(
            show_time, show_battery, show_profile, show_eq, weather,
            show_mic_status=show_mic_status, mic_status=mic_status,
            show_sonar_mode=show_sonar_mode,
            show_eq_chat=show_eq_chat, eq_chat_preset=eq_chat_preset,
            sz_time=sz_time, sz_battery=sz_battery, sz_profile=sz_profile,
            sz_eq=sz_eq, sz_weather_tmp=sz_weather_tmp,
            sz_mic=sz_mic, sz_sonar_mode=sz_sonar_mode, sz_eq_chat=sz_eq_chat,
            display_order=order,
        )
        buf_h = max(self.HEIGHT, natural_h)

        image = Image.new("1", (self.WIDTH, buf_h), color=0)
        draw = ImageDraw.Draw(image)
        y = 1

        # Fixed top row — time (left) + battery/status (right)
        if show_time or show_battery:
            if show_battery:
                if not connected:
                    offline_label = "Offline"
                    offline_w = int(font_battery.getlength(offline_label))
                    draw.text((self.WIDTH - offline_w - 2, y), offline_label, font=font_battery, fill=1)
                else:
                    bat_label = f"{max(0, battery_percent)}%" if battery_percent >= 0 else "?%"
                    bat_label_w = int(font_battery.getlength(bat_label))
                    icon_w = 10  # 8px vertical icon + 2px gap
                    bat_x = self.WIDTH - icon_w - bat_label_w - 4
                    icon_y = y + max(0, (sz_time - 14) // 2)
                    label_y = y + max(0, (sz_time - sz_battery) // 2)
                    self._draw_battery_icon(draw, bat_x, icon_y, battery_percent if battery_percent >= 0 else 0, charging, blink_state)
                    draw.text((bat_x + icon_w, label_y), bat_label, font=font_battery, fill=1)
            if show_time:
                draw.text((1, y - 1), time_str, font=font_time, fill=1)
                y += sz_time + 2
            else:
                y += sz_battery + 2
            draw.line([(0, y), (self.WIDTH - 1, y)], fill=1)
            y += 3

        # Orderable elements
        for element in order:
            if element == 'profile' and show_profile:
                draw.text((1 - profile_scroll_offset, y), f"Profile: {active_profile}", font=font_profile, fill=1)
                y += sz_profile + 3
            elif element == 'eq' and show_eq and eq_preset:
                # Negative x scrolls the text left; PIL clips at canvas edge automatically
                draw.text((1 - eq_scroll_offset, y), f"EQ: {eq_preset}", font=font_eq, fill=1)
                y += sz_eq + 3
            elif element == 'weather' and weather is not None:
                y += 2
                icon_h = max(_ICON_SIZE, sz_weather_tmp)
                self._draw_weather_icon_id(draw, 1, y + (icon_h - _ICON_SIZE) // 2, weather.icon_id)
                temp_str = f"{weather.temp:g}{weather.unit_label}"
                draw.text((_ICON_SIZE + 4, y), temp_str, font=font_wtmp, fill=1)
                if show_weather_city:
                    city = weather.city[:10] if len(weather.city) > 10 else weather.city
                    temp_w = int(font_wtmp.getlength(temp_str))
                    city_x = _ICON_SIZE + 4 + temp_w + 4
                    if city_x + int(font_small.getlength(city)) <= self.WIDTH:
                        draw.text((city_x, y + sz_weather_tmp - 8), city, font=font_small, fill=1)
                    else:
                        draw.text((_ICON_SIZE + 4, y + sz_weather_tmp + 2), city, font=font_small, fill=1)
                y += icon_h + 4
            elif element == 'mic_status' and show_mic_status and mic_status:
                is_muted = mic_status == "muted"
                icon_y = y + max(0, (sz_mic - 8) // 2)
                draw.rectangle([2, icon_y, 7, icon_y + 7], outline=1, fill=0)
                draw.arc([2, icon_y, 7, icon_y + 4], 180, 0, fill=1)
                draw.arc([0, icon_y + 4, 9, icon_y + 10], 180, 0, fill=1)
                draw.line([(4, icon_y + 10), (4, icon_y + 12)], fill=1)
                draw.line([(1, icon_y + 12), (7, icon_y + 12)], fill=1)
                if is_muted:
                    draw.line([(0, icon_y), (9, icon_y + 12)], fill=1, width=1)
                status_text = "Muted" if is_muted else "Active"
                draw.text((12, y), status_text, font=font_mic, fill=1)
                y += max(14, sz_mic) + 3
            elif element == 'sonar_mode' and show_sonar_mode:
                mode_label = "Sonar" if eq_mode == "sonar" else "Custom EQ"
                draw.text((1, y), f"Mode: {mode_label}", font=font_sonar, fill=1)
                y += sz_sonar_mode + 3
            elif element == 'eq_chat' and show_eq_chat and eq_chat_preset:
                draw.text((1 - eq_chat_scroll_offset, y), f"Chat: {eq_chat_preset}", font=font_eq_chat, fill=1)
                y += sz_eq_chat + 3

        return image

    def _natural_height(
        self,
        show_time: bool, show_battery: bool, show_profile: bool,
        show_eq: bool, weather: "WeatherData | None",
        show_mic_status: bool = False, mic_status: str = "",
        show_sonar_mode: bool = False,
        show_eq_chat: bool = False, eq_chat_preset: str = "",
        sz_time: int = _FONT_BIG_SIZE, sz_battery: int = _FONT_MED_SIZE,
        sz_profile: int = 8, sz_eq: int = 8, sz_weather_tmp: int = _FONT_BIG_SIZE,
        sz_mic: int = 8, sz_sonar_mode: int = 8, sz_eq_chat: int = 8,
        display_order: "list[str] | None" = None,
    ) -> int:
        y = 1
        if show_time or show_battery:
            top_row = sz_time + 2 if show_time else sz_battery + 2
            y += top_row + 1 + 3
        order = display_order if display_order is not None else self._DEFAULT_DISPLAY_ORDER
        for element in order:
            if element == 'profile' and show_profile:
                y += sz_profile + 3
            elif element == 'eq' and show_eq:
                y += sz_eq + 3
            elif element == 'weather' and weather is not None:
                y += 2 + max(_ICON_SIZE, sz_weather_tmp) + 4
            elif element == 'mic_status' and show_mic_status and mic_status:
                y += max(14, sz_mic) + 3
            elif element == 'sonar_mode' and show_sonar_mode:
                y += sz_sonar_mode + 3
            elif element == 'eq_chat' and show_eq_chat and eq_chat_preset:
                y += sz_eq_chat + 3
        return y

    def render_splash_image(self) -> bytes:
        """Startup splash: 'ASM' fills the top, 'By Loteran' bottom-right at 16pt."""
        image = Image.new("1", (self.WIDTH, self.HEIGHT), color=0)
        draw = ImageDraw.Draw(image)

        font_by = ImageFont.load_default(size=16)
        by_text = "By Loteran"
        by_h = 18  # 16pt + 2px baseline
        by_y = self.HEIGHT - by_h
        by_w = int(font_by.getlength(by_text))
        draw.text((self.WIDTH - by_w - 2, by_y), by_text, font=font_by, fill=1)

        # ASM fills available space above "By Loteran"
        available_h = by_y - 4
        asm_size = max(8, available_h - 2)
        font_asm = ImageFont.load_default(size=asm_size)
        asm_w = int(font_asm.getlength("ASM"))
        asm_x = (self.WIDTH - asm_w) // 2
        asm_y = max(0, (available_h - asm_size) // 2 + 2)
        # Simulate bold by drawing twice with 1px horizontal offset
        draw.text((asm_x,     asm_y), "ASM", font=font_asm, fill=1)
        draw.text((asm_x + 1, asm_y), "ASM", font=font_asm, fill=1)

        return self._image_to_bytes(image.convert("1"))

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
        eq_scroll_offset: int = 0,
        profile_scroll_offset: int = 0,
    ) -> bytes:
        image = self.render_status_image(
            battery_percent=battery_percent, charging=charging, connected=connected,
            time_str=time_str, active_profile=active_profile, blink_state=blink_state,
            eq_preset=eq_preset, weather=weather, show_time=show_time,
            show_battery=show_battery, show_profile=show_profile, show_eq=show_eq,
            eq_scroll_offset=eq_scroll_offset,
            profile_scroll_offset=profile_scroll_offset,
        )
        return self.crop_frame(image, scroll_offset)
