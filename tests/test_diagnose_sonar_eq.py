# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for scripts/diagnose-sonar-eq.py's parsing logic — the part with
real bugs to have.

Issue #181 ("I clicked presets and nothing changes") took a second report
(#203) root-caused with measurements before the actual break points were
known: a stale filter-chain unit missing LADSPA_PATH, a LADSPA plugin that
never loads, an Audio/Sink/Internal node PipeWire refuses to link into
cross-process, and a bypass conf that never had a preset written into it.
This diagnostic exists to answer "which one, on this box" without anyone
having to re-derive #203's investigation by hand.

These tests cover the pure functions only: extracting `plugin =` references
and `label =` node kinds from a generated filter-chain conf, classifying
which of those labels are real filters vs. a bypass's `copy` passthrough, and
parsing `systemctl show` output to decide whether the unit actually in force
sets LADSPA_PATH. None of them touch a subprocess, the filesystem beyond
what a test itself writes, or the live PipeWire graph — the live-probing
functions built on top of these are exercised by hand against fixture
directories (see the task notes), not here, matching the suite's rule
against reaching into the developer's own audio server (see conftest.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "diagnose-sonar-eq.py"

spec = importlib.util.spec_from_file_location("diagnose_sonar_eq", SCRIPT)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


# ---------------------------------------------------------------------------
# extract_ladspa_plugin_refs — must not require `type = ladspa` on the same
# line as `plugin =`.
# ---------------------------------------------------------------------------

def test_extracts_a_same_line_plugin_reference():
    text = (
        '{ type = ladspa  name = plate_L  plugin = /usr/lib64/ladspa/plate_1423.so'
        '  label = plate }'
    )
    assert diag.extract_ladspa_plugin_refs(text) == ["/usr/lib64/ladspa/plate_1423.so"]


def test_extracts_a_plugin_reference_on_a_continuation_line():
    """DeepFilterNet/RNNoise/the compressor/the noise gate all put `plugin =`
    on the line *after* `type = ladspa` (sonar_to_pipewire.py's micro-EQ
    generator) — a same-line-only scan misses these entirely, which is
    exactly what production's own `_conf_has_bare_ladspa` helper does (it
    exists for a narrower purpose, repair-triggering, so the gap there is not
    itself a bug); this diagnostic must not repeat that gap."""
    text = (
        "                    { type = ladspa  name = dfn\n"
        "                      plugin = /home/deck/.ladspa/libdeep_filter_ladspa.so"
        "  label = deep_filter_mono\n"
        '                      control = { "Attenuation Limit (dB)" = 50.0 } }'
    )
    assert diag.extract_ladspa_plugin_refs(text) == [
        "/home/deck/.ladspa/libdeep_filter_ladspa.so"
    ]


def test_extracts_a_bare_plugin_name():
    text = "{ type = ladspa  name = plate_L  plugin = plate_1423  label = plate }"
    assert diag.extract_ladspa_plugin_refs(text) == ["plate_1423"]


def test_no_plugin_lines_yields_empty_list():
    assert diag.extract_ladspa_plugin_refs("nodes = [ { type = builtin label = copy } ]") == []


def test_extracts_every_plugin_reference_in_a_multi_node_conf():
    text = (
        "{ type = ladspa name = plate_L plugin = /a/plate_1423.so label = plate }\n"
        "{ type = ladspa name = plate_R plugin = /a/plate_1423.so label = plate }\n"
        "{ type = ladspa name = limiter plugin = /a/fast_lookahead_limiter_1913.so label = fastLookaheadLimiter }\n"
    )
    assert diag.extract_ladspa_plugin_refs(text) == [
        "/a/plate_1423.so", "/a/plate_1423.so", "/a/fast_lookahead_limiter_1913.so",
    ]


def test_ladspa_ref_is_absolute():
    assert diag.ladspa_ref_is_absolute("/usr/lib64/ladspa/plate_1423.so") is True
    assert diag.ladspa_ref_is_absolute("plate_1423") is False


# ---------------------------------------------------------------------------
# extract_node_labels / classify_filter_nodes / is_bypass_conf
# ---------------------------------------------------------------------------

_REAL_GAME_CONF_EXCERPT = """\
nodes = [
            { type = builtin  name = bq0  label = bq_peaking
              control = { Freq = 50.0  Q = 2.0  Gain = 6.0 } }
            { type = builtin  name = bq1  label = bq_peaking
              control = { Freq = 70.0  Q = 1.0  Gain = 0.0 } }
            { type = builtin  name = boost  label = bq_highshelf
              control = { Freq = 10.0  Q = 0.7071  Gain = 0.0 } }
]
"""

_BYPASS_STEREO_CONF = """\
nodes = [
            { type = builtin  name = copy_L  label = copy }
            { type = builtin  name = copy_R  label = copy }
]
inputs  = [ "copy_L:In"  "copy_R:In" ]
outputs = [ "copy_L:Out" "copy_R:Out" ]
"""

_BYPASS_MULTICHANNEL_CONF = """\
nodes = [
            { type = builtin  name = copy  label = copy }
]
"""


def test_extract_node_labels_real_conf():
    assert diag.extract_node_labels(_REAL_GAME_CONF_EXCERPT) == [
        "bq_peaking", "bq_peaking", "bq_highshelf",
    ]


def test_extract_node_labels_bypass_conf():
    assert diag.extract_node_labels(_BYPASS_STEREO_CONF) == ["copy", "copy"]


def test_classify_filter_nodes_all_real():
    real, passthrough = diag.classify_filter_nodes(["bq_peaking", "bq_peaking", "bq_highshelf"])
    assert (real, passthrough) == (3, 0)


def test_classify_filter_nodes_all_passthrough():
    real, passthrough = diag.classify_filter_nodes(["copy", "copy"])
    assert (real, passthrough) == (0, 2)


def test_classify_filter_nodes_a_flat_eq_is_still_real_filters():
    """A macro slider at Gain=0.0 is still a real bq_peaking node (Phase 1,
    issue #100/#88) — only `copy` is a passthrough. A conf full of
    zero-gain filters must not be classified as a bypass."""
    real, passthrough = diag.classify_filter_nodes(["bq_peaking"] * 22)
    assert (real, passthrough) == (22, 0)


def test_is_bypass_conf_true_for_stereo_bypass():
    assert diag.is_bypass_conf(_BYPASS_STEREO_CONF) is True


def test_is_bypass_conf_true_for_multichannel_bypass():
    assert diag.is_bypass_conf(_BYPASS_MULTICHANNEL_CONF) is True


def test_is_bypass_conf_false_for_a_real_eq():
    assert diag.is_bypass_conf(_REAL_GAME_CONF_EXCERPT) is False


def test_is_bypass_conf_false_when_no_nodes_found_at_all():
    """An empty/unparseable conf must not be misreported as 'bypass' — that
    would silently overstate confidence about a file this diagnostic
    couldn't actually read the shape of."""
    assert diag.is_bypass_conf("garbage, not a filter-chain conf") is False


# ---------------------------------------------------------------------------
# systemctl show parsing / LADSPA_PATH extraction / unit-file resolution
# ---------------------------------------------------------------------------

def test_parse_systemctl_show_basic():
    output = (
        "FragmentPath=/usr/lib/systemd/user/filter-chain.service\n"
        "DropInPaths=\n"
        "ActiveState=active\n"
    )
    parsed = diag.parse_systemctl_show(output)
    assert parsed == {
        "FragmentPath": "/usr/lib/systemd/user/filter-chain.service",
        "DropInPaths": "",
        "ActiveState": "active",
    }


def test_parse_systemctl_show_ignores_lines_without_equals():
    assert diag.parse_systemctl_show("no equals sign here\nActiveState=active\n") == {
        "ActiveState": "active",
    }


def test_extract_ladspa_path_present():
    env = "LADSPA_PATH=/home/deck/.ladspa:/usr/lib64/ladspa:/usr/lib/ladspa:/usr/lib"
    assert diag.extract_ladspa_path_from_environment(env) == (
        "/home/deck/.ladspa:/usr/lib64/ladspa:/usr/lib/ladspa:/usr/lib"
    )


def test_extract_ladspa_path_among_other_assignments():
    env = "MALLOC_ARENA_MAX=1 LADSPA_PATH=/a:/b OTHER=x"
    assert diag.extract_ladspa_path_from_environment(env) == "/a:/b"


def test_extract_ladspa_path_absent():
    assert diag.extract_ladspa_path_from_environment("MALLOC_ARENA_MAX=1") is None


def test_extract_ladspa_path_empty_string():
    assert diag.extract_ladspa_path_from_environment("") is None


def test_describe_filter_chain_unit_packaged_with_ladspa_path():
    props = {
        "FragmentPath": "/usr/lib/systemd/user/filter-chain.service",
        "DropInPaths": "",
        "Environment": "LADSPA_PATH=/usr/lib64/ladspa",
        "ActiveState": "active",
        "LoadState": "loaded",
    }
    info = diag.describe_filter_chain_unit(props, home="/home/deck")
    assert info["found"] is True
    assert info["sets_ladspa_path"] is True
    assert info["ladspa_path"] == "/usr/lib64/ladspa"
    assert info["is_home_copy"] is False


def test_describe_filter_chain_unit_stale_home_copy_without_ladspa_path():
    """The exact #203/PKG-3 shape: a copy under ~/.config/systemd/user
    outranks the packaged unit and can predate the v1.4.5 LADSPA_PATH fix."""
    props = {
        "FragmentPath": "/home/deck/.config/systemd/user/filter-chain.service",
        "DropInPaths": "",
        "Environment": "",
        "ActiveState": "active",
        "LoadState": "loaded",
    }
    info = diag.describe_filter_chain_unit(props, home="/home/deck")
    assert info["is_home_copy"] is True
    assert info["sets_ladspa_path"] is False
    assert info["ladspa_path"] is None


def test_describe_filter_chain_unit_drop_in_supplies_the_variable():
    """A drop-in overriding an otherwise-bare unit: systemctl show reports
    the merged Environment, so this must read as 'sets LADSPA_PATH' even
    though the base unit alone does not."""
    props = {
        "FragmentPath": "/usr/lib/systemd/user/filter-chain.service",
        "DropInPaths": "/home/deck/.config/systemd/user/filter-chain.service.d/10-ladspa-path.conf",
        "Environment": "LADSPA_PATH=/home/deck/.ladspa:/usr/lib64/ladspa",
        "ActiveState": "active",
        "LoadState": "loaded",
    }
    info = diag.describe_filter_chain_unit(props, home="/home/deck")
    assert info["sets_ladspa_path"] is True
    assert info["drop_in_paths"] == [
        "/home/deck/.config/systemd/user/filter-chain.service.d/10-ladspa-path.conf",
    ]


def test_describe_filter_chain_unit_not_found():
    info = diag.describe_filter_chain_unit({}, home="/home/deck")
    assert info["found"] is False
    assert info["fragment_path"] == "(unit not found)"


def test_extract_unit_version_marker_present():
    text = "# ASM-UNIT-VERSION: 2\n[Unit]\nDescription=PipeWire filter-chain\n"
    assert diag.extract_unit_version_marker(text) == 2


def test_extract_unit_version_marker_absent():
    assert diag.extract_unit_version_marker("[Unit]\nDescription=x\n") is None


# ---------------------------------------------------------------------------
# _downstream_of — check 6's core. Checks 1-5 all stop at the EQ's input side,
# so a chain can be green on every one of them and still deliver audio
# nowhere: that is what issue #181 looked like for weeks, five PASSes and
# silence. This is the traversal that names the hop where it stops.
# ---------------------------------------------------------------------------

def _graph(nodes, links):
    """(names, name_to_id, link_pairs) as check 6 consumes them."""
    names = dict(nodes)
    return names, {v: k for k, v in names.items()}, list(links)


def test_downstream_of_reports_the_node_a_chain_feeds():
    names, name_to_id, links = _graph(
        {10: "effect_output.sonar-game-eq", 20: "effect_input.virtual-surround-7.1-hesuvi"},
        [(10, 20)],
    )
    assert diag._downstream_of("effect_output.sonar-game-eq", name_to_id, names, links) == [
        "effect_input.virtual-surround-7.1-hesuvi"
    ]


def test_downstream_of_a_dead_end_is_empty():
    """The #181 shape: the node exists, upstream is linked, nothing comes out."""
    names, name_to_id, links = _graph(
        {10: "effect_output.sonar-game-eq", 5: "Arctis_Game_sink_out"},
        [(5, 99)],  # something feeds INTO the chain, nothing leaves the EQ
    )
    assert diag._downstream_of("effect_output.sonar-game-eq", name_to_id, names, links) == []


def test_downstream_of_an_absent_node_is_empty():
    names, name_to_id, links = _graph({10: "effect_output.sonar-chat-eq"}, [])
    assert diag._downstream_of("effect_output.sonar-media-eq", name_to_id, names, links) == []


def test_downstream_of_lists_every_destination_sorted():
    names, name_to_id, links = _graph(
        {10: "effect_output.sonar-media-eq", 20: "zzz_sink", 30: "aaa_sink"},
        [(10, 20), (10, 30)],
    )
    assert diag._downstream_of("effect_output.sonar-media-eq", name_to_id, names, links) == [
        "aaa_sink", "zzz_sink"
    ]


def test_downstream_of_deduplicates_multichannel_links():
    """One link per channel pair (FL, FR, …) is still one destination."""
    names, name_to_id, links = _graph(
        {10: "effect_output.sonar-game-eq", 20: "alsa_output.usb-SteelSeries"},
        [(10, 20), (10, 20), (10, 20)],
    )
    assert diag._downstream_of("effect_output.sonar-game-eq", name_to_id, names, links) == [
        "alsa_output.usb-SteelSeries"
    ]


def test_downstream_of_ignores_links_that_only_arrive():
    """Direction matters: a node fed by many things still feeds nothing."""
    names, name_to_id, links = _graph(
        {10: "effect_output.sonar-game-eq", 5: "Arctis_Game_sink_out"},
        [(5, 10)],
    )
    assert diag._downstream_of("effect_output.sonar-game-eq", name_to_id, names, links) == []


def test_downstream_of_names_an_unknown_destination_by_id():
    """A link to a node that vanished between dump and traversal still reports."""
    names, name_to_id, links = _graph({10: "effect_output.sonar-chat-eq"}, [(10, 77)])
    assert diag._downstream_of("effect_output.sonar-chat-eq", name_to_id, names, links) == [
        "<id 77>"
    ]
