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
import re
from pathlib import Path

from arctis_sound_manager.eq_types import EqBand, PW_LABEL

_log = logging.getLogger(__name__)


def _ladspa_plugin_ref(name_pattern: str) -> str | None:
    """Return the reference to write into a filter-chain ``plugin =`` directive
    for the first LADSPA .so matching *name_pattern*, or ``None`` if no plugin
    was found (adapted from PR #104's ``_resolve_ladspa_plugin``).

    Single source of truth for the search itself (LADSPA_PATH + ~/.ladspa +
    system dirs) lives in ``system_deps_checker._find_ladspa_plugin``, which
    already returns an absolute path — reused here.

    NOTE: under Distrobox/Flatpak the filter-chain service runs on the HOST
    while this scan sees the CONTAINER filesystem. A plugin found here may be
    absent on the host, making filter-chain SEGV when it tries to dlopen() it
    (issue #88). Writing the CONTAINER's absolute path into the config would
    make that worse (the host has no reason to have that exact path), so:

    - native (no container) → absolute path is safe, use it. This fixes
      LADSPA_PATH lookups failing inside a systemd user unit that doesn't
      inherit the shell's environment (e.g. Fedora's /usr/lib64/ladspa/).
    - container + path under ``~/.ladspa`` → HOME is bind-mounted into the
      container, so the host sees the same file at the same path — absolute
      path stays safe.
    - container + system-wide path (e.g. /usr/lib64/ladspa/…) → the host may
      not have that plugin at all (Bazzite/Fedora Atomic ships no swh-plugins,
      so plate_1423/sc4m/gate fail to dlopen on the HOST and take the whole
      filter-chain module — HeSuVi included — down with them, issue #100).
      A bare plugin name only worked when the host happened to have the plugin;
      it silently killed Spatial Audio when it didn't. Instead we STAGE the
      plugin into ``~/.ladspa`` (bind-mounted, same x86_64/glibc ABI) and hand
      the host an absolute path it can always load. Falls back to the bare name
      only if the copy fails, so we are never worse than before.
    """
    from arctis_sound_manager.system_deps_checker import _find_ladspa_plugin
    resolved = _find_ladspa_plugin(name_pattern)
    if resolved is None:
        return None

    try:
        from arctis_sound_manager.bug_reporter import _detect_container_env
        _container = _detect_container_env()
    except Exception:
        _container = 'native'

    if _container == 'native':
        return resolved

    resolved_path = Path(resolved)
    try:
        resolved_path.relative_to(Path.home())
        return resolved  # under ~/.ladspa (or elsewhere in HOME) — shared with the host
    except ValueError:
        pass

    # System-wide container path: not guaranteed to exist on the host. Stage a
    # copy into ~/.ladspa (shared with the host) and return that absolute path
    # so the host's filter-chain loads it directly instead of searching its own
    # dirs and failing (issue #100).
    import shutil
    try:
        dest_dir = Path.home() / ".ladspa"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / resolved_path.name
        if not dest.exists() or dest.stat().st_size != resolved_path.stat().st_size:
            shutil.copy(resolved_path, dest)
        _log.info(
            "Staged container LADSPA plugin %s into %s so the host filter-chain "
            "can load it (issue #100)", resolved_path.name, dest,
        )
        return str(dest)
    except OSError as exc:
        _log.warning(
            "Could not stage LADSPA plugin %s into ~/.ladspa (%s); falling back "
            "to the bare name — the host must provide the plugin itself.",
            resolved_path.name, exc,
        )
        return resolved_path.stem


def _ladspa_plugin_available(name_pattern: str) -> bool:
    """Return True if a LADSPA .so matching name_pattern is found in standard
    dirs. Back-compat boolean wrapper around :func:`_ladspa_plugin_ref`."""
    return _ladspa_plugin_ref(name_pattern) is not None


def _conf_has_bare_ladspa(content: str) -> bool:
    """True if a generated filter-chain config references a LADSPA plugin by
    bare name (no path separator) rather than an absolute path.

    A bare name (e.g. ``plugin = plate_1423``) is what the pre-#100 container
    fallback wrote; it fails to dlopen on a host that lacks the plugin and takes
    the whole module — HeSuVi — down. Detecting it lets the config repair pass
    regenerate the conf so it picks up the staged ~/.ladspa absolute path.
    """
    for line in content.splitlines():
        if "type = ladspa" in line and "plugin =" in line:
            after = line.split("plugin =", 1)[1].strip()
            token = after.split()[0] if after else ""
            if token and not token.startswith("/"):
                return True
    return False


# ── Generated-config versioning ─────────────────────────────────────────────
#
# Every filter-chain .conf this module writes carries an
# "# ASM-CONF-VERSION: <n>" line in its header, right under the standard
# "Auto-generated by Arctis Sound Manager" comment. check_and_fix_stale_configs()
# compares that marker against _CONF_VERSION and regenerates any file whose
# marker is missing or lower, so an upgrade that changes what a conf *contains*
# actually reaches users who already have an older conf sitting on disk.
#
# Bump _CONF_VERSION whenever a change alters the *shape* of a generated
# conf — a node added/removed/reordered/retyped, a link changed, a new
# processing stage inserted, playback/capture props gaining or losing a
# field, etc. Do NOT bump it for changes that only alter the *values* written
# into an already-existing node (Freq/Q/Gain literals, a target string, a
# comment) — those are picked up the next time the conf is regenerated for
# any other reason and don't need every existing user's conf force-rewritten.
#
# Scope — read before extending this:
# The marker is written into EVERY generated conf, but an outdated marker is
# only a *regeneration trigger* for the HeSuVi surround conf. That one is
# rebuilt losslessly from sonar_spatial_audio.json, so regenerating it costs
# the user nothing. The Sonar EQ and micro confs are different: their repair
# path in check_and_fix_stale_configs()/ensure_sonar_eq_configs() can only
# write a *bypass* (flat) conf, because nothing in this module can read back
# the user's bands, macros, boost and smart-volume settings — only
# gui/sonar_page.py knows how to rebuild those, and the daemon has no access
# to it. Triggering regeneration on a version bump there would silently
# flatten every user's EQ on the first launch after an upgrade. Extend the
# trigger to those confs only once their repair path can restore the real
# settings instead of a bypass.
#
# History:
#   1 — baseline. Introduced after v1.2.5 added a LADSPA limiter node to the
#       HeSuVi surround chain (generate_hesuvi_conf) but users upgrading from
#       1.2.4 kept their pre-limiter conf forever: none of the existing
#       staleness checks in check_and_fix_stale_configs() ever matched it, so
#       the fix was silently inert for every existing install. This mechanism
#       exists so that class of bug can't recur.
_CONF_VERSION = 1

_CONF_VERSION_RE = re.compile(r"^\s*#\s*ASM-CONF-VERSION:\s*(\d+)\s*$", re.MULTILINE)


def _conf_version_header() -> str:
    """The version marker line written into every generated conf's header."""
    return f"# ASM-CONF-VERSION: {_CONF_VERSION}"


def _conf_is_outdated(content: str) -> bool:
    """True if a generated conf's *content* predates the current _CONF_VERSION.

    True when the ``# ASM-CONF-VERSION:`` marker is absent entirely (every
    conf written before this mechanism existed — pre-1.2.6 — including the
    pre-1.2.5 HeSuVi confs missing the limiter node) or when it names a value
    lower than _CONF_VERSION (any later shape change). False only when the
    marker is present and already current, so a stable, up-to-date conf is
    never rewritten (and never triggers a needless filter-chain restart) just
    because check_and_fix_stale_configs() ran again.
    """
    match = _CONF_VERSION_RE.search(content)
    if not match:
        return True
    try:
        return int(match.group(1)) < _CONF_VERSION
    except ValueError:
        return True


# ── Constants ─────────────────────────────────────────────────────────────────

_SURROUND = "effect_input.virtual-surround-7.1-hesuvi"

