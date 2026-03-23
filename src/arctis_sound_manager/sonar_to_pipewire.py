"""
sonar_to_pipewire.py — Generate PipeWire filter-chain configs for Sonar EQ channels.

One config per channel (game / chat / micro).  Each config inserts a chain of
biquad nodes between the virtual capture sink and its playback target.

Routing:
  game  → effect_input.virtual-surround-7.1-hesuvi  (8ch 7.1 → HeSuVi virtualisation)
  chat  → alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo  (2ch stereo)
  micro → virtual source backed by the physical mic input  (Source/Virtual, 1ch mono)

All configs are written to filter-chain.conf.d/ and loaded by the filter-chain service.
Restarting only filter-chain (not pipewire) preserves active audio streams.
"""
from __future__ import annotations

from pathlib import Path

from arctis_sound_manager.gui.eq_curve_widget import EqBand, PW_LABEL

# ── Constants ─────────────────────────────────────────────────────────────────

_PHYSICAL_OUT = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
_PHYSICAL_IN  = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"
_SURROUND     = "effect_input.virtual-surround-7.1-hesuvi"

_CHANNEL_TARGET: dict[str, str] = {
    "game":  _SURROUND,       # 8ch → HeSuVi 7.1 virtualisation → 2ch → ALSA
    "chat":  _PHYSICAL_OUT,   # 2ch stereo direct
}

_CHANNEL_CHANNELS: dict[str, int] = {
    "game": 8,
    "chat": 2,
}

_CHANNEL_POSITION: dict[str, str] = {
    "game": "FL FR FC LFE RL RR SL SR",
    "chat": "FL FR",
}

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


def _sc4m_node(name: str, preset: dict, level: float) -> str:
    """Generate a LADSPA SC4M compressor node.

    *level* (0-100) scales ratio from 1.0 to the preset's max and adjusts
    makeup gain proportionally.
    """
    t = max(0.0, min(100.0, level)) / 100.0
    ratio  = 1.0 + (preset["ratio"] - 1.0) * t
    makeup = preset["makeup"] * t
    return (
        f'                    {{ type = ladspa  name = {name}  plugin = sc4m_1916  label = sc4m\n'
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


# ── Config generator — game / chat ────────────────────────────────────────────

def generate_sonar_eq_conf(
    channel: str,
    bands: list[EqBand],
    basses_db: float,
    voix_db: float,
    aigus_db: float,
    output_path: Path | None = None,
    spatial_audio: bool = True,
    boost_db: float = 0.0,
    smart_volume: dict | None = None,
) -> str:
    """
    Build and optionally write a filter-chain .conf for a game/chat EQ channel.

    Game channel: 8ch 7.1, single filter nodes (PipeWire auto-duplicates per channel),
    no explicit inputs/outputs, targets HeSuVi virtual surround.
    Chat channel: 2ch stereo, L/R filter pairs, explicit inputs/outputs, targets ALSA.
    """
    if channel not in ("game", "chat"):
        raise ValueError(f"channel must be 'game' or 'chat', got {channel!r}")

    target = _CHANNEL_TARGET[channel]
    sink_name = f"effect_input.sonar-{channel}-eq"
    channels = _CHANNEL_CHANNELS[channel]
    position = _CHANNEL_POSITION[channel]

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
        text = _bypass_conf(sink_name, target, channels, position)
        _write_conf(output_path, text)
        return text

    if channels == 8:
        text = _active_conf_8ch(channel, sink_name, target, position,
                                all_filters, active_bands, macro_bands,
                                boost_db, smart_volume)
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
) -> str:
    """8ch config: single filter nodes, PipeWire auto-duplicates per channel."""
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
        mode = smart_volume.get("loudness", "balanced")
        level = smart_volume.get("level", 50)
        preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
        node_lines.append(_sc4m_node("compressor", preset, level))
        link_lines.append(_link_to_ladspa(last_name, "compressor"))

    nodes_text = "\n".join(node_lines)
    links_block = ""
    if link_lines:
        links_text = "\n".join(link_lines)
        links_block = f"""        links = [
{links_text}
        ]"""

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
        node.name      = "{sink_name}"
        media.class    = Audio/Sink
        audio.channels = 8
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-{channel}-eq"
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 8
        audio.position = [ {position} ]
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

    if smart_volume and smart_volume.get("enabled"):
        mode = smart_volume.get("loudness", "balanced")
        level = smart_volume.get("level", 50)
        preset = _SMART_PRESETS.get(mode, _SMART_PRESETS["balanced"])
        node_lines.append(_sc4m_node("comp_L", preset, level))
        node_lines.append(_sc4m_node("comp_R", preset, level))
        link_lines.append(_link_to_ladspa(last_L, "comp_L"))
        link_lines.append(_link_to_ladspa(last_R, "comp_R"))
        last_L, last_R = "comp_L", "comp_R"

    nodes_text   = "\n".join(node_lines)
    links_text   = "\n".join(link_lines)
    inputs_text  = f'"{names_L[0]}:In"  "{names_R[0]}:In"'
    # LADSPA nodes use "Output" port name, builtins use "Out"
    is_ladspa = smart_volume and smart_volume.get("enabled")
    out_port = "Output" if is_ladspa else "Out"
    outputs_text = f'"{last_L}:{out_port}"  "{last_R}:{out_port}"'

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
        node.name      = "{sink_name}"
        media.class    = Audio/Sink
        audio.channels = 2
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-{channel}-eq"
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 2
        audio.position = [ {position} ]
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
) -> str:
    """
    Build and optionally write a filter-chain .conf for the microphone EQ.

    Creates a virtual Audio/Source node backed by the physical mic input.
    Pattern: capture side is passive (faces hardware), playback side has
    media.class = Audio/Source (faces applications).
    """
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

    if not all_filters:
        text = _bypass_micro_conf()
        _write_conf(output_path, text)
        return text

    node_lines: list[str] = []
    link_lines: list[str] = []
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

    nodes_text  = "\n".join(node_lines)
    links_text  = "\n".join(link_lines)

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
        outputs = [ "{last_node}:Out" ]
      }}
      capture.props = {{
        node.name      = "effect_input.sonar-micro-eq"
        node.passive   = true
        target.object  = "{_PHYSICAL_IN}"
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-micro-eq"
        media.class    = Audio/Source
        audio.channels = 1
        audio.position = [ MONO ]
      }}
    }}
  }}
]
"""
    _write_conf(output_path, text)
    return text


# ── Bypass / passthrough ──────────────────────────────────────────────────────

def _bypass_conf(sink_name: str, target: str, channels: int, position: str) -> str:
    """Generate a bypass config. 8ch uses auto-dup (no inputs/outputs), 2ch uses L/R."""
    if channels == 8:
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
        node.name      = "{sink_name}"
        media.class    = Audio/Sink
        audio.channels = 8
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name      = "{sink_name.replace('effect_input.', 'effect_output.')}"
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 8
        audio.position = [ {position} ]
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
        node.name      = "{sink_name}"
        media.class    = Audio/Sink
        audio.channels = 2
        audio.position = [ {position} ]
      }}
      playback.props = {{
        node.name      = "{sink_name.replace('effect_input.', 'effect_output.')}"
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 2
        audio.position = [ {position} ]
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
        target.object  = "{_PHYSICAL_IN}"
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-micro-eq"
        media.class    = Audio/Source
        audio.channels = 1
        audio.position = [ MONO ]
      }}
    }}
  }}
]
"""


