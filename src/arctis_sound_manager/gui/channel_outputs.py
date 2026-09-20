# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where each channel's application audio should physically play.

Shared between every GUI surface that lists or changes a channel's output
device — the Sonar/Equalizer page's per-channel selectors (#262) and, before
them, the Channels page's own combos. ``sonar_to_pipewire.py`` keeps its own
copy of the same path and reads it independently on the daemon side; it is
not imported from here to avoid a GUI module depending on daemon internals
(or the reverse), matching how ``scripts/video_router.py`` already does the
same thing as a third, independent reader.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CHANNEL_OUTPUTS_FILE = Path.home() / ".config" / "arctis_manager" / "channel_output_devices.json"


def load_channel_outputs() -> dict:
    if CHANNEL_OUTPUTS_FILE.exists():
        try:
            return json.loads(CHANNEL_OUTPUTS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_channel_outputs(data: dict) -> None:
    CHANNEL_OUTPUTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHANNEL_OUTPUTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(CHANNEL_OUTPUTS_FILE)


def set_channel_output(channel: str, sink_name: str | None) -> None:
    """Send *channel* to *sink_name* — by moving the channel, not its apps.

    This used to drag every application off the channel's virtual sink and
    onto the chosen device. The routing overrides then pulled them back on
    the next pass, so the selection undid itself within seconds and looked
    like it did nothing. Re-linking the channel's own output leaves every
    application exactly where the user put it, and there is nothing left to
    contest the change.
    """
    data = load_channel_outputs()
    if sink_name:
        data[channel] = sink_name
    else:
        data.pop(channel, None)
    save_channel_outputs(data)

    # Apply now rather than waiting for the daemon's next tick — a device
    # switch has to be immediate to feel like it worked. It has to go through
    # the daemon: the enforcement passes resolve the headset via
    # device_state, which is per-process and empty here, so running them in
    # the GUI resolves an empty target and links nothing.
    try:
        from arctis_sound_manager.gui.dbus_wrapper import DbusWrapper
        DbusWrapper.apply_channel_outputs()
    except Exception:
        logger.exception("could not retarget channel '%s'", channel)


def channel_output_options(sinks) -> list[tuple[str, str]]:
    """(id, label) pairs for a "send this channel elsewhere" combo.

    Not "this channel's destination" like the Output channel's own selector:
    the empty first entry means "the headset, same as if nothing were
    chosen", and the headset itself is deliberately excluded from the
    explicit list — that is already what leaving it on "" means, and this
    combo has always been about sending a channel to something *other* than
    the headset ASM already feeds it.
    """
    from arctis_sound_manager.i18n import I18n
    from arctis_sound_manager.pw_utils import is_external_output_sink

    def _label(sink) -> str:
        # pulsectl's own description before the node name. Both PipeWire
        # properties are optional and Bluetooth sinks routinely ship without
        # either, so a pair of earbuds was listed as
        # "bluez_output.30_96_10_49_54_E2.1" — a MAC address where a product
        # name belongs, which reads as a bug rather than a device.
        # build_sink_options() already ends its ladder this way for the
        # D-Bus pickers (#134 / #146); these combos never got it.
        return (sink.proplist.get("node.description")
                or sink.proplist.get("node.nick")
                or getattr(sink, "description", "")
                or sink.name)

    physical = [s for s in sinks if is_external_output_sink(s)]
    # Name the headset rather than saying "Headset". The default entry is a
    # device like any other in this list, and calling it by a generic word
    # while every sibling shows a product name reads as a placeholder — worse
    # when a second headset is connected and neither row says which one this
    # is. Falls back to the generic label only when the headset is absent,
    # where there is no name to give.
    headset = next(
        (s for s in sinks
         if is_external_output_sink(s, allow_headset=True)
         and not is_external_output_sink(s)),
        None,
    )
    default_label = (_label(headset) if headset is not None
                     else I18n.translate("ui", "headset_output"))
    return [("", default_label)] + [(s.name, _label(s)) for s in physical]
