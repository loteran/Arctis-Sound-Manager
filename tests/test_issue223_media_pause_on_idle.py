# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #223 — passive EQ and HeSuVi chains must not be torn down between tracks.

A browser or VoIP client closes and reopens its stream during normal use. With a
passive chain allowed to pause on idle, WirePlumber suspends the downstream
nodes after a few seconds of silence, and audio disappears until something else
wakes it — which on Game and Chat means the sound cuts out entirely (#223,
#NNN).

``node.pause-on-idle = false`` in the *playback* block of every passive chain
(Game, Chat, Media, Aux, and the corresponding HeSuVi chains) keeps the
downstream nodes running across stream gaps. The Output channel is the only
exception: it is Audio/Sink (not Internal) and carries a user fallback, so it
must still be allowed to idle for headset power-off to work (#180).
"""

from pathlib import Path

import pytest

from arctis_sound_manager import sonar_to_pipewire as _s2p
from arctis_sound_manager.sonar_to_pipewire import (
    EqBand,
    check_and_fix_stale_configs,
)

_PAUSE_ON_IDLE = "node.pause-on-idle"


def _playback_block(conf: str) -> str:
    """The playback.props block alone — the property belongs to that side."""
    match = _s2p._PLAYBACK_BLOCK_RE.search(conf)
    assert match is not None, "conf has no playback.props block"
    return match.group(1)


def _capture_block(conf: str) -> str:
    """Everything before playback.props, which is where capture.props lives."""
    return conf.split("playback.props")[0]


def _one_band() -> tuple[list[tuple[str, EqBand]], list[EqBand]]:
    """The smallest filter rack that keeps the generators off the bypass path."""
    band = EqBand(freq=1000.0, gain=3.0, q=0.7, type="peakingEQ", enabled=True)
    return [("bq0", band)], [band]


# ── the generators ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("channel", ["game", "media", "chat", "output"])
def test_active_8ch_conf_pauses_on_idle_for_all_except_output(channel: str) -> None:
    all_filters, band_slots = _one_band()
    conf = _s2p._active_conf_8ch(
        channel, f"effect_input.sonar-{channel}-eq", "alsa_output.test",
        "FL FR FC LFE RL RR SL SR", all_filters, band_slots, [], 0.0,
    )
    assert (_PAUSE_ON_IDLE in _playback_block(conf)) is (channel != "output")


@pytest.mark.parametrize("channel", ["game", "media", "chat", "output"])
def test_active_2ch_conf_pauses_on_idle_for_all_except_output(channel: str) -> None:
    all_filters, band_slots = _one_band()
    conf = _s2p._active_conf_2ch(
        channel, f"effect_input.sonar-{channel}-eq", "alsa_output.test",
        "FL FR", all_filters, band_slots, [], 0.0,
    )
    assert (_PAUSE_ON_IDLE in _playback_block(conf)) is (channel != "output")


@pytest.mark.parametrize("channel", ["game", "media"])
def test_hesuvi_conf_pauses_on_idle_for_all_except_output(channel: str, monkeypatch) -> None:
    monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
    monkeypatch.setattr(_s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
    monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
    monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: None)
    conf = _s2p.generate_hesuvi_conf(output_path=Path("/dev/null"), channel=channel)
    assert (_PAUSE_ON_IDLE in _playback_block(conf)) is (channel != "output")


@pytest.mark.parametrize(
    "maker",
    ["active_8ch", "active_2ch", "hesuvi"],
)
def test_generated_media_conf_keeps_the_property_out_of_capture(
    maker: str, monkeypatch,
) -> None:
    """The capture side is the sink applications play into.

    Only the playback node feeds the convolver and has to be held open; a
    stray copy on the capture side would keep the Media sink itself from ever
    idling, for no benefit.
    """
    all_filters, band_slots = _one_band()
    if maker == "active_8ch":
        conf = _s2p._active_conf_8ch(
            "media", "effect_input.sonar-media-eq", "alsa_output.test",
            "FL FR FC LFE RL RR SL SR", all_filters, band_slots, [], 0.0,
        )
    elif maker == "active_2ch":
        conf = _s2p._active_conf_2ch(
            "media", "effect_input.sonar-media-eq", "alsa_output.test",
            "FL FR", all_filters, band_slots, [], 0.0,
        )
    else:
        monkeypatch.setattr(_s2p, "_device_attached", lambda: True)
        monkeypatch.setattr(
            _s2p, "_get_physical_out_game", lambda: "alsa_output.test-game")
        monkeypatch.setattr(_s2p, "_write_conf", lambda path, text: None)
        monkeypatch.setattr(_s2p, "_ladspa_plugin_ref", lambda name: None)
        conf = _s2p.generate_hesuvi_conf(
            output_path=Path("/dev/null"), channel="media")

    assert _PAUSE_ON_IDLE in _playback_block(conf)
    assert _PAUSE_ON_IDLE not in _capture_block(conf)
    # Exactly one occurrence in the whole file — no duplicate slipped in.
    assert conf.count(_PAUSE_ON_IDLE) == 1


# ── the in-place repair for confs written by an older ASM ─────────────────────

# A Media EQ conf as ASM wrote it before this fix: node.passive is there (issue
# #180 shipped first), node.pause-on-idle is not. The EQ conf pads its playback
# block one column wider than the HeSuVi one, which is why the repair has to
# read the alignment off the file instead of hardcoding it.
_OLD_MEDIA_EQ_CONF = """\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# ASM-CONF-VERSION: 4
context.modules = [
  { name = libpipewire-module-filter-chain
    args = {
      filter.graph = {
        nodes = [
          { type = builtin name = eq_band_1 label = bq_peaking
            control = { Freq = 100.0 Q = 1.4 Gain = 6.0 } }
        ]
      }
      capture.props = {
        node.name         = "effect_input.sonar-media-eq"
        media.class       = Audio/Sink/Internal
      }
      playback.props = {
        node.name           = "effect_output.sonar-media-eq"
        node.dont-fallback  = true
        node.linger         = true
        node.passive        = true
        audio.channels      = 8
      }
    }
  }
]
"""

# The HeSuVi conf's playback block is one column narrower than the EQ one.
_OLD_HESUVI_MEDIA_CONF = """\
# Auto-generated by Arctis Sound Manager — DO NOT EDIT
# ASM-CONF-VERSION: 4
context.modules = [
  { name = libpipewire-module-filter-chain
    args = {
      capture.props = {
        node.name      = "effect_input.virtual-surround-7.1-hesuvi-media"
        media.class    = Audio/Sink/Internal
      }
      playback.props = {
        node.name          = "effect_output.virtual-surround-7.1-hesuvi-media"
        node.dont-fallback = true
        node.linger        = true
        node.passive       = true
        audio.channels     = 2
      }
    }
  }
]
"""


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("sonar-media-eq.conf", _OLD_MEDIA_EQ_CONF),
        ("sink-virtual-surround-7.1-hesuvi-media.conf", _OLD_HESUVI_MEDIA_CONF),
    ],
)
def test_repair_inserts_pause_on_idle_in_both_media_confs(
    name: str, text: str, tmp_path: Path,
) -> None:
    conf = tmp_path / name
    conf.write_text(text)
    assert _s2p._ensure_media_pause_on_idle(conf) is True
    assert _PAUSE_ON_IDLE in _playback_block(conf.read_text())


def test_repair_preserves_the_users_eq(tmp_path: Path) -> None:
    """Same reason as issue #180's repair: nothing here can read the bands back,
    so this has to be a one-line patch and not a regeneration."""
    conf = tmp_path / "sonar-media-eq.conf"
    conf.write_text(_OLD_MEDIA_EQ_CONF)
    _s2p._ensure_media_pause_on_idle(conf)
    after = conf.read_text()
    assert "Freq = 100.0 Q = 1.4 Gain = 6.0" in after
    assert "# ASM-CONF-VERSION: 4" in after
    # Exactly one line added, nothing removed.
    assert len(after.splitlines()) == len(_OLD_MEDIA_EQ_CONF.splitlines()) + 1


def test_repair_is_idempotent(tmp_path: Path) -> None:
    conf = tmp_path / "sonar-media-eq.conf"
    conf.write_text(_OLD_MEDIA_EQ_CONF)
    assert _s2p._ensure_media_pause_on_idle(conf) is True
    once = conf.read_text()
    assert _s2p._ensure_media_pause_on_idle(conf) is False
    assert conf.read_text() == once


def test_repair_leaves_other_channels_alone(tmp_path: Path) -> None:
    """The Output channel is the only one that must NOT get pause-on-idle,
    because it is Audio/Sink (not Internal) and must be allowed to suspend
    for headset power-off to work (#180)."""
    conf = tmp_path / "sonar-output-eq.conf"
    conf.write_text(_OLD_MEDIA_EQ_CONF.replace("media", "output"))
    before = conf.read_text()
    assert _s2p._ensure_media_pause_on_idle(conf) is False
    assert conf.read_text() == before


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("sonar-game-eq.conf", _OLD_MEDIA_EQ_CONF.replace("media", "game")),
        ("sonar-chat-eq.conf", _OLD_MEDIA_EQ_CONF.replace("media", "chat")),
        ("sink-virtual-surround-7.1-hesuvi.conf",
         _OLD_HESUVI_MEDIA_CONF.replace("-media", "")),
    ],
)
def test_repair_adds_pause_on_idle_to_game_and_chat(
    name: str, text: str, tmp_path: Path,
) -> None:
    """Game, Chat, and their HeSuVi chains need the property just as much as
    Media does: any passive chain suspended by WirePlumber after a few seconds
    of silence will cut audio until something else wakes it."""
    conf = tmp_path / name
    conf.write_text(text)
    assert _s2p._ensure_media_pause_on_idle(conf) is True
    assert _PAUSE_ON_IDLE in _playback_block(conf.read_text())


def test_repair_on_missing_file_is_harmless(tmp_path: Path) -> None:
    assert _s2p._ensure_media_pause_on_idle(tmp_path / "sonar-media-eq.conf") is False


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("sonar-media-eq.conf", _OLD_MEDIA_EQ_CONF),
        ("sink-virtual-surround-7.1-hesuvi-media.conf", _OLD_HESUVI_MEDIA_CONF),
    ],
)
def test_repair_keeps_the_equals_aligned(name: str, text: str, tmp_path: Path) -> None:
    """The file still has to read like a generated one, not a hand edit — and
    the two confs do not pad their playback block to the same width, so the
    column has to be read off node.passive rather than assumed."""
    conf = tmp_path / name
    conf.write_text(text)
    _s2p._ensure_media_pause_on_idle(conf)
    lines = [ln for ln in conf.read_text().splitlines()
             if "node.passive" in ln or _PAUSE_ON_IDLE in ln]
    assert len(lines) == 2
    assert lines[0].index("=") == lines[1].index("=")


