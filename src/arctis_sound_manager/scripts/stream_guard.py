#!/usr/bin/env python3
# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep unwanted audio out of Discord's screen share, event-driven.

Discord creates its own ``discord_capture`` node when a screen share starts and
links every playback stream into it — the whole system's audio goes out to the
call. It relinks continuously, so a one-shot ``pw-link -d`` does not hold; the
obvious workaround is a polling loop, but any poll interval is also the window
during which private audio is already on the wire.

This daemon instead uses ``pw-mon`` as a wake signal: PipeWire announces graph
changes as they happen, so a link Discord has just created is destroyed within
the debounce interval (~0.1 s) rather than at the next tick of a timer. After
the initial object enumeration ``pw-mon`` is silent on an idle graph, so the
guard costs nothing while nothing is happening.

Which sources are allowed is decided by :mod:`arctis_sound_manager.stream_guard`
from the Arctis channel each stream sits on — see that module for the rule.
"""
import errno
import json
import logging
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from arctis_sound_manager.log_setup import configure_logging
from arctis_sound_manager.pw_utils import _abs_exe, _pw_run, _parse_pw_dump_output
from arctis_sound_manager.stream_guard import (CONFIG_FILE,
                                               DEFAULT_CAPTURE_NODES,
                                               GuardConfig, describe_cut,
                                               link_ports, links_to_cut,
                                               load_config)

configure_logging(default=logging.INFO, fmt="[%(levelname)s] %(message)s")
log = logging.getLogger("stream_guard")

# Quiet period after the last pw-mon line before we act. PipeWire announces a
# new link as several objects (link, ports, node params); acting on the first
# line would mean re-dumping the graph once per object in the burst.
DEBOUNCE = 0.08          # seconds
# Safety net against a missed or coalesced pw-mon event — never the primary
# mechanism. It is deliberately two-speed: `pw-dump` is a subprocess spawn plus
# a full-graph JSON parse, so running it on a fixed short timer costs real CPU
# around the clock to guard against something that only matters while a screen
# share is actually up. While no capture node exists there is nothing a missed
# event could have done, so the net can be slack.
SAFETY_INTERVAL_ACTIVE = 5.0    # seconds, while a capture node is present
SAFETY_INTERVAL_IDLE = 60.0     # seconds, while none is
# How long a dead pw-mon stays dead before we respawn it.
RESPAWN_DELAY = 2.0      # seconds

_PID_FILE = Path.home() / ".config" / "arctis_manager" / "stream_guard.pid"


# ── config caching ─────────────────────────────────────────────────────────
# Re-read stream_guard.json when it changes so a channel switch in the GUI
# takes effect without restarting the service.
_cfg_cache: tuple[float, GuardConfig] | None = None


def _current_config() -> GuardConfig:
    global _cfg_cache
    try:
        mtime = CONFIG_FILE.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _cfg_cache is None or _cfg_cache[0] != mtime:
        cfg = load_config()
        _cfg_cache = (mtime, cfg)
        log.info("Config: enabled=%s channels=%s", cfg.enabled, ",".join(cfg.channels) or "(none)")
    return _cfg_cache[1]


# ── PipeWire I/O ───────────────────────────────────────────────────────────

def _pw_dump() -> list:
    """Snapshot the graph. Returns [] on failure — the tick then does nothing.

    ``pw-dump`` can print more than one concatenated JSON document when a
    registry object is added/removed mid-enumeration (see
    ``pw_utils._parse_pw_dump_output`` for the full explanation). Falls back
    to that recovery path instead of treating a momentary hiccup as an empty
    graph, which used to make Stream Guard miss a tick of screen-share
    policing.
    """
    try:
        r = _pw_run(["pw-dump"], capture_output=True, text=True, timeout=3)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            recovered = _parse_pw_dump_output(r.stdout)
            if recovered is not None:
                return recovered
            raise
    except Exception as exc:
        log.warning("pw-dump failed: %s", exc)
        return []


def _destroy_links(dump: list, link_ids: list[int]) -> int:
    """Destroy each doomed link, identified by its ports rather than its id.

    Returns how many destroy calls succeeded. Failures are expected and
    benign: Discord may have already torn the link down between our dump and
    this call, in which case the port pair simply names nothing any more.

    Destroying by id (``pw-cli destroy <id>``) used to be a use-after-free on
    the graph: PipeWire recycles global ids within seconds, especially on a
    graph Discord keeps churning by relinking continuously, so an id captured
    in *dump* can name a different object — of any type, not only another
    link — by the time this runs (issue CHA-3, reproduced live: a stale id
    landed on ``Arctis_Game`` and killed the sink outright). ``link_ports``
    converts each id to its ``(output-port, input-port)`` pair from this same
    *dump* first, and destruction goes through ``pw-link -d out in``, which
    is content-addressed: it only ever removes the link presently connecting
    those two exact ports.
    """
    destroyed = 0
    for out_port, in_port in link_ports(dump, link_ids):
        try:
            r = _pw_run(["pw-link", "-d", str(out_port), str(in_port)],
                        capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                destroyed += 1
            else:
                log.debug("destroy %d->%d failed: %s", out_port, in_port,
                          (r.stderr or "").strip())
        except Exception as exc:
            log.debug("destroy %d->%d raised: %s", out_port, in_port, exc)
    return destroyed


def capture_node_present(dump: list) -> bool:
    """True when one of the policed capture nodes exists in *dump*."""
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        name = (obj.get("info") or {}).get("props", {}).get("node.name")
        if name in DEFAULT_CAPTURE_NODES:
            return True
    return False


def process_tick() -> tuple[int, bool]:
    """Run one guard pass.

    Returns ``(links destroyed, a capture node is present)``. The second value
    drives the safety-net cadence: there is no point re-dumping the graph every
    few seconds when nothing is capturing it.
    """
    cfg = _current_config()
    if not cfg.enabled:
        return 0, False
    dump = _pw_dump()
    if not dump:
        return 0, False
    active = capture_node_present(dump)
    doomed = links_to_cut(dump, cfg.allowed_sinks())
    if not doomed:
        return 0, active
    for label in describe_cut(dump, doomed):
        log.info("Cutting from stream: %s", label)
    return _destroy_links(dump, doomed), active


# ── singleton ──────────────────────────────────────────────────────────────

def _acquire_singleton() -> bool:
    """Return True if we are the sole running instance, False otherwise."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            log.warning(
                "Another asm-stream-guard instance (PID %d) is already running — exiting.",
                old_pid,
            )
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale PID file — take over
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    return True


