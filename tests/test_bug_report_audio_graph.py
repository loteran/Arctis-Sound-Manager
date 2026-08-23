# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the audio-graph section of the bug report.

Why this section exists, and why it is worth locking down: issue #180 (a
headset that never reaches its inactivity timeout) took two wrong diagnoses,
both because the report showed sink names without node *states* and without
*links*. "Something keeps the device awake" is not actionable; "the HeSuVi
output holds it" is. These tests pin the three properties that made the
difference, so a later refactor of the report cannot quietly drop them again.
"""
from __future__ import annotations

from arctis_sound_manager.bug_reporter import (
    _alsa_pcm_state,
    _arctis_pw_nodes,
    _audio_graph,
    _is_asm_node,
    _pw_clients,
)


def _node(nid: int, name: str, mclass: str, state: str, **props) -> dict:
    return {
        'id': nid,
        'type': 'PipeWire:Interface:Node',
        'info': {'state': state, 'props': {'node.name': name, 'media.class': mclass, **props}},
    }


def _link(lid: int, out_id: int, in_id: int, state: str = 'active') -> dict:
    return {
        'id': lid,
        'type': 'PipeWire:Interface:Link',
        'info': {'output-node-id': out_id, 'input-node-id': in_id, 'state': state},
    }


def _graph() -> list[dict]:
    """The #180 topology: everything idle except the chain feeding the device."""
    return [
        _node(66, 'alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo', 'Audio/Sink', 'running'),
        _node(72, 'alsa_output.pci-0000_00_1f.3.analog-stereo', 'Audio/Sink', 'running'),
        _node(241, 'Arctis_Game', 'Audio/Sink', 'idle'),
        _node(242, 'Arctis_Game_sink_out', 'Stream/Output/Audio', 'idle',
              **{'target.object': 'effect_input.sonar-game-eq', 'node.pause-on-idle': 'true'}),
        _node(94, 'effect_input.sonar-game-eq', 'Audio/Sink/Internal', 'idle'),
        _node(95, 'effect_output.sonar-game-eq', 'Stream/Output/Audio', 'running',
              **{'node.linger': 'true'}),
        _node(103, 'effect_output.virtual-surround-7.1-hesuvi', 'Stream/Output/Audio', 'running'),
        _node(900, 'some-video-node', 'Video/Source', 'idle'),
        _link(500, 242, 94),
        _link(501, 95, 103),
        _link(502, 103, 66),
    ]


# ── node states ──────────────────────────────────────────────────────────────

def test_node_state_is_reported():
    """The single most important field, and the one that was missing."""
    text = _audio_graph(_graph())
    assert 'running' in text and 'idle' in text
    device_line = next(l for l in text.splitlines()
                       if 'alsa_output.usb-SteelSeries' in l)
    assert 'running' in device_line


def test_stream_output_nodes_are_not_dropped():
    """`Stream/Output/Audio` is the class of every node that feeds a device.

    A `media.class.startswith("Audio")` filter silently drops all of them,
    which would omit exactly the nodes this section exists to show.
    """
    text = _audio_graph(_graph())
    for name in ('Arctis_Game_sink_out', 'effect_output.sonar-game-eq',
                 'effect_output.virtual-surround-7.1-hesuvi'):
        assert name in text, f'{name} missing from the graph section'


def test_non_audio_nodes_are_excluded():
    assert 'some-video-node' not in _audio_graph(_graph())


# ── links ────────────────────────────────────────────────────────────────────

def test_links_name_both_ends():
    """Knowing a device is held is useless without knowing by what."""
    text = _audio_graph(_graph())
    assert ('effect_output.virtual-surround-7.1-hesuvi  ->  '
            'alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo') in text


def test_empty_graph_says_so_rather_than_looking_healthy():
    text = _audio_graph([])
    assert 'no audio nodes' in text
    assert 'no links' in text


def test_unavailable_dump_is_explicit():
    """Absent data must never be indistinguishable from an empty graph."""
    assert 'unavailable' in _audio_graph(None)


# ── ASM ownership marker ─────────────────────────────────────────────────────

def test_physical_device_is_not_flagged_as_an_asm_node():
    """The headset's own sink is named `alsa_output.usb-SteelSeries_Arctis_7…`
    and matches the `Arctis_` fragment. Flagging it as ours would confuse the
    one comparison this section is for."""
    assert not _is_asm_node('alsa_output.usb-SteelSeries_Arctis_7_-00.analog-stereo')
    assert not _is_asm_node('bluez_output.AA_BB_CC.a2dp-sink')
    assert _is_asm_node('Arctis_Game')
    assert _is_asm_node('effect_output.virtual-surround-7.1-hesuvi')

    text = _audio_graph(_graph())
    device_line = next(l for l in text.splitlines() if 'alsa_output.usb-SteelSeries' in l)
    assert '<-- ASM' not in device_line
    asm_line = next(l for l in text.splitlines() if l.rstrip().endswith('Arctis_Game <-- ASM'))
    assert asm_line


# ── routing props ────────────────────────────────────────────────────────────

def test_routing_props_shown_for_asm_nodes():
    """Whether the on-disk config reached the running graph (#100, #102, #180)."""
    text = _audio_graph(_graph())
    sink_out = next(l for l in text.splitlines() if 'Arctis_Game_sink_out' in l)
    assert 'target.object=effect_input.sonar-game-eq' in sink_out
    assert 'node.pause-on-idle=true' in sink_out
    # The device carries plenty of props too; printing them would bury the rest.
    device_line = next(l for l in text.splitlines() if 'alsa_output.pci-' in l)
    assert '[' not in device_line