def test_repair_never_writes_a_malformed_line_when_the_column_is_too_narrow(
    tmp_path: Path,
) -> None:
    """``node.pause-on-idle`` is six characters longer than ``node.passive``.

    A conf whose block is padded too tightly for it cannot be aligned; the
    repair must then fall back to a single space rather than emit
    ``node.pause-on-idle= false`` or eat the property name.
    """
    conf = tmp_path / "sonar-media-eq.conf"
    conf.write_text(_OLD_MEDIA_EQ_CONF.replace(
        "node.passive        = true", "node.passive = true"))
    assert _s2p._ensure_media_pause_on_idle(conf) is True
    line = next(ln for ln in conf.read_text().splitlines()
                if _PAUSE_ON_IDLE in ln)
    assert line == f"        {_PAUSE_ON_IDLE} = false"


def test_repair_inserts_into_playback_even_when_capture_is_passive_too(
    tmp_path: Path,
) -> None:
    """Regression guard: the insertion point must come from the matched block.

    The repair locates ``playback.props`` with a regex, and used to then look
    up ``node.passive`` in the *whole file*. That happened to work only because
    the generated confs carry the property exactly once. The day capture.props
    grows one — a hand edit, a future template — the first match is the capture
    one and the line lands in the wrong block, where it would pin the Media
    sink itself open instead of the convolver.
    """
    conf = tmp_path / "sonar-media-eq.conf"
    conf.write_text(_OLD_MEDIA_EQ_CONF.replace(
        '        media.class       = Audio/Sink/Internal\n',
        '        media.class       = Audio/Sink/Internal\n'
        '        node.passive      = true\n',
    ))
    assert _s2p._ensure_media_pause_on_idle(conf) is True

    after = conf.read_text()
    assert _PAUSE_ON_IDLE in _playback_block(after)
    assert _PAUSE_ON_IDLE not in _capture_block(after)
    assert after.count(_PAUSE_ON_IDLE) == 1