# ── File I/O ──────────────────────────────────────────────────────────────────

def _write_conf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def apply_sonar_channel(
    channel: str,
    bands: list[EqBand],
    basses_db: float = 0.0,
    voix_db: float = 0.0,
    aigus_db: float = 0.0,
) -> None:
    """Generate config and restart PipeWire to reload filter-chain modules.

    All Sonar configs go to pipewire.conf.d/ and are loaded by the main
    PipeWire daemon.  This requires a PipeWire restart but avoids the
    WirePlumber deadlock caused by the separate filter-chain service.
    """
    import subprocess

    if channel == "micro":
        generate_sonar_micro_conf(bands, basses_db, voix_db, aigus_db)
    else:
        generate_sonar_eq_conf(channel, bands, basses_db, voix_db, aigus_db)

    subprocess.run(["systemctl", "--user", "restart", "pipewire"], check=False, timeout=15)


def check_and_fix_stale_configs() -> bool:
    """Detect and fix stale Sonar configs.

    Checks for:
    1. Configs using the broken ``label = gain`` builtin (PipeWire 1.6.x).
    2. Sonar configs left in ``pipewire.conf.d/`` (must be in
       ``filter-chain.conf.d/`` — restarting pipewire is too disruptive).
    3. Configs with wrong channel count (2ch game should be 8ch).

    Returns True if any config was regenerated or cleaned.
    """
    import logging
    log = logging.getLogger(__name__)
    fixed = False
    bad_dir = _CONF_DIR.parent / "pipewire.conf.d"

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

            # Game EQ must be 8ch for HeSuVi virtual surround
            if name == "sonar-game-eq.conf" and "audio.channels = 2" in content:
                log.warning("Stale config (%s uses 2ch, should be 8ch), regenerating", name)
                needs_regen = True

            if needs_regen:
                channel = name.replace("sonar-", "").replace("-eq.conf", "")
                sink_name = f"effect_input.sonar-{channel}-eq"
                target = _CHANNEL_TARGET.get(channel, _PHYSICAL_OUT)
                channels = _CHANNEL_CHANNELS.get(channel, 2)
                position = _CHANNEL_POSITION.get(channel, "FL FR")
                _write_conf(path, _bypass_conf(sink_name, target, channels, position))
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

    return fixed