def test_application_stream_pin_is_visible():
    """An app pinning itself to a foreign device is the #185 failure mode.

    A soundboard feeding a virtual microphone gets dragged onto an Arctis
    channel, which breaks that app's feature rather than merely moving its
    sound. Diagnosing it needs the pin, so application streams show their
    target even though they are not ASM nodes.
    """
    graph = _graph() + [
        _node(700, 'soundboard-mic-feed', 'Stream/Output/Audio', 'running',
              **{'application.name': 'SoundDeck', 'target.object': 'SoundDeck_virtmic'}),
    ]
    line = next(l for l in _audio_graph(graph).splitlines() if 'soundboard-mic-feed' in l)
    assert 'app=SoundDeck' in line
    assert 'target.object=SoundDeck_virtmic' in line
    assert '<-- ASM' not in line


# ── Arctis node list keeps its state field ───────────────────────────────────

def test_arctis_node_list_includes_state():
    text = _arctis_pw_nodes(_graph())
    assert 'state=' in text
    assert 'state=running' in text


# ── kernel view ──────────────────────────────────────────────────────────────

def test_alsa_pcm_state_never_raises():
    """Runs on machines with no /proc/asound at all (containers, CI)."""
    result = _alsa_pcm_state()
    assert isinstance(result, str) and result


# ── link permissions (#181) ──────────────────────────────────────────────────

def _client(cid: int, access: str, binary: str, **extra) -> dict:
    return {
        'id': cid,
        'type': 'PipeWire:Interface:Client',
        'info': {'props': {'pipewire.access': access,
                           'application.process.binary': binary, **extra}},
    }


def test_node_owner_is_reported():
    """PipeWire refuses a link when the client owning either end cannot see
    the other node, so the owner is half the answer to "why was this refused"
    (#181). A node with no owner is exempt from that check entirely, which
    makes the field's absence meaningful too."""
    graph = _graph() + [
        _node(300, 'effect_input.sonar-chat-eq', 'Audio/Sink/Internal', 'idle',
              **{'client.id': '155'}),
    ]
    line = next(l for l in _audio_graph(graph).splitlines()
                if 'effect_input.sonar-chat-eq' in l)
    assert 'client.id=155' in line
    # The daemon-owned node in _graph() has none, and must not grow a fake one.
    other = next(l for l in _audio_graph(graph).splitlines()
                 if 'effect_input.sonar-game-eq' in l)
    assert 'client.id' not in other


def test_client_table_shows_access_level():
    """The other half: what each owning client was actually granted."""
    text = _pw_clients([
        _client(112, 'default', 'pw-loopback'),
        _client(155, 'unrestricted', 'pipewire'),
    ])
    assert 'access=default' in text and 'pw-loopback' in text
    assert 'access=unrestricted' in text


def test_security_context_is_surfaced():
    """A client behind a security context is restricted, which on its own
    explains a refused link. It must not be silently dropped."""
    text = _pw_clients([
        _client(200, 'flatpak', 'someapp', **{'pipewire.sec.engine': 'org.flatpak'}),
    ])
    assert 'pipewire.sec.engine=org.flatpak' in text


def test_client_table_degrades_without_a_dump():
    assert 'unavailable' in _pw_clients(None)
    assert 'no clients' in _pw_clients([])


# ── duplicate node names (#205) ──────────────────────────────────────────────


def _doubled_graph() -> list[dict]:
    """Every filter loaded twice, as when a second filter-chain instance runs.

    Reduced from the #205 report: the daemon and filter-chain.service each held
    a complete copy of the EQ graph, so two nodes answered to every name ASM
    routes by.
    """
    return [
        _node(363, 'Arctis_Media', 'Audio/Sink', 'running'),
        _node(357, 'Arctis_Media_sink_out', 'Stream/Output/Audio', 'suspended',
              **{'target.object': 'effect_input.sonar-media-eq', 'client.id': '311'}),
        _node(61, 'effect_input.sonar-media-eq', 'Audio/Sink/Internal', 'suspended',
              **{'client.id': '43'}),
        _node(84, 'effect_input.sonar-media-eq', 'Audio/Sink/Internal', 'suspended',
              **{'client.id': '69'}),
        _node(62, 'effect_output.sonar-media-eq', 'Stream/Output/Audio', 'suspended',
              **{'client.id': '43'}),
        _node(85, 'effect_output.sonar-media-eq', 'Stream/Output/Audio', 'suspended',
              **{'client.id': '69'}),
    ]


def test_a_name_carried_by_two_nodes_is_called_out():
    """The finding that took a manual read of the whole graph to spot on #205.

    ASM routes by name, so a name owned by two nodes makes every one of those
    routes ambiguous — and the loopback that cannot resolve it links to nothing.
    What the user sees is a channel whose meters move while the headset stays
    silent, which reads as a headset fault rather than a graph fault.
    """
    out = _audio_graph(_doubled_graph())

    assert 'DUPLICATE NODE NAMES' in out
    assert 'effect_input.sonar-media-eq' in out.split('-- audio nodes')[0]
    # Both ids, so the reader can cross-reference which client owns each copy.
    assert '61' in out.split('-- audio nodes')[0]
    assert '84' in out.split('-- audio nodes')[0]


def test_a_healthy_graph_says_nothing_about_duplicates():
    """A warning that fires on every report is a warning nobody reads."""
    assert 'DUPLICATE' not in _audio_graph(_graph())


def test_two_windows_of_one_app_are_not_a_duplicate():
    """Application streams share a node.name all the time — two browser windows
    are both "librewolf". Flagging those would bury the one duplicate that
    matters under noise, so only routing targets are counted."""
    graph = _graph() + [
        _node(801, 'librewolf', 'Stream/Output/Audio', 'running'),
        _node(802, 'librewolf', 'Stream/Output/Audio', 'running'),
    ]

    assert 'DUPLICATE' not in _audio_graph(graph)
