"""Lightweight weather fetcher — Open-Meteo (no API key) + Nominatim geocoding."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 8  # seconds
_CACHE_TTL_S = 1800  # 30 min

# WMO weather codes → short label
_WMO_LABEL: dict[int, str] = {
    0: "Clear",
    1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Lt drizzle", 53: "Drizzle", 55: "Hvy drizzle",
    61: "Lt rain", 63: "Rain", 65: "Hvy rain",
    71: "Lt snow", 73: "Snow", 75: "Hvy snow", 77: "Snow grains",
    80: "Showers", 81: "Showers", 82: "Hvy showers",
    85: "Snow shower", 86: "Hvy snow shower",
    95: "Thunderstorm", 96: "T-storm+hail", 99: "T-storm+hail",
}

# WMO code → icon category: 0=sun, 1=cloud, 2=rain, 3=snow, 4=fog, 5=storm
_WMO_ICON: dict[int, int] = {
    0: 0, 1: 0, 2: 1, 3: 1,
    45: 4, 48: 4,
    51: 2, 53: 2, 55: 2,
    61: 2, 63: 2, 65: 2,
    71: 3, 73: 3, 75: 3, 77: 3,
    80: 2, 81: 2, 82: 2,
    85: 3, 86: 3,
    95: 5, 96: 5, 99: 5,
}


@dataclass
class WeatherData:
    temp: float
    unit_label: str       # "°C" or "°F"
    condition: str        # short human label
    icon_id: int          # 0=sun 1=cloud 2=rain 3=snow 4=fog 5=storm
    city: str             # display name used for geocoding


@dataclass
class GeoResult:
    lat: float
    lon: float
    display_name: str     # city/region shown in GUI


class WeatherService:
    def __init__(self) -> None:
        self._cache: Optional[WeatherData] = None
        self._cache_ts: float = 0.0

    # ── Geocoding ─────────────────────────────────────────────────────────────

    def geocode(self, city: str) -> Optional[GeoResult]:
        params = urllib.parse.urlencode({
            "q": city, "format": "json", "limit": 1,
        })
        url = f"{_NOMINATIM_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "ArctisWeather/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                results = json.loads(resp.read())
            if not results:
                return None
            r = results[0]
            # Extract a short city name from the display_name
            parts = r.get("display_name", city).split(",")
            short = parts[0].strip()
            return GeoResult(lat=float(r["lat"]), lon=float(r["lon"]), display_name=short)
        except Exception as exc:
            log.warning("Geocoding failed for %r: %s", city, exc)
            return None

    # ── Weather fetch ──────────────────────────────────────────────────────────

    def fetch(self, lat: float, lon: float, units: str, city: str) -> Optional[WeatherData]:
        temp_unit = "celsius" if units == "celsius" else "fahrenheit"
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weathercode",
            "temperature_unit": temp_unit,
            "timezone": "auto",
            "forecast_days": 1,
        })
        url = f"{_OPENMETEO_URL}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read())
            current = data.get("current", {})
            temp = round(float(current.get("temperature_2m", 0)), 1)
            code = int(current.get("weathercode", 0))
            unit_label = "°C" if units == "celsius" else "°F"
            condition = _WMO_LABEL.get(code, "Unknown")
            icon_id = _WMO_ICON.get(code, 1)
            result = WeatherData(
                temp=temp,
                unit_label=unit_label,
                condition=condition,
                icon_id=icon_id,
                city=city,
            )
            self._cache = result
            self._cache_ts = datetime.now(timezone.utc).timestamp()
            return result
        except Exception as exc:
            log.warning("Weather fetch failed: %s", exc)
            return self._cache  # return stale cache on error

    def get(self, lat: float, lon: float, units: str, city: str) -> Optional[WeatherData]:
        """Return cached data if fresh, otherwise fetch."""
        now = datetime.now(timezone.utc).timestamp()
        if self._cache and (now - self._cache_ts) < _CACHE_TTL_S:
            return self._cache
        return self.fetch(lat, lon, units, city)

    def invalidate(self) -> None:
        """Force a fresh fetch on next call."""
        self._cache_ts = 0.0