# Bundled HRIR profile used when the user has not picked one, so the HeSuVi
# convolver always has a WAV to load and Spatial Audio is never silent (#100).
_DEFAULT_HRIR_ID = "atmos"

# Where the HeSuVi convolver reads its impulse response from. generate_hesuvi_conf
# writes this exact path into every convolver node.
_HRIR_DEST = Path.home() / ".local" / "share" / "pipewire" / "hrir_hesuvi" / "hrir.wav"


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
                    if (s.name.startswith("alsa_output") or s.name.startswith("bluez_output"))
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


def _sc4m_node(name: str, preset: dict, level: float, plugin_ref: str = "sc4m_1916") -> str:
    """Generate a LADSPA SC4M compressor node.

    *level* (0-100) scales ratio from 1.0 to the preset's max and adjusts
    makeup gain proportionally.

    *plugin_ref* is the value written to the ``plugin =`` directive — either
    the bare plugin name (default, PipeWire resolves via LADSPA_PATH) or an
    absolute path resolved by :func:`_ladspa_plugin_ref`.
    """
    t = max(0.0, min(100.0, level)) / 100.0
    ratio  = 1.0 + (preset["ratio"] - 1.0) * t
    makeup = preset["makeup"] * t
    return (
        f'                    {{ type = ladspa  name = {name}  plugin = {plugin_ref}  label = sc4m\n'
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


def _current_env_versions() -> dict[str, str]:
    """Versions whose change could resolve a filter-chain crash (issue #88).

    Recorded in the safe-mode marker so auto-recovery is retried only when the
    environment actually changed (an ASM or PipeWire update), not on every boot
    of a system that is still genuinely crashing."""
    asm = "unknown"
    try:
        from importlib.metadata import version
        asm = version("arctis-sound-manager")
    except Exception:
        pass
    pipewire = "unknown"
    try:
        import subprocess as _sp
        r = _sp.run(["pw-cli", "--version"], capture_output=True, text=True, timeout=2)
        for line in r.stdout.splitlines():
            if "libpipewire" in line:
                pipewire = line.split()[-1]
                break
    except Exception:
        pass
    return {"asm_version": asm, "pipewire_version": pipewire}


def is_filter_chain_safe_mode_armed() -> bool:
    """True when the on-disk safe-mode marker exists (issue #88).

    Cheap file stat the GUI can poll to surface a 'safe mode is on / re-enable
    EQ' banner, even though it runs in a different process from the daemon."""
    return _SAFE_MODE_MARKER.exists()


def _restore_disabled_configs() -> None:
    """Move the ASM configs safe mode set aside back into the active dir.

    Overwrites any stale copy already present and removes the (now-empty)
    disabled dir. Downstream regeneration refreshes their contents."""
    try:
        if not _CONF_DIR_DISABLED.exists():
            return
        _CONF_DIR.mkdir(parents=True, exist_ok=True)
        for name in _ASM_CONF_NAMES:
            src = _CONF_DIR_DISABLED / name
            if src.exists():
                try:
                    src.replace(_CONF_DIR / name)  # overwrites any stale copy
                except OSError as exc:
                    _log.warning("safe mode: could not restore %s: %s", name, exc)
        try:
            _CONF_DIR_DISABLED.rmdir()  # only succeeds once empty
        except OSError:
            pass
    except Exception as exc:
        _log.debug("safe mode: restore step failed: %s", exc)


def clear_safe_mode_and_restore() -> None:
    """User-initiated safe-mode reset: restore the disabled EQ configs, clear
    the latch, and restart the filter-chain so EQ audio returns.

    Unlike maybe_recover_from_safe_mode() this is unconditional (no version
    gate) — it's what the GUI 'Re-enable EQ' button triggers. The restart goes
    through _restart_filter_chain(), so if the graph genuinely still crashes it
    re-arms safe mode rather than crash-looping."""
    _restore_disabled_configs()
    reset_filter_chain_safe_mode()
    _restart_filter_chain()


def maybe_recover_from_safe_mode() -> bool:
    """Auto-clear safe mode when the environment changed since it was armed.

    Safe mode (issue #88) latches on a filter-chain SEGV crash-loop and then
    suppresses EQ config regeneration until cleared. Historically the only way
    out was deleting the marker by hand, so a user stayed in flat/no-EQ audio
    forever even after the crash cause was fixed by an ASM or PipeWire update.

    This clears the latch once when the recorded ASM or PipeWire version differs
    from the current one, restores the configs safe mode moved aside, and lets
    the normal init path regenerate + re-test them. If the filter-chain still
    crashes, ensure_filter_chain_healthy()/the watchdog simply re-arm — now
    stamped with the new versions, so a still-broken system won't thrash on
    every boot.

    Returns True if safe mode was cleared for a recovery attempt."""
    if not _filter_chain_safe_mode:
        return False

    try:
        import json as _json
        stored = _json.loads(_SAFE_MODE_MARKER.read_text())
    except Exception:
        stored = {}
    current = _current_env_versions()

    changed = any(
        stored.get(k) != current.get(k)
        for k in ("asm_version", "pipewire_version")
    )
    if not changed:
        _log.info(
            "safe mode armed, environment unchanged (asm=%s pipewire=%s) — "
            "staying in safe mode, not re-testing",
            current.get("asm_version"), current.get("pipewire_version"),
        )
        return False

    _log.warning(
        "safe mode: environment changed since arming (asm %s->%s, pipewire "
        "%s->%s) — auto-clearing to re-test the filter-chain; it will re-arm "
        "automatically if it still crashes",
        stored.get("asm_version"), current.get("asm_version"),
        stored.get("pipewire_version"), current.get("pipewire_version"),
    )

    # Restore the configs safe mode moved aside so the re-test runs the full
    # graph; regeneration downstream will refresh their contents.
    _restore_disabled_configs()
    reset_filter_chain_safe_mode()
    return True


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
            # Recorded so maybe_recover_from_safe_mode() only re-tests once the
            # ASM/PipeWire version changes, not on every boot (issue #88).
            **_current_env_versions(),
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

    # Park the graph before the SIGTERM: PipeWire 1.6.7 segfaults when the
    # filter-chain is killed mid-cycle (issue #100). Most settings are applied
    # live since v1.1.95 and never come through here, but changing the HRIR
    # profile must reload the convolver, so this path still exists.
    try:
        from arctis_sound_manager.pw_utils import quiesce_filter_chain
        quiesce_filter_chain()
    except Exception as exc:  # never block the restart on this
        _log.debug("quiesce_filter_chain failed (continuing): %s", exc)

    sc.restart("filter-chain", timeout=15)

    if not _poll_filter_chain_stable():
        _log.warning(
            "filter-chain did not stay active after restart (crash-loop "
            "detected) — entering safe mode")
        _enter_filter_chain_safe_mode()


def ensure_filter_chain_healthy() -> bool:
    """Detect a crash-looping filter-chain at boot or device-attach time and arm
    the safe-mode fallback if needed (Correctif 1, issue #88; start-then-poll
    behaviour adapted from PR #104 for issue #88's Fedora reports).

    Checks (in order):
    0. If none of ``_ASM_CONF_NAMES`` exist on disk in ``_CONF_DIR``, ASM
       cannot be the cause of whatever state filter-chain is in — return True
       immediately without touching the service at all.
    1. ``sc.is_active("filter-chain")`` — if False, the service may simply not
       have started yet (a boot-ordering race, or it was disabled). Instead of
       treating that alone as a crash-loop, call ``sc.start("filter-chain")``
       and poll for stability via ``_poll_filter_chain_stable()``: if it comes
       up and stays up, return True without ever entering safe mode. If it
       stays down (or crashes again), that *is* a crash-loop — enter safe mode
       exactly as before. This avoids disabling the EQ on a merely-not-yet-
       started service while still catching a genuine crash-loop.
    2. ``NRestarts`` (systemd only) — if >= 3 the service has restarted at
       least 3 times, which strongly indicates a crash-loop.

    If unhealthy → calls ``_enter_filter_chain_safe_mode()`` which moves ASM
    configs aside and restarts filter-chain without them so audio is flat but
    stable rather than permanently cut.

    Returns True when the filter-chain appears healthy (or there is nothing
    ASM could have broken).  Returns False when safe mode was entered or was
    already active.

    Callers must not call this in a tight loop — each call may block up to
    ``3 × 1 s`` for the poll and ``5 s`` for the NRestarts subprocess."""
    from arctis_sound_manager import service_control as sc

    if _filter_chain_safe_mode:
        return False  # already in safe mode — nothing more to do

    # If ASM never wrote any config, it cannot have caused a crash loop —
    # skip touching the service entirely.
    if not any((_CONF_DIR / name).exists() for name in _ASM_CONF_NAMES):
        return True

    # Primary check: is the service running right now?
    if not sc.is_active("filter-chain"):
        _log.warning(
            "ensure_filter_chain_healthy: filter-chain is not active at "
            "boot/attach — starting it and checking for stability before "
            "deciding on safe mode"
        )
        sc.start("filter-chain")
        if _poll_filter_chain_stable():
            return True
        _log.warning(
            "ensure_filter_chain_healthy: filter-chain did not stay active "
            "after start (crash-loop detected) — entering safe mode"
        )
        _enter_filter_chain_safe_mode()
        return False

    # Secondary check (systemd only): NRestarts — a high restart count means
    # the service has been repeatedly crashing even if it appears momentarily
    # active between systemd's rapid auto-restarts. Goes through service_control
    # so no raw systemctl spawn escapes the posix_spawn path (issue #123).
    n_restarts = sc.nrestarts("filter-chain")
    if n_restarts is not None and n_restarts >= 3:
        _log.warning(
            "ensure_filter_chain_healthy: filter-chain NRestarts=%d "
            "(crash-loop detected) — entering safe mode",
            n_restarts,
        )
        _enter_filter_chain_safe_mode()
        return False

    return True


def ensure_hrir_materialized(hrir_id: str | None = None) -> bool:
    """Guarantee the HeSuVi HRIR WAV exists on disk so the convolver can load.

    generate_hesuvi_conf() always points every convolver node at
    :data:`_HRIR_DEST`. If that file is missing the convolver fails to load,
    the ``effect_input.virtual-surround-7.1-hesuvi`` node never appears in the
    graph, and enabling Spatial Audio routes game/media at a dead target =
    dead silence (issue #100). This copies the configured HRIR — or the
    bundled :data:`_DEFAULT_HRIR_ID` fallback — into place when it is absent.

    Idempotent: a no-op when a non-empty WAV already exists (so it is cheap to
    call on every device init / watchdog pass). Returns True if it wrote the
    file. Unlike :func:`apply_hrir_choice` it never overwrites an existing
    WAV, so it does not fight a user's explicit profile choice.
    """
    try:
        if _HRIR_DEST.exists() and _HRIR_DEST.stat().st_size > 0:
            return False
    except OSError:
        pass

    from arctis_sound_manager.hrir_catalog import package_hrir_path
    if hrir_id is None:
        try:
            from arctis_sound_manager.settings import GeneralSettings
            hrir_id = GeneralSettings.read_from_file().hrir_id
        except Exception:
            hrir_id = None

    src = package_hrir_path(hrir_id) if hrir_id else None
    if src is None:
        src = package_hrir_path(_DEFAULT_HRIR_ID)
    if src is None:
        _log.warning(
            "No bundled HRIR WAV available to materialise (wanted %s)",
            hrir_id or _DEFAULT_HRIR_ID,
        )
        return False

    import shutil
    try:
        _HRIR_DEST.parent.mkdir(parents=True, exist_ok=True)
        _HRIR_DEST.unlink(missing_ok=True)
        shutil.copy(src, _HRIR_DEST)
        _log.info("Materialised HRIR %s → %s", src.stem, _HRIR_DEST)
        return True
    except OSError as exc:
        _log.warning("Failed to materialise HRIR WAV: %s", exc)
        return False


def apply_hrir_choice(hrir_id: str | None) -> None:
    """Copy the chosen HRIR WAV to ~/.local/share/pipewire/hrir_hesuvi/hrir.wav
    and restart filter-chain so PipeWire picks up the new file.

    A restart is unavoidable here (Phase 4, issue #100/#88): the convolver
    nodes only read the HRIR WAV once, at load time. Everything else in
    Phase 3 exists specifically so this stays the ONLY remaining restart in
    the Spatial Audio feature set. The restart recreates the game/media EQ
    nodes with node.autoconnect=false and nothing linked into them yet, so
    ensure_spatial_eq_links() re-establishes the EQ→target link once the
    service is back up (idempotent, no-op if safe mode was entered instead).

    A falsy *hrir_id* falls back to the bundled default rather than leaving
    the WAV absent (which would silence Spatial Audio, issue #100)."""
    import shutil
    from arctis_sound_manager.hrir_catalog import package_hrir_path
    src = package_hrir_path(hrir_id) if hrir_id else package_hrir_path(_DEFAULT_HRIR_ID)
    if src is None:
        _log.warning("HRIR WAV not found for id: %s", hrir_id or _DEFAULT_HRIR_ID)
    else:
        _HRIR_DEST.parent.mkdir(parents=True, exist_ok=True)
        _HRIR_DEST.unlink(missing_ok=True)  # remove read-only copies (e.g. from Nix store)
        shutil.copy(src, _HRIR_DEST)
        _log.info("HRIR changed → %s", src.name)
    _restart_filter_chain()
    ensure_spatial_eq_links(("game", "media"))


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
    Media channel: 8ch 7.1, same node shape as game, targets HeSuVi virtual surround.
    Output channel: 8ch 7.1, single filter nodes, targets external sink (HDMI, etc.).

    Game/media channel count and static target no longer depend on
    *spatial_audio*/*media_spatial_audio* (Phase 3, issue #100/#88): both
    channels are now ALWAYS 8ch and their playback.props always carries the
    HeSuVi node name as a frozen hint, with ``node.autoconnect=false`` so
    WirePlumber never actually uses it to link. This keeps the generated conf
    byte-identical across a Spatial Audio toggle — which is what lets
    ``diff_filter_conf``/the "unchanged conf" guard in ``_ApplyWorker`` skip
    the filter-chain restart entirely for that toggle. The *actual* routing
    decision (HeSuVi vs. physical output) is made live by
    :func:`ensure_spatial_eq_links`, which moves ASM's own
    ``effect_output.sonar-<channel>-eq`` → {HeSuVi | physical} link — exactly
    the same "ASM owns this link" pattern ``pw_utils.ensure_loopback_link``
    already uses for the loopback→EQ links (issue #100). The two parameters
    are kept (unused for game/media routing) purely for source compatibility
    with existing call sites.
    """
    if channel not in ("game", "chat", "media", "output"):
        raise ValueError(f"channel must be 'game', 'chat', 'media' or 'output', got {channel!r}")

    # Only chat still targets the physical Arctis output directly from this
    # conf and therefore needs a connected device to resolve a target. Game
    # and media always target HeSuVi (frozen hint, see docstring) regardless
    # of device-attach state — HeSuVi's OWN conf is what needs the device.
    needs_physical = channel == "chat"
    if needs_physical and not _device_attached():
        _log.info(
            "%s EQ config: device not attached, writing with empty target — "
            "PipeWire will bind on device arrival.",
            channel,
        )

    owns_link = channel in ("game", "media")

    if channel == "chat":
        target = target_override or (_get_physical_out_chat() if _device_attached() else "")
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]
    elif channel == "output":
        target, channels, position = _resolve_external_output(target_override)
    else:
        # game / media: always 8ch, always (nominally) targets HeSuVi.
        target = target_override or _CHANNEL_TARGET.get(channel, "")
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]

    sink_name = f"effect_input.sonar-{channel}-eq"

    if output_path is None:
        output_path = _CONF_DIR / f"sonar-{channel}-eq.conf"

    boost_db = max(-12.0, min(12.0, boost_db))

    # Collect active filter nodes: preset bands (only enabled ones) + macro
    # sliders. Once the channel is not fully flat, the 3 macro nodes are
    # ALWAYS emitted — even at Gain=0.0 — instead of only when non-zero
    # (Phase 1, issue #100/#88): a bq_peaking node at Gain=0.0 is a true unity
    # passthrough, so this keeps the node count/order stable while the user
    # drags a macro slider across zero, which previously added/removed a node
    # and forced a filter-chain restart on every crossing. The fully-flat case
    # (no bands, all macros/boost at 0) still takes the cheap _bypass_conf
    # "copy" path below — the one-time transition in/out of that state is the
    # only structural change left for macros/boost.
    active_bands: list[EqBand] = [b for b in bands if b.enabled]
    macro_values = {"basses": basses_db, "voix": voix_db, "aigus": aigus_db}
    is_flat = (
        not active_bands
        and all(abs(v) < 0.01 for v in macro_values.values())
        and abs(boost_db) < 0.01
    )
    macro_bands: list[tuple[str, EqBand]] = []
    if not is_flat:
        for macro, db in macro_values.items():
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
        text = _bypass_conf(sink_name, target, channels, position, channel=channel,
                             owns_link=owns_link)
        _write_conf(output_path, text)
        return text

    if channels != 2 or channel == "output":
        text = _active_conf_8ch(channel, sink_name, target, position,
                                all_filters, active_bands, macro_bands,
                                boost_db, smart_volume, channels=channels,
                                owns_link=owns_link)
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
    owns_link: bool = False,
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

    # Boost: always present (Phase 1, issue #100/#88) — bq_highshelf at
    # Gain=0.0 is a true unity passthrough, so adjusting/toggling the boost
    # slider never changes the node count and never needs a filter-chain
    # restart on its own.
    node_lines.append(
        f"                    {{ type = builtin  name = boost  label = bq_highshelf\n"
        f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
    )
    link_lines.append(_link(last_name, "boost"))
    last_name = "boost"

    if smart_volume and smart_volume.get("enabled"):
        # Correctif 4 (issue #88): guard LADSPA node behind plugin availability
        # check. A missing sc4m_1916.so causes dlopen() SEGV in filter-chain.
        _sc4m_ref = _ladspa_plugin_ref("sc4m_1916.so")
        if not _sc4m_ref:
            _log.warning(
                "LADSPA plugin sc4m_1916 not found — skipping Smart Volume "
                "compressor node, feature degraded; install swh-plugins on the host"
            )
        else:
            mode = smart_volume.get("loudness", "balanced")
            level = smart_volume.get("level", 50)
            preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
            node_lines.append(_sc4m_node("compressor", preset, level, _sc4m_ref))
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
    # Phase 3 (issue #100/#88): game/media own their EQ→target link exactly
    # like the loopbacks do (issue #100) — node.autoconnect=false so
    # WirePlumber never links or moves it, and state.restore-target=false so
    # a stale restored target can't fight ensure_spatial_eq_links(). The
    # target line above is kept as a documentary/pre-link hint only (mirrors
    # loopback_manager.py's own comment on the same pattern).
    _autoconnect_line = (
        '        node.autoconnect     = false\n'
        '        state.restore-target = false\n'
    ) if owns_link else ''

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
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
{_target_line}{_autoconnect_line}        node.dont-fallback  = true
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

    # Boost: always present (Phase 1, issue #100/#88) — see _active_conf_8ch.
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

    # Correctif 4 (issue #88): track whether LADSPA comp nodes were actually
    # added — affects port name ("Output" vs "Out") used in outputs_text below.
    _smart_vol_ladspa = False
    if smart_volume and smart_volume.get("enabled"):
        # Guard: a missing sc4m_1916.so causes dlopen() SEGV in filter-chain.
        _sc4m_ref = _ladspa_plugin_ref("sc4m_1916.so")
        if not _sc4m_ref:
            _log.warning(
                "LADSPA plugin sc4m_1916 not found — skipping Smart Volume "
                "compressor nodes, feature degraded; install swh-plugins on the host"
            )
        else:
            mode = smart_volume.get("loudness", "balanced")
            level = smart_volume.get("level", 50)
            preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
            node_lines.append(_sc4m_node("comp_L", preset, level, _sc4m_ref))
            node_lines.append(_sc4m_node("comp_R", preset, level, _sc4m_ref))
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

    # The Output channel is the one users route applications *to* from any
    # mixer, so its sink must be visible to PulseAudio clients; every other
    # channel is fed by ASM's own loopbacks and stays Internal. This matched
    # _active_conf_8ch and _bypass_conf, but was hardcoded to Internal here —
    # so a stereo Output channel with an active EQ vanished from every output
    # picker, and a saved routing pin to it could no longer be reapplied
    # ("Override target 'effect_input.sonar-output-eq' not found").
    media_class = "Audio/Sink" if channel == "output" else "Audio/Sink/Internal"

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
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
        media.class       = {media_class}
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

    Issue #127: the capture side runs with ``node.autoconnect = false`` and
    ``state.restore-target = false`` — the same "ASM owns this link" pattern
    already applied to the loopback/EQ output links (issue #100), but on the
    *input* side this time. Without it, every filter-chain restart triggered
    by a micro EQ edit recreates ``effect_input.sonar-micro-eq`` and
    WirePlumber does not reliably honor ``target.object``: it can link the
    capture to whatever it considers the current default/restored source
    (another connected mic) instead of the Arctis. ``target.object`` is kept
    as a documentary hint only; :func:`ensure_micro_capture_link` is what
    actually (re)establishes and enforces the link.
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

    # Phase 1 (issue #100/#88): once the mic channel is not fully flat, the 3
    # macro nodes are ALWAYS emitted (Gain=0.0 is a true bq_peaking unity
    # passthrough) instead of only when non-zero — see generate_sonar_eq_conf
    # for the full rationale. This also removes the need for the old 0 dB
    # "pass" placeholder node: the macros already guarantee all_filters is
    # non-empty whenever has_processing alone makes the channel non-flat.
    is_flat = (
        not active_bands
        and all(abs(v) < 0.01 for v in macro_values.values())
        and abs(boost_db) < 0.01
        and not has_processing
    )
    macro_bands: list[tuple[str, EqBand]] = []
    if not is_flat:
        for macro, db in macro_values.items():
            p = _MACRO_PARAMS[macro]
            macro_bands.append((macro, EqBand(
                freq=p["freq"], gain=db, q=p["q"], type="peakingEQ", enabled=True,
            )))

    all_filters = (
        [(f"bq{i}", b) for i, b in enumerate(active_bands)]
        + [(f"macro_{name}", b) for name, b in macro_bands]
    )

    if is_flat:
        text = _bypass_micro_conf()
        _write_conf(output_path, text)
        return text

    node_lines: list[str] = []
    link_lines: list[str] = []

    # all_filters is guaranteed non-empty here: is_flat is False, and it is
    # only False when active_bands is non-empty or the macros were forced in.
    names = [n for n, _ in all_filters]

    for (name, band), nm in zip(all_filters, names):
        label = PW_LABEL.get(band.type, "bq_peaking")
        node_lines.append(_node_block(nm, label, band.freq, band.q, band.gain))

    for i in range(len(all_filters) - 1):
        link_lines.append(_link(names[i], names[i + 1]))

    # Boost: always present (Phase 1) — see generate_sonar_eq_conf.
    node_lines.append(
        f"                    {{ type = builtin  name = boost  label = bq_highshelf\n"
        f"                      control = {{ Freq = 10.0  Q = 0.7071  Gain = {boost_db} }} }}"
    )
    link_lines.append(_link(names[-1], "boost"))
    last_node = "boost"

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
        _gate_ref = _ladspa_plugin_ref("gate_1410.so")
        if not _gate_ref:
            _log.warning(
                "LADSPA plugin gate_1410 not found — skipping noise gate node, "
                "feature degraded; install swh-plugins on the host"
            )
        else:
            threshold = max(-60.0, min(-10.0, ng.get("value", -40.0)))
            node_lines.append(
                f"                    {{ type = ladspa  name = ngate  plugin = {_gate_ref}  label = gate\n"
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
        _rnnoise_ref = _ladspa_plugin_ref("librnnoise_ladspa.so")
        if not _rnnoise_ref:
            _log.warning(
                "LADSPA plugin librnnoise_ladspa not found — skipping noise "
                "cancellation node, feature degraded; install "
                "noise-suppression-for-voice on the host"
            )
        else:
            vad_threshold = max(0.0, min(100.0, nc.get("value", 0.5) * 100))
            node_lines.append(
                f"                    {{ type = ladspa  name = rnnoise\n"
                f"                      plugin = {_rnnoise_ref}  label = noise_suppressor_mono\n"
                f"                      control = {{ \"VAD Threshold (%)\" = {vad_threshold:.1f} }} }}"
            )
            link_lines.append(_smart_link("rnnoise", True))
            last_node = "rnnoise"
            last_is_ladspa = True

    # ── Compressor / volume stabilizer (LADSPA sc4m_1916) ──
    # Correctif 4 (issue #88): guard LADSPA node. A missing sc4m_1916.so causes
    # dlopen() SEGV in filter-chain; omit node gracefully if plugin not found.
    if comp.get("enabled", False):
        _comp_ref = _ladspa_plugin_ref("sc4m_1916.so")
        if not _comp_ref:
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
                f"                    {{ type = ladspa  name = comp  plugin = {_comp_ref}  label = sc4m\n"
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
{_conf_version_header()}
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
        node.autoconnect     = false
        state.restore-target = false
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

def _bypass_conf(
    sink_name: str, target: str, channels: int, position: str, channel: str = "",
    owns_link: bool = False,
) -> str:
    """Generate a bypass config. Multi-channel uses auto-dup (no inputs/outputs), 2ch uses L/R."""
    _target_line = (
        f'        node.target         = "{target}"\n'
        f'        target.object       = "{target}"\n'
    ) if target else ''
    # Phase 3 (issue #100/#88): same ASM-owned link pattern as _active_conf_8ch
    # — see its comment for the rationale.
    _autoconnect_line = (
        '        node.autoconnect     = false\n'
        '        state.restore-target = false\n'
    ) if owns_link else ''
    media_class = "Audio/Sink" if channel == "output" else "Audio/Sink/Internal"
    priority = "1" if channel == "output" else "1000"
    if channels != 2:
        return f"""\
# Auto-generated by Arctis Sound Manager — passthrough (all gains = 0)
{_conf_version_header()}
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
{_target_line}{_autoconnect_line}        node.dont-fallback  = true
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
{_conf_version_header()}
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
{_conf_version_header()}
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
        node.autoconnect     = false
        state.restore-target = false
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


# ── Live-apply diff (Phase 2, issue #100/#88) ────────────────────────────────
#
# _node_block() always emits a builtin bq_* node as exactly two lines:
#   { type = builtin  name = <name>  label = <label>
#     control = { Freq = <f>  Q = <q>  Gain = <g> } }
# This format is fully under our control, so a plain line-by-line diff of two
# generated conf texts can reliably tell "only Freq/Q/Gain literals changed"
# (safe to live-apply via pw-cli set-param) apart from any other difference —
# a node added/removed/reordered/retyped, a LADSPA node's params changed, a
# target/channel-count/link change, … — all of which require a full restart.

_BQ_HEADER_RE = re.compile(
    r'^\s*\{\s*type\s*=\s*builtin\s+name\s*=\s*(\S+)\s+label\s*=\s*(\S+)\s*$'
)
_BQ_CONTROL_RE = re.compile(
    r'^\s*control\s*=\s*\{\s*Freq\s*=\s*([-\d.eE]+)\s+Q\s*=\s*([-\d.eE]+)'
    r'\s+Gain\s*=\s*([-\d.eE]+)\s*\}\s*\}\s*$'
)


def diff_filter_conf(old_text: str, new_text: str) -> dict[str, dict[str, float]] | None:
    """Compare two generated filter-chain conf texts.

    Returns ``{internal_node_name: {"Freq": f, "Q": q, "Gain": g}}`` — only
    the fields that actually changed — for every builtin bq_* node whose
    control literals differ between *old_text* and *new_text*, when the two
    texts are otherwise byte-identical (same nodes, in the same order, same
    links/targets/channels/LADSPA params).

    Returns ``None`` when any other difference is found: a node was added,
    removed, reordered, or retyped; a LADSPA node's params changed; a target,
    channel count, or link changed; etc. — the caller must fall back to a
    full filter-chain restart in that case, since the graph shape itself
    changed and a simple ``pw-cli set-param`` cannot express that.
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    if len(old_lines) != len(new_lines):
        return None

    changed: dict[str, dict[str, float]] = {}
    pending_name: str | None = None

    for old_line, new_line in zip(old_lines, new_lines):
        header = _BQ_HEADER_RE.match(old_line)
        if old_line == new_line:
            if header:
                pending_name = header.group(1)
            continue

        # Lines differ. The only acceptable difference is a bq_* control
        # block (Freq/Q/Gain literals) belonging to the node named on the
        # immediately preceding (identical) header line. A header line itself
        # is never expected to differ (that would mean a node was renamed or
        # retyped) — if _BQ_HEADER_RE matched old_line here, new_line must be
        # a different header, i.e. a structural change.
        if header is not None:
            return None
        old_m = _BQ_CONTROL_RE.match(old_line)
        new_m = _BQ_CONTROL_RE.match(new_line)
        if not old_m or not new_m or pending_name is None:
            return None

        fields: dict[str, float] = {}
        for key, old_val, new_val in (
            ("Freq", float(old_m.group(1)), float(new_m.group(1))),
            ("Q",    float(old_m.group(2)), float(new_m.group(2))),
            ("Gain", float(old_m.group(3)), float(new_m.group(3))),
        ):
            if old_val != new_val:
                fields[key] = new_val
        if fields:
            changed[pending_name] = fields
        pending_name = None

    return changed


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
    8. HeSuVi config whose ``# ASM-CONF-VERSION`` marker is missing or older
       than ``_CONF_VERSION`` (see :func:`_conf_is_outdated`) — i.e. the file
       predates a change to the *shape* of what this module generates. This is
       what makes a fix like v1.2.5's HeSuVi output limiter actually reach
       users who already had a ``sink-virtual-surround-7.1-hesuvi.conf`` on
       disk from an older release: without it, none of checks 1-7 above ever
       matched that file and it was silently never regenerated across the
       upgrade. Deliberately limited to the HeSuVi conf — see the "Scope" note
       on ``_CONF_VERSION`` for why the EQ/micro confs are excluded.

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

    for name in ("sonar-game-eq.conf", "sonar-media-eq.conf", "sonar-chat-eq.conf"):
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

            # Phase 3 (issue #100/#88): game and media are now ALWAYS 8ch,
            # independent of the Spatial Audio toggle (the live routing
            # decision is made by ensure_spatial_eq_links(), not by channel
            # count). A 2ch game/media conf is therefore always stale.
            if name in ("sonar-game-eq.conf", "sonar-media-eq.conf") and "audio.channels = 2" in content:
                log.warning("Stale config (%s uses 2ch, should be 8ch), regenerating", name)
                needs_regen = True

            # NOTE: _conf_is_outdated() is deliberately NOT a regeneration
            # trigger here — see the "Scope" note on _CONF_VERSION. The regen
            # path below writes a *bypass* (flat) conf because nothing in this
            # module can read back the user's bands/macros/boost, so triggering
            # it on a version bump would silently flatten every user's EQ on
            # the first launch after an upgrade.
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
                _write_conf(path, _bypass_conf(sink_name, target, channels, position, channel=channel,
                                                owns_link=channel in ("game", "media")))
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
        # As with the EQ confs above, an outdated ASM-CONF-VERSION is NOT a
        # trigger here: the regen writes a bypass (flat) micro conf, which
        # would drop the user's mic processing on every version bump.
        if (
            "Audio/Source/Virtual" in content
            or "Audio/Sink" in content
            or "label = gain" in content
        ):
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

    # Ensure HeSuVi is present and targets the current physical output (Phase 3,
    # issue #100/#88: HeSuVi is now generated unconditionally, independent of
    # the Spatial Audio toggle, so it is always ready for
    # ensure_spatial_eq_links() to move the EQ→target link onto it live, with
    # no filter-chain restart, the moment the user turns Spatial Audio back
    # on. It stays idle — no incoming link, no CPU cost worth mentioning —
    # whenever nothing feeds it. Also catches configs written with the old
    # hardcoded _PHYSICAL_OUT constant before v1.0.23.
    if sonar:
        # Guarantee the HRIR WAV exists before (re)generating the HeSuVi conf,
        # otherwise the convolver references a missing file and the surround
        # node never loads = silent Spatial Audio (issue #100). Idempotent, so
        # existing users who already have the conf but never picked an HRIR
        # get the WAV materialised here too. When it actually writes the WAV the
        # convolver needs a filter-chain restart to pick it up (it only reads
        # the file at load), so flag `fixed` to trigger one.
        if ensure_hrir_materialized():
            fixed = True
        try:
            import json as _json
            _spatial_file = Path.home() / ".config" / "arctis_manager" / "sonar_spatial_audio.json"
            _spatial = _json.loads(_spatial_file.read_text()) if _spatial_file.exists() else {}
        except Exception:
            _spatial = {}
        hesuvi_path = _CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf"
        if not hesuvi_path.exists():
            if not _device_attached():
                # generate_hesuvi_conf() itself would skip the write and
                # return "" — checking here avoids repeatedly reporting
                # "fixed" on every call while no device is attached.
                log.debug("HeSuVi config missing but no device attached yet — skipping")
            else:
                log.warning("HeSuVi config missing — generating (Phase 3: always present)")
                generate_hesuvi_conf(
                    immersion_pct=_spatial.get("immersion", 50),
                    distance_pct=_spatial.get("distance", 50),
                )
                fixed = True
        else:
            hesuvi_content = hesuvi_path.read_text()
            if f'node.target        = "{_get_physical_out_game()}"' not in hesuvi_content:
                log.warning("HeSuVi config has stale node.target, regenerating")
                generate_hesuvi_conf(
                    immersion_pct=_spatial.get("immersion", 50),
                    distance_pct=_spatial.get("distance", 50),
                )
                fixed = True
            elif _conf_has_bare_ladspa(hesuvi_content):
                # A plate plugin written by bare name (pre-#100 container
                # fallback) fails to load on a distrobox host without
                # swh-plugins, so the whole HeSuVi module — and its surround
                # node — never comes up. Regenerate so it picks up the staged
                # ~/.ladspa absolute path (or drops the plate if unavailable).
                log.warning("HeSuVi config references a bare-name LADSPA plugin "
                            "(fails on a host without the plugin), regenerating (issue #100)")
                generate_hesuvi_conf(
                    immersion_pct=_spatial.get("immersion", 50),
                    distance_pct=_spatial.get("distance", 50),
                )
                fixed = True
            elif _conf_is_outdated(hesuvi_content):
                # Covers config-shape changes shipped in a later ASM version
                # that this file predates — e.g. v1.2.5 added an output
                # limiter node to this exact chain (see _CONF_VERSION's
                # history), but a user upgrading from 1.2.4 kept their old
                # limiter-less conf forever because none of the checks above
                # ever matched it. Regenerating with the saved
                # Immersion/Distance values keeps the user's settings intact.
                log.warning(
                    "HeSuVi config predates ASM-CONF-VERSION %d, regenerating",
                    _CONF_VERSION,
                )
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
    count, wrong target) — not just absent files. An outdated
    ``# ASM-CONF-VERSION`` marker is *not* a trigger here: this function writes
    bypass confs, so it would flatten a configured EQ (see the "Scope" note on
    ``_CONF_VERSION``).

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

    # Phase 3 (issue #100/#88): game and media are always 8ch, always
    # (nominally) targeting HeSuVi — independent of the Spatial Audio toggle.
    # The live routing decision is made by ensure_spatial_eq_links(), not by
    # this static conf (see generate_sonar_eq_conf's docstring).
    expected: dict[str, dict] = {
        "game": {
            "channels": _CHANNEL_CHANNELS["game"],
            "position": _CHANNEL_POSITION["game"],
            "target":   _SURROUND,
        },
        "media": {
            "channels": _CHANNEL_CHANNELS["media"],
            "position": _CHANNEL_POSITION["media"],
            "target":   _SURROUND,
        },
        "chat": {
            "channels": _CHANNEL_CHANNELS["chat"],
            "position": _CHANNEL_POSITION["chat"],
            "target":   _get_physical_out_chat(),
        },
    }

    # Output is a passthrough to the external sink at its native channel count
    # (2.0–7.1). Include it so its node is (re)created if missing — its config
    # is otherwise only written when the user opens the Output EQ tab (#111).
    _out_target, _out_channels, _out_position = _resolve_external_output()
    expected["output"] = {
        "channels": _out_channels,
        "position": _out_position,
        "target":   _out_target,
    }

    for channel in ("game", "media", "chat", "output"):
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

        # No ASM-CONF-VERSION check here either — same reason as in
        # check_and_fix_stale_configs(): the regen below writes a bypass conf.
        if needs_regen:
            _write_conf(
                conf_path,
                _bypass_conf(sink_name, exp["target"], exp["channels"], exp["position"],
                             owns_link=channel in ("game", "media")),
            )
            generated = True

    return generated


# ── Phase 3 — Spatial Audio toggle without a filter-chain restart (#100/#88) ──

def _spatial_enabled(channel: str) -> bool:
    """Read whether Spatial Audio is currently enabled for *channel*.

    *channel* is ``"game"`` or ``"media"``, matching the two independent
    Spatial Audio toggles in the GUI (``gui/sonar_page.py``'s
    ``SpatialAudioWidget``). Mirrors that module's own file-naming convention
    (``sonar_spatial_audio.json`` for game, ``sonar_spatial_audio_media.json``
    for media) so both sides of the toggle read the exact same state.

    Best-effort: any missing file or parse error is treated as enabled,
    matching the on-by-default behaviour used throughout this module and in
    ``gui/sonar_page.py``.
    """
    suffix = "" if channel == "game" else f"_{channel}"
    path = Path.home() / ".config" / "arctis_manager" / f"sonar_spatial_audio{suffix}.json"
    try:
        import json as _json
        data = _json.loads(path.read_text()) if path.exists() else {}
        return data.get("enabled", True)
    except Exception:
        return True


def ensure_spatial_eq_links(
    channels: tuple[str, ...] = ("game", "media"),
    data: list | None = None,
) -> dict[str, bool]:
    """Move each EQ's live output link to match its Spatial Audio toggle.

    ``effect_output.sonar-<channel>-eq`` (game/media only) runs with
    ``node.autoconnect=false`` — exactly the same "ASM owns this link"
    pattern ``pw_utils.ensure_loopback_link`` already uses for the
    loopback→EQ links (issue #100). WirePlumber therefore never links or
    moves this node; toggling Spatial Audio is nothing more than moving that
    one link between the HeSuVi virtual-surround sink (ON) and the physical
    output (OFF) — a plain ``pw-link`` operation, with **no** filter-chain
    restart. This is what sidesteps the SIGTERM-during-DSP race that SEGVs
    filter-chain on PipeWire 1.6.7 (#100/#88): the service itself is never
    touched by a Spatial Audio toggle once its EQ node is up.

    The EQ node stays 8ch and its playback.props always carries the HeSuVi
    node name as a static (but inert, since autoconnect is off) hint — see
    :func:`generate_sonar_eq_conf`'s docstring — so calling this after any
    conf regeneration, restart, or plain watchdog tick is always safe and
    idempotent: it either confirms the existing link is already correct or
    moves it, and never restarts anything itself.

    Feasibility note (Phase 3 hardware validation, issue #100/#88): the OFF
    path links an **8ch** EQ output to the **2ch** physical output. This is
    done with ``ensure_loopback_link``, which creates explicit *channel-matched
    pw-link* connections (FL→FL, FR→FR) — it does NOT route through an
    adapter that channel-mixes 8→2. That distinction is what keeps OFF-mode
    stereo bit-clean and avoids any regression versus the old 2ch-EQ OFF path:
    PipeWire's 2→8 upmix (which fills the EQ's 8 channels from the 2ch
    loopback) never alters the passthrough front channels — FL/FR always carry
    the original L/R at unity — and any synthesised centre/surround content it
    adds to FC/LFE/RL/RR/SL/SR is simply *dropped* here (those source channels
    have no matching port on the 2ch target), rather than folded back in by a
    downmix matrix. So the round-trip in OFF is loopback-2ch → EQ-FL/FR →
    physical-FL/FR, i.e. clean stereo. (An adapter 8→2 downmix of the
    psd-upmixed signal WOULD colour the sound — center/surround re-summed —
    which is exactly why we link channel-matched, not through a downmixer.)

    Parameters
    ----------
    channels:
        Which EQ channels to (re)link. Only ``"game"``/``"media"`` are
        meaningful — anything else is silently ignored.
    data:
        Optional pre-fetched ``pw-dump`` payload, so a caller that already
        fetched one this tick (e.g. the daemon's loopback watchdog) does not
        pay for a second ``pw-dump`` subprocess.

    Returns
    -------
    dict[str, bool]
        ``{channel: linked}``. ``False`` most commonly means the EQ node or
        its target is not yet up (filter-chain starting/restarting, or no
        device attached) — treat as "retry later", not an error.
    """
    from arctis_sound_manager.pw_utils import ensure_loopback_link, pw_node_exists

    results: dict[str, bool] = {}
    for channel in channels:
        if channel not in ("game", "media"):
            continue
        enabled = _spatial_enabled(channel)
        target = _SURROUND if enabled else _get_physical_out_game()
        if enabled and not pw_node_exists(_SURROUND, data):
            # HeSuVi is not in the graph. If its HRIR WAV is missing the
            # convolver can never load and the node will never appear —
            # targeting it here would be permanent silence, so fall back to
            # the physical output so the user still hears their game/media
            # (issue #100). If the WAV *is* present this is only a transient
            # (filter-chain restarting): keep targeting HeSuVi and let the
            # next watchdog tick relink, rather than flap onto physical.
            if not _HRIR_DEST.exists():
                phys = _get_physical_out_game()
                if phys:
                    _log.warning(
                        "Spatial ON but HeSuVi is not loaded and no HRIR is present; "
                        "routing %s to the physical output (pick an HRIR profile to "
                        "enable surround) — issue #100",
                        channel,
                    )
                    target = phys
        if not target:
            # No device attached yet — nothing to link to.
            results[channel] = False
            continue
        playback_name = f"effect_output.sonar-{channel}-eq"
        results[channel] = ensure_loopback_link(playback_name, target, data=data)
    return results


_HESUVI_OUTPUT_NAME = "effect_output.virtual-surround-7.1-hesuvi"
_CHAT_OUTPUT_NAME = "effect_output.sonar-chat-eq"
_OUTPUT_EQ_OUTPUT_NAME = "effect_output.sonar-output-eq"

_CONF_TARGET_RE = re.compile(r'node\.target\s*=\s*"([^"]*)"')


def _node_in_graph(data: list | None, node_name: str) -> bool:
    """True if *node_name* is present in a ``pw-dump`` payload.

    Returns True when *data* is ``None`` (no snapshot to check against): the
    caller then falls back to attempting the link, which is the behaviour that
    predates this check.
    """
    if data is None:
        return True
    for obj in data:
        if not obj.get("type", "").endswith("Node"):
            continue
        if obj.get("info", {}).get("props", {}).get("node.name") == node_name:
            return True
    return False


def _get_configured_external_output() -> str:
    """Return the external sink the Output channel's conf currently targets.

    The Output channel (HDMI / TV / speakers) resolves its target through
    :func:`_resolve_external_output`, which opens a pulsectl connection. That
    is fine when writing the conf, but this value is needed on every watchdog
    tick, so it is read back from the generated conf instead — the conf is
    rewritten whenever the user changes the external output, so it stays the
    single source of truth without paying for a PulseAudio round-trip a few
    times a minute.

    Returns an empty string when the conf is missing or carries no target
    (no external sink configured), which callers treat as "skip this hop".
    """
    try:
        content = (_CONF_DIR / "sonar-output-eq.conf").read_text()
    except OSError:
        return ""
    match = _CONF_TARGET_RE.search(content)
    return match.group(1) if match else ""


def ensure_physical_output_links(data: list | None = None) -> dict[str, bool]:
    """Ensure the LAST hop into the physical Arctis output(s) is linked.

    Issue observed twice on hardware: the headset powers off and back on, the
    kernel/ALSA/PipeWire destroy and recreate the physical output node under a
    NEW node id, and the two nodes that carry sound the rest of the way to the
    speakers — :data:`_CHAT_OUTPUT_NAME` and :data:`_HESUVI_OUTPUT_NAME` —
    stay linked to nothing. Both nodes hard-code a ``node.target``/
    ``target.object`` hint at the physical output when their filter-chain
    config is written (see :func:`generate_sonar_eq_conf`'s chat path and
    :func:`generate_hesuvi_conf`), but that hint is only ever acted on by
    WirePlumber once, at node-creation time — it does not get re-applied when
    the *destination* node is later destroyed and recreated with a new id.
    Nothing else in the watchdog was watching this last hop:
    :func:`ensure_loopback_link` (via the loopback watchdog pass) only covers
    loopback→EQ, and :func:`ensure_spatial_eq_links` only covers game/media
    EQ→{HeSuVi,physical}. This closes that gap, exactly the same "ASM owns
    this link" pattern (issue #100) applied to the final hop:

    - ``effect_output.sonar-chat-eq`` → the physical CHAT output (mono PCM
      on dual-PCM devices, :func:`_get_physical_out_chat`).
    - ``effect_output.virtual-surround-7.1-hesuvi`` → the physical GAME
      output (stereo PCM, :func:`_get_physical_out_game`). This is the
      unconditional HeSuVi→physical hop; it does NOT touch the game/media
      EQ→{HeSuVi,physical} link that :func:`ensure_spatial_eq_links` already
      owns, so the two compose without either duplicating the other's work.
    - ``effect_output.sonar-output-eq`` → the configured EXTERNAL sink
      (HDMI/TV/speakers, :func:`_get_configured_external_output`). This hop
      had no owner whatsoever until it was added here.

    Thin wrapper around :func:`~arctis_sound_manager.pw_utils.ensure_loopback_link`
    — same idempotent semantics (no-op when already correct, stray links torn
    down, channel-name matching with the AUX0/AUX1 positional fallback for
    pro-audio devices from issue #129) applied to this last hop instead of the
    loopback→EQ or EQ→HeSuVi hops it already covers.

    When a physical target is unknown (headset off — ``device_state`` empty)
    the corresponding channel is skipped entirely: not attempted, not logged.
    It self-heals on the tick after the headset reappears, once
    ``device_state`` is populated again and the physical node re-enters the
    graph.

    Parameters
    ----------
    data:
        Optional pre-fetched ``pw-dump`` payload, so a caller that already
        fetched one this tick (the daemon's loopback watchdog) does not pay
        for a second ``pw-dump`` subprocess.

    Returns
    -------
    dict[str, bool]
        ``{"chat": bool, "hesuvi": bool, "output": bool}`` — only hops whose
        target is currently known (device attached / external sink
        configured) are included at all.
    """
    from arctis_sound_manager.pw_utils import ensure_loopback_link

    results: dict[str, bool] = {}

    chat_target = _get_physical_out_chat()
    if chat_target:
        results["chat"] = ensure_loopback_link(_CHAT_OUTPUT_NAME, chat_target, data=data)

    game_target = _get_physical_out_game()
    if game_target:
        results["hesuvi"] = ensure_loopback_link(_HESUVI_OUTPUT_NAME, game_target, data=data)

    # The Output channel's last hop (EQ → external sink: HDMI, TV, speakers)
    # was owned by nobody at all. Unlike chat/game/media it is not covered by
    # ensure_spatial_eq_links either, so once quiesce_filter_chain() tore its
    # link down on a filter-chain restart — or the external sink was destroyed
    # and recreated by a display hotplug — nothing ever put it back. Any app
    # routed to the Output channel then played into a dead end: no sound, no
    # error. Same idempotent treatment as the two hops above.
    output_target = _get_configured_external_output()
    if output_target and _node_in_graph(data, output_target):
        # Only counted as a hop at all when the external sink is actually in
        # the graph. A configured-but-absent target is the normal state of a
        # TV or monitor that is switched off: reporting it as a failure would
        # have the watchdog retry with a fresh pw-dump every tick and escalate
        # on a situation that is not a fault at all.
        results["output"] = ensure_loopback_link(
            _OUTPUT_EQ_OUTPUT_NAME, output_target, data=data
        )

    return results


_MICRO_CAPTURE_NAME = "effect_input.sonar-micro-eq"


def _get_micro_input_source_setting() -> str:
    """Return the configured ``micro_input_source`` general setting.

    Defaults to ``"__auto__"`` (issue #127 behaviour) when the settings file
    doesn't have the key yet (older config, or a fresh install) or is empty.
    Lazy-imported to avoid a settings.py <-> sonar_to_pipewire.py import cycle.
    """
    from arctis_sound_manager.settings import GeneralSettings

    value = getattr(GeneralSettings.read_from_file(), 'micro_input_source', None)
    return value or "__auto__"


def ensure_micro_capture_link(data: list | None = None) -> bool:
    """Ensure the Sonar Micro EQ's capture is fed by the configured source.

    Issue #127: ``effect_input.sonar-micro-eq`` runs with
    ``node.autoconnect = false`` / ``state.restore-target = false`` (see
    :func:`generate_sonar_micro_conf`'s docstring), so WirePlumber never
    links or moves it — ASM must own this link, exactly like
    :func:`ensure_spatial_eq_links` already does for the EQ output side.
    Every micro EQ apply (config regen + filter-chain restart) recreates the
    capture node with nothing linked into it, and the watchdog calls this on
    every tick so a link stolen by a competing mic between applies is caught
    and repaired automatically.

    Issue #131: this used to unconditionally force the Arctis microphone,
    which fought any manual qpwgraph routing to a different mic. The source
    is now driven by the ``micro_input_source`` general setting:

    - ``"__auto__"`` (default, or unset/empty) — Arctis microphone, exactly
      the #127 behaviour, via :func:`_get_physical_in`.
    - ``"__manual__"`` — enforcement is skipped entirely (no link created,
      no stray link torn down), so a manual routing sticks.
    - anything else — treated as the ``node.name`` of the source to pin the
      capture to. If that source isn't in the graph yet, ``ensure_capture_link``
      returns False and the watchdog retries on the next tick.

    Thin wrapper around :func:`~arctis_sound_manager.pw_utils.ensure_capture_link`
    that resolves the configured mic input; see that function's docstring for
    why the stray-link teardown is scoped to the capture node's input side
    rather than the source's output side (the physical mic may legitimately
    feed other consumers — a recorder, OBS, …).

    Parameters
    ----------
    data:
        Optional pre-fetched ``pw-dump`` payload, so a caller that already
        fetched one this tick (e.g. the daemon's loopback watchdog) does not
        pay for a second ``pw-dump`` subprocess.

    Returns
    -------
    bool
        True when linked. False when in manual mode, no device is attached
        yet (nothing to link to — retry later), or the capture/source node
        is not yet in the graph (filter-chain starting/restarting).
    """
    from arctis_sound_manager.pw_utils import ensure_capture_link

    setting = _get_micro_input_source_setting()

    if setting == "__manual__":
        # User has taken manual control (qpwgraph, …) — don't touch the link.
        return False

    if setting == "__auto__":
        source = _get_physical_in()
    else:
        source = setting

    if not source:
        # No device attached — skip, the watchdog will retry on the next
        # tick once a headset is connected.
        return False
    return ensure_capture_link(source, _MICRO_CAPTURE_NAME, data=data)


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
    hrir_path = _HRIR_DEST  # ensure_hrir_materialized() guarantees this exists
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
    _plate_ref = _ladspa_plugin_ref("plate_1423.so") if distance_pct > 0 else None
    use_plate = _plate_ref is not None
    if use_plate:
        node_lines.append(f"{I}# distance reverb (LADSPA plate — requires swh-plugins)")
        node_lines.append(
            f'{I}{{ type = ladspa  name = plate_L  plugin = {_plate_ref}  label = plate'
            f'  control = {{ "Reverb time" = 2.5  "Damping" = 0.5  "Dry/wet mix" = {distance_wet:.2f} }} }}'
        )
        node_lines.append(
            f'{I}{{ type = ladspa  name = plate_R  plugin = {_plate_ref}  label = plate'
            f'  control = {{ "Reverb time" = 2.5  "Damping" = 0.5  "Dry/wet mix" = {distance_wet:.2f} }} }}'
        )

    # 6. Output limiter (independent of Distance) — prevents hot HRIRs
    #    (e.g. Nahimic 3) from clipping on loud passages. The Immersion slider
    #    adds up to +12 dB broadband *before* the HRTF convolution, and each
    #    stereo mixer sums four convolvers, so peaks can exceed 0 dBFS with no
    #    headroom stage. A fast-lookahead limiter tames only those peaks while
    #    leaving quieter content untouched. Requires swh-plugins (same package
    #    as the plate reverb above); if absent, the chain is emitted without it
    #    — graceful fallback, exactly like the reverb. _ladspa_plugin_ref stages
    #    the plugin into ~/.ladspa so it also loads on the host under Distrobox
    #    (issue #100).
    _limiter_ref = _ladspa_plugin_ref("fast_lookahead_limiter_1913.so")
    use_limiter = _limiter_ref is not None
    if use_limiter:
        node_lines.append(f"{I}# output limiter (LADSPA fast lookahead — requires swh-plugins)")
        node_lines.append(
            f'{I}{{ type = ladspa  name = limiter  plugin = {_limiter_ref}  label = fastLookaheadLimiter'
            f'  control = {{ "Input gain (dB)" = 0.0  "Limit (dB)" = -1.0  "Release time (s)" = 0.1 }} }}'
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

    # Final stereo pair feeding the sink: plate reverb outputs when reverb is
    # active, otherwise the raw stereo mixers.
    pre_out_l, pre_out_r = (
        ("plate_L:Left output", "plate_R:Right output")
        if use_plate else
        ("mixL:Out", "mixR:Out")
    )

    if use_limiter:
        link_lines.append(f"{L}# -> output limiter")
        link_lines.append(f'{L}{{ output = "{pre_out_l}"  input = "limiter:Input 1" }}')
        link_lines.append(f'{L}{{ output = "{pre_out_r}"  input = "limiter:Input 2" }}')
        out_l, out_r = "limiter:Output 1", "limiter:Output 2"
    else:
        out_l, out_r = pre_out_l, pre_out_r

    nodes_text = "\n".join(node_lines)
    links_text = "\n".join(link_lines)
    outputs_line = f'        outputs = [ "{out_l}" "{out_r}" ]'

    text = f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
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
