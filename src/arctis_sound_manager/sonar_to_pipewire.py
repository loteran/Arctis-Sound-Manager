# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
sonar_to_pipewire.py — Generate PipeWire filter-chain configs for Sonar EQ channels.

One config per channel (game / chat / micro).  Each config inserts a chain of
biquad nodes between the virtual capture sink and its playback target.

Routing (targets resolved at generation time from the currently-attached device):
  game  → effect_input.virtual-surround-7.1-hesuvi     (8ch 7.1 → HeSuVi)
  chat  → physical ALSA output of the current Arctis   (2ch stereo)
  micro → virtual source backed by the physical mic    (1ch mono)

All configs are written to filter-chain.conf.d/ and loaded by the filter-chain service.
Restarting only filter-chain (not pipewire) preserves active audio streams.

Config generators return an empty string without writing anything when no
Arctis device is currently attached (`device_state.is_device_set()` == False).
"""
from __future__ import annotations

import logging
from pathlib import Path

from arctis_sound_manager.eq_types import EqBand, PW_LABEL

_log = logging.getLogger(__name__)


def _resolve_ladspa_plugin(name_pattern: str) -> str | None:
    """Return the absolute path of the first LADSPA .so matching *name_pattern*.

    Returns ``None`` if no matching plugin is found.  The absolute path is used
    in generated PipeWire filter-chain configs so that ``dlopen()`` loads the
    plugin directly, bypassing ``LADSPA_PATH`` lookup — which is often unset
    inside a systemd user unit on Fedora (``/usr/lib64/ladspa/``).

    NOTE: under Distrobox/Flatpak the filter-chain service runs on the HOST while
    this scan sees the CONTAINER filesystem. A plugin found here may be absent on
    the host, making filter-chain SEGV when it tries to dlopen() it (issue #88).
    We warn but do not disable LADSPA in containers, since the user may have
    installed the plugins on the host too.
    """
    import fnmatch
    from pathlib import Path
    try:
        from arctis_sound_manager.bug_reporter import _detect_container_env
        _container = _detect_container_env()
    except Exception:
        _container = 'native'
    if _container != 'native':
        _log.warning(
            "LADSPA scan for '%s' runs inside a container (%s); a plugin found "
            "here may be missing on the host, which can crash filter-chain. "
            "Install the LADSPA plugins on the host if filter-chain fails to start.",
            name_pattern, _container,
        )
    _dirs = (
        "/usr/lib64/ladspa",
        "/usr/lib/ladspa",
        "/usr/lib/x86_64-linux-gnu/ladspa",
    )
    for d in _dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        try:
            for entry in p.iterdir():
                if entry.is_file() and fnmatch.fnmatch(entry.name, name_pattern):
                    return str(entry)
        except OSError:
            continue
    return None


def _ladspa_plugin_available(name_pattern: str) -> bool:
    """Backward-compat wrapper: True if a LADSPA .so matching *name_pattern* exists."""
    return _resolve_ladspa_plugin(name_pattern) is not None


# ── Constants ─────────────────────────────────────────────────────────────────

_SURROUND = "effect_input.virtual-surround-7.1-hesuvi"


def _get_physical_out() -> str:
    """Return the ALSA output node name for the currently connected device, or ''.
    Back-compat: returns the game output (stereo PCM) or falls back to chat."""
    from arctis_sound_manager import device_state as _ds
    return _ds.get_physical_out()


def _get_physical_out_game() -> str:
    """Stereo PCM used by game, media and HeSuVi (pro-output-1 on dual-PCM devices)."""
    from arctis_sound_manager import device_state as _ds
    return _ds.get_physical_out_game()


def _get_physical_out_chat() -> str:
    """Mono PCM used by chat and sidetone (pro-output-0 on dual-PCM devices)."""
    from arctis_sound_manager import device_state as _ds
    return _ds.get_physical_out_chat()


def _get_physical_in() -> str:
    """Return the ALSA input node name for the currently connected device, or ''."""
    from arctis_sound_manager import device_state as _ds
    return _ds.get_physical_in()


def _get_device_name() -> str:
    """Return the short device name for the currently connected device, or ''."""
    from arctis_sound_manager import device_state as _ds
    return _ds.get_device_name()


def _device_attached() -> bool:
    from arctis_sound_manager import device_state as _ds
    return _ds.is_device_set()

_CHANNEL_CHANNELS: dict[str, int] = {
    "game":   8,
    "chat":   2,
    "media":  8,
    "output": 8,
}

_CHANNEL_POSITION: dict[str, str] = {
    "game":   "FL FR FC LFE RL RR SL SR",
    "chat":   "FL FR",
    "media":  "FL FR FC LFE RL RR SL SR",
    "output": "FL FR FC LFE RL RR SL SR",
}

# Static channel targets; chat target is device-specific → use _get_physical_out()
_CHANNEL_TARGET: dict[str, str] = {
    "game":   _SURROUND,
    "media":  _SURROUND,
    "output": "",
}

_EXT_OUTPUT_POSITIONS: dict[int, str] = {
    2: "FL FR",
    4: "FL FR RL RR",
    6: "FL FR FC LFE RL RR",
    8: "FL FR FC LFE RL RR SL SR",
}


def _resolve_external_output(target_override: str | None = None) -> tuple[str, int, str]:
    """Detect the external output sink (HDMI / DisplayPort / aux) at runtime.

    Queries PipeWire via pulsectl to get the actual channel count and position
    of the target sink, so the generated filter-chain conf matches the hardware
    (2.0 stereo, 5.1 surround, 7.1 surround, …).

    Returns (sink_name, channels, position_str).
    Falls back to ("", 2, "FL FR") when no suitable sink is found.
    """
    try:
        import pulsectl
        with pulsectl.Pulse("asm-ext-output") as p:
            sinks = p.sink_list()
            if target_override:
                for s in sinks:
                    if s.name == target_override:
                        ch = s.channel_count
                        pos = _EXT_OUTPUT_POSITIONS.get(ch, "FL FR")
                        return s.name, ch, pos
            else:
                candidates = [
                    s for s in sinks
                    if s.name.startswith("alsa_output")
                    and s.proplist.get("device.vendor.id", "") != "0x1038"
                ]
                # Prefer HDMI/DisplayPort sinks over other outputs (S/PDIF, etc.)
                hdmi = next((s for s in candidates if "hdmi" in s.name.lower()), None)
                chosen = hdmi or (candidates[0] if candidates else None)
                if chosen:
                    ch = chosen.channel_count
                    pos = _EXT_OUTPUT_POSITIONS.get(ch, "FL FR")
                    return chosen.name, ch, pos
    except Exception:
        pass
    return "", 2, "FL FR"

# Macro slider filter parameters (estimations from visual captures)
_MACRO_PARAMS = {
    "basses": {"freq": 80.0,   "q": 0.50},
    "voix":   {"freq": 2000.0, "q": 0.60},
    "aigus":  {"freq": 9000.0, "q": 0.80},
}

_CONF_DIR = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"

# ── Smart Volume presets (LADSPA SC4M compressor) ────────────────────────────
#
# Each mode defines base compressor parameters.  The *level* (0-100) scales
# the ratio from 1 (bypass) up to the mode's max ratio and adjusts the
# makeup gain proportionally.
#
# SC4M ports: RMS/peak, Attack (ms), Release (ms), Threshold (dB),
#             Ratio (1:n), Knee (dB), Makeup (dB)

_SMART_PRESETS: dict[str, dict] = {
    "quiet":    {"threshold": -30.0, "ratio": 6.0, "makeup": 4.0,
                 "attack": 5.0,  "release": 200.0, "knee": 8.0},
    "balanced": {"threshold": -20.0, "ratio": 4.0, "makeup": 8.0,
                 "attack": 10.0, "release": 200.0, "knee": 6.0},
    "loud":     {"threshold": -12.0, "ratio": 3.0, "makeup": 12.0,
                 "attack": 15.0, "release": 300.0, "knee": 4.0},
}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _node_block(name: str, label: str, freq: float, q: float, gain: float) -> str:
    return (
        f"                    {{ type = builtin  name = {name}  label = {label}\n"
        f"                      control = {{ Freq = {freq}  Q = {q}  Gain = {gain} }} }}"
    )


def _sc4m_node(name: str, preset: dict, level: float, plugin_path: str = "sc4m_1916") -> str:
    """Generate a LADSPA SC4M compressor node.

    *level* (0-100) scales ratio from 1.0 to the preset's max and adjusts
    makeup gain proportionally.

    *plugin_path* should be the absolute path to sc4m_1916.so (e.g.
    ``/usr/lib64/ladspa/sc4m_1916.so``).  Falls back to the bare name
    ``sc4m_1916`` if not provided (PipeWire will search LADSPA_PATH).
    """
    t = max(0.0, min(100.0, level)) / 100.0
    ratio  = 1.0 + (preset["ratio"] - 1.0) * t
    makeup = preset["makeup"] * t
    return (
        f'                    {{ type = ladspa  name = {name}  plugin = {plugin_path}  label = sc4m\n'
        f'                      control = {{ "RMS/peak" = 0  "Attack time (ms)" = {preset["attack"]}'
        f'  "Release time (ms)" = {preset["release"]}'
        f'  "Threshold level (dB)" = {preset["threshold"]}'
        f'  "Ratio (1:n)" = {ratio:.1f}'
        f'  "Knee radius (dB)" = {preset["knee"]}'
        f'  "Makeup gain (dB)" = {makeup:.1f} }} }}'
    )


def _link(out: str, inp: str) -> str:
    return f'                    {{ output = "{out}:Out"  input = "{inp}:In" }}'


def _link_to_ladspa(out: str, inp: str) -> str:
    """Link from a builtin node (Out) to a LADSPA node (Input)."""
    return f'                    {{ output = "{out}:Out"  input = "{inp}:Input" }}'


def _link_from_ladspa(out: str, inp: str) -> str:
    """Link from a LADSPA node (Output) to a builtin node (In)."""
    return f'                    {{ output = "{out}:Output"  input = "{inp}:In" }}'


def _link_ladspa(out: str, inp: str) -> str:
    """Link from a LADSPA node (Output) to another LADSPA node (Input)."""
    return f'                    {{ output = "{out}:Output"  input = "{inp}:Input" }}'


# ── HRIR choice ───────────────────────────────────────────────────────────────

# ASM-generated config filenames inside _CONF_DIR — the complete list.
# Keep in sync with generate_sonar_eq_conf / generate_sonar_micro_conf and the
# dynamic HeSuVi sink. Only these are moved aside in safe mode; unrelated/system
# configs in the same directory are never touched.
_ASM_CONF_NAMES = frozenset({
    "sonar-game-eq.conf",
    "sonar-chat-eq.conf",
    "sonar-media-eq.conf",
    "sonar-output-eq.conf",
    "sonar-micro-eq.conf",
    "sink-virtual-surround-7.1-hesuvi.conf",
})

# Backup dir for safe mode: a sibling of _CONF_DIR. PipeWire's filter-chain
# loader ignores it because it is not the *.conf.d directory it scans.
_CONF_DIR_DISABLED = _CONF_DIR.parent / "filter-chain.conf.d.disabled"

# Disk marker for safe-mode persistence across daemon restarts (Correctif 2,
# issue #88).  Written by _enter_filter_chain_safe_mode(), removed by
# reset_filter_chain_safe_mode().  PipeWire filter-chain crash-loops survive
# daemon restarts — without the marker, ASM would re-enable the crashing
# configs on the next session start.
_SAFE_MODE_MARKER = Path.home() / ".config" / "arctis_manager" / "filter_chain_safe_mode.json"

# Set once the safe-mode fallback has run, to stop any code path from recursing
# back into the crash-loop handler.  Initialised from the disk marker so the
# flag survives a daemon restart (the filter-chain crash-loop persists across
# restarts until the configs are removed).
# Reset explicitly via reset_filter_chain_safe_mode() on a deliberate user action.
_filter_chain_safe_mode: bool = _SAFE_MODE_MARKER.exists()


def reset_filter_chain_safe_mode() -> None:
    """Clear the safe-mode flag and remove the disk marker so the next
    _restart_filter_chain() re-enables EQ configs. Call this when the user
    deliberately re-enables EQ after a safe-mode warning."""
    global _filter_chain_safe_mode
    _filter_chain_safe_mode = False
    try:
        _SAFE_MODE_MARKER.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("reset_filter_chain_safe_mode: could not remove marker: %s", exc)


def _enter_filter_chain_safe_mode() -> None:
    """Move ASM-generated configs out of filter-chain.conf.d/ and restart once.

    Called when the filter-chain is detected in a SEGV crash-loop after a
    restart. Only moves files in _ASM_CONF_NAMES; never touches unrelated/system
    configs. Idempotent and guarded against recursion via _filter_chain_safe_mode
    (set before any work begins)."""
    global _filter_chain_safe_mode
    if _filter_chain_safe_mode:
        return  # already in safe mode — never recurse
    _filter_chain_safe_mode = True

    # Persist safe-mode flag to disk so it survives a daemon restart (issue #88
    # Correctif 2): the filter-chain crash-loop is not reset by restarting ASM,
    # so without the marker check_and_fix_stale_configs / ensure_sonar_eq_configs
    # would re-enable the crashing configs on the next session.
    try:
        import datetime as _dt
        import json as _json
        _SAFE_MODE_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SAFE_MODE_MARKER.write_text(_json.dumps({
            "timestamp": _dt.datetime.now().isoformat(),
            "reason": "crash-loop detected after filter-chain restart",
        }))
    except OSError as exc:
        _log.warning("safe_mode: could not write marker %s: %s", _SAFE_MODE_MARKER, exc)

    from arctis_sound_manager import service_control as sc

    moved: list[str] = []
    try:
        _CONF_DIR_DISABLED.mkdir(parents=True, exist_ok=True)
        for name in _ASM_CONF_NAMES:
            src = _CONF_DIR / name
            if src.exists():
                try:
                    src.rename(_CONF_DIR_DISABLED / name)
                    moved.append(name)
                except OSError as exc:
                    _log.warning("safe_mode: could not move %s: %s", name, exc)
    except OSError as exc:
        _log.warning("safe_mode: could not create backup dir %s: %s",
                     _CONF_DIR_DISABLED, exc)

    _log.warning(
        "filter-chain SAFE MODE: disabled ASM EQ configs because the filter-chain "
        "entered a SEGV crash-loop after restart. Moved %d config(s) to %s: %s. "
        "Audio will be flat but stable. Use 'Report a Bug' to capture diagnostics.",
        len(moved), _CONF_DIR_DISABLED, moved,
    )

    # Restart once more — with no ASM modules to load the filter-chain should
    # come up clean and give flat-but-stable audio instead of a permanent cut.
    sc.restart("filter-chain", timeout=15)


def _poll_filter_chain_stable() -> bool:
    """Poll filter-chain stability: 3 checks, 1 s apart.

    Returns True if ``sc.is_active("filter-chain")`` returns True at least once
    within the grace period.  A SEGV crash-loop keeps the service in
    auto-restart/failed state between systemd's rapid restarts, so is_active()
    stays False throughout.

    Extracted from ``_restart_filter_chain()`` so it can be reused by
    ``ensure_filter_chain_healthy()`` (Correctif 1, issue #88)."""
    import time
    from arctis_sound_manager import service_control as sc

    for _ in range(3):
        time.sleep(1.0)
        if sc.is_active("filter-chain"):
            return True
    return False


def _restart_filter_chain() -> None:
    """Restart the filter-chain service with crash-loop detection and fallback.

    After the restart, polls sc.is_active() via _poll_filter_chain_stable()
    over a short grace period. A SEGV crash-loop keeps the service in
    auto-restart/failed state, so is_active() returns False; on that signal we
    enter safe mode (see _enter_filter_chain_safe_mode). The fallback runs at
    most once per process and cannot recurse."""
    from arctis_sound_manager import service_control as sc

    if _filter_chain_safe_mode:
        _log.warning(
            "filter-chain restart skipped: safe mode active (EQ configs disabled "
            "after a prior crash-loop). Change a setting to re-enable.")
        return

    sc.restart("filter-chain", timeout=15)

    if not _poll_filter_chain_stable():
        _log.warning(
            "filter-chain did not stay active after restart (crash-loop "
            "detected) — entering safe mode")
        _enter_filter_chain_safe_mode()


def ensure_filter_chain_healthy() -> bool:
    """Detect a crash-looping filter-chain at boot or device-attach time and arm
    the safe-mode fallback if needed (Correctif 1, issue #88).

    Checks (in order):
    1. ``sc.is_active("filter-chain")`` — if False the service is already down.
    2. ``NRestarts`` (systemd only) — if >= 3 the service has restarted at
       least 3 times, which strongly indicates a crash-loop.

    If unhealthy → calls ``_enter_filter_chain_safe_mode()`` which moves ASM
    configs aside and restarts filter-chain without them so audio is flat but
    stable rather than permanently cut.

    Returns True when the filter-chain appears healthy (or the init system is
    not systemd and a live check is not possible).  Returns False when safe mode
    was entered or was already active.

    Callers must not call this in a tight loop — each call may block up to
    ``3 × 1 s`` for the poll and ``5 s`` for the NRestarts subprocess."""
    from arctis_sound_manager import service_control as sc

    if _filter_chain_safe_mode:
        return False  # already in safe mode — nothing more to do

    # Primary check: is the service running right now?
    if not sc.is_active("filter-chain"):
        _log.warning(
            "ensure_filter_chain_healthy: filter-chain is not active at "
            "boot/attach — attempting to start it (not entering safe mode)"
        )
        sc.start("filter-chain")
        return False

    # Secondary check (systemd only): NRestarts — a high restart count means
    # the service has been repeatedly crashing even if it appears momentarily
    # active between systemd's rapid auto-restarts.
    try:
        import subprocess as _sp
        from arctis_sound_manager.init_system import detect_init
        if detect_init() == "systemd":
            r = _sp.run(
                ["systemctl", "--user", "show", "filter-chain", "-p", "NRestarts"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if line.startswith("NRestarts="):
                    n_restarts = int(line.split("=", 1)[1].strip())
                    if n_restarts >= 3:
                        _log.warning(
                            "ensure_filter_chain_healthy: filter-chain NRestarts=%d "
                            "(crash-loop detected) — entering safe mode",
                            n_restarts,
                        )
                        _enter_filter_chain_safe_mode()
                        return False
    except Exception as exc:
        _log.debug("ensure_filter_chain_healthy: NRestarts query failed: %s", exc)

    return True


def apply_hrir_choice(hrir_id: str | None) -> None:
    """Copy the chosen HRIR WAV to ~/.local/share/pipewire/hrir_hesuvi/hrir.wav
    and restart filter-chain so PipeWire picks up the new file."""
    import shutil
    dest = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    if hrir_id:
        from arctis_sound_manager.hrir_catalog import package_hrir_path
        src = package_hrir_path(hrir_id)
        if src is None:
            _log.warning("HRIR WAV not found for id: %s", hrir_id)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)  # remove read-only copies (e.g. from Nix store)
        shutil.copy(src, dest)
        _log.info("HRIR changed → %s", src.name)
    _restart_filter_chain()


# ── Config generator — game / chat ────────────────────────────────────────────

def generate_sonar_eq_conf(
    channel: str,
    bands: list[EqBand],
    basses_db: float,
    voix_db: float,
    aigus_db: float,
    output_path: Path | None = None,
    spatial_audio: bool = True,
    media_spatial_audio: bool = True,
    boost_db: float = 0.0,
    smart_volume: dict | None = None,
    target_override: str | None = None,
) -> str:
    """
    Build and optionally write a filter-chain .conf for a game/chat/media/output EQ channel.

    Game channel: 8ch 7.1, single filter nodes (PipeWire auto-duplicates per channel),
    no explicit inputs/outputs, targets HeSuVi virtual surround.
    Chat channel: 2ch stereo, L/R filter pairs, explicit inputs/outputs, targets ALSA.
    Media channel: 2ch stereo, same as chat, targets physical Arctis output.
    Output channel: 8ch 7.1, single filter nodes, targets external sink (HDMI, etc.).
    """
    if channel not in ("game", "chat", "media", "output"):
        raise ValueError(f"channel must be 'game', 'chat', 'media' or 'output', got {channel!r}")

    # Channels that depend on the physical Arctis output need a connected device.
    # Instead of skipping generation entirely when the device is transiently absent
    # (e.g. during the PipeWire restart that _ApplyWorker itself triggers), write
    # the conf with an empty target — PipeWire will bind on device arrival.
    needs_physical = (
        channel == "chat"
        or (channel == "game" and not spatial_audio)
        or (channel == "media" and not media_spatial_audio)
    )
    if needs_physical and not _device_attached():
        _log.info(
            "%s EQ config: device not attached, writing with empty target — "
            "PipeWire will bind on device arrival.",
            channel,
        )

    if channel == "game" and not spatial_audio:
        target = _get_physical_out_game() if _device_attached() else ""
        channels = 2
        position = "FL FR"
    elif channel == "media" and not media_spatial_audio:
        target = _get_physical_out_game() if _device_attached() else ""
        channels = 2
        position = "FL FR"
    elif channel == "chat":
        target = target_override or (_get_physical_out_chat() if _device_attached() else "")
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]
    elif channel == "output":
        target, channels, position = _resolve_external_output(target_override)
    else:
        # game (spatial on), media (spatial on) → HeSuVi
        target = target_override or _CHANNEL_TARGET.get(channel, "")
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]

    sink_name = f"effect_input.sonar-{channel}-eq"

    if output_path is None:
        output_path = _CONF_DIR / f"sonar-{channel}-eq.conf"

    boost_db = max(-12.0, min(12.0, boost_db))

    # Collect active filter nodes: preset bands + macro sliders (if non-zero)
    active_bands: list[EqBand] = [b for b in bands if b.enabled]
    macro_values = {"basses": basses_db, "voix": voix_db, "aigus": aigus_db}
    macro_bands: list[tuple[str, EqBand]] = []
    for macro, db in macro_values.items():
        if abs(db) >= 0.01:
            p = _MACRO_PARAMS[macro]
            macro_bands.append((macro, EqBand(
                freq=p["freq"], gain=db, q=p["q"], type="peakingEQ", enabled=True,
            )))

    all_filters: list[tuple[str, EqBand]] = (
        [(f"bq{i}", b) for i, b in enumerate(active_bands)]
        + [(f"macro_{name}", b) for name, b in macro_bands]
    )

    # Passthrough / bypass if nothing to do
    if not all_filters:
        text = _bypass_conf(sink_name, target, channels, position, channel=channel)
        _write_conf(output_path, text)
        return text

    if channels != 2 or channel == "output":
        text = _active_conf_8ch(channel, sink_name, target, position,
                                all_filters, active_bands, macro_bands,
                                boost_db, smart_volume, channels=channels)
    else:
        text = _active_conf_2ch(channel, sink_name, target, position,
                                all_filters, active_bands, macro_bands,
                                boost_db, smart_volume)

    _write_conf(output_path, text)
    return text


def _active_conf_8ch(
    channel: str, sink_name: str, target: str, position: str,
    all_filters: list[tuple[str, EqBand]],
    active_bands: list[EqBand],
    macro_bands: list[tuple[str, EqBand]],
    boost_db: float,
    smart_volume: dict | None = None,
    channels: int = 8,
) -> str:
    """Multi-channel config: single filter nodes, PipeWire auto-duplicates per channel."""
    node_lines: list[str] = []
    link_lines: list[str] = []
    names = [n for n, _ in all_filters]
    last_name = names[-1]

    for (name, band), nm in zip(all_filters, names):
        label = PW_LABEL.get(band.type, "bq_peaking")
        node_lines.append(_node_block(nm, label, band.freq, band.q, band.gain))

    for i in range(len(all_filters) - 1):
        link_lines.append(_link(names[i], names[i + 1]))

    if abs(boost_db) >= 0.01:
        node_lines.append(
            f"                    {{ type = builtin  name = boost  label = bq_highshelf\n"
            f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
        )
        link_lines.append(_link(last_name, "boost"))
        last_name = "boost"

    if smart_volume and smart_volume.get("enabled"):
        # Correctif 4 (issue #88): guard LADSPA node behind plugin availability
        # check. A missing sc4m_1916.so causes dlopen() SEGV in filter-chain.
        _sc4m_path = _resolve_ladspa_plugin("sc4m_1916.so")
        if not _sc4m_path:
            _log.warning(
                "LADSPA plugin sc4m_1916 not found — skipping Smart Volume "
                "compressor node, feature degraded; install swh-plugins on the host"
            )
        else:
            mode = smart_volume.get("loudness", "balanced")
            level = smart_volume.get("level", 50)
            preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
            node_lines.append(_sc4m_node("compressor", preset, level, _sc4m_path))
            link_lines.append(_link_to_ladspa(last_name, "compressor"))

    nodes_text = "\n".join(node_lines)
    links_block = ""
    if link_lines:
        links_text = "\n".join(link_lines)
        links_block = f"""        links = [
{links_text}
        ]"""

    media_class = "Audio/Sink" if channel == "output" else "Audio/Sink/Internal"
    priority = "1" if channel == "output" else "1000"
    _target_line = (
        f'        node.target         = "{target}"\n'
        f'        target.object       = "{target}"\n'
    ) if target else ''

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# Channel: {channel}  |  Active bands: {len(active_bands)}  |  Macros: {len(macro_bands)}
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar {channel.capitalize()} EQ"
      filter.graph = {{
        nodes = [
{nodes_text}
        ]
{links_block}
      }}
      capture.props = {{
        node.name         = "{sink_name}"
        media.class       = {media_class}
        priority.session  = {priority}
        audio.channels = {channels}
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name           = "effect_output.sonar-{channel}-eq"
{_target_line}        node.dont-fallback  = true
        node.linger         = true
        audio.channels      = {channels}
        audio.position      = [ {position} ]
      }}
    }}
  }}
]
"""


def _active_conf_2ch(
    channel: str, sink_name: str, target: str, position: str,
    all_filters: list[tuple[str, EqBand]],
    active_bands: list[EqBand],
    macro_bands: list[tuple[str, EqBand]],
    boost_db: float,
    smart_volume: dict | None = None,
) -> str:
    """2ch config: L/R filter pairs with explicit inputs/outputs."""
    node_lines: list[str] = []
    link_lines: list[str] = []

    names_L = [f"{n}_L" for n, _ in all_filters]
    names_R = [f"{n}_R" for n, _ in all_filters]

    for (name, band), nL, nR in zip(all_filters, names_L, names_R):
        label = PW_LABEL.get(band.type, "bq_peaking")
        node_lines.append(_node_block(nL, label, band.freq, band.q, band.gain))
        node_lines.append(_node_block(nR, label, band.freq, band.q, band.gain))

    for i in range(len(all_filters) - 1):
        link_lines.append(_link(names_L[i], names_L[i + 1]))
        link_lines.append(_link(names_R[i], names_R[i + 1]))

    if abs(boost_db) >= 0.01:
        node_lines.append(
            f"                    {{ type = builtin  name = boost_L  label = bq_highshelf\n"
            f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
        )
        node_lines.append(
            f"                    {{ type = builtin  name = boost_R  label = bq_highshelf\n"
            f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
        )
        link_lines.append(_link(names_L[-1], "boost_L"))
        link_lines.append(_link(names_R[-1], "boost_R"))
        last_L, last_R = "boost_L", "boost_R"
    else:
        last_L, last_R = names_L[-1], names_R[-1]

    # Correctif 4 (issue #88): track whether LADSPA comp nodes were actually
    # added — affects port name ("Output" vs "Out") used in outputs_text below.
    _smart_vol_ladspa = False
    if smart_volume and smart_volume.get("enabled"):
        # Guard: a missing sc4m_1916.so causes dlopen() SEGV in filter-chain.
        _sc4m_path = _resolve_ladspa_plugin("sc4m_1916.so")
        if not _sc4m_path:
            _log.warning(
                "LADSPA plugin sc4m_1916 not found — skipping Smart Volume "
                "compressor nodes, feature degraded; install swh-plugins on the host"
            )
        else:
            mode = smart_volume.get("loudness", "balanced")
            level = smart_volume.get("level", 50)
            preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
            node_lines.append(_sc4m_node("comp_L", preset, level, _sc4m_path))
            node_lines.append(_sc4m_node("comp_R", preset, level, _sc4m_path))
            link_lines.append(_link_to_ladspa(last_L, "comp_L"))
            link_lines.append(_link_to_ladspa(last_R, "comp_R"))
            last_L, last_R = "comp_L", "comp_R"
            _smart_vol_ladspa = True

    nodes_text   = "\n".join(node_lines)
    links_text   = "\n".join(link_lines)
    inputs_text  = f'"{names_L[0]}:In"  "{names_R[0]}:In"'
    # LADSPA nodes use "Output" port name, builtins use "Out".
    # Use _smart_vol_ladspa (not smart_volume.get("enabled")) so that a missing
    # plugin that was skipped does not produce a broken "Output" port reference.
    out_port = "Output" if _smart_vol_ladspa else "Out"
    outputs_text = f'"{last_L}:{out_port}"  "{last_R}:{out_port}"'
    _target_line = (
        f'        node.target         = "{target}"\n'
        f'        target.object       = "{target}"\n'
    ) if target else ''

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# Channel: {channel}  |  Active bands: {len(active_bands)}  |  Macros: {len(macro_bands)}
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar {channel.capitalize()} EQ"
      filter.graph = {{
        nodes = [
{nodes_text}
        ]
        links = [
{links_text}
        ]
        inputs  = [ {inputs_text} ]
        outputs = [ {outputs_text} ]
      }}
      capture.props = {{
        node.name         = "{sink_name}"
        media.class       = Audio/Sink/Internal
        priority.session  = 1000
        audio.channels = 2
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name           = "effect_output.sonar-{channel}-eq"
{_target_line}        node.dont-fallback  = true
        node.linger         = true
        audio.channels      = 2
        audio.position      = [ {position} ]
      }}
    }}
  }}
]
"""


