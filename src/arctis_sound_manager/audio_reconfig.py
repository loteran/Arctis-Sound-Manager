# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep applications on their channel across an ASM audio reconfiguration.

Every ASM change that touches the graph (an EQ curve, a profile switch, the
Sonar toggle, "restart the audio engine") restarts ``filter-chain``. The
``effect_input.sonar-*-eq`` nodes and the ``Arctis_*`` loopback sinks that feed
them disappear for a few seconds, and PipeWire parks every stream that was
sitting on them wherever it can. That is the flicker users describe: the
channel vanishes for a moment and the game comes back on a different one.

Two separate things went wrong there, and this module addresses both.

**The stream is not put back.** Only apps with a saved routing override were
restored, because :func:`pw_utils.reapply_routing_overrides` is all most restart
paths called. An app that simply happened to be on a channel (never dragged
anywhere, so never given an override) had nothing to bring it home.
:func:`snapshot_channel_streams` records where every stream actually is, keyed
by application identity rather than by PulseAudio index, because an app that
reconnects during the restart comes back with a new index and the old one then
names either nothing or somebody else.

**The accident is written down as intent.** ``arctis-video-router`` watches for
streams drifting off their channel and, after its stability delay on the new
one, saves that as the user's override. A restart outlasts that delay
comfortably, so the router persisted the displacement, and the very next
``reapply_routing_overrides()`` enforced the wrong channel from then on. This is
what made the symptom stick instead of being a one-off flicker. The window
marker below lets the router tell "ASM is rebuilding the graph" from "the user
moved this in pavucontrol", so it stops recording during the former.

The marker is a file with an expiry timestamp rather than a D-Bus call: the
router must keep working when the GUI is not running, must not deadlock on a
service that is restarting, and must recover on its own if whoever opened the
window dies before closing it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Channels an app can be on. The virtual sink and its filter-chain input are the
# same channel as far as the user is concerned: a stream sits on the former with
# the Sonar EQ off and on the latter with it on.
_CHANNEL_OF = {
    "Arctis_Game": "Arctis_Game",
    "Arctis_Chat": "Arctis_Chat",
    "Arctis_Media": "Arctis_Media",
    "Arctis_Aux": "Arctis_Aux",
    "effect_input.sonar-game-eq": "Arctis_Game",
    "effect_input.sonar-chat-eq": "Arctis_Chat",
    "effect_input.sonar-media-eq": "Arctis_Media",
    "effect_input.sonar-aux-eq": "Arctis_Aux",
}

# The Output channel has no Arctis_* loopback in front of it, so its EQ node is
# the channel: restoring a stream there means putting it back on the node itself.
_SELF_TARGET = ("effect_input.sonar-output-eq",)

# How long a reconfiguration window stays open when nobody closes it. Long
# enough for the worst real path (restart, wait up to 8 s for the EQ node,
# recreate the loopback, move the streams back), short enough that a crashed GUI
# cannot leave the router deaf for a whole session.
DEFAULT_WINDOW_S = 25.0

# Closing the window does not clear it outright. The router is event driven and
# may still be working through the burst the restart produced; a short tail
# lets it settle on the final state before it starts recording moves again.
_CLOSE_TAIL_S = 3.0