# ── the caller that carries the repair to existing installs ───────────────────

def test_check_and_fix_stale_configs_repairs_all_passive_confs(
    tmp_path: Path, monkeypatch,
) -> None:
    """Nothing else carries this fix forward: sonar-*-eq.conf files are not
    regenerated by a version bump alone (that would flatten the user's EQ), so
    the daemon's startup sweep is the only path that reaches an existing
    install."""
    monkeypatch.setattr(_s2p, "_CONF_DIR", tmp_path)
    monkeypatch.setattr(_s2p, "_SINKS_CONF_DIR", tmp_path / "pipewire.conf.d")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    # Game, Chat, Media EQ + Game, Media HeSuVi — all need the property.
    for _name, _template in (
        ("sonar-game-eq.conf", _OLD_MEDIA_EQ_CONF.replace("media", "game")),
        ("sonar-chat-eq.conf", _OLD_MEDIA_EQ_CONF.replace("media", "chat")),
        ("sonar-media-eq.conf", _OLD_MEDIA_EQ_CONF),
        ("sink-virtual-surround-7.1-hesuvi.conf",
         _OLD_HESUVI_MEDIA_CONF.replace("-media", "")),
        ("sink-virtual-surround-7.1-hesuvi-media.conf",
         _OLD_HESUVI_MEDIA_CONF),
    ):
        (tmp_path / _name).write_text(_template)

    fixed, _needs_pw_restart = check_and_fix_stale_configs()

    assert fixed is True
    for _name in (
        "sonar-game-eq.conf",
        "sonar-chat-eq.conf",
        "sonar-media-eq.conf",
        "sink-virtual-surround-7.1-hesuvi.conf",
        "sink-virtual-surround-7.1-hesuvi-media.conf",
    ):
        assert _PAUSE_ON_IDLE in _playback_block(
            (tmp_path / _name).read_text()), _name
