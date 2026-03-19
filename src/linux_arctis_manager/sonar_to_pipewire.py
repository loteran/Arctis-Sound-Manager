"""
sonar_to_pipewire.py — Generate PipeWire filter-chain configs for Sonar EQ channels.

One config per channel (game / chat / micro).  Each config inserts a chain of
biquad nodes between the virtual capture sink and its playback target.

Routing:
  game  → effect_input.virtual-surround-7.1-hesuvi  (keeps 7.1 HeSuVi processing)
  chat  → alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo
  micro → virtual source backed by the physical mic input  (Source/Virtual)
"""
from __future__ import annotations

from pathlib import Path

from linux_arctis_manager.gui.eq_curve_widget import EqBand, PW_LABEL

# ── Constants ─────────────────────────────────────────────────────────────────

_PHYSICAL_OUT = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
_PHYSICAL_IN  = "alsa_input.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.mono-fallback"
_SURROUND     = "effect_input.virtual-surround-7.1-hesuvi"

def _game_target(spatial_audio: bool) -> str:
    """Return the playback target for the game EQ channel."""
    return _SURROUND if spatial_audio else _PHYSICAL_OUT

_CHANNEL_TARGET: dict[str, str] = {
    "game":  _SURROUND,   # overridden at call time via spatial_audio param
    "chat":  _PHYSICAL_OUT,
    # micro handled separately (Source/Virtual)
}

# Macro slider filter parameters (estimations from visual captures)
_MACRO_PARAMS = {
    "basses": {"freq": 80.0,   "q": 0.50},
    "voix":   {"freq": 2000.0, "q": 0.60},
    "aigus":  {"freq": 9000.0, "q": 0.80},
}

_CONF_DIR = Path.home() / ".config" / "pipewire" / "filter-chain.conf.d"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _node_block(name: str, label: str, freq: float, q: float, gain: float) -> str:
    return (
        f"                    {{ type = builtin  name = {name}  label = {label}\n"
        f"                      control = {{ Freq = {freq}  Q = {q}  Gain = {gain} }} }}"
    )


def _link(out: str, inp: str) -> str:
    return f'                    {{ output = "{out}:Out"  input = "{inp}:In" }}'


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
) -> str:
    """
    Build and optionally write a filter-chain .conf for a game/chat EQ channel.

    Returns the config text.  If output_path is None, uses the default location
    (~/.config/pipewire/filter-chain.conf.d/sonar-<channel>-eq.conf).
    """
    if channel not in ("game", "chat"):
        raise ValueError(f"channel must be 'game' or 'chat', got {channel!r}")

    target = _game_target(spatial_audio) if channel == "game" else _CHANNEL_TARGET[channel]
    sink_name = f"effect_input.sonar-{channel}-eq"

    if output_path is None:
        output_path = _CONF_DIR / f"sonar-{channel}-eq.conf"

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
        text = _bypass_conf(sink_name, target)
        _write_conf(output_path, text)
        return text

    # Build node blocks and link chain (L + R for each filter)
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

    # Optional boost gain node at the end of the chain.
    # bq_highshelf at 10 Hz is used instead of label=gain — gain builtin is unavailable
    # in some PipeWire 1.6.x builds. A highshelf at 10 Hz is flat across all audible
    # frequencies (20 Hz – 20 kHz), making it a transparent master gain substitute.
    import math
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

    nodes_text   = "\n".join(node_lines)
    links_text   = "\n".join(link_lines)
    inputs_text  = f'"{names_L[0]}:In"  "{names_R[0]}:In"'
    outputs_text = f'"{last_L}:Out"  "{last_R}:Out"'

    text = f"""\
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
        audio.position = [ FL FR ]
      }}
      playback.props = {{
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 2
        audio.position = [ FL FR ]
      }}
    }}
  }}
]
"""
    _write_conf(output_path, text)
    return text


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

    Creates a virtual Source/Virtual node backed by the physical mic input.
    """
    if output_path is None:
        output_path = _CONF_DIR / "sonar-micro-eq.conf"

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

    import math
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
        node.name      = "{_PHYSICAL_IN}"
        node.passive   = true
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-micro-eq"
        media.class    = Audio/Source/Virtual
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

def _bypass_conf(sink_name: str, target: str) -> str:
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
        audio.position = [ FL FR ]
      }}
      playback.props = {{
        node.target    = "{target}"
        node.passive   = true
        audio.channels = 2
        audio.position = [ FL FR ]
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
        node.name      = "{_PHYSICAL_IN}"
        node.passive   = true
        audio.channels = 1
        audio.position = [ MONO ]
      }}
      playback.props = {{
        node.name      = "effect_output.sonar-micro-eq"
        media.class    = Audio/Source/Virtual
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
    """Generate config and restart filter-chain.  channel = 'game'|'chat'|'micro'."""
    import subprocess

    if channel == "micro":
        generate_sonar_micro_conf(bands, basses_db, voix_db, aigus_db)
    else:
        generate_sonar_eq_conf(channel, bands, basses_db, voix_db, aigus_db)

    subprocess.run(["systemctl", "--user", "restart", "filter-chain"], check=False, timeout=15)