# ── Config generator — micro ──────────────────────────────────────────────────


def generate_sonar_micro_conf(
    bands: list[EqBand],
    basses_db: float,
    voix_db: float,
    aigus_db: float,
    output_path: Path | None = None,
    boost_db: float = 0.0,
    noise_canceling: dict | None = None,
    noise_reduction: dict | None = None,
) -> str:
    """
    Build and optionally write a filter-chain .conf for the microphone EQ.

    Creates a virtual Audio/Source node backed by the physical mic input.
    Pattern: capture side is passive (faces hardware), playback side has
    media.class = Audio/Source (faces applications).
    """
    if not _device_attached():
        _log.info(
            "micro EQ config: device not attached, writing with empty target — "
            "PipeWire will bind on device arrival."
        )

    if output_path is None:
        output_path = _CONF_DIR / "sonar-micro-eq.conf"

    boost_db = max(-12.0, min(12.0, boost_db))

    active_bands = [b for b in bands if b.enabled]
    macro_values = {"basses": basses_db, "voix": voix_db, "aigus": aigus_db}
    macro_bands: list[tuple[str, EqBand]] = []
    for macro, db in macro_values.items():
        if abs(db) >= 0.01:
            p = _MACRO_PARAMS[macro]
            macro_bands.append((macro, EqBand(
                freq=p["freq"], gain=db, q=p["q"], type="peakingEQ", enabled=True,
            )))

    all_filters = (
        [(f"bq{i}", b) for i, b in enumerate(active_bands)]
        + [(f"macro_{name}", b) for name, b in macro_bands]
    )

    nc = noise_canceling or {}
    nr = noise_reduction or {}
    bg = nr.get("bgReduction", {})
    impact = nr.get("impactReduction", {})
    ng = nr.get("noiseGate", {})
    comp = nr.get("compressor", {})
    has_processing = (nc.get("enabled", False)
                      or bg.get("enabled", False)
                      or impact.get("enabled", False)
                      or ng.get("enabled", False)
                      or comp.get("enabled", False))

    if not all_filters and not has_processing:
        text = _bypass_micro_conf()
        _write_conf(output_path, text)
        return text

    node_lines: list[str] = []
    link_lines: list[str] = []

    # If no EQ filters but processing nodes are enabled, insert a 0 dB passthrough
    if not all_filters:
        all_filters = [("pass", EqBand(freq=10.0, gain=0.0, q=0.707, type="peakingEQ", enabled=True))]
    names = [n for n, _ in all_filters]

    for (name, band), nm in zip(all_filters, names):
        label = PW_LABEL.get(band.type, "bq_peaking")
        node_lines.append(_node_block(nm, label, band.freq, band.q, band.gain))

    for i in range(len(all_filters) - 1):
        link_lines.append(_link(names[i], names[i + 1]))

    if abs(boost_db) >= 0.01:
        node_lines.append(
            f"                    {{ type = builtin  name = boost  label = bq_highshelf\n"
            f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
        )
        link_lines.append(_link(names[-1], "boost"))
        last_node = "boost"
    else:
        last_node = names[-1]

    # Track whether last_node is LADSPA (uses Input/Output ports) or builtin (In/Out)
    last_is_ladspa = False

    def _smart_link(new_name: str, new_is_ladspa: bool) -> str:
        """Pick the right link helper based on source/dest node types.

        Reads ``last_node`` / ``last_is_ladspa`` from the enclosing scope; it
        never rebinds them, so no ``nonlocal`` declaration is needed.
        """
        if last_is_ladspa and new_is_ladspa:
            return _link_ladspa(last_node, new_name)
        elif last_is_ladspa:
            return _link_from_ladspa(last_node, new_name)
        elif new_is_ladspa:
            return _link_to_ladspa(last_node, new_name)
        else:
            return _link(last_node, new_name)

    # ── Background noise reduction (high-pass: cuts low-frequency rumble) ──
    if bg.get("enabled", False):
        # value 0→1 maps cutoff 30→350 Hz
        bg_val = max(0.0, min(1.0, bg.get("value", 0.0)))
        hp_freq = 30.0 + bg_val * 320.0
        node_lines.append(
            f"                    {{ type = builtin  name = nr_bg  label = bq_highpass\n"
            f"                      control = {{ Freq = {hp_freq:.1f}  Q = 0.7071  Gain = 0.0 }} }}"
        )
        link_lines.append(_smart_link("nr_bg", False))
        last_node = "nr_bg"
        last_is_ladspa = False

    # ── Impact noise reduction (high-shelf cut: softens transients) ──
    if impact.get("enabled", False):
        # value 0→1 maps gain 0→-12 dB at 4 kHz
        impact_val = max(0.0, min(1.0, impact.get("value", 0.0)))
        impact_gain = -impact_val * 12.0
        if abs(impact_gain) >= 0.01:
            node_lines.append(
                f"                    {{ type = builtin  name = nr_impact  label = bq_highshelf\n"
                f"                      control = {{ Freq = 4000.0  Q = 0.7071  Gain = {impact_gain:.1f} }} }}"
            )
            link_lines.append(_smart_link("nr_impact", False))
            last_node = "nr_impact"
            last_is_ladspa = False

    # ── Noise gate (LADSPA swh-plugins gate_1410) ──
    # Correctif 4 (issue #88): guard LADSPA node. A missing gate_1410.so causes
    # dlopen() SEGV in filter-chain; omit node gracefully if plugin not found.
    if ng.get("enabled", False):
        _gate_path = _resolve_ladspa_plugin("gate_1410.so")
        if not _gate_path:
            _log.warning(
                "LADSPA plugin gate_1410 not found — skipping noise gate node, "
                "feature degraded; install swh-plugins on the host"
            )
        else:
            threshold = max(-60.0, min(-10.0, ng.get("value", -40.0)))
            node_lines.append(
                f"                    {{ type = ladspa  name = ngate  plugin = {_gate_path}  label = gate\n"
                f"                      control = {{ \"Threshold (dB)\" = {threshold:.1f}"
                f"  \"Attack (ms)\" = 5.0  \"Hold (ms)\" = 50.0  \"Decay (ms)\" = 100.0"
                f"  \"Range (dB)\" = -90.0"
                f"  \"Output select (-1 = key listen, 0 = gate, 1 = bypass)\" = 0"
                f" }} }}"
            )
            link_lines.append(_smart_link("ngate", True))
            last_node = "ngate"
            last_is_ladspa = True

    # ── rnnoise noise cancellation ──
    # Correctif 4 (issue #88): guard LADSPA node. A missing librnnoise_ladspa.so
    # causes dlopen() SEGV in filter-chain; omit node gracefully if not found.
    if nc.get("enabled", False):
        _rnnoise_path = _resolve_ladspa_plugin("librnnoise_ladspa.so")
        if not _rnnoise_path:
            _log.warning(
                "LADSPA plugin librnnoise_ladspa not found — skipping noise "
                "cancellation node, feature degraded; install "
                "noise-suppression-for-voice on the host"
            )
        else:
            vad_threshold = max(0.0, min(100.0, nc.get("value", 0.5) * 100))
            node_lines.append(
                f"                    {{ type = ladspa  name = rnnoise\n"
                f"                      plugin = {_rnnoise_path}  label = noise_suppressor_mono\n"
                f"                      control = {{ \"VAD Threshold (%)\" = {vad_threshold:.1f} }} }}"
            )
            link_lines.append(_smart_link("rnnoise", True))
            last_node = "rnnoise"
            last_is_ladspa = True

    # ── Compressor / volume stabilizer (LADSPA sc4m_1916) ──
    # Correctif 4 (issue #88): guard LADSPA node. A missing sc4m_1916.so causes
    # dlopen() SEGV in filter-chain; omit node gracefully if plugin not found.
    if comp.get("enabled", False):
        _sc4m_path_micro = _resolve_ladspa_plugin("sc4m_1916.so")
        if not _sc4m_path_micro:
            _log.warning(
                "LADSPA plugin sc4m_1916 not found — skipping micro compressor "
                "node, feature degraded; install swh-plugins on the host"
            )
        else:
            # value 0→1 maps compression intensity
            comp_val = max(0.0, min(1.0, comp.get("value", 0.0)))
            comp_threshold = -10.0 - comp_val * 20.0   # -10 → -30 dB
            comp_ratio = 2.0 + comp_val * 6.0           # 2:1 → 8:1
            comp_makeup = comp_val * 10.0                # 0 → 10 dB
            node_lines.append(
                f"                    {{ type = ladspa  name = comp  plugin = {_sc4m_path_micro}  label = sc4m\n"
                f"                      control = {{ \"RMS/peak\" = 0.5"
                f"  \"Attack time (ms)\" = 10.0  \"Release time (ms)\" = 150.0"
                f"  \"Threshold level (dB)\" = {comp_threshold:.1f}"
                f"  \"Ratio (1:n)\" = {comp_ratio:.1f}"
                f"  \"Knee radius (dB)\" = 6.0"
                f"  \"Makeup gain (dB)\" = {comp_makeup:.1f}"
                f" }} }}"
            )
            link_lines.append(_smart_link("comp", True))
            last_node = "comp"
            last_is_ladspa = True

    nodes_text  = "\n".join(node_lines)
    links_text  = "\n".join(link_lines)

    # LADSPA nodes use port name "Output", builtin nodes use "Out"
    last_out_port = "Output" if last_is_ladspa else "Out"

    text = f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# Channel: micro  |  Active bands: {len(active_bands)}  |  Macros: {len(macro_bands)}
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar Micro EQ"
      filter.graph = {{
        nodes = [
{nodes_text}
        ]
        links = [
{links_text}
        ]
        inputs  = [ "{names[0]}:In" ]
        outputs = [ "{last_node}:{last_out_port}" ]
      }}
      capture.props = {{
        node.name      = "effect_input.sonar-micro-eq"
        node.passive   = true
        target.object  = "{_get_physical_in()}"
        audio.rate     = 48000
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name             = "effect_output.sonar-micro-eq"
        media.class           = Audio/Source
        audio.rate            = 48000
        audio.channels        = 1
        audio.position        = [ MONO ]
        node.latency          = 1024/48000
        node.lock-quantum     = true
        priority.session      = 1010
      }}
    }}
  }}
]
"""
    _write_conf(output_path, text)
    return text


# ── Bypass / passthrough ──────────────────────────────────────────────────────

def _bypass_conf(sink_name: str, target: str, channels: int, position: str, channel: str = "") -> str:
    """Generate a bypass config. Multi-channel uses auto-dup (no inputs/outputs), 2ch uses L/R."""
    _target_line = (
        f'        node.target         = "{target}"\n'
        f'        target.object       = "{target}"\n'
    ) if target else ''
    media_class = "Audio/Sink" if channel == "output" else "Audio/Sink/Internal"
    priority = "1" if channel == "output" else "1000"
    if channels != 2:
        return f"""\
