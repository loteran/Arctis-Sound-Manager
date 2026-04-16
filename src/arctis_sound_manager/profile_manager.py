"""Audio profile management — snapshot and restore audio settings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

_CFG = Path.home() / ".config" / "arctis_manager"
_PROFILES_DIR = _CFG / "profiles"
_ACTIVE_FILE = _CFG / ".active_profile"
CHANNELS = ("game", "chat", "micro")


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
    """Snapshot current EQ presets, macros, spatial audio, volumes and EQ mode."""
    from arctis_sound_manager.gui.sonar_page import (
        _active_preset_name, _load_macro, _load_spatial_audio,
    )
    presets = {c: _active_preset_name(c) for c in CHANNELS}
    macros = {c: _load_macro(c) for c in CHANNELS}
    spatial = _load_spatial_audio()
    volumes = _snapshot_volumes()
    eq_mode = _current_eq_mode()
    return Profile(name="", presets=presets, macros=macros,
                   spatial_audio=spatial, volumes=volumes, eq_mode=eq_mode)


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

    set_active_profile(profile.name)


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
