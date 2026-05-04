"""Audio profile management — snapshot and restore audio settings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

_CFG = Path.home() / ".config" / "arctis_manager"
_PROFILES_DIR = _CFG / "profiles"
_ACTIVE_FILE = _CFG / ".active_profile"
_GENERAL_SETTINGS_FILE = _CFG / "settings" / "general_settings.yaml"
CHANNELS = ("game", "chat", "micro")

# DAC tab keys that profiles capture & restore (mirror of dac_settings_config
# in settings.py, plus the few list/font keys that aren't represented as
# ConfigSetting entries but are still managed by the DAC page).
DAC_KEYS: tuple[str, ...] = (
    "oled_custom_display",
    "oled_brightness",
    "oled_screen_timeout",
    "oled_scroll_speed",
    "oled_eq_scroll_speed",
    "oled_show_time",
    "oled_show_battery",
    "oled_show_profile",
    "oled_show_eq",
    "oled_display_order",
    "oled_font_time",
    "oled_font_battery",
    "oled_font_profile",
    "oled_font_eq",
    "oled_font_weather_temp",
    "weather_enabled",
    "weather_location",
    "weather_lat",
    "weather_lon",
    "weather_units",
    "weather_city_display",
)


_EQ_MODE_FILE = _CFG / ".eq_mode"


def _current_eq_mode() -> str:
    return _EQ_MODE_FILE.read_text().strip() if _EQ_MODE_FILE.exists() else "custom"


@dataclass
class Profile:
    name: str
    presets: dict[str, str]           # channel → preset name
    macros: dict[str, dict[str, float]]  # channel → {basses, voix, aigus}
    spatial_audio: dict               # {enabled, mode, immersion, distance}
    volumes: dict[str, int]           # {game, chat, media} → 0-100
    eq_mode: str = "custom"           # "sonar" or "custom"
    # OLED / weather / display-order settings from the DAC tab.
    # Older profile files (pre-v1.0.80) don't have this — Profile.load() defaults
    # it to {} so nothing changes when restoring an old profile.
    dac: dict = field(default_factory=dict)

    def slug(self) -> str:
        return re.sub(r"[^\w-]", "_", self.name.lower().strip())

    def save(self) -> None:
        _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        (_PROFILES_DIR / f"{self.slug()}.json").write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False)
        )

    def delete(self) -> None:
        (_PROFILES_DIR / f"{self.slug()}.json").unlink(missing_ok=True)

    @staticmethod
    def load(path: Path) -> "Profile":
        data = json.loads(path.read_text())
        data.setdefault("eq_mode", "")  # backward compat with older profiles
        data.setdefault("dac", {})       # pre-v1.0.80 profiles have no DAC block
        return Profile(**data)

    @staticmethod
    def list_all() -> list["Profile"]:
        if not _PROFILES_DIR.exists():
            return []
        profiles = []
        for p in sorted(_PROFILES_DIR.glob("*.json")):
            try:
                profiles.append(Profile.load(p))
            except Exception:
                pass
        return profiles


def active_profile_name() -> str | None:
    if _ACTIVE_FILE.exists():
        name = _ACTIVE_FILE.read_text().strip()
        return name if name else None
    return None


def set_active_profile(name: str | None) -> None:
    _CFG.mkdir(parents=True, exist_ok=True)
    if name:
        _ACTIVE_FILE.write_text(name)
    else:
        _ACTIVE_FILE.unlink(missing_ok=True)


def snapshot_current() -> Profile:
    """Snapshot current EQ presets, macros, spatial audio, volumes, EQ mode and DAC settings."""
    from arctis_sound_manager.gui.sonar_page import (
        _active_preset_name, _load_macro, _load_spatial_audio,
    )
    presets = {c: _active_preset_name(c) for c in CHANNELS}
    macros = {c: _load_macro(c) for c in CHANNELS}
    spatial = _load_spatial_audio()
    volumes = _snapshot_volumes()
    eq_mode = _current_eq_mode()
    dac = _snapshot_dac()
    return Profile(name="", presets=presets, macros=macros,
                   spatial_audio=spatial, volumes=volumes, eq_mode=eq_mode,
                   dac=dac)


def _snapshot_dac() -> dict:
    """Read the DAC-relevant subset of general_settings.yaml, in the same
    process as the GUI (no D-Bus round-trip needed). Falls back to {} if the
    file doesn't exist yet or can't be parsed."""
    if not _GENERAL_SETTINGS_FILE.exists():
        return {}
    try:
        from ruamel.yaml import YAML
        data = YAML(typ='safe').load(_GENERAL_SETTINGS_FILE) or {}
        if not isinstance(data, dict):
            return {}
        return {k: data[k] for k in DAC_KEYS if k in data}
    except Exception:
        return {}


def _snapshot_volumes() -> dict[str, int]:
    try:
        import pulsectl
        result: dict[str, int] = {}
        with pulsectl.Pulse("asm-profile-snapshot") as pulse:
            for sink in pulse.sink_list():
                n = sink.name
                if "Arctis_Game" in n:
                    result["game"] = round(sink.volume.value_flat * 100)
                elif "Arctis_Chat" in n:
                    result["chat"] = round(sink.volume.value_flat * 100)
                elif "Arctis_Media" in n:
                    result["media"] = round(sink.volume.value_flat * 100)
        return result
    except Exception:
        return {"game": 100, "chat": 100, "media": 100}


def apply_profile(profile: Profile) -> None:
    """Write all config files and apply volumes immediately.

    EQ re-apply (filter-chain restart) is triggered separately by
    SonarPage.apply_all_from_files() after this call returns.
    """
    from arctis_sound_manager.gui.sonar_page import (
        _set_active_preset, _save_macro, _save_spatial_audio,
    )
    for ch in CHANNELS:
        if ch in profile.presets:
            _set_active_preset(ch, profile.presets[ch])
        if ch in profile.macros:
            _save_macro(ch, profile.macros[ch])
    _save_spatial_audio(profile.spatial_audio)
    _apply_volumes(profile.volumes)

    # Switch EQ mode if needed (updates YAMLs + virtual sinks conf)
    eq_mode = getattr(profile, "eq_mode", None)
    if eq_mode:
        _apply_eq_mode(eq_mode)

    # Restore DAC tab settings (OLED, weather, display order, font sizes).
    # Old profiles have an empty dict here so this is a no-op.
    dac = getattr(profile, "dac", None) or {}
    if dac:
        _apply_dac(dac)

    set_active_profile(profile.name)


def _apply_dac(dac: dict) -> None:
    """Push the saved DAC settings to the daemon over D-Bus so the OLED
    redraw fires immediately AND general_settings.yaml is persisted by the
    daemon's existing SetSetting handler — no parallel write paths."""
    try:
        from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
    except Exception:
        return
    for key in DAC_KEYS:
        if key not in dac:
            continue
        try:
            DbusWrapper.change_setting(key, dac[key])
        except Exception:
            pass


def _apply_eq_mode(new_mode: str) -> None:
    """Write EQ mode state and regenerate virtual sinks config if mode changed."""
    current = _current_eq_mode()
    if current == new_mode:
        return
    try:
        from arctis_sound_manager.gui.sonar_toggle_widget import _apply_yaml
        from arctis_sound_manager.sonar_to_pipewire import generate_virtual_sinks_conf
        _apply_yaml(new_mode)
        generate_virtual_sinks_conf(sonar=(new_mode == "sonar"))
        _EQ_MODE_FILE.write_text(new_mode)
    except Exception:
        pass


def _apply_volumes(volumes: dict[str, int]) -> None:
    try:
        import pulsectl
        mapping = {"game": "Arctis_Game", "chat": "Arctis_Chat", "media": "Arctis_Media"}
        with pulsectl.Pulse("asm-profile-apply") as pulse:
            sinks = pulse.sink_list()
            for key, substr in mapping.items():
                if key not in volumes:
                    continue
                val = max(0, min(100, volumes[key])) / 100.0
                for sink in sinks:
                    if substr in sink.name:
                        pulse.volume_set_all_chans(sink, val)
    except Exception:
        pass