# Auto-generated by Arctis Sound Manager — passthrough (all gains = 0)
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar EQ (bypass)"
      filter.graph = {{
        nodes = [
                    {{ type = builtin  name = copy  label = copy }}
        ]
      }}
      capture.props = {{
        node.name         = "{sink_name}"
        media.class       = {media_class}
        priority.session  = {priority}
        audio.channels = {channels}
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name           = "{sink_name.replace('effect_input.', 'effect_output.')}"
{_target_line}        node.dont-fallback  = true
        node.linger         = true
        audio.channels      = {channels}
        audio.position      = [ {position} ]
      }}
    }}
  }}
]
"""
    return f"""\
# Auto-generated by Arctis Sound Manager — passthrough (all gains = 0)
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar EQ (bypass)"
      filter.graph = {{
        nodes = [
                    {{ type = builtin  name = copy_L  label = copy }}
                    {{ type = builtin  name = copy_R  label = copy }}
        ]
        inputs  = [ "copy_L:In"  "copy_R:In" ]
        outputs = [ "copy_L:Out" "copy_R:Out" ]
      }}
      capture.props = {{
        node.name         = "{sink_name}"
        media.class       = {media_class}
        priority.session  = {priority}
        audio.channels = 2
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name           = "{sink_name.replace('effect_input.', 'effect_output.')}"
{_target_line}        node.dont-fallback  = true
        node.linger         = true
        audio.channels      = 2
        audio.position      = [ {position} ]
      }}
    }}
  }}
]
"""


def _bypass_micro_conf() -> str:
    return f"""\
