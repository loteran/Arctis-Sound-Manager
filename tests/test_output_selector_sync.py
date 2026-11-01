# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""One output device, one source of truth.

Three places let the user pick the Output channel's device: the Settings page,
the Channels tab's Output card, and the Equalizer's selector. The first two
write ``external_output_device``, which is also what the daemon reads to aim
the Output chain. The selector resolved purely from ``OutputMemory``, a file
nothing else writes — so a device chosen anywhere else never reached it, and
the two disagreed with no sign that they had.

Worse, the two sides did not even name devices the same way: the Channels card
saves a ``node.nick``, the selector keys its combo on ``node.name``. So even
when a value did travel, it could fail to match.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

HEADSET = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
HDMI = "alsa_output.pci-0000_09_00.1.hdmi-stereo"


class _FakeSink:
    def __init__(self, name, nick=""):
        self.name = name
        self.description = name
        self.proplist = {"node.nick": nick} if nick else {}


def _selector(tmp_path, monkeypatch, *, configured: str | None, sinks,
              memory_prefers: str = HDMI):
    """An OutputSelector wired to a throwaway settings file and sink list."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    from arctis_sound_manager.gui import output_selector as osel

    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    if configured is not None:
        (settings_dir / "general_settings.yaml").write_text(
            f"external_output_device: {configured}\n")
    monkeypatch.setattr("arctis_sound_manager.constants.SETTINGS_FOLDER", settings_dir)

    pulse = MagicMock()
    pulse.__enter__ = lambda s: s
    pulse.__exit__ = lambda *a: False
    pulse.sink_list.return_value = sinks
    fake_pulsectl = types.ModuleType("pulsectl")
    fake_pulsectl.Pulse = lambda *a, **kw: pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake_pulsectl)

    # The memory prefers HDMI, so a selector that ignores the settings shows
    # HDMI — which is precisely the bug these tests pin down.
    memory = MagicMock()
    memory.resolve.return_value = memory_prefers
    memory.is_fallback.return_value = False
    memory.preferred = memory_prefers
    monkeypatch.setattr(osel.OutputMemory, "load", staticmethod(lambda: memory))

    with patch.object(osel.OutputSelector, "_available",
                      lambda self: [(s.name, s.name) for s in sinks]):
        yield_widget = osel.OutputSelector()
    return yield_widget


def test_the_selector_shows_what_settings_configured(tmp_path, monkeypatch):
    """A device chosen from Settings or the Channels tab must reach this combo."""
    sel = _selector(tmp_path, monkeypatch,
                    configured=HEADSET,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HEADSET, "the selector ignored external_output_device"


def test_a_nick_saved_by_the_channels_card_still_matches(tmp_path, monkeypatch):
    """The Channels card saves node.nick; this combo is keyed on node.name.

    Without matching both, the value travelled and then failed to resolve —
    the selector fell back to its own memory and the two views disagreed.
    """
    sel = _selector(tmp_path, monkeypatch,
                    configured="Q95A",
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI, nick="Q95A")],
                    memory_prefers=HEADSET)   # so only a real match can win
    assert sel._current == HDMI


def test_an_incoming_change_is_not_echoed_back(tmp_path, monkeypatch):
    """Adopting the configured device must not re-emit it.

    Every emission ends in a filter-chain restart. A change made in Settings is
    already applied — the daemon aims the Output chain from the same value — so
    echoing it would interrupt audio each time the device is changed elsewhere.
    """
    emitted = []
    sel = _selector(tmp_path, monkeypatch,
                    configured=HEADSET,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    sel.target_changed.connect(emitted.append)

    sel._current = None          # force the change branch on the next pass
    sel.refresh()

    assert sel._current == HEADSET
    assert emitted == [], "a settings-driven change was sent straight back out"


def test_the_memory_still_covers_a_device_that_is_gone(tmp_path, monkeypatch):
    """The fallback ladder keeps its job: configured device absent from the
    graph means the memory decides, which is what it exists for."""
    sel = _selector(tmp_path, monkeypatch,
                    configured="bluez_output.30_96_10_49_54_E2.1",
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HDMI     # the memory's answer
    assert sel._on_configured is False


def test_nothing_configured_falls_back_to_the_memory(tmp_path, monkeypatch):
    sel = _selector(tmp_path, monkeypatch,
                    configured=None,
                    sinks=[_FakeSink(HEADSET), _FakeSink(HDMI)])
    assert sel._current == HDMI


# ── a temporary absence must not change the saved choice ─────────────────────

def test_a_disappearing_configured_device_is_a_live_only_change(tmp_path, monkeypatch):
    """The monitor goes to sleep: the link falls back, the setting does not.

    This is the reported bug. OutputSelector used to emit target_changed for
    this, and SonarPage persisted it as external_output_device — so when the
    monitor woke up it was no longer the configured device at all.
    """
    sinks = [_FakeSink(HEADSET), _FakeSink(HDMI)]
    sel = _selector(tmp_path, monkeypatch, configured=HDMI, sinks=sinks,
                    memory_prefers=HEADSET)
    assert sel._current == HDMI

    picked, live = [], []
    sel.target_changed.connect(picked.append)
    sel.live_target_changed.connect(live.append)

    sinks[:] = [s for s in sinks if s.name != HDMI]
    sel.refresh()

    assert sel._current == HEADSET
    assert live == [HEADSET]
    assert picked == [], "a temporary fallback was persisted as a user choice"
    settings = (tmp_path / "settings" / "general_settings.yaml").read_text()
    assert HDMI in settings, "the saved device was overwritten by the fallback"


def test_a_returning_configured_device_is_relinked_without_being_persisted(
        tmp_path, monkeypatch):
    """The monitor wakes up again: the live link follows it back."""
    sinks = [_FakeSink(HEADSET)]
    sel = _selector(tmp_path, monkeypatch, configured=HDMI, sinks=sinks,
                    memory_prefers=HEADSET)
    assert sel._current == HEADSET          # the monitor is asleep at startup
    assert sel._on_configured is False

    picked, live = [], []
    sel.target_changed.connect(picked.append)
    sel.live_target_changed.connect(live.append)

    sinks.append(_FakeSink(HDMI))
    sel.refresh()

    assert sel._current == HDMI
    assert live == [HDMI]
    assert picked == []
    assert sel._on_configured is True


# ── the outbound direction ────────────────────────────────────────────────────

def test_the_equalizer_saves_the_id_the_other_tabs_use(monkeypatch):
    """What the Equalizer writes has to be findable by the other two combos.

    dbus_service._device_list builds every option as ``nick or node_name``, and
    Channels and Settings key their items on it. OutputSelector works in
    node.name, so persisting it verbatim wrote an id `findData()` could not
    match: the setting changed, the daemon re-aimed the chain, and the other
    views did not move. That is the reported symptom, in one line.
    """
    pytest.importorskip("PySide6")
    from arctis_sound_manager.gui.sonar_page import SonarPage

    pulse = MagicMock()
    pulse.__enter__ = lambda s: s
    pulse.__exit__ = lambda *a: False
    pulse.sink_list.return_value = [_FakeSink(HDMI, nick="Q95A"), _FakeSink(HEADSET)]
    fake_pulsectl = types.ModuleType("pulsectl")
    fake_pulsectl.Pulse = lambda *a, **kw: pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake_pulsectl)

    assert SonarPage._canonical_device_id(HDMI) == "Q95A"


def test_a_device_without_a_nick_keeps_its_name(monkeypatch):
    """Same rule as the daemon's: nick when there is one, node.name otherwise."""
    pytest.importorskip("PySide6")
    from arctis_sound_manager.gui.sonar_page import SonarPage

    pulse = MagicMock()
    pulse.__enter__ = lambda s: s
    pulse.__exit__ = lambda *a: False
    pulse.sink_list.return_value = [_FakeSink(HEADSET)]
    fake_pulsectl = types.ModuleType("pulsectl")
    fake_pulsectl.Pulse = lambda *a, **kw: pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake_pulsectl)

    assert SonarPage._canonical_device_id(HEADSET) == HEADSET


