# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
sonar_to_pipewire.py — Generate PipeWire filter-chain configs for Sonar EQ channels.

One config per channel (game / chat / micro).  Each config inserts a chain of
biquad nodes between the virtual capture sink and its playback target.

Routing (targets resolved at generation time from the currently-attached device):
  game  → effect_input.virtual-surround-7.1-hesuvi        (8ch 7.1 → HeSuVi)
  media → effect_input.virtual-surround-7.1-hesuvi-media  (8ch 7.1 → HeSuVi, #169)
  chat  → physical ALSA output of the current Arctis      (2ch stereo)
  micro → virtual source backed by the physical mic       (1ch mono)

Game and Media each own a SEPARATE HeSuVi chain (issue #169) so their
Immersion/Distance are independent; both surround chains drive the same
physical GAME output. Game keeps the historical un-suffixed names.

All configs are written to filter-chain.conf.d/ and loaded by the filter-chain service.
Restarting only filter-chain (not pipewire) preserves active audio streams.

Config generators return an empty string without writing anything when no
Arctis device is currently attached (`device_state.is_device_set()` == False).
"""
from __future__ import annotations

import logging
import json
import math
import os
import re
from pathlib import Path

from arctis_sound_manager import device_state
from arctis_sound_manager.eq_types import EqBand, PW_LABEL

_log = logging.getLogger(__name__)


# ── Link-permission fallback (issue #203) ───────────────────────────────────
# The channel EQ nodes are declared Audio/Sink/Internal so they stay out of
# every output picker: they are ASM's plumbing, not somewhere a user picks.
# Only the Output channel is a real Audio/Sink, because a routing pin has to be
# able to name it.
#
# On a session that starts ASM's clients restricted — SteamOS through Distrobox
# is the reported case (issue #203, same family as #181) — PipeWire refuses the
# cross-client link from pw-loopback into an Internal node, while the identical
# link into an Audio/Sink node succeeds. Measured on a normal Arch/KDE session,
# the Internal link works, so this is a property of the session's permissions
# and not of the class: flipping every install to Audio/Sink would put three
# plumbing nodes in everyone's output picker to fix a case most people do not
# have.
#
# So the class stays Internal, and a channel is degraded to Audio/Sink only
# after PipeWire has actually refused its link on permissions, repeatedly. The
# decision is remembered here so the next conf regeneration keeps it.
_LINK_FALLBACK_FILE = "link_permission_fallback.json"


def _link_fallback_path() -> Path:
    return Path.home() / ".config" / "arctis_manager" / _LINK_FALLBACK_FILE


def link_permission_fallback_channels() -> set[str]:
    """Channels already degraded to a pickable Audio/Sink. Never raises."""
    try:
        raw = json.loads(_link_fallback_path().read_text())
    except (OSError, ValueError):
        return set()
    if not isinstance(raw, list):
        return set()
    return {c for c in raw if isinstance(c, str)}


def mark_link_permission_fallback(channel: str) -> bool:
    """Remember that *channel* needs a pickable class. True if newly added."""
    channels = link_permission_fallback_channels()
    if channel in channels:
        return False
    channels.add(channel)
    path = _link_fallback_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(channels), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError as exc:
        _log.warning("could not record the link-permission fallback: %s", exc)
        return False
    _log.warning(
        "channel '%s': PipeWire keeps refusing the loopback link into its EQ "
        "node on permissions. Regenerating it as a pickable Audio/Sink so the "
        "channel can carry audio at all; it will now show up in output pickers.",
        channel,
    )
    return True


def _media_class_for(channel: str) -> str:
    if channel == "output" or channel in link_permission_fallback_channels():
        return "Audio/Sink"
    return "Audio/Sink/Internal"


def _ladspa_plugin_ref(name_pattern: str, resolved: str | None = None) -> str | None:
    """Return the reference to write into a filter-chain ``plugin =`` directive
    for the first LADSPA .so matching *name_pattern*, or ``None`` if no plugin
    was found (adapted from PR #104's ``_resolve_ladspa_plugin``).

    *resolved* lets a caller pass an already-chosen absolute path (e.g. the
    version-aware DeepFilterNet pick) and still get the same container staging;
    when omitted the path is resolved by first glob match as before.

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
    if resolved is None:
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


def _deepfilter_plugin_ref() -> str | None:
    """``plugin =`` reference for the DeepFilterNet LADSPA plugin, or None.

    Resolution is version-aware (newest installed wins — see
    :func:`system_deps_checker._find_best_deepfilter_ladspa`) and the result is
    staged for a container host exactly like any other plugin. This does NOT
    download anything: the GUI calls ``ensure_deepfilter_plugin()`` to fetch the
    pinned build when the user opts in and none is installed; by the time the
    conf is generated the plugin is already on disk.
    """
    from arctis_sound_manager.system_deps_checker import _find_best_deepfilter_ladspa
    return _ladspa_plugin_ref("libdeep_filter_ladspa*.so", resolved=_find_best_deepfilter_ladspa())


_PLUGIN_REF_RE = re.compile(r"plugin\s*=\s*(\S+)")


def _conf_has_bare_ladspa(content: str) -> bool:
    """True if a generated filter-chain config references a LADSPA plugin by
    bare name (no path separator) rather than an absolute path.

    A bare name (e.g. ``plugin = plate_1423``) is what the pre-#100 container
    fallback wrote; it fails to dlopen on a host that lacks the plugin and takes
    the whole module — HeSuVi — down. Detecting it lets the config repair pass
    regenerate the conf so it picks up the staged ~/.ladspa absolute path.

    Every ``plugin =`` in a generated conf belongs to a ladspa node — builtin
    nodes carry a ``label`` and no plugin — so the value alone is the test.
    Requiring ``type = ladspa`` on the *same line* used to miss the nodes this
    module writes across two lines (``dfn`` and ``rnnoise``, see
    generate_sonar_micro_conf), which meant a micro chain referencing them by
    bare name was never seen as stale and never regenerated: exactly the #100
    failure this function exists to catch, still open for the one channel whose
    nodes happen to be formatted differently.
    """
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            continue  # the header comments mention plugins by name on purpose
        match = _PLUGIN_REF_RE.search(line)
        if match:
            token = match.group(1).strip()
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
# path in check_and_fix_stale_configs()/ensure_sonar_eq_configs() now prefers
# a lossless rebuild too (CHA-7) — from the last-applied state this module
# snapshots itself (_save_eq_state/_load_eq_state for game/chat/media/output,
# _save_micro_state/_load_micro_state for micro, both written the moment a
# conf is actually generated, not by the GUI) — but it can still fall back to
# a flat *bypass* conf when no such snapshot exists yet, e.g. a channel that
# was never Applied. A version bump alone is not a reason to force that risk
# on every existing conf on every upgrade, so it stays out of the trigger set
# for these confs even now that the common case rebuilds losslessly.
#
# History:
#   1 — baseline. Introduced after v1.2.5 added a LADSPA limiter node to the
#       HeSuVi surround chain (generate_hesuvi_conf) but users upgrading from
#       1.2.4 kept their pre-limiter conf forever: none of the existing
#       staleness checks in check_and_fix_stale_configs() ever matched it, so
#       the fix was silently inert for every existing install. This mechanism
#       exists so that class of bug can't recur.
#   2 — issue #169: the Media channel gained its OWN HeSuVi chain
#       (sink-virtual-surround-7.1-hesuvi-media.conf) so Game and Media carry
#       independent Immersion/Distance. Bumping forces the daemon to rebuild
#       the (still un-suffixed) Game conf once so both chains are regenerated
#       from their per-channel JSON — lossless, sourced from sonar_spatial_audio*.json.
#   3 — the EQ and micro confs now emit a fixed rack of band slots (see
#       _band_slot_rack) instead of one node per active band, so adding,
#       deleting or disabling a band is a control change rather than a new
#       graph. Node count and order changed → a shape change by the rule
#       above. Note this does not force those confs to be rewritten (see the
#       Scope note): they are regenerated by the GUI on the next EQ edit,
#       which pays the last restart the old shape will ever cost.
#   4 — issue #180: every playback.props of an output chain gained
#       node.passive, so the chain (and the headset it feeds) can suspend when
#       nothing is playing. A props field added → a shape change by the rule
#       above. This one needs more than the marker, though: the marker only
#       triggers regeneration for the HeSuVi conf, and the EQ confs cannot be
#       regenerated without flattening the user's bands. A single non-passive
#       node anywhere in the chain keeps the headset awake, so an EQ conf left
#       untouched would make the whole fix inert — exactly the failure mode
#       this mechanism exists to prevent. _ensure_passive_playback() therefore
#       repairs those in place, inserting the one missing line and touching
#       nothing else.
_CONF_VERSION = 4

_CONF_VERSION_RE = re.compile(r"^\s*#\s*ASM-CONF-VERSION:\s*(\d+)\s*$", re.MULTILINE)

# Header line every HeSuVi conf carries: "… Immersion: N%  |  Distance: N%".
# check_and_fix_stale_configs() reads it back to tell whether the saved
# Immersion/Distance in sonar_spatial_audio*.json still matches what's baked
# into the conf on disk — the drift trigger that makes the sliders live (#169).
_HESUVI_HEADER_RE = re.compile(r"Immersion:\s*(\d+)%\s*\|\s*Distance:\s*(\d+)%")


def _channel_node_description(channel: str) -> str:
    """Label shown in system audio pickers for a channel's EQ node.

    Only the Output channel's node is user-visible (the others are declared
    Audio/Sink/Internal), and it sat in the list as "Sonar Output EQ" — no
    mention of the headset, next to three siblings that all carry its name.
    Reported as confusing in #146.
    """
    if channel != "output":
        return f"Sonar {channel.capitalize()} EQ"
    device = device_state.get_device_name()
    return f"{device} Output" if device else "Arctis Output"


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


def _load_spatial_pct(channel: str) -> tuple[int, int]:
    """Return the saved ``(immersion_pct, distance_pct)`` for *channel*.

    Reads the same per-channel files the GUI writes — sonar_spatial_audio.json
    (game) / sonar_spatial_audio_media.json (media). Missing or unparseable
    files fall back to the 50/50 default used everywhere else in this module.
    """
    suffix = "" if channel == "game" else f"_{channel}"
    path = Path.home() / ".config" / "arctis_manager" / f"sonar_spatial_audio{suffix}.json"
    try:
        import json as _json
        data = _json.loads(path.read_text()) if path.exists() else {}
        return int(data.get("immersion", 50)), int(data.get("distance", 50))
    except Exception:
        return 50, 50


def _hesuvi_conf_has_spatial_drift(
    content: str, immersion_pct: int, distance_pct: int
) -> bool:
    """True if the conf's baked Immersion/Distance differ from the saved JSON.

    The header carries the two percentages verbatim; if it's missing (older
    conf shape) or the numbers no longer match the current
    sonar_spatial_audio*.json, the conf is stale and must be regenerated. This
    is the trigger that makes a slider move actually reach the running
    filter-chain (#169) instead of only landing in the JSON file.
    """
    match = _HESUVI_HEADER_RE.search(content)
    if not match:
        return True
    return (
        int(match.group(1)) != int(immersion_pct)
        or int(match.group(2)) != int(distance_pct)
    )


# ── Constants ─────────────────────────────────────────────────────────────────

_SURROUND = "effect_input.virtual-surround-7.1-hesuvi"
# Per-channel HeSuVi chains (issue #169). Game keeps the historical, un-suffixed
# node/conf names so an existing install's Game chain is byte-identical and the
# #100/#88-sensitive path is untouched. Media gets its own parallel chain so the
# two channels can carry independent Immersion/Distance profiles (e.g. reverb on
# Game, none on Media) instead of sharing one shared chain fed by the Game JSON.
_SURROUND_MEDIA = "effect_input.virtual-surround-7.1-hesuvi-media"


# Game and Media always; Aux only when the user switched it on. Everything that
# used to spell ("game", "media") asks this instead — the hand-written list is
# how the Output EQ and the Media surround stage were forgotten by the
# duplicate-config cleanup for months (#205), and this file has a dozen of them.
_SPATIAL_BASE = ("game", "media")


def _aux_enabled() -> bool:
    """Whether the optional Aux channel is switched on (#209).

    Read per call rather than cached: the user can turn it on while the daemon
    is running, and a stale answer here means either a HeSuVi convolver running
    for a channel nobody asked for, or a channel with no chain behind it.
    """
    try:
        from arctis_sound_manager.settings import GeneralSettings
        return bool(GeneralSettings.read_from_file().aux_enabled)
    except Exception:  # noqa: BLE001
        return False


def spatial_channels() -> tuple[str, ...]:
    """The channels that own an 8-channel HeSuVi chain right now."""
    return _SPATIAL_BASE + (("aux",) if _aux_enabled() else ())


def _hesuvi_suffix(channel: str) -> str:
    """Filename/node suffix for a channel's HeSuVi chain.

    Empty for ``game`` (historical, un-suffixed names — keeps the Game chain
    identical to pre-#169 installs); ``-<channel>`` otherwise.
    """
    return "" if channel == "game" else f"-{channel}"


def _hesuvi_conf_name(channel: str) -> str:
    """Conf filename in ``filter-chain.conf.d`` for *channel*'s HeSuVi chain."""
    return f"sink-virtual-surround-7.1-hesuvi{_hesuvi_suffix(channel)}.conf"


def _hesuvi_output_node(channel: str) -> str:
    """The ``playback.props`` node.name — what links OUT to the device."""
    return f"effect_output.virtual-surround-7.1-hesuvi{_hesuvi_suffix(channel)}"


def _hesuvi_input_node(channel: str) -> str:
    """The ``capture.props`` node.name — the sink EQ output links INTO."""
    return f"effect_input.virtual-surround-7.1-hesuvi{_hesuvi_suffix(channel)}"


def _hesuvi_output_node(channel: str) -> str:
    """The ``playback.props`` node.name — links OUT to the physical output."""
    return f"effect_output.virtual-surround-7.1-hesuvi{_hesuvi_suffix(channel)}"


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


CHANNEL_OUTPUTS_FILE = Path.home() / ".config" / "arctis_manager" / "channel_output_devices.json"


def _load_channel_outputs() -> dict:
    try:
        data = json.loads(CHANNEL_OUTPUTS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def channel_destination(channel: str, data: list | None = None) -> str:
    """Which device *channel* should end up on.

    The user's per-channel choice when they have made one, and the headset
    otherwise. Selecting a device has to mean "send this channel there", not
    "drag this channel's applications onto that sink" — the latter is what the
    home page used to do, and the routing-override replay dragged them straight
    back, so the choice appeared to do nothing at all.

    A saved device that is not in the graph (earbuds in their case, a dock
    unplugged) falls back to the headset rather than leaving the channel linked
    to nothing, and is picked up again by the watchdog as soon as it returns.
    """
    from arctis_sound_manager.pw_utils import pw_node_exists

    physical = (_get_physical_out_chat() if channel == "chat"
                else _get_physical_out_game())

    chosen = _load_channel_outputs().get(channel)
    if not chosen or chosen == physical:
        return physical
    if pw_node_exists(chosen, data):
        return chosen

    _log.info("channel '%s': saved output '%s' is not present — using %s",
              channel, chosen, physical or "(no device)")
    return physical


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
    "aux":    8,
    "output": 8,
}

_CHANNEL_POSITION: dict[str, str] = {
    "game":   "FL FR FC LFE RL RR SL SR",
    "chat":   "FL FR",
    "media":  "FL FR FC LFE RL RR SL SR",
    "aux":    "FL FR FC LFE RL RR SL SR",
    "output": "FL FR FC LFE RL RR SL SR",
}

# Static channel targets; chat target is device-specific → use _get_physical_out()
_CHANNEL_TARGET: dict[str, str] = {
    "game":   _SURROUND,
    "media":  _SURROUND,
    # Same path as media: through the spatial stage, then to the headset.
    "aux":    _SURROUND,
    "output": "",
}

_EXT_OUTPUT_POSITIONS: dict[int, str] = {
    2: "FL FR",
    4: "FL FR RL RR",
    6: "FL FR FC LFE RL RR",
    8: "FL FR FC LFE RL RR SL SR",
}


def _read_external_output_setting() -> str:
    """Return the user's saved ``external_output_device`` (node.nick or name).

    Empty string when unset or unreadable, which means "auto-detect".
    """
    path = Path.home() / ".config" / "arctis_manager" / "settings" / "general_settings.yaml"
    try:
        from ruamel.yaml import YAML
        raw = YAML(typ="safe").load(path) or {}
    except Exception:
        return ""
    value = raw.get("external_output_device")
    return value if isinstance(value, str) else ""


# ── CHA-6: the setting is authoritative for the Output channel's target ──────
#
# The Output channel had two readers of "where does it go": the daemon's
# every-5s watchdog (ensure_physical_output_links → _get_configured_external_
# output, cheap — reads the target baked into sonar-output-eq.conf) and the
# repair path (ensure_sonar_eq_configs → _resolve_external_output, expensive —
# resolves the general_settings.yaml `external_output_device` setting against
# the live PipeWire graph). Nothing rewrote the conf when the setting changed
# through SetSetting over D-Bus, a hand-edit, a config restore, a settings
# sync or a package upgrade, so the two could sit diverged indefinitely — the
# watchdog kept enforcing a link to whatever the conf said, not what the
# setting said, until some unrelated escalation happened to call
# ensure_sonar_eq_configs() and silently "fixed" it with no user action.
#
# Fix: the setting is the single owner. This snapshot records which raw
# setting value produced the conf currently on disk, written every time
# generate_sonar_eq_conf("output", ...) actually resolves one. Comparing the
# CURRENT setting against the snapshot is a cheap YAML read — no pulsectl —
# so _get_configured_external_output() can do it on every watchdog tick
# without paying the round-trip _resolve_external_output() itself warns
# about. Only on an actual mismatch does it pay that cost once, to reconcile
# and refresh the snapshot; every following tick is cheap again.
def _output_setting_snapshot_path() -> Path:
    # Resolved at call time, not import time (like _read_external_output_setting
    # itself), so tests that redirect Path.home() see it move too.
    return Path.home() / ".config" / "arctis_manager" / ".sonar_output_setting_snapshot"


def _read_output_setting_snapshot() -> str:
    """Raw ``external_output_device`` value the on-disk conf was last built
    for, or ``""`` if never recorded — the same "unset" sentinel
    :func:`_read_external_output_setting` itself returns, so an install that
    has never set an explicit output device never reports a spurious
    mismatch (both sides read as "").
    """
    try:
        return _output_setting_snapshot_path().read_text().strip()
    except OSError:
        return ""


def _sync_output_setting_snapshot() -> None:
    """Record the current ``external_output_device`` setting as the one the
    output conf was just (re)built for. Best-effort: a failure here only
    means the next tick pays one extra reconciliation pass, never a crash.
    """
    try:
        path = _output_setting_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_read_external_output_setting())
    except OSError as exc:
        _log.warning("Could not record external-output setting snapshot: %s", exc)


def _resolve_external_output(target_override: str | None = None) -> tuple[str, int, str]:
    """Detect the external output sink (HDMI / DisplayPort / aux) at runtime.

    Queries PipeWire via pulsectl to get the actual channel count and position
    of the target sink, so the generated filter-chain conf matches the hardware
    (2.0 stereo, 5.1 surround, 7.1 surround, …).

    Returns (sink_name, channels, position_str).
    Falls back to ("", 2, "FL FR") when no suitable sink is found.

    With no *target_override*, the user's saved ``external_output_device``
    setting is consulted before falling back to auto-detection. Without that
    step the GUI and the daemon disagreed: the GUI passes the user's choice as
    an override, but ``ensure_sonar_eq_configs()`` calls this with none, so it
    would auto-detect a *different* sink, see a mismatch against the conf on
    disk and regenerate it — silently reverting the user's pick on every
    startup and every repair pass. It also made the headset unselectable in
    practice (issue #139), since auto-detection deliberately skips it.
    """
    if not target_override:
        target_override = _read_external_output_setting()
    try:
        import pulsectl
        with pulsectl.Pulse("asm-ext-output") as p:
            sinks = p.sink_list()
            if target_override:
                for s in sinks:
                    # Match node.name OR node.nick: the setting stores a nick
                    # (that is the id the D-Bus options list hands out), while
                    # callers that already resolved a sink pass a node.name.
                    if target_override in (s.name, s.proplist.get("node.nick", "")):
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

# Real domains a band literal can take, matching what the UI itself already
# clamps to (gui/eq_curve_widget.py: _FREQ_RANGE, _GAIN_RANGE, and the Q
# spinbox clamp at :420/:617) — not invented here, read off the one place a
# user can actually move these numbers.
_BAND_FREQ_RANGE = (20.0, 20000.0)
_BAND_Q_RANGE = (0.1, 10.0)
_BAND_GAIN_RANGE = (-12.0, 12.0)


def _clamp_finite(value: float, lo: float, hi: float, default: float) -> float:
    """Clamp *value* into ``[lo, hi]``; fall back to *default* when not finite.

    CHA-10: presets are shared third-party content (the import dialog, the
    asm-presets feature), and Python's ``json`` module happily parses
    ``Infinity``/``NaN`` literals and turns ``1e400`` into ``inf``. Nothing
    downstream validated a band's numbers before they were formatted with a
    bare ``str()`` into the filter-chain conf, so PipeWire would get
    ``Freq = inf`` / ``Gain = nan`` with no diagnostic at all. This is the
    single point every band literal passes through on the way into the conf
    text (:func:`_node_block`), so it catches every producer — GUI curve
    edits, macro sliders, imported presets, hardware EQ import — regardless
    of which one let a non-finite or out-of-range value through.
    """
    if not math.isfinite(value):
        return default
    return max(lo, min(hi, value))


def _node_block(name: str, label: str, freq: float, q: float, gain: float) -> str:
    freq = _clamp_finite(freq, *_BAND_FREQ_RANGE, 1000.0)
    q = _clamp_finite(q, *_BAND_Q_RANGE, 0.7071)
    gain = _clamp_finite(gain, *_BAND_GAIN_RANGE, 0.0)
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


# ── Band slot rack ────────────────────────────────────────────────────────────
#
# Phase 4 (issue #100/#88): a channel's bands are not emitted one node per
# band. They are laid into a rack of a fixed number of slots — the enabled
# bands first, then unity passthroughs filling what is left. Adding a band,
# deleting one or toggling one off therefore only rewrites Freq/Q/Gain
# literals: the node count, order and labels stay exactly where they were, so
# diff_filter_conf() can report the change and _ApplyWorker pushes it into the
# running graph with pw-cli set-param instead of restarting filter-chain —
# which tears every EQ node down and takes the audio with it for a few
# seconds. That restart is what users heard as "the sound cuts out when I hit
# Apply". Same trick Phase 1 used to pin the macro and boost nodes into the
# graph, applied to the bands themselves.
#
# The rack has to be bigger than the curves that go in it, or it buys
# nothing. Sonar's parametric EQ *is* a ten-filter rack and every preset on
# disk carries exactly filter1..filter10 — so a rack of ten is full the moment
# a preset uses all its filters (plenty do: Music - Punchy, Flat), and the
# first band the user adds on top overflows it and takes the restart the rack
# exists to avoid. Sixteen leaves six free slots under the worst preset, which
# is more bands than a curve drawn by hand tends to grow by.
#
# Past that the rack grows a step at a time rather than band by band, so
# crossing the line costs one restart and then buys another eight edits.
_BAND_SLOTS = 16
_BAND_SLOT_STEP = 8

# The rack is laid out by filter type, not in the order the bands happen to
# sit in the curve. Cascaded biquads are linear and commute, so the sound
# coming out is the same either way — but a fixed layout means deleting a band
# only ever empties a slot of its own type, instead of sliding the band below
# it into a slot holding a different filter. That is a relabelled node, and a
# relabelled node is a restart: without this, one shelf anywhere in a preset
# made *every* delete on that channel restart, and the stock Flat preset has
# one at each end.
#
# Only these three are pre-allocated: Gain=0.0 makes each of them a true unity
# passthrough, so an unused slot costs nothing but the arithmetic. A high- or
# low-pass filters at any gain and cannot be parked as unity, so those are
# emitted only while in use — adding or removing one stays structural.
_RACK_TYPES = ("peakingEQ", "lowShelving", "highShelving")


def _unity_band(band_type: str = "peakingEQ") -> EqBand:
    """An empty rack slot: any of the _RACK_TYPES at Gain=0.0 is a passthrough."""
    return EqBand(freq=1000.0, gain=0.0, q=0.7071, type=band_type, enabled=True)


def _band_slot_rack(active_bands: list[EqBand]) -> list[EqBand]:
    """The channel's active bands, laid into a rack of a stable shape."""
    rack: list[EqBand] = []
    for band_type in _RACK_TYPES:
        used = [b for b in active_bands if b.type == band_type]
        if band_type == "peakingEQ":
            size = _BAND_SLOTS
            while size < len(used):
                size += _BAND_SLOT_STEP
        else:
            # Presets carry at most one of each shelf, so one spare slot is
            # the whole of the headroom worth reserving here.
            size = max(1, len(used))
        rack += used + [_unity_band(band_type) for _ in range(size - len(used))]

    # Filter types gain cannot neutralise, in a stable order so that a curve
    # holding the same set of them still produces the same rack.
    rack += [b for b in active_bands if b.type not in _RACK_TYPES]
    return rack


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
    # Media's own HeSuVi chain (issue #169) — must be moved aside in safe mode
    # too, or a crash in the media convolver would survive the #88 quiesce.
    "sink-virtual-surround-7.1-hesuvi-media.conf",
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


def _dinit_crash_loop_settled(service: str, window_s: float = 12.0,
                               interval_s: float = 1.0) -> bool:
    """Init-agnostic crash-loop fallback for dinit (SD-2).

    systemd exposes a restart counter (``NRestarts``, see :func:`~.service_control.nrestarts`)
    that lets :func:`ensure_filter_chain_healthy` catch a service that has been
    repeatedly crashing even though it happens to be momentarily STARTED at
    the instant it is checked. dinit has no equivalent: verified against the
    dinit 0.22.1 control protocol (``control-cmds.h`` defines no restart-count
    query) and ``dinitctl(8)``, whose ``status`` command only ever reports
    STOPPED/STARTING/STARTED/STOPPING plus pid/exit-status — there is nothing
    to poll for a count.

    What dinit does have is its own crash-loop breaker: ``restart-limit-count``
    / ``restart-limit-interval`` (``dinit-service(5)``), defaulting to 3
    restarts per 10 s. This repository's ``dinit/pipewire-filter-chain`` unit
    does not override either, so on an unmodified ASM install a genuine
    crash-loop exhausts that limit and dinit stops retrying — the service
    settles into STOPPED for good. Instead of a counter, this watches for that
    settling: it samples ``sc.is_active(service)`` once a second for
    ``window_s`` seconds (12 s by default — margin over the 10 s default
    interval) and reports whether the *last* sample is inactive.

    A service that saw one legitimate, unrelated restart mid-window will have
    recovered to STARTED by the final sample, so this does not false-positive
    on an isolated restart — only on dinit itself having given up.
    """
    import time
    from arctis_sound_manager import service_control as sc

    samples = max(1, round(window_s / interval_s))
    settled_inactive = False
    for _ in range(samples):
        time.sleep(interval_s)
        settled_inactive = not sc.is_active(service)
    return settled_inactive


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
    2b. On dinit (where ``NRestarts`` has no equivalent, SD-2), a
        ``_dinit_crash_loop_settled()`` fallback watches the service live for
        up to 12 s and treats it settling into STOPPED as dinit's own
        restart-limit having been hit — see that function's docstring for why
        this is init-appropriate rather than an invented output format.

    If unhealthy → calls ``_enter_filter_chain_safe_mode()`` which moves ASM
    configs aside and restarts filter-chain without them so audio is flat but
    stable rather than permanently cut.

    Returns True when the filter-chain appears healthy (or there is nothing
    ASM could have broken).  Returns False when safe mode was entered or was
    already active.

    Callers must not call this in a tight loop — each call may block up to
    ``3 × 1 s`` for the poll and ``5 s`` for the NRestarts subprocess (or, on
    dinit, up to 12 s for the ``_dinit_crash_loop_settled()`` fallback)."""
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
    if n_restarts is not None:
        if n_restarts >= 3:
            _log.warning(
                "ensure_filter_chain_healthy: filter-chain NRestarts=%d "
                "(crash-loop detected) — entering safe mode",
                n_restarts,
            )
            _enter_filter_chain_safe_mode()
            return False
    elif sc.detect_init() == "dinit" and _dinit_crash_loop_settled("filter-chain"):
        # SD-2: dinit exposes no restart counter (see
        # _dinit_crash_loop_settled()'s docstring), so a service that looked
        # active on the primary check above is instead watched live for it
        # settling into STOPPED — dinit's own restart-limit having fired.
        _log.warning(
            "ensure_filter_chain_healthy: filter-chain settled into an "
            "inactive state after a live crash-loop observation window "
            "(dinit restart-limit reached) — entering safe mode"
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
    ensure_spatial_eq_links(spatial_channels())


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
    if channel not in ("game", "chat", "media", "aux", "output"):
        raise ValueError(
            f"channel must be 'game', 'chat', 'media', 'aux' or 'output', got {channel!r}")

    owns_link = channel in spatial_channels()
    sink_name = f"effect_input.sonar-{channel}-eq"

    # Only a conf written to the channel's real path represents the live EQ;
    # callers passing an explicit output_path are diffing or testing, and must
    # not overwrite the state snapshot (CHA-7).
    writes_live_conf = output_path is None
    if output_path is None:
        output_path = _CONF_DIR / f"sonar-{channel}-eq.conf"

    if channel == "chat":
        # Only chat still targets the physical Arctis output directly from
        # this conf and therefore needs a connected device to resolve a
        # target. Game and media always target HeSuVi (frozen hint, see
        # docstring) regardless of device-attach state — HeSuVi's OWN conf is
        # what needs the device.
        target = target_override or (_get_physical_out_chat() if _device_attached() else "")
        if not target:
            target = _target_already_written(output_path)
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]
    elif channel == "output":
        target, channels, position = _resolve_external_output(target_override)
        # CHA-6: record which raw external_output_device setting produced
        # this target, so a later read can tell — cheaply, without a
        # pulsectl round-trip — whether the setting has since moved on
        # without this conf being rewritten to match.
        _sync_output_setting_snapshot()
    else:
        # game / media: always 8ch, always (nominally) targets HeSuVi.
        target = target_override or _CHANNEL_TARGET.get(channel, "")
        channels = _CHANNEL_CHANNELS[channel]
        position = _CHANNEL_POSITION[channel]

    boost_db = max(-12.0, min(12.0, boost_db))

    # Collect active filter nodes: preset bands (only enabled ones) + macro
    # sliders. Once the channel is not fully flat, the 3 macro nodes are
    # ALWAYS emitted — even at Gain=0.0 — instead of only when non-zero
    # (Phase 1, issue #100/#88): a bq_peaking node at Gain=0.0 is a true unity
    # passthrough, so this keeps the node count/order stable while the user
    # drags a macro slider across zero, which previously added/removed a node
    # and forced a filter-chain restart on every crossing. The bands get the
    # same treatment one phase later — they go into a fixed-size rack, see
    # _band_slot_rack. The fully-flat case (no bands, all macros/boost at 0)
    # still takes the cheap _bypass_conf "copy" path below — the one-time
    # transition in/out of that state is the only structural change left.
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

    band_slots = [] if is_flat else _band_slot_rack(active_bands)

    all_filters: list[tuple[str, EqBand]] = (
        [(f"bq{i}", b) for i, b in enumerate(band_slots)]
        + [(f"macro_{name}", b) for name, b in macro_bands]
    )

    # Passthrough / bypass if nothing to do
    if not all_filters:
        text = _bypass_conf(sink_name, target, channels, position, channel=channel,
                             owns_link=owns_link)
        _write_conf(output_path, text)
        if writes_live_conf:
            _save_eq_state(channel, bands, basses_db, voix_db, aigus_db,
                           boost_db, smart_volume)
        return text

    if channels != 2 or channel == "output":
        text = _active_conf_8ch(channel, sink_name, target, position,
                                all_filters, band_slots, macro_bands,
                                boost_db, smart_volume, channels=channels,
                                owns_link=owns_link)
    else:
        text = _active_conf_2ch(channel, sink_name, target, position,
                                all_filters, band_slots, macro_bands,
                                boost_db, smart_volume)

    _write_conf(output_path, text)
    if writes_live_conf:
        _save_eq_state(channel, bands, basses_db, voix_db, aigus_db,
                       boost_db, smart_volume)
    return text


def _active_conf_8ch(
    channel: str, sink_name: str, target: str, position: str,
    all_filters: list[tuple[str, EqBand]],
    band_slots: list[EqBand],
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

    media_class = _media_class_for(channel)
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

    # All passive EQ chains (Game, Chat, Media, Aux) need
    # node.pause-on-idle = false so the downstream node stays running across
    # stream gaps; without it, WirePlumber suspends the sink after a few
    # seconds of silence and audio disappears until something else wakes it
    # (issue #223, issue #NNN).  The Output channel is the only exception:
    # it is Audio/Sink (not Internal) and carries a user fallback — applying
    # pause-on-idle there would prevent the headset from powering off.
    _pause_on_idle_line = (
        '        node.pause-on-idle  = false\n'
    ) if channel != "output" else ''

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
# Channel: {channel}  |  Band slots: {len(band_slots)}  |  Macros: {len(macro_bands)}
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "{_channel_node_description(channel)}"
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
        node.passive        = true
{_pause_on_idle_line}        audio.channels      = {channels}
        audio.position      = [ {position} ]
      }}
    }}
  }}
]
"""


def _active_conf_2ch(
    channel: str, sink_name: str, target: str, position: str,
    all_filters: list[tuple[str, EqBand]],
    band_slots: list[EqBand],
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
    media_class = _media_class_for(channel)

    # All passive EQ chains (Game, Chat, Media, Aux) need
    # node.pause-on-idle = false so the downstream node stays running across
    # stream gaps; without it, WirePlumber suspends the sink after a few
    # seconds of silence and audio disappears until something else wakes it
    # (issue #223, issue #NNN).  The Output channel is the only exception:
    # it is Audio/Sink (not Internal) and carries a user fallback — applying
    # pause-on-idle there would prevent the headset from powering off.
    _pause_on_idle_line = (
        '        node.pause-on-idle  = false\n'
    ) if channel != "output" else ''

    return f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
# Channel: {channel}  |  Band slots: {len(band_slots)}  |  Macros: {len(macro_bands)}
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "{_channel_node_description(channel)}"
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
        node.passive        = true
{_pause_on_idle_line}        audio.channels      = 2
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
    # Only a conf written to the real path represents the live mic EQ;
    # callers passing an explicit output_path are diffing or testing, and
    # must not overwrite the state snapshot (CHA-7, micro).
    writes_live_conf = output_path is None
    if output_path is None:
        output_path = _CONF_DIR / "sonar-micro-eq.conf"

    # Same "this process cannot see the device" fallback the chat channel
    # needs — see _target_already_written. Harmless here where it is missed
    # (the capture side runs autoconnect=false and ensure_micro_capture_link
    # owns the link, issue #127), but there is no reason to throw the hint
    # away every time the GUI rewrites this conf.
    capture_target = _get_physical_in() if _device_attached() else ""
    if not capture_target:
        capture_target = _target_already_written(output_path)
    if not capture_target:
        _log.info(
            "micro EQ config: no device and no target on disk, writing with "
            "empty target — the capture link is established on device arrival."
        )

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

    band_slots = [] if is_flat else _band_slot_rack(active_bands)

    all_filters = (
        [(f"bq{i}", b) for i, b in enumerate(band_slots)]
        + [(f"macro_{name}", b) for name, b in macro_bands]
    )

    if is_flat:
        text = _bypass_micro_conf()
        _write_conf(output_path, text)
        if writes_live_conf:
            _save_micro_state(bands, basses_db, voix_db, aigus_db, boost_db, nc, nr)
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

    # ── Noise cancellation — RNNoise or DeepFilterNet (user-selectable) ──
    # Correctif 4 (issue #88): guard the LADSPA node. A missing .so causes
    # dlopen() SEGV in filter-chain, so omit the node gracefully when absent.
    if nc.get("enabled", False):
        engine = nc.get("engine", "rnnoise")
        if engine == "deepfilternet":
            _dfn_ref = _deepfilter_plugin_ref()
            if not _dfn_ref:
                _log.warning(
                    "DeepFilterNet LADSPA plugin not found — skipping noise "
                    "cancellation node; install it or switch back to RNNoise "
                    "(the GUI can download the prebuilt plugin on opt-in)"
                )
            else:
                # nc.value 0..1 → DeepFilter "Attenuation Limit (dB)" 0..100
                # (higher = stronger suppression). Its other controls keep their
                # descriptor defaults, and the model is baked into the .so, so
                # there is no separate file to manage.
                atten_limit = max(0.0, min(100.0, nc.get("value", 0.9) * 100.0))
                node_lines.append(
                    f"                    {{ type = ladspa  name = dfn\n"
                    f"                      plugin = {_dfn_ref}  label = deep_filter_mono\n"
                    f"                      control = {{ \"Attenuation Limit (dB)\" = {atten_limit:.1f} }} }}"
                )
                link_lines.append(_smart_link("dfn", True))
                last_node = "dfn"
                last_is_ladspa = True
        else:
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
# Channel: micro  |  Band slots: {len(band_slots)}  |  Macros: {len(macro_bands)}
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
        target.object  = "{capture_target}"
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
    if writes_live_conf:
        _save_micro_state(bands, basses_db, voix_db, aigus_db, boost_db, nc, nr)
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
    media_class = _media_class_for(channel)
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
        node.passive        = true
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
        node.passive        = true
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
    """Write *text* to *path* atomically (CHA-7).

    A bare ``write_text()`` here used to leave a truncated conf on disk if
    the process died mid-write — the reproduction was a filter-chain
    restart landing between the truncation and the next write, at which
    point PipeWire loaded a fragment instead of the full chain. Serializes
    to a sibling tempfile, fsyncs, then renames over the target — the same
    tmp+fsync+rename pattern already used by
    ``GeneralSettings.write_to_file`` (settings.py) and
    ``stream_guard.save_config``, kept local to this module rather than
    factored into a shared helper (settings.py's own atomic-write work is
    happening in parallel).

    ``/dev/null`` is special-cased: callers (mainly tests) pass it as a
    throwaway ``output_path`` to get the generated text back without
    touching disk. There is nothing to make atomic there, and a sibling
    tempfile under ``/dev`` would need root — so just write straight
    through, exactly like the pre-CHA-7 behaviour.
    """
    if str(path) == os.devnull:
        path.write_text(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _backup_conf(path: Path) -> Path | None:
    """Copy *path* to a ``.bak`` sibling before a repair overwrites it (CHA-7).

    Best-effort and never blocks the repair: a failure to back up leaves the
    caller no worse off than before this existed. The conf being backed up
    may itself already be truncated or corrupt — that's fine, a partial copy
    is still worth keeping for support/diagnosis, and this is a forensic
    copy, not something anything reads back automatically.
    """
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        backup.write_text(path.read_text())
        return backup
    except OSError as exc:
        _log.warning("Could not back up %s before regenerating: %s", path, exc)
        return None


# ── Lossless EQ-conf rebuild (CHA-7) ──────────────────────────────────────────
#
# ensure_sonar_eq_configs()/check_and_fix_stale_configs() used to have exactly
# one regeneration primitive for a missing/corrupt/stale sonar-<channel>-eq.conf:
# _bypass_conf(), a flat passthrough. That is correct the first time a channel
# is ever configured (nothing to lose), but every trigger below it — a missing
# file, a truncated one, a wrong channel count, a wrong target — fires on the
# exact same path even when the conf on disk carried a real, user-tuned EQ
# curve, silently discarding every band, macro and boost node.
#
# gui/sonar_page.py's _ApplyWorker now leaves a JSON snapshot of the EQ state
# that actually produced a channel's conf (_save_eq_state) every time Apply
# reaches the filter-chain. _load_eq_state() below reads it back so a repair
# can rebuild the real conf through generate_sonar_eq_conf() instead of
# flattening it — a bypass is now the fallback for "nothing was ever applied
# yet", not the only option.

def _eq_state_path(channel: str) -> Path:
    return Path.home() / ".config" / "arctis_manager" / f"sonar_eq_state_{channel}.json"


def _save_eq_state(channel: str, bands: list[EqBand],
                   basses_db: float, voix_db: float, aigus_db: float,
                   boost_db: float, smart_volume: dict | None) -> None:
    """Snapshot the EQ state that just produced *channel*'s conf.

    Written here, from the one function every conf producer goes through,
    rather than from the GUI's Apply worker: an install that upgrades and
    never reopens the Sonar page would otherwise have no snapshot at all,
    and the first repair would still flatten its EQ (CHA-7). The daemon
    regenerates confs on its own, so the snapshot exists from the first
    watchdog tick onwards.

    Atomic (tmp+fsync+rename), for the same reason the conf write is: a
    truncated snapshot is a snapshot that fails to load, and a snapshot that
    fails to load is a bypass. Never raises — losing the snapshot must not
    take the conf write down with it.
    """
    path = _eq_state_path(channel)
    data = {
        "bands": [
            {"freq": b.freq, "gain": b.gain, "q": b.q,
             "type": b.type, "enabled": b.enabled}
            for b in bands
        ],
        "basses_db": basses_db,
        "voix_db": voix_db,
        "aigus_db": aigus_db,
        "boost_db": boost_db,
        "smart_volume": smart_volume,
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError as e:
        _log.warning("Could not save EQ state for channel=%s: %s", channel, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_eq_state(channel: str) -> dict | None:
    """Read back the last-applied EQ state for *channel*, or ``None``.

    Fails closed on anything malformed — missing file, non-JSON, wrong
    shape, a band that won't coerce to float — so a corrupt state file can
    never crash a repair; the caller falls back to the flat bypass exactly
    as it always has. Non-finite band values are not rejected here: they are
    clamped later, in :func:`_node_block`, the single choke point every
    producer's band literals already pass through (CHA-10).
    """
    try:
        raw = json.loads(_eq_state_path(channel).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    bands_raw = raw.get("bands")
    if not isinstance(bands_raw, list):
        return None
    bands: list[EqBand] = []
    try:
        for b in bands_raw:
            if not isinstance(b, dict):
                return None
            bands.append(EqBand(
                freq=float(b.get("freq", 1000.0)),
                gain=float(b.get("gain", 0.0)),
                q=float(b.get("q", 0.7071)),
                type=str(b.get("type", "peakingEQ")),
                enabled=bool(b.get("enabled", True)),
            ))
        basses_db = float(raw.get("basses_db", 0.0))
        voix_db = float(raw.get("voix_db", 0.0))
        aigus_db = float(raw.get("aigus_db", 0.0))
        boost_db = float(raw.get("boost_db", 0.0))
    except (TypeError, ValueError):
        return None
    smart_volume = raw.get("smart_volume")
    if smart_volume is not None and not isinstance(smart_volume, dict):
        smart_volume = None
    return {
        "bands": bands,
        "basses_db": basses_db,
        "voix_db": voix_db,
        "aigus_db": aigus_db,
        "boost_db": boost_db,
        "smart_volume": smart_volume,
    }


def _regenerate_eq_conf(
    channel: str, conf_path: Path, sink_name: str, target: str,
    channels: int, position: str, owns_link: bool, log, reason: str,
) -> None:
    """Repair *conf_path*, preferring a lossless rebuild over a bypass (CHA-7).

    Always backs up whatever is currently on disk first (even if it is the
    very truncation/corruption that triggered the repair — still worth
    keeping). Then tries the last-applied EQ state saved by the GUI; only
    when that is unavailable does it fall back to the flat bypass this
    function replaces, and the log line says so explicitly — "regenerating"
    alone used to give no indication the EQ was actually being discarded.
    """
    backup = _backup_conf(conf_path)
    if backup is not None:
        log.info("%s backed up to %s before regenerating (%s)",
                  conf_path.name, backup.name, reason)

    state = _load_eq_state(channel)
    if state is not None:
        generate_sonar_eq_conf(
            channel, state["bands"],
            state["basses_db"], state["voix_db"], state["aigus_db"],
            output_path=conf_path,
            boost_db=state["boost_db"],
            smart_volume=state["smart_volume"],
            target_override=target,
        )
        log.warning(
            "%s regenerated (%s) — rebuilt from the last saved EQ state, "
            "bands/macros/boost preserved",
            conf_path.name, reason,
        )
    else:
        _write_conf(
            conf_path,
            _bypass_conf(sink_name, target, channels, position,
                         channel=channel, owns_link=owns_link),
        )
        log.warning(
            "%s regenerated (%s) as a flat bypass — no saved EQ state was "
            "found, so any bands/macros/boost on this channel were reset "
            "to flat",
            conf_path.name, reason,
        )
        if channel == "output":
            _sync_output_setting_snapshot()


# ── Lossless micro-conf rebuild (CHA-7, the debt left for the mic channel) ────
#
# The four EQ channels above got a lossless rebuild; the mic channel kept
# calling _bypass_micro_conf() on every repair trigger — a missing file, the
# old Audio/Source/Virtual media.class, `label = gain` — discarding the
# user's bands, macros, boost AND their noise-cancelling / noise-reduction
# settings (background rumble cut, impact softening, gate, compressor),
# which have no equivalent in the EQ-channel state at all.
#
# generate_sonar_micro_conf() now snapshots the state that actually produced
# its conf (_save_micro_state), the same way generate_sonar_eq_conf() does
# for _save_eq_state — written from the one function every producer goes
# through (the GUI's _ApplyWorker AND _ApplyAllWorker both funnel through
# it), not from the GUI layer itself, so an install that upgrades and never
# reopens the Sonar page's Micro tab still gets a snapshot from the first
# time anything regenerates this conf onwards.

def _micro_state_path() -> Path:
    return Path.home() / ".config" / "arctis_manager" / "sonar_micro_state.json"


def _save_micro_state(bands: list[EqBand],
                      basses_db: float, voix_db: float, aigus_db: float,
                      boost_db: float,
                      noise_canceling: dict, noise_reduction: dict) -> None:
    """Snapshot the state that just produced the micro conf (CHA-7, micro).

    *noise_canceling* and *noise_reduction* are stored verbatim as the two
    dicts generate_sonar_micro_conf() itself already reads defensively
    (``.get(key, default)`` throughout, for both the sub-dict and its
    fields) — no separate schema is invented for them; a rebuild replays
    exactly what was last applied.

    Atomic (tmp+fsync+rename), for the same reason the conf write is: a
    truncated snapshot is a snapshot that fails to load, and a snapshot
    that fails to load is a bypass. Never raises.
    """
    path = _micro_state_path()
    data = {
        "bands": [
            {"freq": b.freq, "gain": b.gain, "q": b.q,
             "type": b.type, "enabled": b.enabled}
            for b in bands
        ],
        "basses_db": basses_db,
        "voix_db": voix_db,
        "aigus_db": aigus_db,
        "boost_db": boost_db,
        "noise_canceling": noise_canceling or {},
        "noise_reduction": noise_reduction or {},
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except OSError as e:
        _log.warning("Could not save micro EQ state: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _sanitize_nr_subproc(raw: object, value_default: float) -> dict | None:
    """Validate one noise_reduction sub-processor dict (bgReduction,
    impactReduction, noiseGate or compressor). ``None``/absent coerces to a
    disabled default; any other non-dict shape is malformed. Never raises —
    a coercion failure on "value" returns None like the rest of this module's
    fail-closed loaders."""
    if raw is None:
        return {"enabled": False, "value": value_default}
    if not isinstance(raw, dict):
        return None
    try:
        return {
            "enabled": bool(raw.get("enabled", False)),
            "value": float(raw.get("value", value_default)),
        }
    except (TypeError, ValueError):
        return None


def _load_micro_state() -> dict | None:
    """Read back the last-applied micro state, or ``None``.

    Fails closed on anything malformed — missing file, non-JSON, wrong
    shape, a value that won't coerce to float — so a corrupt state file can
    never crash a repair; the caller falls back to the flat bypass exactly
    as it always has. Unlike generate_sonar_micro_conf()'s own ``.get(...,
    default)`` reads, this validates the *type* of every nested field too:
    generate_sonar_micro_conf() would otherwise raise deep inside its
    noise-reduction block (e.g. comparing a non-numeric "value" against
    0.0/1.0) on a snapshot that was merely mangled, not absent.
    """
    try:
        raw = json.loads(_micro_state_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    bands_raw = raw.get("bands")
    if not isinstance(bands_raw, list):
        return None
    bands: list[EqBand] = []
    try:
        for b in bands_raw:
            if not isinstance(b, dict):
                return None
            bands.append(EqBand(
                freq=float(b.get("freq", 1000.0)),
                gain=float(b.get("gain", 0.0)),
                q=float(b.get("q", 0.7071)),
                type=str(b.get("type", "peakingEQ")),
                enabled=bool(b.get("enabled", True)),
            ))
        basses_db = float(raw.get("basses_db", 0.0))
        voix_db = float(raw.get("voix_db", 0.0))
        aigus_db = float(raw.get("aigus_db", 0.0))
        boost_db = float(raw.get("boost_db", 0.0))
    except (TypeError, ValueError):
        return None

    nc_raw = raw.get("noise_canceling")
    if nc_raw is None:
        noise_canceling: dict = {}
    elif not isinstance(nc_raw, dict):
        return None
    else:
        try:
            noise_canceling = {
                "enabled": bool(nc_raw.get("enabled", False)),
                "value": float(nc_raw.get("value", 0.9)),
                "engine": str(nc_raw.get("engine", "rnnoise")),
            }
        except (TypeError, ValueError):
            return None

    nr_raw = raw.get("noise_reduction")
    if nr_raw is None:
        nr_raw = {}
    elif not isinstance(nr_raw, dict):
        return None
    noise_reduction: dict = {}
    for key, default_value in (
        ("bgReduction", 0.0), ("impactReduction", 0.0),
        ("noiseGate", -40.0), ("compressor", 0.0),
    ):
        sub = _sanitize_nr_subproc(nr_raw.get(key), default_value)
        if sub is None:
            return None
        noise_reduction[key] = sub

    return {
        "bands": bands,
        "basses_db": basses_db,
        "voix_db": voix_db,
        "aigus_db": aigus_db,
        "boost_db": boost_db,
        "noise_canceling": noise_canceling,
        "noise_reduction": noise_reduction,
    }


def _regenerate_micro_conf(conf_path: Path, log, reason: str) -> None:
    """Repair *conf_path*, preferring a lossless rebuild over a bypass
    (CHA-7, micro).

    Mirrors _regenerate_eq_conf for the micro channel: always backs up
    whatever is on disk first, then tries the last-applied micro state
    (bands, macros, boost, noise cancelling and the four noise-reduction
    sub-processors); only when no such snapshot exists does it fall back to
    the flat bypass this function replaces.
    """
    backup = _backup_conf(conf_path)
    if backup is not None:
        log.info("%s backed up to %s before regenerating (%s)",
                  conf_path.name, backup.name, reason)

    state = _load_micro_state()
    if state is not None:
        generate_sonar_micro_conf(
            state["bands"], state["basses_db"], state["voix_db"], state["aigus_db"],
            output_path=conf_path,
            boost_db=state["boost_db"],
            noise_canceling=state["noise_canceling"],
            noise_reduction=state["noise_reduction"],
        )
        log.warning(
            "%s regenerated (%s) — rebuilt from the last saved micro state, "
            "bands/macros/boost/noise-reduction preserved",
            conf_path.name, reason,
        )
    else:
        _write_conf(conf_path, _bypass_micro_conf())
        log.warning(
            "%s regenerated (%s) as a flat bypass — no saved micro state "
            "was found, so any bands/macros/boost/noise-reduction on this "
            "channel were reset to flat",
            conf_path.name, reason,
        )


_WRITTEN_TARGET_RE = re.compile(
    r'^\s*(?:node\.target|target\.object)\s*=\s*"([^"]+)"', re.MULTILINE
)


# The playback.props block of a generated conf, captured so a repair can look
# inside it alone: the capture side must never gain node.passive (it is the
# sink applications play into — passive there would keep it from ever waking).
_PLAYBACK_BLOCK_RE = re.compile(r"playback\.props\s*=\s*\{(.*?)\n\s*\}", re.DOTALL)

# Confs whose playback node feeds a device (or another chain) and must
# therefore be passive. sonar-micro-eq is deliberately absent: it is an
# Audio/Source, the capture end of the graph, and has no device to hold awake.
_PASSIVE_CONFS = (
    "sonar-game-eq.conf",
    "sonar-media-eq.conf",
    "sonar-chat-eq.conf",
    "sonar-output-eq.conf",
    "sink-virtual-surround-7.1-hesuvi.conf",
    "sink-virtual-surround-7.1-hesuvi-media.conf",
)


def _ensure_passive_playback(path: Path) -> bool:
    """Add the missing ``node.passive`` to an existing conf, in place (#180).

    Returns True if the file was changed.

    Why in place rather than regenerating: a conf written before this fix has
    the user's real EQ baked into it, and nothing in this module can read back
    their bands, macros and boost — the regeneration path here can only write
    a flat bypass (see the "Scope" note on ``_CONF_VERSION``). So a version
    bump alone cannot carry this fix to existing installs without silently
    flattening everyone's EQ. Since the change is a single line inside
    playback.props, inserting it is both lossless and sufficient.

    Nothing else in the file is touched, and the edit is idempotent: a conf
    that already carries the property in its playback block is left alone.
    """
    try:
        content = path.read_text()
    except OSError:
        return False

    match = _PLAYBACK_BLOCK_RE.search(content)
    if match is None or "node.passive" in match.group(1):
        return False

    # Anchor on node.linger, present in every generated output chain, and copy
    # its exact indentation so the repaired file still reads like a generated
    # one rather than something hand-edited.
    block = match.group(1)
    linger = re.search(r"^(\s*)node\.linger(\s*)=", block, re.MULTILINE)
    if linger is None:
        return False
    indent, spacing = linger.group(1), linger.group(2)
    # "node.passive" is one character shorter than "node.linger", so pad by one
    # to keep the "=" of both lines in the same column.
    line = f"{indent}node.passive{spacing[:-1] or ' '}= true"

    # Insert right after the node.linger line, positions taken in the file's
    # own coordinates (the match is a slice of it, so offsets must be shifted).
    eol = content.find("\n", match.start(1) + linger.start())
    if eol == -1:
        return False
    _write_conf(path, content[:eol + 1] + line + "\n" + content[eol + 1:])
    return True


def _ensure_media_pause_on_idle(path: Path) -> bool:
    """Add ``node.pause-on-idle = false`` to passive channel confs (issue #223).

    Every passive EQ and HeSuVi chain (Game, Chat, Media, Aux) needs this
    property; without it, WirePlumber suspends the chain after a few seconds
    of silence and audio disappears until something else wakes it.

    Returns True if the file was changed.

    This repair is in-place (like _ensure_passive_playback) because:
    - HeSuVi confs are rebuilt losslessly from JSON on version bump,
      so they don't need this repair.
    - EQ confs (sonar-*-eq.conf) are NOT regenerated by a version bump alone
      — they only regenerate on user EQ edit.  So existing installs need this
      in-place fix to carry the fix forward without flattening EQ.
    """
    # Applies to all passive EQ + HeSuVi channel confs (not output, which
    # is Audio/Sink with a user fallback and must be allowed to suspend).
    pause_idle_confs = {
        "sonar-game-eq.conf",
        "sonar-media-eq.conf",
        "sonar-chat-eq.conf",
        "sink-virtual-surround-7.1-hesuvi.conf",
        "sink-virtual-surround-7.1-hesuvi-media.conf",
    }
    if path.name not in pause_idle_confs:
        return False

    try:
        content = path.read_text()
    except OSError:
        return False

    match = _PLAYBACK_BLOCK_RE.search(content)
    if match is None:
        return False
    if "node.pause-on-idle" in match.group(1):
        return False  # already has it

    # Work entirely within the matched playback.props block so the insertion
    # lands in the right place even if capture.props also grows a node.passive
    block = match.group(1)

    # Find node.passive inside the block (it always follows node.linger)
    passive = re.search(r"^(\s*)node\.passive(\s*)=", block, re.MULTILINE)
    if passive is None:
        return False  # should not happen if passive is present
    indent, spacing = passive.group(1), passive.group(2)

    # Align the "=" on the same column as node.passive, measured within the
    # line itself (not absolute file coordinates). The string
    # "node.pause-on-idle" is 6 chars longer than "node.passive"; if the
    # column is too narrow we fall back to a single space.
    eq_col = passive.end(2) - passive.start(1)  # column of "=" within the line
    key = "node.pause-on-idle"
    if len(indent) + len(key) > eq_col:
        line = f"{indent}{key} = false"
    else:
        pad = eq_col - len(indent) - len(key)
        line = f"{indent}{key}{' ' * pad}= false"

    # Insert right after the node.passive line within the block, then translate
    # to file coordinates using match.start(1).
    eol = block.find("\n", passive.start())
    if eol == -1:
        return False
    file_offset = match.start(1) + eol + 1
    _write_conf(path, content[:file_offset] + line + "\n" + content[file_offset:])
    return True


def _restore_missing_target(content: str, channel: str, target: str) -> str | None:
    """Put *target* back into a generated conf that carries none, or ``None``.

    The repair for a conf already written without a target, which
    :func:`_target_already_written` cannot help with — there is nothing left
    on disk to preserve. Only the daemon knows the device, so only the daemon
    can do this; it runs from ensure_sonar_eq_configs().

    ``None`` when the conf is not in that state at all: it already names a
    target (a *wrong* one is a different problem — the device moved, and the
    caller regenerates), or it does not have the playback node this expects.
    """
    anchor = f'        node.name           = "effect_output.sonar-{channel}-eq"\n'
    if not target or "node.target" in content or anchor not in content:
        return None
    return content.replace(
        anchor,
        f'{anchor}        node.target         = "{target}"\n'
        f'        target.object       = "{target}"\n',
        1,
    )


def _target_already_written(path: Path) -> str:
    """The target the conf at *path* already carries, or ``""``.

    A fallback for the case where the process regenerating a conf cannot see
    the device. Only ``core.py`` fills :mod:`device_state`, and it runs in the
    daemon — so the GUI, which rewrites these confs every time the user edits
    an EQ, always believes no device is attached. Writing the target out empty
    there is not the harmless "PipeWire will bind on device arrival" the old
    comment claimed: an output node with no target and ``autoconnect`` on is
    handed to WirePlumber, which tries to route it to the default sink, finds
    that sink is one of ASM's own loopbacks (linking there would close a
    cycle), gives up — and retries on every graph change from then on. The
    chat channel lost its target exactly this way, and the retry storm was
    audible on the other channels.

    What the file already says is the best answer a process in the dark has,
    and it is right in the case that matters: the device has not moved, only
    this process cannot see it. It is no worse in the case it isn't — a target
    naming an absent node leaves the node unlinked until it returns, which is
    what an unresolvable target is supposed to mean.
    """
    try:
        match = _WRITTEN_TARGET_RE.search(path.read_text())
    except OSError:
        return ""
    return match.group(1) if match else ""


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


def _purge_duplicate_pipewire_confs(bad_dir: Path, log) -> list[str]:
    """Remove filter configs from ``pipewire.conf.d/`` that duplicate ours.

    Both directories are read, by different processes: ``filter-chain.conf.d/``
    by ``filter-chain.service``, ``pipewire.conf.d/`` by the pipewire daemon
    itself. The same filter declared in both loads twice, and two nodes then
    answer to one ``node.name`` — at which point every route ASM sets by name
    (``target.object = effect_input.sonar-media-eq``) is ambiguous. The loopback
    that cannot resolve it links to nothing and, with
    ``node.dont-fallback = true``, nothing falls back either: the channel's
    meters move while the headset stays silent.

    That was issue #14 (a static HeSuVi conf left in ``pipewire.conf.d/`` by an
    old install.sh) and it came back as #205 with the whole EQ chain doubled.
    The reason it came back is that the repair was a hand-written list of four
    filenames: ``sonar-output-eq.conf`` and the ``-media`` HeSuVi variant were
    added to the generator later and nobody added them here. So this derives the
    set instead of listing it, and stays correct for filters not written yet.

    Two ways a file earns removal:

    - **A name we generate.** Same filename in ``filter-chain.conf.d/`` means a
      copy of ours, wherever it came from. This is what catches #14: the static
      HeSuVi template carries no ASM header at all (it opens with "# Convolver
      sink"), so a marker check alone would walk straight past the original bug.
    - **Our marker.** ``# Auto-generated by Arctis Sound Manager`` identifies a
      leftover of ours even with no counterpart left to compare against.

    Anything else is left alone and only reported. A conf in there that is not
    ours is the user's, and a duplicate node is not a good enough reason to
    delete someone's file — #205's reporter had a spatializer of his own in that
    directory and was surprised to learn it was not part of ASM.

    Returns the filenames removed, so the caller can restart PipeWire: the
    daemon has already loaded these modules and only a restart unloads them.
    """
    if not bad_dir.is_dir():
        return []

    ours = {p.name for p in _CONF_DIR.glob("*.conf")} if _CONF_DIR.is_dir() else set()
    removed: list[str] = []

    for path in sorted(bad_dir.glob("*.conf")):
        try:
            head = path.read_text(errors="replace")[:512]
        except OSError:
            continue

        if path.name in ours:
            why = "duplicates filter-chain.conf.d/" + path.name
        elif "Auto-generated by Arctis Sound Manager" in head or _CONF_VERSION_RE.search(head):
            why = "ASM-generated leftover"
        else:
            if "libpipewire-module-filter-chain" in head:
                log.warning(
                    "%s loads a filter-chain in the pipewire daemon. It is not "
                    "ours, so it is left as is — but if it declares a node.name "
                    "ASM also uses, that name is ambiguous and the channel "
                    "through it will be silent.", path,
                )
            continue

        try:
            path.unlink()
        except OSError as exc:
            log.warning("Could not remove %s: %s", path, exc)
            continue
        log.warning("Removed duplicate config from pipewire.conf.d: %s (%s)", path, why)
        removed.append(path.name)

    return removed


def check_and_fix_stale_configs() -> tuple[bool, bool]:
    """Detect and fix stale Sonar configs.

    Checks for:
    1. Any filter conf in ``pipewire.conf.d/`` duplicating one of ours (see
       :func:`_purge_duplicate_pipewire_confs`): the daemon loads that directory
       on top of what filter-chain.service loads, two nodes answer to one name,
       and the channel routed by that name goes silent (#14, then #205).
    2. Configs using the broken ``label = gain`` builtin (PipeWire 1.6.x).
    3. (folded into 1 — it used to be a separate hand-listed set of filenames,
       which is exactly how ``sonar-output-eq.conf`` and the ``-media`` HeSuVi
       variant were missed until #205.)
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
    9. Micro config missing entirely. ``ensure_sonar_eq_configs()`` guarantees
       game/media/chat/output but not micro, and no package or install script
       ships it, so on an install where the user never pressed "Apply" in the
       micro EQ tab ``effect_output.sonar-micro-eq`` simply never existed —
       yet the daemon makes it the default source at startup. This is the
       only guarantor that file has, and it runs whatever ``.eq_mode`` says.
       Repaired via :func:`_regenerate_micro_conf`, which — like
       :func:`_regenerate_eq_conf` for the other four channels (CHA-7) —
       prefers rebuilding from the last saved micro state (bands, macros,
       boost, noise cancelling and the four noise-reduction sub-processors)
       and only falls back to a flat bypass when nothing was ever saved.
    10. Output chain confs written without ``node.passive`` (issue #180). Any
       node that is not passive drives its chain continuously, so the physical
       sink never idles and the headset's auto-off timer never starts. Repaired
       in place by :func:`_ensure_passive_playback` rather than regenerated,
       because the regen path here can only write a flat bypass and would drop
       the user's EQ — and leaving even one conf unpatched would make the fix
       inert, since a single active node keeps the whole chain awake.

    Returns ``(fixed, needs_pipewire_restart)``.  *fixed* is True if any config
    was regenerated or cleaned.  *needs_pipewire_restart* is True when anything
    was removed from ``pipewire.conf.d/``; the caller must restart the main
    PipeWire process, because the daemon loaded those modules at startup and
    deleting the file does not unload the duplicate node it created.
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
    purged = _purge_duplicate_pipewire_confs(bad_dir, log)
    if purged:
        fixed = True
        # Every removal needs the restart, not just HeSuVi's: the daemon loaded
        # each of these modules at startup and the duplicate node stays in the
        # graph until it is restarted. Deleting the file alone changed nothing
        # until the next reboot.
        needs_pw_restart = True
        # HeSuVi is the one whose dynamic counterpart may not exist yet — the
        # static copy was all some installs ever had. Write it before the
        # restart so the chain comes back without a gap.
        if "sink-virtual-surround-7.1-hesuvi.conf" in purged:
            if not (_CONF_DIR / "sink-virtual-surround-7.1-hesuvi.conf").exists():
                generate_hesuvi_conf()
                generate_hesuvi_conf(channel="media")

    for name in ("sonar-game-eq.conf", "sonar-media-eq.conf", "sonar-chat-eq.conf"):
        # Fix broken 'label = gain' or wrong channel count in correct location
        path = _CONF_DIR / name
        if path.exists():
            content = path.read_text()
            regen_reason: str | None = None

            if "label = gain" in content:
                log.warning("Stale config (%s uses 'label = gain'), regenerating", name)
                regen_reason = "'label = gain'"

            # Phase 3 (issue #100/#88): game and media are now ALWAYS 8ch,
            # independent of the Spatial Audio toggle (the live routing
            # decision is made by ensure_spatial_eq_links(), not by channel
            # count). A 2ch game/media conf is therefore always stale.
            if name in ("sonar-game-eq.conf", "sonar-media-eq.conf") and "audio.channels = 2" in content:
                log.warning("Stale config (%s uses 2ch, should be 8ch), regenerating", name)
                regen_reason = "2ch, should be 8ch"

            # NOTE: _conf_is_outdated() is deliberately NOT a regeneration
            # trigger here — see the "Scope" note on _CONF_VERSION. The regen
            # below now prefers rebuilding from the last saved EQ state
            # (CHA-7); it only falls back to a flat bypass when no such
            # state exists, so triggering it here does not usually flatten
            # a user's EQ, but the bypass fallback still can — and a bump
            # alone still isn't a reason to force it on every existing conf.
            if regen_reason is not None:
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
                _regenerate_eq_conf(
                    channel, path, sink_name, target, channels, position,
                    owns_link=channel in spatial_channels(), log=log,
                    reason=regen_reason,
                )
                fixed = True

    # Fix micro configs using old Audio/Source/Virtual or Audio/Sink pattern
    micro_path = _CONF_DIR / "sonar-micro-eq.conf"
    if not micro_path.exists():
        # Nobody else guarantees this file. ensure_sonar_eq_configs() covers
        # game/media/chat/output but never micro, the packages and install
        # scripts do not ship it, and the only writer is the GUI's micro EQ
        # "Apply" — which a user in Custom EQ mode has no reason to ever press.
        # Meanwhile the daemon unconditionally makes
        # ``effect_output.sonar-micro-eq`` the default source at startup, so
        # without the conf that node does not exist: the mic vanishes from the
        # selectable inputs and micro_input_source / micro_autoswitch /
        # ensure_micro_capture_link() all become inert.
        #
        # CHA-7 (micro): _regenerate_micro_conf() prefers rebuilding from the
        # last saved micro state (bands/macros/boost/noise-reduction) over a
        # flat bypass — a bypass is now only what it falls back to when no
        # such state was ever saved, the same as the EQ channels above.
        _regenerate_micro_conf(micro_path, log, reason="conf missing")
        fixed = True
    else:
        content = micro_path.read_text()
        # As with the EQ confs above, an outdated ASM-CONF-VERSION is NOT a
        # trigger here: the regen falls back to a bypass (flat) micro conf
        # only when nothing was ever saved, so triggering it does not
        # usually flatten the user's mic processing — but the fallback
        # still can, and a bump alone still isn't a reason to force it.
        if (
            "Audio/Source/Virtual" in content
            or "Audio/Sink" in content
            or "label = gain" in content
        ):
            _regenerate_micro_conf(
                micro_path, log,
                reason="wrong media.class or label=gain",
            )
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
        # Regenerate each channel's HeSuVi chain (issue #169). Game keeps the
        # historical un-suffixed conf; Media has its own …-hesuvi-media.conf.
        # Each is sourced from its own sonar_spatial_audio*.json, so the two
        # channels carry independent Immersion/Distance. This loop is also what
        # makes the sliders LIVE: a slider move only rewrites the JSON (in the
        # GUI process, where generate_hesuvi_conf can't write — no device is
        # registered there), so the daemon detects the drift here, regenerates,
        # and the caller restarts the filter-chain (see `fixed`).
        _dev = _device_attached()
        for _hz_channel in spatial_channels():
            immersion_pct, distance_pct = _load_spatial_pct(_hz_channel)
            hesuvi_path = _CONF_DIR / _hesuvi_conf_name(_hz_channel)
            if not hesuvi_path.exists():
                if not _dev:
                    # generate_hesuvi_conf() would skip the write and return ""
                    # — checking here avoids repeatedly reporting "fixed" while
                    # no device is attached.
                    log.debug("HeSuVi %s config missing but no device attached yet — skipping", _hz_channel)
                    continue
                log.warning("HeSuVi %s config missing — generating (Phase 3: always present)", _hz_channel)
                generate_hesuvi_conf(
                    immersion_pct=immersion_pct, distance_pct=distance_pct, channel=_hz_channel,
                )
                fixed = True
                continue

            hesuvi_content = hesuvi_path.read_text()
            _reason = None
            if f'node.target        = "{_get_physical_out_game()}"' not in hesuvi_content:
                _reason = "stale node.target"
            elif _conf_has_bare_ladspa(hesuvi_content):
                # A plate plugin written by bare name (pre-#100 container
                # fallback) fails to load on a distrobox host without
                # swh-plugins, so the whole HeSuVi module — and its surround
                # node — never comes up. Regenerate so it picks up the staged
                # ~/.ladspa absolute path (or drops the plate if unavailable).
                _reason = "bare-name LADSPA plugin (issue #100)"
            elif _conf_is_outdated(hesuvi_content):
                # Covers config-shape changes shipped in a later ASM version
                # that this file predates — e.g. v1.2.5 added an output limiter
                # node, v1.2.x (#169) split Media into its own chain. Regenerating
                # with the saved Immersion/Distance keeps the user's settings.
                _reason = f"predates ASM-CONF-VERSION {_CONF_VERSION}"
            elif _hesuvi_conf_has_spatial_drift(hesuvi_content, immersion_pct, distance_pct):
                # The user moved an Immersion/Distance slider: the JSON changed
                # but the conf on disk still bakes the old percentages. This is
                # the trigger that finally makes the sliders do something (#169).
                _reason = "Immersion/Distance changed (issue #169)"

            if _reason is None:
                continue  # conf is current — no needless regen/restart.
            if not _dev:
                log.debug(
                    "HeSuVi %s needs regen (%s) but no device attached — deferring",
                    _hz_channel, _reason,
                )
                continue
            log.warning("HeSuVi %s config: %s — regenerating", _hz_channel, _reason)
            generate_hesuvi_conf(
                immersion_pct=immersion_pct, distance_pct=distance_pct, channel=_hz_channel,
            )
            fixed = True

    # Ensure sonar EQ nodes exist when in Sonar mode
    if sonar and ensure_sonar_eq_configs():
        fixed = True

    # ── Correctif 10 (issue #180): output chains missing node.passive ────────
    # Runs last, after every path above may have regenerated a conf, and is
    # idempotent — a freshly generated conf already carries the property, so
    # this only ever fires on files written by an older ASM.
    #
    # Without it the headset is never allowed to sleep: an active node pushes
    # its chain continuously, the physical sink stays RUNNING with nothing
    # playing, the firmware never sees an idle moment and its auto-off timer
    # never starts. It takes only ONE such node to hold the entire chain — and
    # the device — awake, which is why the repair covers all six output confs
    # rather than the ones a version bump could regenerate.
    for _conf_name in _PASSIVE_CONFS:
        if _ensure_passive_playback(_CONF_DIR / _conf_name):
            log.warning(
                "%s had no node.passive — patched in place so the chain can "
                "suspend and the headset can power off (issue #180)", _conf_name,
            )
            fixed = True

    # ── Issue #223 / passive-chain suspension: pause-on-idle = false ─────────
    # Every passive EQ and HeSuVi chain (Game, Chat, Media, Aux) needs
    # node.pause-on-idle = false; without it, WirePlumber suspends the chain
    # after a few seconds of silence and audio disappears until something else
    # wakes it.  This in-place repair adds the property to existing confs
    # without regenerating (which would flatten EQ or lose saved settings).
    _all_pause_confs = (
        "sonar-game-eq.conf",
        "sonar-media-eq.conf",
        "sonar-chat-eq.conf",
        "sink-virtual-surround-7.1-hesuvi.conf",
        "sink-virtual-surround-7.1-hesuvi-media.conf",
    )
    for _conf_name in _all_pause_confs:
        if _ensure_media_pause_on_idle(_CONF_DIR / _conf_name):
            log.warning(
                "%s had no node.pause-on-idle — patched in place so the "
                "chain stays running across stream gaps (issue #223)",
                _conf_name,
            )
            fixed = True

    return fixed, needs_pw_restart


def regenerate_hesuvi_if_changed() -> bool:
    """Rewrite each HeSuVi chain from its saved JSON and report if anything changed.

    Runs in the DAEMON (device attached) so :func:`generate_hesuvi_conf` can
    actually write — the GUI process has no device registered, so the same call
    there no-ops (issue #169). That is exactly why moving an Immersion/Distance
    slider needs this daemon-side round-trip to take effect: the slider only
    rewrites sonar_spatial_audio*.json; this regenerates the on-disk conf(s)
    from it.

    For each of Game and Media it regenerates the conf and compares the result
    to what was on disk. A returned value that differs — a moved slider, a stale
    ``node.target``, a bare-name LADSPA reference, an outdated conf shape, or a
    missing file — is reported as changed. When it returns True the caller must
    restart the ``filter-chain`` service so PipeWire reloads the new conf.

    Returns False (nothing to do) when no device is attached.
    """
    if not device_state.is_device_set():
        return False
    # A slider move can't require a new HRIR, but keep this idempotent with the
    # daemon-init path so a first-ever generation here still has its WAV staged.
    ensure_hrir_materialized()
    changed = False
    for channel in spatial_channels():
        immersion_pct, distance_pct = _load_spatial_pct(channel)
        path = _CONF_DIR / _hesuvi_conf_name(channel)
        old = path.read_text() if path.exists() else None
        new = generate_hesuvi_conf(
            immersion_pct=immersion_pct, distance_pct=distance_pct, channel=channel,
        )
        # generate_hesuvi_conf returns "" only when no device is attached, which
        # the guard above already excludes; treat any real diff as a change.
        if new and new != (old or ""):
            changed = True
    return changed


def apply_spatial_audio_change() -> bool:
    """Make an Immersion/Distance slider move take effect on the live pipeline (#169).

    Regenerates the per-channel HeSuVi confs from the saved JSON and, only when
    a conf actually changed, restarts the filter-chain — through
    :func:`_restart_filter_chain`, which quiesces the graph first (the #100 SEGV
    guard) and arms safe mode if the restart crash-loops (#88) — then re-owns the
    EQ→{HeSuVi,physical} and HeSuVi→physical links the restart tore down.

    Runs in the DAEMON (device attached); a no-op change (e.g. a pure Spatial
    Audio toggle, which leaves Immersion/Distance untouched) regenerates an
    identical conf and returns False without ever touching the service.

    Returns True when it restarted the filter-chain, False otherwise.
    """
    if not regenerate_hesuvi_if_changed():
        return False
    _restart_filter_chain()
    # Same recovery as apply_hrir_choice()'s restart: re-establish ASM-owned
    # links immediately for a responsive slider; the loopback watchdog also
    # heals these on its next tick if a node is still coming up.
    ensure_spatial_eq_links(spatial_channels())
    ensure_physical_output_links()
    return True


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
    # Aux is Media's twin — same channel count, same position, same spatial
    # stage — and only present while it is switched on. Without an entry here
    # nothing guarantees sonar-aux-eq.conf, so the loopback would point at a
    # node that never loads and the channel would be silent (#209); the same
    # gap the micro EQ had.
    if _aux_enabled():
        expected["aux"] = {
            "channels": _CHANNEL_CHANNELS["aux"],
            "position": _CHANNEL_POSITION["aux"],
            "target":   _SURROUND,
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

    for channel in (*spatial_channels(), "chat", "output"):
        conf_path = _CONF_DIR / f"sonar-{channel}-eq.conf"
        sink_name = f"effect_input.sonar-{channel}-eq"
        exp = expected[channel]
        regen_reason: str | None = None

        if not conf_path.exists():
            log.warning(
                "sonar-%s-eq.conf missing — regenerating so %s node exists",
                channel, sink_name,
            )
            regen_reason = "conf missing"
        else:
            content = conf_path.read_text()
            ch_str  = f"audio.channels = {exp['channels']}"
            tgt_str = f'node.target         = "{exp["target"]}"'
            if ch_str not in content:
                log.warning(
                    "sonar-%s-eq.conf has wrong channel count (expected %d) — regenerating",
                    channel, exp["channels"],
                )
                regen_reason = "wrong channel count"
            elif exp["target"] and tgt_str not in content:
                # A conf with NO target at all is repairable without touching
                # anything else, and that is worth doing: regenerating writes
                # a bypass, which would silently flatten the user's EQ for
                # this channel. Only a conf naming the *wrong* target — the
                # device genuinely moved — falls through to that.
                patched = _restore_missing_target(content, channel, exp["target"])
                if patched is not None:
                    log.warning(
                        "sonar-%s-eq.conf lost its target — putting %r back, "
                        "keeping the EQ", channel, exp["target"],
                    )
                    _write_conf(conf_path, patched)
                    if channel == "output":
                        _sync_output_setting_snapshot()
                    generated = True
                    continue
                log.warning(
                    "sonar-%s-eq.conf has wrong target (expected %r) — regenerating",
                    channel, exp["target"],
                )
                regen_reason = "wrong target"

        # No ASM-CONF-VERSION check here either — same reason as in
        # check_and_fix_stale_configs(): the regen below can only fall back
        # to a bypass conf when there is no saved EQ state to rebuild from.
        if regen_reason is not None:
            # channel= is not optional: _bypass_conf/_regenerate_eq_conf
            # derive media.class and priority.session from it. Omitting it
            # wrote the Output conf as Audio/Sink/Internal priority 1000
            # instead of Audio/Sink priority 1, so the Output channel
            # disappeared from the selectable outputs — while
            # check_and_fix_stale_configs()'s regen of the same file wrote
            # it correctly. Two writers, one file, one of them wrong.
            _regenerate_eq_conf(
                channel, conf_path, sink_name, exp["target"],
                exp["channels"], exp["position"],
                owns_link=channel in spatial_channels(), log=log,
                reason=regen_reason,
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
    channels: tuple[str, ...] | None = None,
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

    # None means "whatever carries a spatial chain right now", which is what
    # every caller wanted — spelling it as a default argument would freeze the
    # answer at import time, before the user has switched Aux on.
    if channels is None:
        channels = spatial_channels()

    results: dict[str, bool] = {}
    for channel in channels:
        if channel not in spatial_channels():
            continue
        enabled = _spatial_enabled(channel)
        # Each channel links to its OWN HeSuVi chain (issue #169): Game keeps the
        # historical un-suffixed node, Media routes to effect_input.…-hesuvi-media,
        # so their independent Immersion/Distance never bleed into each other.
        # _hesuvi_input_node() already derives this from the channel name, and
        # spelling it as a two-way choice sent Aux into Media's chain — which
        # is exactly the bleed between channels #169 set out to stop.
        surround_node = _hesuvi_input_node(channel)
        target = surround_node if enabled else channel_destination(channel, data)
        if enabled and not pw_node_exists(surround_node, data):
            # HeSuVi is not in the graph. If its HRIR WAV is missing the
            # convolver can never load and the node will never appear —
            # targeting it here would be permanent silence, so fall back to
            # the physical output so the user still hears their game/media
            # (issue #100). If the WAV *is* present this is only a transient
            # (filter-chain restarting): keep targeting HeSuVi and let the
            # next watchdog tick relink, rather than flap onto physical.
            if not _HRIR_DEST.exists():
                phys = channel_destination(channel, data)
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
_HESUVI_OUTPUT_NAME_MEDIA = "effect_output.virtual-surround-7.1-hesuvi-media"
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
    """Return the external sink the Output channel should currently target.

    CHA-6: this used to just read the target baked into ``sonar-output-eq.conf``
    — cheap, and correct only as long as something rewrites that conf every
    time the ``external_output_device`` setting changes. Nothing does: not a
    ``SetSetting`` over D-Bus, a hand-edit, a config restore, a settings sync
    nor a package upgrade. The two had already diverged once on real use (the
    setting said the headset, the conf said the TV) with no user action and
    no message — the conf just quietly won until an unrelated repair pass
    regenerated it.

    The setting is now the single owner. :func:`_read_output_setting_snapshot`
    records which raw setting value the on-disk conf was actually built for,
    so comparing it against the CURRENT setting is a cheap YAML read — no
    pulsectl — and catches every write path at once, including the ones this
    module cannot intercept directly (a D-Bus ``SetSetting`` call lands in
    ``settings.py``, outside this module). Only on a genuine mismatch does
    this pay for the one pulsectl round-trip needed to resolve the setting
    and reconcile the conf; the log line names both sides so the jump is
    never silent again. Every following tick is back to the cheap path once
    the snapshot is refreshed.

    Returns an empty string when there is nothing to target (no external
    sink configured), which callers treat as "skip this hop".
    """
    conf_path = _CONF_DIR / "sonar-output-eq.conf"
    try:
        content = conf_path.read_text()
    except OSError:
        content = None

    conf_target = ""
    if content is not None:
        match = _CONF_TARGET_RE.search(content)
        conf_target = match.group(1) if match else ""

    current_setting = _read_external_output_setting()
    snapshot = _read_output_setting_snapshot()
    if current_setting == snapshot:
        return conf_target

    _log.warning(
        "external_output_device setting (%r) has diverged from the "
        "external output baked into sonar-output-eq.conf (%r, recorded for "
        "setting %r) — reconciling toward the setting (CHA-6)",
        current_setting, conf_target, snapshot,
    )
    resolved_target, channels, position = _resolve_external_output()
    _regenerate_eq_conf(
        "output", conf_path, "effect_input.sonar-output-eq",
        resolved_target, channels, position, owns_link=False, log=_log,
        reason="external_output_device setting changed",
    )
    return resolved_target


_output_fallback_active: str | None = None


def _note_output_fallback(absent_target: str | None) -> None:
    """Log the Output channel's fallback once per transition, not per tick.

    The watchdog runs every 5 s; an unplugged monitor would otherwise fill the
    journal with the same line for as long as it stays off, which is how a real
    fault becomes invisible.
    """
    global _output_fallback_active
    if absent_target == _output_fallback_active:
        return
    _output_fallback_active = absent_target
    if absent_target:
        _log.warning(
            "Output channel: configured sink '%s' is not in the graph — "
            "sending it to the headset meanwhile; the setting is unchanged and "
            "the channel returns there as soon as it comes back",
            absent_target,
        )
    else:
        _log.info("Output channel: configured sink is back — routing restored")


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
        ``{"chat": bool, "hesuvi": bool, "hesuvi_media": bool, "output": bool}``
        — only hops whose target is currently known (device attached / external
        sink configured) are included at all.
    """
    from arctis_sound_manager.pw_utils import ensure_loopback_link

    results: dict[str, bool] = {}

    chat_target = channel_destination("chat", data)
    if chat_target:
        results["chat"] = ensure_loopback_link(_CHAT_OUTPUT_NAME, chat_target, data=data)

    # Each channel's HeSuVi stage reaches that channel's OWN device: #169 gave
    # Media its own chain, and channel_destination decides where that chain
    # comes out. Sharing one destination is what made Media's device menu inert
    # and dragged it along with Game.
    # Derived, not listed: Aux was missing from the pair above, so picking a
    # device for it changed the saved setting and relinked nothing — the exact
    # inertness described for Media two comments up (#209).
    for _ch, _hes_node in ((c, _hesuvi_output_node(c)) for c in spatial_channels()):
        _dest = channel_destination(_ch, data)
        if not _dest:
            continue
        # "hesuvi" stays the game key so existing callers keep working; media
        # is additive. ensure_loopback_link already reports False when the node
        # is absent (media's stage only exists once it has been generated), so
        # no separate existence check is needed here.
        _key = "hesuvi" if _ch == "game" else "hesuvi_media"
        results[_key] = ensure_loopback_link(_hes_node, _dest, data=data)

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
        _note_output_fallback(None)
        results["output"] = ensure_loopback_link(
            _OUTPUT_EQ_OUTPUT_NAME, output_target, data=data
        )
    elif output_target:
        # The configured sink is gone — the monitor is off, the Bluetooth
        # speaker walked away, the dock was unplugged. Skipping the hop left
        # everything routed to the Output channel playing into a dead end,
        # silently and indefinitely, whenever the tray GUI was not running to
        # do the fallback itself (SD-1). Game/Chat/Media never had that gap:
        # channel_destination() falls back to the headset the moment the saved
        # device is absent, re-evaluated on every tick.
        #
        # The setting is deliberately NOT rewritten: the user's choice stays
        # the user's, so the channel returns to the external sink on its own as
        # soon as it comes back. This is a link-level fallback, not a decision.
        fallback = _get_physical_out_game()
        if fallback and _node_in_graph(data, fallback):
            _note_output_fallback(output_target)
            results["output"] = ensure_loopback_link(
                _OUTPUT_EQ_OUTPUT_NAME, fallback, data=data
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


def resolve_micro_input_source() -> str:
    """Return the source ``node.name`` that should feed the Sonar Micro EQ.

    Resolves the three shapes ``micro_input_source`` can take into a single
    answer: ``"__auto__"`` becomes the attached Arctis microphone, a concrete
    node name is returned as-is, and ``"__manual__"`` — along with auto mode
    with no headset attached — returns ``""``, meaning "ASM is not feeding the
    micro EQ". Callers that act on the mic chain (the capture-link enforcement
    below, the daemon claiming the default input) ask here first, so they can
    only ever agree about who owns the mic.
    """
    setting = _get_micro_input_source_setting()
    if setting == "__manual__":
        return ""
    if setting == "__auto__":
        return _get_physical_in()
    return setting


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

    # "" covers both "user has taken manual control (qpwgraph, …), don't touch
    # the link" and "auto mode with no device attached" — either way there is
    # nothing to enforce, and the watchdog retries on the next tick.
    source = resolve_micro_input_source()
    if not source:
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
    channel: str = "game",
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
        ``_CONF_DIR / _hesuvi_conf_name(channel)``.
    channel:
        ``"game"`` (default) writes the historical, un-suffixed chain
        (``effect_input/output.virtual-surround-7.1-hesuvi``,
        ``sink-virtual-surround-7.1-hesuvi.conf``) so an existing Game chain is
        byte-identical. ``"media"`` writes a parallel ``…-hesuvi-media`` chain
        so the two channels carry independent Immersion/Distance (issue #169).
        Both chains target the same physical GAME output.

    Returns
    -------
    str
        The generated config text (also written to *output_path*).
    """
    if not _device_attached():
        _log.warning("Skipping HeSuVi config generation: no Arctis device attached.")
        return ""

    _hes_dest = channel_destination(channel)
    if output_path is None:
        output_path = _CONF_DIR / _hesuvi_conf_name(channel)
    if channel == "game":
        # Remove any static copy from pipewire.conf.d to avoid duplicate node name conflict.
        # install.sh places the static HeSuVi config there; ASM's dynamic version (here)
        # supersedes it when Sonar mode is active. Having both causes the game channel to
        # go silent because PipeWire and filter-chain both try to register the same node name.
        # Only the Game chain ever had a static counterpart — Media is ASM-only.
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

    # Per-channel identity (issue #169). Game keeps the historical names so its
    # chain is byte-identical to pre-#169 installs; Media gets the -media suffix.
    _in_node = _hesuvi_input_node(channel)
    _out_node = _hesuvi_output_node(channel)
    _sink_desc = (
        "Virtual Surround Sink"
        if channel == "game"
        else f"Virtual Surround Sink ({channel.capitalize()})"
    )

    # All passive HeSuVi chains (Game, Media, Aux) need node.pause-on-idle =
    # false so the convolver stays warm across stream gaps; without it,
    # WirePlumber suspends the chain after a few seconds of silence and audio
    # disappears until something else wakes it (issue #223, issue #NNN).  Game
    # was not included in the original #223 fix because the report only
    # covered track-change pops in Media, but the underlying suspension logic
    # applies equally to every passive chain (see also the EQ generator).
    _pause_on_idle_line = (
        '        node.pause-on-idle = false\n'
    ) if channel != "output" else ''

    text = f"""\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
{_conf_version_header()}
# HeSuVi 7.1 Virtual Surround  |  Immersion: {immersion_pct}%  |  Distance: {distance_pct}%
context.modules = [
  {{ name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
      node.description = "{_sink_desc}"
      media.name       = "{_sink_desc}"
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
        node.name      = "{_in_node}"
        media.class    = Audio/Sink/Internal
        audio.channels = 8
        audio.position = [ FL FR FC LFE RL RR SL SR ]
      }}
      playback.props = {{
        node.name          = "{_out_node}"
        node.target        = "{_hes_dest}"
        target.object      = "{_hes_dest}"
        node.dont-fallback = true
        node.linger        = true
        node.passive       = true
{_pause_on_idle_line}        audio.channels     = 2
        audio.position     = [ FL FR ]
      }}
    }}
  }}
]
"""

    _write_conf(output_path, text)
    return text