# Auto-generated by Arctis Sound Manager — micro passthrough (all gains = 0)
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Sonar Micro EQ (bypass)"
      filter.graph = {{
        nodes = [
                    {{ type = builtin  name = copy  label = copy }}
        ]
        inputs  = [ "copy:In" ]
        outputs = [ "copy:Out" ]
      }}
      capture.props = {{
        node.name      = "effect_input.sonar-micro-eq"
        node.passive   = true
        target.object  = "{_get_physical_in()}"
        audio.rate     = 48000
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name             = "effect_output.sonar-micro-eq"
        media.class           = Audio/Source
        audio.rate            = 48000
        audio.channels        = 1
        audio.position        = [ MONO ]
        node.latency          = 1024/48000
        node.lock-quantum     = true
        priority.session      = 1010
      }}
    }}
  }}
]
"""


# ── Virtual sinks generation ─────────────────────────────────────────────────

_SINKS_CONF_DIR = Path.home() / ".config" / "pipewire" / "pipewire.conf.d"

_VIRTUAL_SINKS = [
    {"desc": "Game",  "capture": "Arctis_Game",  "playback": "Arctis_Game_sink_out",
     "sonar_target": "effect_input.sonar-game-eq",  "role": "game"},
    {"desc": "Chat",  "capture": "Arctis_Chat",  "playback": "Arctis_Chat_sink_out",
     "sonar_target": "effect_input.sonar-chat-eq",  "role": "chat"},
    {"desc": "Media", "capture": "Arctis_Media", "playback": "Arctis_Media_sink_out",
     "sonar_target": "effect_input.sonar-media-eq", "role": "game"},
]


def generate_virtual_sinks_conf(sonar: bool) -> str:
    """DEPRECATED: loopbacks are now managed dynamically by the daemon.

    This function no longer generates a static PipeWire config.  Instead it
    removes the legacy ``10-arctis-virtual-sinks.conf`` file if it still exists
    (one-shot migration: the next PipeWire restart will unload the old static
    modules, and the daemon creates dynamic loopbacks via ``LoopbackManager``).

    The signature ``(sonar: bool) -> str`` is kept unchanged to avoid breaking
    existing callers (equalizer_page, sonar_toggle_widget, sonar_page,
    profile_manager) — they will all become no-ops transparently.

    Returns an empty string (no config text produced).
    """
    _log.warning(
        "generate_virtual_sinks_conf() is deprecated: loopbacks are now "
        "managed dynamically by the daemon.  Removing static file if present."
    )
    static_path = _SINKS_CONF_DIR / "10-arctis-virtual-sinks.conf"
    if static_path.exists():
        try:
            static_path.unlink()
            _log.info("Removed legacy static loopback config: %s", static_path)
        except OSError as exc:
            _log.warning("Could not remove legacy static loopback config %s: %s", static_path, exc)
    return ""


# ── File I/O ──────────────────────────────────────────────────────────────────

def _write_conf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def check_and_fix_stale_configs() -> tuple[bool, bool]:
    """Detect and fix stale Sonar configs.

    Checks for:
    1. Static HeSuVi conf in ``pipewire.conf.d/`` conflicting with the filter-chain
       dynamic version (old installs placed it there — creates a duplicate node that
       silences the Game channel).
    2. Configs using the broken ``label = gain`` builtin (PipeWire 1.6.x).
    3. Sonar configs left in ``pipewire.conf.d/`` (must be in
       ``filter-chain.conf.d/`` — restarting pipewire is too disruptive).
    4. 2ch game EQ when spatial audio is ON (must be 8ch for HeSuVi).
    5. Virtual sink targets out of sync with current EQ mode.
    6. HeSuVi config with stale ``node.target`` (wrong physical output).
    7. Micro config with empty ``target.object`` written before device attach —
       PipeWire would otherwise bind to the first available source (e.g. a game
       controller mic) instead of the Arctis headset.

    Returns ``(fixed, needs_pipewire_restart)``.  *fixed* is True if any config
    was regenerated or cleaned.  *needs_pipewire_restart* is True when the static
    HeSuVi file was removed from ``pipewire.conf.d/``; the caller must restart
    the main PipeWire process so the duplicate node is unloaded.
    """
    import logging
    log = logging.getLogger(__name__)

    # Correctif 2 (issue #88): safe mode is active — the ASM configs were
    # intentionally moved aside to break a filter-chain SEGV crash-loop.
    # Regenerating them now would re-arm the crash.  Skip all repairs until the
    # user explicitly resets safe mode via reset_filter_chain_safe_mode().
    if _filter_chain_safe_mode or _SAFE_MODE_MARKER.exists():
        log.info(
            "check_and_fix_stale_configs: safe mode active — skipping config "
            "regeneration (filter_chain_safe_mode marker present)"
        )
        return False, False

    fixed = False
    needs_pw_restart = False
    bad_dir = _CONF_DIR.parent / "pipewire.conf.d"

    # ── Migration: remove static HeSuVi from pipewire.conf.d ─────────────────
    # Old install.sh put sink-virtual-surround-7.1-hesuvi.conf in pipewire.conf.d
    # so main PipeWire loads it.  The daemon also generates a dynamic version in
    # filter-chain.conf.d, creating TWO nodes with the same name.  WirePlumber
    # can then route sonar-game-eq to the wrong one → silent Game channel.
    # Fix: remove the static copy so only the filter-chain version exists.
    static_hesuvi = bad_dir / "sink-virtual-surround-7.1-hesuvi.conf"
    if static_hesuvi.exists():
        log.warning("Removing stale static HeSuVi config from pipewire.conf.d "
                    "(duplicate node conflict — will restart PipeWire to clear it)")
        static_hesuvi.unlink()
        # Ensure the dynamic version exists in filter-chain.conf.d so it survives
        # the upcoming pipewire restart without a gap.
        dynamic_hesuvi = _CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
        if not dynamic_hesuvi.exists():
            generate_hesuvi_conf()
        fixed = True
        needs_pw_restart = True

    for name in ("sonar-game-eq.conf", "sonar-chat-eq.conf"):
        # Remove stale copies from pipewire.conf.d (wrong location)
        bad_path = bad_dir / name
        if bad_path.exists():
            log.warning("Removing sonar config from pipewire.conf.d: %s", bad_path)
            bad_path.unlink()
            fixed = True

        # Fix broken 'label = gain' or wrong channel count in correct location
        path = _CONF_DIR / name
        if path.exists():
            content = path.read_text()
            needs_regen = False

            if "label = gain" in content:
                log.warning("Stale config (%s uses 'label = gain'), regenerating", name)
                needs_regen = True

            # Game EQ must be 8ch for HeSuVi virtual surround — but only if spatial audio is ON.
            # When spatial audio is disabled, 2ch game EQ is intentional (routes to physical out).
            if name == "sonar-game-eq.conf" and "audio.channels = 2" in content:
                try:
                    import json as _json
                    _spatial_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio.json"
                    _spatial = _json.loads(_spatial_file.read_text()) if _spatial_file.exists() else {}
                    _spatial_on = _spatial.get("enabled", True)
                except Exception:
                    _spatial_on = True
                if _spatial_on:
                    log.warning("Stale config (%s uses 2ch, should be 8ch), regenerating", name)
                    needs_regen = True

            if name == "sonar-media-eq.conf" and "audio.channels = 2" in content:
                try:
                    import json as _json
                    _media_sp_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio_media.json"
                    _media_sp = _json.loads(_media_sp_file.read_text()) if _media_sp_file.exists() else {}
                    _media_sp_on = _media_sp.get("enabled", True)
                except Exception:
                    _media_sp_on = True
                if _media_sp_on:
                    log.warning("Stale config (%s uses 2ch, should be 8ch), regenerating", name)
                    needs_regen = True

            if needs_regen:
                channel = name.replace("sonar-", "").replace("-eq.conf", "")
                sink_name = f"effect_input.sonar-{channel}-eq"
                target = {
                    "game":   _SURROUND,
                    "media":  _SURROUND,
                    "chat":   _get_physical_out_chat(),
                    "output": "",
                }.get(channel, _get_physical_out_game())
                channels = _CHANNEL_CHANNELS.get(channel, 2)
                position = _CHANNEL_POSITION.get(channel, "FL FR")
                _write_conf(path, _bypass_conf(sink_name, target, channels, position, channel=channel))
                fixed = True

    # Micro EQ: remove stale copies from pipewire.conf.d
    micro_bad = bad_dir / "sonar-micro-eq.conf"
    if micro_bad.exists():
        log.warning("Removing micro config from pipewire.conf.d: %s", micro_bad)
        micro_bad.unlink()
        fixed = True

    # Fix micro configs using old Audio/Source/Virtual or Audio/Sink pattern
    micro_path = _CONF_DIR / "sonar-micro-eq.conf"
    if micro_path.exists():
        content = micro_path.read_text()
        if "Audio/Source/Virtual" in content or "Audio/Sink" in content or "label = gain" in content:
            log.warning("Stale micro config (wrong media.class or label=gain), regenerating")
            _write_conf(micro_path, _bypass_micro_conf())
            fixed = True
        elif 'target.object  = ""' in content:
            physical_in = _get_physical_in()
            if physical_in:
                log.warning(
                    "Micro config has empty target.object but device is now attached (%s) — patching",
                    physical_in,
                )
                _write_conf(micro_path, content.replace(
                    'target.object  = ""',
                    f'target.object  = "{physical_in}"',
                ))
                fixed = True

    # Migration: remove static 10-arctis-virtual-sinks.conf if still present.
    #
    # Loopbacks are now managed dynamically by the daemon via LoopbackManager
    # (pw-loopback child processes).  The old static config loaded by the main
    # PipeWire daemon via pipewire.conf.d/ cannot be unloaded at runtime
    # ("Access denied" from pactl).  Removing the file and restarting PipeWire
    # once (one-shot migration) tears down the static loopbacks; the daemon
    # will immediately re-create them dynamically.
    state_file = Path.home() / ".config" / "arctis_manager" / ".eq_mode"
    sonar = state_file.exists() and state_file.read_text().strip() == "sonar"
    sinks_path = _SINKS_CONF_DIR / "10-arctis-virtual-sinks.conf"
    if sinks_path.exists():
        log.warning(
            "Legacy static loopback config found at %s — removing for "
            "migration to dynamic loopbacks.  A one-shot PipeWire restart "
            "will unload the old static modules.", sinks_path
        )
        try:
            sinks_path.unlink()
            fixed = True
            needs_pw_restart = True
        except OSError as exc:
            log.error("Failed to remove legacy static loopback config %s: %s", sinks_path, exc)

    # Ensure HeSuVi config targets the current physical output (catches configs written
    # with the old hardcoded _PHYSICAL_OUT constant before v1.0.23).
    if sonar:
        try:
            import json as _json
            _spatial_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio.json"
            _spatial = _json.loads(_spatial_file.read_text()) if _spatial_file.exists() else {}
            _spatial_on = _spatial.get("enabled", True)
        except Exception:
            _spatial = {}
            _spatial_on = True
        if _spatial_on:
            hesuvi_path = _CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
            if hesuvi_path.exists():
                hesuvi_content = hesuvi_path.read_text()
                if f'node.target        = "{_get_physical_out_game()}"' not in hesuvi_content:
                    log.warning("HeSuVi config has stale node.target, regenerating")
                    generate_hesuvi_conf(
                        immersion_pct=_spatial.get("immersion", 50),
                        distance_pct=_spatial.get("distance", 50),
                    )
                    fixed = True

    # Ensure sonar EQ nodes exist when in Sonar mode
    if sonar and ensure_sonar_eq_configs():
        fixed = True

    return fixed, needs_pw_restart


def ensure_sonar_eq_configs() -> bool:
    """Generate or fix bypass EQ configs for game and chat channels.

    Validates that ``effect_input.sonar-game-eq`` and ``effect_input.sonar-chat-eq``
    exist as PipeWire nodes with the correct channel count and target sink.
    Regenerates any config that is missing OR has stale content (wrong channel
    count, wrong target) — not just absent files.

    Returns True if any config was generated or regenerated.
    """
    import logging
    log = logging.getLogger(__name__)

    # Correctif 2 (issue #88): safe mode is active — the ASM configs were
    # intentionally moved aside to break a filter-chain SEGV crash-loop.
    # Regenerating them here would re-arm the crash.  Skip until the user
    # explicitly resets safe mode via reset_filter_chain_safe_mode().
    if _filter_chain_safe_mode or _SAFE_MODE_MARKER.exists():
        log.info(
            "ensure_sonar_eq_configs: safe mode active — skipping config "
            "regeneration"
        )
        return False

    generated = False

    # Determine if spatial audio is enabled — affects game channel count and target.
    try:
        import json as _json
        _spatial_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio.json"
        _spatial = _json.loads(_spatial_file.read_text()) if _spatial_file.exists() else {}
        spatial_on = _spatial.get("enabled", True)
    except Exception:
        spatial_on = True

    # Read media spatial state
    try:
        import json as _json
        _media_spatial_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio_media.json"
        _media_spatial = _json.loads(_media_spatial_file.read_text()) if _media_spatial_file.exists() else {}
        media_spatial_on = _media_spatial.get("enabled", True)
    except Exception:
        media_spatial_on = True

    expected: dict[str, dict] = {
        "game": {
            "channels": 8 if spatial_on else 2,
            "position": _CHANNEL_POSITION["game"] if spatial_on else "FL FR",
            "target":   _SURROUND if spatial_on else _get_physical_out_game(),
        },
        "media": {
            "channels": 8 if media_spatial_on else 2,
            "position": _CHANNEL_POSITION["media"] if media_spatial_on else "FL FR",
            "target":   _SURROUND if media_spatial_on else _get_physical_out_game(),
        },
        "chat": {
            "channels": _CHANNEL_CHANNELS["chat"],
            "position": _CHANNEL_POSITION["chat"],
            "target":   _get_physical_out_chat(),
        },
    }

    for channel in ("game", "media", "chat"):
        conf_path = _CONF_DIR / f"sonar-{channel}-eq.conf"
        sink_name = f"effect_input.sonar-{channel}-eq"
        exp = expected[channel]
        needs_regen = False

        if not conf_path.exists():
            log.warning(
                "sonar-%s-eq.conf missing — generating bypass so %s node exists",
                channel, sink_name,
            )
            needs_regen = True
        else:
            content = conf_path.read_text()
            ch_str  = f"audio.channels = {exp['channels']}"
            tgt_str = f'node.target         = "{exp["target"]}"'
            if ch_str not in content:
                log.warning(
                    "sonar-%s-eq.conf has wrong channel count (expected %d) — regenerating",
                    channel, exp["channels"],
                )
                needs_regen = True
            elif exp["target"] and tgt_str not in content:
                log.warning(
                    "sonar-%s-eq.conf has wrong target (expected %r) — regenerating",
                    channel, exp["target"],
                )
                needs_regen = True

        if needs_regen:
            _write_conf(
                conf_path,
                _bypass_conf(sink_name, exp["target"], exp["channels"], exp["position"]),
            )
            generated = True

    return generated


# ── Config generator — HeSuVi 7.1 virtual surround ──────────────────────────

_HESUVI_CHANNELS = ("FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR")

# Convolver definitions: (name, hrir channel index)
# Order matches the static config exactly.
_HESUVI_CONVOLVERS = [
    ("convFL_L",  0), ("convFL_R",  1),
    ("convSL_L",  2), ("convSL_R",  3),
    ("convRL_L",  4), ("convRL_R",  5),
    ("convFC_L",  6), ("convFR_R",  7),
    ("convFR_L",  8), ("convSR_R",  9),
    ("convSR_L", 10), ("convRR_R", 11),
    ("convRR_L", 12), ("convFC_R", 13),
    # LFE treated as FC
    ("convLFE_L", 6), ("convLFE_R", 13),
]

# copy→convolver feed mapping: gain node → list of convolver inputs
# (matches the static config link order)
_HESUVI_COPY_CONV_LINKS = [
    ("FL",  ["convFL_L",  "convFL_R"]),
    ("SL",  ["convSL_L",  "convSL_R"]),
    ("RL",  ["convRL_L",  "convRL_R"]),
    ("FC",  ["convFC_L"]),
    ("FR",  ["convFR_R",  "convFR_L"]),
    ("SR",  ["convSR_R",  "convSR_L"]),
    ("RR",  ["convRR_R",  "convRR_L"]),
    ("FC",  ["convFC_R"]),
    ("LFE", ["convLFE_L", "convLFE_R"]),
]

# convolver→mixer feed mapping (matches the static config link order)
_HESUVI_CONV_MIX_LINKS = [
    ("convFL_L",  "mixL", 1), ("convFL_R",  "mixR", 1),
    ("convSL_L",  "mixL", 2), ("convSL_R",  "mixR", 2),
    ("convRL_L",  "mixL", 3), ("convRL_R",  "mixR", 3),
    ("convFC_L",  "mixL", 4), ("convFC_R",  "mixR", 4),
    ("convFR_R",  "mixR", 5), ("convFR_L",  "mixL", 5),
    ("convSR_R",  "mixR", 6), ("convSR_L",  "mixL", 6),
    ("convRR_R",  "mixR", 7), ("convRR_L",  "mixL", 7),
    ("convLFE_R", "mixR", 8), ("convLFE_L", "mixL", 8),
]


def generate_hesuvi_conf(
    immersion_pct: int = 50,
    distance_pct: int = 50,
    output_path: Path | None = None,
) -> str:
    """Generate a dynamic HeSuVi 7.1 virtual surround PipeWire filter-chain config.

    Parameters
    ----------
    immersion_pct:
        0-100, maps linearly to 0.0-12.0 dB gain applied uniformly to all
        8 channels *before* the HRTF convolution via bq_highshelf nodes.
    distance_pct:
        0-100, maps linearly to 0.0-1.0 wet mix for the LADSPA plate reverb
        applied *after* the stereo mixers.
    output_path:
        Where to write the config.  Defaults to
        ``_CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"``.

    Returns
    -------
    str
        The generated config text (also written to *output_path*).
    """
    if not _device_attached():
        _log.warning("Skipping HeSuVi config generation: no Arctis device attached.")
        return ""

    if output_path is None:
        output_path = _CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
        # Remove any static copy from pipewire.conf.d to avoid duplicate node name conflict.
        # install.sh places the static HeSuVi config there; ASM's dynamic version (here)
        # supersedes it when Sonar mode is active. Having both causes the game channel to
        # go silent because PipeWire and filter-chain both try to register the same node name.
        _pw_static = _SINKS_CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
        if _pw_static.exists():
            _log.warning(
                "Removing duplicate HeSuVi config from pipewire.conf.d "
                "(superseded by filter-chain.conf.d version)"
            )
            _pw_static.unlink()

    immersion_pct = max(0, min(100, immersion_pct))
    distance_pct = max(0, min(100, distance_pct))

    immersion_db = immersion_pct / 100.0 * 12.0
    distance_wet = distance_pct / 100.0

    # ── Nodes ────────────────────────────────────────────────────────────
    node_lines: list[str] = []
    I = "                    "  # noqa: E741 — indentation constant

    # 1. Copy nodes
    node_lines.append(f"{I}# duplicate inputs")
    for ch in _HESUVI_CHANNELS:
        node_lines.append(f'{I}{{ type = builtin  label = copy  name = copy{ch} }}')

    # 2. Gain nodes (Immersion — bq_highshelf between copy and convolvers)
    node_lines.append(f"{I}# immersion gain")
    for ch in _HESUVI_CHANNELS:
        node_lines.append(
            f'{I}{{ type = builtin  name = gain{ch}  label = bq_highshelf'
            f'  control = {{ Freq = 10  Q = 0.7071  Gain = {immersion_db:.1f} }} }}'
        )

    # 3. Convolver nodes
    hrir_path = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"
    node_lines.append(f"{I}# apply hrir — HeSuVi 14-channel WAV")
    for conv_name, ch_idx in _HESUVI_CONVOLVERS:
        node_lines.append(
            f'{I}{{ type = builtin  label = convolver  name = {conv_name}'
            f'  config = {{ filename = "{hrir_path}" channel = {ch_idx:2d} }} }}'
        )

    # 4. Mixer nodes
    node_lines.append(f"{I}# stereo output mixers")
    node_lines.append(f"{I}{{ type = builtin  label = mixer  name = mixL }}")
    node_lines.append(f"{I}{{ type = builtin  label = mixer  name = mixR }}")

    # 5. Plate reverb nodes (Distance) — only if distance_pct > 0 and swh-plugins available
    _plate_path = _resolve_ladspa_plugin("plate_1423.so") if distance_pct > 0 else None
    use_plate = _plate_path is not None
    if use_plate:
        node_lines.append(f"{I}# distance reverb (LADSPA plate — requires swh-plugins)")
        node_lines.append(
            f'{I}{{ type = ladspa  name = plate_L  plugin = {_plate_path}  label = plate'
            f'  control = {{ "Reverb time" = 2.5  "Damping" = 0.5  "Dry/wet mix" = {distance_wet:.2f} }} }}'
        )
        node_lines.append(
            f'{I}{{ type = ladspa  name = plate_R  plugin = {_plate_path}  label = plate'
            f'  control = {{ "Reverb time" = 2.5  "Damping" = 0.5  "Dry/wet mix" = {distance_wet:.2f} }} }}'
        )

    # ── Links ────────────────────────────────────────────────────────────
    link_lines: list[str] = []
    L = "                    "  # indentation constant

    # copy → gain links
    link_lines.append(f"{L}# copy → gain")
    for ch in _HESUVI_CHANNELS:
        link_lines.append(f'{L}{{ output = "copy{ch}:Out"  input = "gain{ch}:In" }}')

    # gain → convolver links
    link_lines.append(f"{L}# gain → convolvers")
    for ch, conv_list in _HESUVI_COPY_CONV_LINKS:
        for conv in conv_list:
            link_lines.append(
                f'{L}{{ output = "gain{ch}:Out"  input = "{conv}:In" }}'
            )

    # convolver → mixer links
    link_lines.append(f"{L}# convolvers → mixers")
    for conv_name, mixer, idx in _HESUVI_CONV_MIX_LINKS:
        link_lines.append(
            f'{L}{{ output = "{conv_name}:Out"  input = "{mixer}:In {idx}" }}'
        )

    if use_plate:
        # mixer → plate reverb links
        link_lines.append(f"{L}# mixers → plate reverb")
        link_lines.append(f'{L}{{ output = "mixL:Out"  input = "plate_L:Input" }}')
        link_lines.append(f'{L}{{ output = "mixR:Out"  input = "plate_R:Input" }}')

    nodes_text = "\n".join(node_lines)
    links_text = "\n".join(link_lines)
    outputs_line = (
        '        outputs = [ "plate_L:Left output" "plate_R:Right output" ]'
        if use_plate else
        '        outputs = [ "mixL:Out" "mixR:Out" ]'
    )

    text = f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# HeSuVi 7.1 Virtual Surround  |  Immersion: {immersion_pct}%  |  Distance: {distance_pct}%
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "Virtual Surround Sink"
      media.name       = "Virtual Surround Sink"
      filter.graph = {{
        nodes = [
{nodes_text}
        ]
        links = [
{links_text}
        ]
        inputs  = [ "copyFL:In" "copyFR:In" "copyFC:In" "copyLFE:In" "copyRL:In" "copyRR:In" "copySL:In" "copySR:In" ]
{outputs_line}
      }}
      capture.props = {{
        node.name      = "effect_input.virtual-surround-7.1-hesuvi"
        media.class    = Audio/Sink/Internal
        audio.channels = 8
        audio.position = [ FL FR FC LFE RL RR SL SR ]
      }}
      playback.props = {{
        node.name          = "effect_output.virtual-surround-7.1-hesuvi"
        node.target        = "{_get_physical_out_game()}"
        target.object      = "{_get_physical_out_game()}"
        node.dont-fallback = true
        node.linger        = true
        audio.channels     = 2
        audio.position     = [ FL FR ]
      }}
    }}
  }}
]
"""

    _write_conf(output_path, text)
    return text