def test_the_round_trip_closes(tmp_path, monkeypatch):
    """Write it from the Equalizer, read it back into the Equalizer.

    The id the EQ persists must be one _configured_target() resolves again —
    otherwise the two fixes cancel out and the selector drifts back.
    """
    pytest.importorskip("PySide6")
    from arctis_sound_manager.gui.sonar_page import SonarPage

    sinks = [_FakeSink(HDMI, nick="Q95A"), _FakeSink(HEADSET)]
    pulse = MagicMock()
    pulse.__enter__ = lambda s: s
    pulse.__exit__ = lambda *a: False
    pulse.sink_list.return_value = sinks
    fake_pulsectl = types.ModuleType("pulsectl")
    fake_pulsectl.Pulse = lambda *a, **kw: pulse
    monkeypatch.setitem(sys.modules, "pulsectl", fake_pulsectl)

    stored = SonarPage._canonical_device_id(HDMI)
    sel = _selector(tmp_path, monkeypatch, configured=stored, sinks=sinks,
                    memory_prefers=HEADSET)
    assert sel._current == HDMI


# ── the user's own pick must survive the next refresh ─────────────────────────

def test_a_pick_is_not_undone_while_it_is_being_saved(tmp_path, monkeypatch):
    """The regression the settings-first rule introduced.

    Persisting goes out over D-Bus on a worker thread, so it lands after the
    click — and this widget re-reads the settings every 5s. Reading the old
    value back and applying it puts the previous device on screen again, which
    is indistinguishable from the Equalizer refusing to change at all.
    """
    sinks = [_FakeSink(HEADSET), _FakeSink(HDMI)]
    sel = _selector(tmp_path, monkeypatch, configured=HEADSET, sinks=sinks,
                    memory_prefers=HEADSET)
    assert sel._current == HEADSET

    # The user picks the other device; the settings file has not caught up.
    sel._combo.clear()
    for name, _ in [(s.name, s.name) for s in sinks]:
        sel._combo.addItem(name, name)
    sel._combo.setCurrentIndex(sel._combo.findData(HDMI))
    sel._on_picked(sel._combo.currentIndex())
    assert sel._current == HDMI

    sel.refresh()      # the settings still say HEADSET
    assert sel._current == HDMI, "the pending pick was overwritten by a stale read"