def _release_singleton() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── main loop ──────────────────────────────────────────────────────────────

def _spawn_monitor() -> subprocess.Popen | None:
    """Start ``pw-mon``, whose stdout we use only as a change notification."""
    try:
        return subprocess.Popen(
            [_abs_exe("pw-mon")],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=False,
        )
    except Exception as exc:
        log.warning("Could not start pw-mon: %s", exc)
        return None


def _drain(fd: int) -> bytes | None:
    """Read whatever pw-mon has buffered. ``None`` when the pipe hit EOF.

    The bytes are returned rather than discarded because they are the cheapest
    signal available for "did a capture node just appear": we have already paid
    to read them, and scanning them beats spawning ``pw-dump`` to find out.
    """
    try:
        chunk = os.read(fd, 65536)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
            return b""
        return None
    return chunk if chunk else None


_CAPTURE_NEEDLES = tuple(n.encode() for n in DEFAULT_CAPTURE_NODES)


def _mentions_capture_node(chunk: bytes) -> bool:
    """True when a pw-mon burst names one of the capture nodes.

    PipeWire announces the node itself — ``node.name = "discord_capture"`` —
    when Discord creates it, so this catches the start of a share. Individual
    link objects identify their ends by id rather than name, which is why this
    is only used to decide *whether a share is up*, never to decide which links
    to cut: that always comes from a real graph snapshot.
    """
    return any(n in chunk for n in _CAPTURE_NEEDLES)


def _main_loop() -> None:
    log.info("Starting screen-share stream guard")
    mon = _spawn_monitor()
    dirty = True          # evaluate once at startup — a share may already be up
    active = False        # a capture node exists, i.e. a share is running
    last_check = 0.0

    while True:
        try:
            if mon is None or mon.poll() is not None:
                if mon is not None:
                    log.warning("pw-mon exited — respawning")
                time.sleep(RESPAWN_DELAY)
                mon = _spawn_monitor()
                dirty = True
                continue

            fd = mon.stdout.fileno()
            # While events keep arriving, keep draining; only once the graph has
            # been quiet for DEBOUNCE do we snapshot and act. With nothing
            # happening this blocks for the whole safety interval.
            safety = SAFETY_INTERVAL_ACTIVE if active else SAFETY_INTERVAL_IDLE
            timeout = DEBOUNCE if dirty else safety
            ready, _, _ = select.select([fd], [], [], timeout)
            if ready:
                chunk = _drain(fd)
                if chunk is None:
                    log.warning("pw-mon stream closed — respawning")
                    try:
                        mon.terminate()
                    except Exception:
                        pass
                    mon = None
                    continue
                # Graph churn is constant on a busy machine — streams starting,
                # volumes moving, nodes idling. Snapshotting the graph for all
                # of it is what made an event-driven guard cost more than the
                # polling loop it replaced. Only a burst that names a capture
                # node, or one arriving while a share is already up, can have
                # changed anything this guard cares about.
                if active or _mentions_capture_node(chunk):
                    dirty = True
                continue

            now = time.monotonic()
            if dirty or (now - last_check) >= safety:
                last_check = now
                dirty = False
                _, active = process_tick()

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.error("Error: %s", exc)
            time.sleep(1)


def main() -> None:
    if not _acquire_singleton():
        sys.exit(0)
    try:
        _main_loop()
    except KeyboardInterrupt:
        pass
    finally:
        _release_singleton()


if __name__ == "__main__":
    main()