def _marker_path() -> Path:
    """Where the window marker lives.

    ``XDG_RUNTIME_DIR`` is the right home for it: per user, per session, wiped
    at logout, so a marker can never survive a reboot into a session where
    nothing is being reconfigured. Falling back to the config directory keeps
    the mechanism working on setups without it rather than silently disabling
    the protection.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "arctis_manager" / "audio_reconfig"
    return Path.home() / ".config" / "arctis_manager" / ".audio_reconfig"


def _read_expiry(path: Path) -> float | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    try:
        return float(raw)
    except ValueError:
        # A truncated or garbage marker means "no window": treating an
        # unreadable file as an open window would silently stop the router from
        # ever recording a manual move again.
        return None


def begin(duration_s: float = DEFAULT_WINDOW_S) -> None:
    """Open a reconfiguration window lasting *duration_s* seconds.

    Idempotent and extending: a nested restart pushes the expiry out rather
    than shortening a window somebody else is relying on.
    """
    path = _marker_path()
    expiry = time.time() + duration_s
    try:
        current = _read_expiry(path)
        if current is not None and current > expiry:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{expiry:.3f}\n")
        logger.debug("audio_reconfig: window open for %.1fs", duration_s)
    except OSError as exc:
        # Losing the marker costs us the router protection, not the restart
        # itself, so this is never fatal.
        logger.warning("audio_reconfig: could not open the window: %s", exc)


def end() -> None:
    """Close the window, leaving a short tail for the router to settle."""
    path = _marker_path()
    try:
        current = _read_expiry(path)
        if current is None:
            return
        tail = time.time() + _CLOSE_TAIL_S
        if current <= tail:
            return
        path.write_text(f"{tail:.3f}\n")
        logger.debug("audio_reconfig: window closing in %.1fs", _CLOSE_TAIL_S)
    except OSError as exc:
        logger.warning("audio_reconfig: could not close the window: %s", exc)


def in_progress(now: float | None = None) -> bool:
    """True while ASM is rebuilding the audio graph.

    Callers use this to tell a stream that PipeWire displaced from one the user
    deliberately moved. Expired markers count as closed, so a process that died
    mid-restart cannot pin the window open.
    """
    expiry = _read_expiry(_marker_path())
    if expiry is None:
        return False
    return (now if now is not None else time.time()) < expiry


# -- stream snapshot / restore -------------------------------------------------

def _stream_identity(props) -> str | None:
    """A key that still names the same app after it reconnects.

    Deliberately the same key :func:`pw_utils.app_override_key` builds, so a
    snapshot entry and a routing override refer to the same thing and the two
    restore passes agree instead of fighting.
    """
    from arctis_sound_manager.pw_utils import app_override_key

    name = props.get("application.name", "") or ""
    binary = props.get("application.process.binary", "") or ""
    if not name and not binary:
        return None
    # ASM's own plumbing shows up as sink-inputs too (the loopbacks, the EQ
    # chains). Moving those would rewire the graph itself.
    if binary in ("pipewire", "pw-loopback", "pw-cli"):
        return None
    media = props.get("media.name", "") or ""
    if any(tag in media for tag in ("EQ output", "Virtual Surround", "Sonar")):
        return None
    return app_override_key(name or binary, binary)


def snapshot_channel_streams() -> dict[str, str]:
    """Record which channel each application is on right now.

    Returns ``{app_key: sink_name}`` covering only streams that are on one of
    ASM's channels. Streams elsewhere (a browser on the HDMI output, say) are
    left out on purpose: they are not ours to move, and a restart does not
    displace them.
    """
    try:
        import pulsectl  # type: ignore
    except Exception as exc:
        logger.debug("audio_reconfig: pulsectl unavailable, no snapshot: %s", exc)
        return {}

    placed: dict[str, str] = {}
    try:
        with pulsectl.Pulse("asm-snapshot-streams") as pulse:
            sink_name_of = {s.index: s.name for s in pulse.sink_list()}
            for si in pulse.sink_input_list():
                sink_name = sink_name_of.get(si.sink, "")
                channel = _CHANNEL_OF.get(sink_name)
                if channel is None and sink_name in _SELF_TARGET:
                    channel = sink_name
                if channel is None:
                    continue
                key = _stream_identity(si.proplist)
                if key:
                    placed[key] = channel
    except Exception as exc:
        logger.warning("audio_reconfig: snapshot failed: %s", exc)
        return {}

    if placed:
        logger.info("audio_reconfig: snapshot of %d stream(s) on ASM channels", len(placed))
    return placed


def restore_channel_streams(placed: dict[str, str], timeout_s: float = 8.0) -> int:
    """Put the apps in *placed* back on the channel they were on.

    Waits for the channels to come back before moving anything, since a move to
    a sink that does not exist yet fails outright and the app stays where the
    restart left it. Returns the number of streams moved.

    An app that has since been given a routing override is skipped: the
    override is the user's standing decision about where it belongs, and the
    override pass that runs after this one is what applies it. Restoring the
    pre-restart position on top of that would undo a change the user made
    between the snapshot and now.
    """
    if not placed:
        return 0

    try:
        import pulsectl  # type: ignore
    except Exception as exc:
        logger.debug("audio_reconfig: pulsectl unavailable, no restore: %s", exc)
        return 0

    from arctis_sound_manager.pw_utils import _load_overrides

    overrides = _load_overrides()
    wanted = {name for name in placed.values() if name.startswith("Arctis_")}
    moved = 0

    try:
        with pulsectl.Pulse("asm-restore-streams") as pulse:
            deadline = time.monotonic() + timeout_s
            sinks: list = []
            while True:
                sinks = pulse.sink_list()
                present = {s.name for s in sinks}
                pending = wanted - present
                if not pending or time.monotonic() >= deadline:
                    if pending:
                        logger.warning(
                            "audio_reconfig: channels did not come back in %.1fs: %s",
                            timeout_s, ", ".join(sorted(pending)),
                        )
                    break
                time.sleep(0.2)

            index_of = {s.name: s.index for s in sinks}
            for si in pulse.sink_input_list():
                key = _stream_identity(si.proplist)
                if key is None or key not in placed:
                    continue
                if key in overrides:
                    continue
                target_idx = index_of.get(placed[key])
                if target_idx is None or si.sink == target_idx:
                    continue
                try:
                    pulse.sink_input_move(si.index, target_idx)
                    moved += 1
                    logger.info(
                        "audio_reconfig: put '%s' back on %s", key, placed[key],
                    )
                except Exception as exc:
                    logger.warning(
                        "audio_reconfig: could not put '%s' back on %s: %s",
                        key, placed[key], exc,
                    )
    except Exception as exc:
        logger.warning("audio_reconfig: restore failed: %s", exc)

    return moved


@contextlib.contextmanager
def audio_reconfiguration(duration_s: float = DEFAULT_WINDOW_S, restore: bool = True):
    """Run a graph-rebuilding block with the channels held in place.

    Opens the router window, snapshots where every app is, runs the block, then
    puts the apps back and re-applies the saved overrides. The restore runs even
    when the block raises: a failed restart is exactly when streams are most
    likely to be stranded off their channel.

    Pass ``restore=False`` for a change that is *meant* to move streams (an EQ
    mode switch that sends everything to a different sink); the router window
    still applies, so the move is not misread as a manual one.
    """
    placed = snapshot_channel_streams() if restore else {}
    begin(duration_s)
    try:
        yield placed
    finally:
        try:
            if restore:
                restore_channel_streams(placed)
                from arctis_sound_manager.pw_utils import reapply_routing_overrides
                reapply_routing_overrides()
        except Exception as exc:
            logger.warning("audio_reconfig: restore pass failed: %s", exc)
        finally:
            end()