def test_the_hold_is_released_once_the_write_lands(tmp_path, monkeypatch):
    sinks = [_FakeSink(HEADSET), _FakeSink(HDMI)]
    sel = _selector(tmp_path, monkeypatch, configured=HEADSET, sinks=sinks,
                    memory_prefers=HEADSET)
    sel._pending_pick = HDMI

    settings = tmp_path / "settings" / "general_settings.yaml"
    settings.write_text(f"external_output_device: {HDMI}\n")
    sel.refresh()

    assert sel._pending_pick is None, "the hold should end when the value matches"
    assert sel._current == HDMI


def test_a_write_that_never_lands_stops_being_believed(tmp_path, monkeypatch):
    """Holding for ever would hide the real state rather than show it."""
    from arctis_sound_manager.gui import output_selector as osel

    sinks = [_FakeSink(HEADSET), _FakeSink(HDMI)]
    sel = _selector(tmp_path, monkeypatch, configured=HEADSET, sinks=sinks,
                    memory_prefers=HEADSET)
    sel._pending_pick = HDMI

    for _ in range(osel._PENDING_MAX_TICKS + 2):
        sel.refresh()

    assert sel._pending_pick is None
    assert sel._current == HEADSET, "should fall back to what is actually configured"


# ── what the page is allowed to persist ──────────────────────────────────────

def _page_for_output(monkeypatch, current):
    """A SonarPage with only the Output plumbing its handlers use."""
    from unittest.mock import MagicMock

    from arctis_sound_manager.gui.sonar_page import SonarPage

    page = SonarPage.__new__(SonarPage)
    page._output_widget = MagicMock()
    page._output_widget._target_override = current

    monkeypatch.setattr(
        SonarPage, "_canonical_device_id",
        staticmethod(lambda node_name: node_name),
    )
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "arctis_sound_manager.gui.dbus_wrapper.DbusWrapper.change_setting",
        staticmethod(lambda name, value: written.append((name, value))),
    )
    retargeted: list[str] = []
    monkeypatch.setattr(
        "arctis_sound_manager.pw_utils.retarget_output",
        lambda target, data=None: retargeted.append(target) or True,
    )
    return page, written, retargeted


def test_an_automatic_fallback_only_moves_the_link(monkeypatch):
    """The daemon's rule, now applied to the GUI too: a fallback is a link,
    not a decision, so the saved device must survive the monitor sleeping."""
    page, written, retargeted = _page_for_output(monkeypatch, current=HDMI)

    page._on_output_live_target_changed(HEADSET)

    assert written == [], "the fallback rewrote external_output_device"
    assert retargeted == [HEADSET]
    assert page._output_widget._target_override == HDMI, (
        "the fallback must not become the target the Output conf is generated "
        "from — that would write the headset into the conf while the setting "
        "still says monitor"
    )


def test_a_user_pick_persists_the_device_and_moves_the_link(monkeypatch):
    page, written, retargeted = _page_for_output(monkeypatch, current=HEADSET)

    page._on_output_device_changed(HDMI)

    assert written == [("external_output_device", HDMI)]
    assert retargeted == [HDMI]
    assert page._output_widget._target_override == HDMI
