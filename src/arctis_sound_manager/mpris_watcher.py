# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import asyncio
import logging
import threading
from typing import NamedTuple

logger = logging.getLogger(__name__)

_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_PATH = "/org/mpris/MediaPlayer2"
_POLL_INTERVAL = 2.0


class NowPlaying(NamedTuple):
    title: str
    artist: str  # empty string if unknown


class MprisWatcher:
    """Polls the active MPRIS2 media player via D-Bus in a background thread.

    Uses dbus_next (already a project dependency) with a dedicated asyncio
    event loop so the threading model of OledManager is unchanged.
    Proxy objects are cached per service to avoid repeated introspection.
    """

    def __init__(self) -> None:
        self._now_playing: NowPlaying | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MprisWatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def get_now_playing(self) -> NowPlaying | None:
        with self._lock:
            return self._now_playing

    # ── background thread ────────────────────────────────────────────────────

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._poll_loop())
        except Exception as exc:
            logger.debug("MprisWatcher event loop exited: %s", exc)
        finally:
            self._loop.close()

    async def _poll_loop(self) -> None:
        try:
            from dbus_next.aio import MessageBus  # type: ignore
        except ImportError:
            logger.warning("dbus_next not available; MPRIS Now Playing disabled")
            return

        try:
            bus = await MessageBus().connect()
        except Exception as exc:
            logger.warning("MPRIS: cannot connect to D-Bus session bus: %s", exc)
            return

        proxies: dict = {}  # service_name → proxy object (cached after first introspect)

        while not self._stop.is_set():
            try:
                result = await self._query(bus, proxies)
                with self._lock:
                    self._now_playing = result
            except Exception as exc:
                logger.debug("MPRIS poll error: %s", exc)
                with self._lock:
                    self._now_playing = None
                proxies.clear()

            await asyncio.sleep(_POLL_INTERVAL)

        try:
            bus.disconnect()
        except Exception:
            pass

    async def _query(self, bus, proxies: dict) -> NowPlaying | None:
        from dbus_next import Message, MessageType  # type: ignore

        # List running D-Bus services and keep only MPRIS ones
        reply = await bus.send_message(Message(
            message_type=MessageType.METHOD_CALL,
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="ListNames",
        ))
        if reply.message_type == MessageType.ERROR:
            return None

        mpris_services = [n for n in reply.body[0] if n.startswith(_MPRIS_PREFIX)]
        if not mpris_services:
            return None

        for service in mpris_services:
            try:
                if service not in proxies:
                    introspection = await bus.introspect(service, _MPRIS_PATH)
                    proxies[service] = bus.get_proxy_object(service, _MPRIS_PATH, introspection)

                player = proxies[service].get_interface("org.mpris.MediaPlayer2.Player")

                status = await player.get_playback_status()
                if status != "Playing":
                    continue

                metadata = await player.get_metadata()

                title_v = metadata.get("xesam:title")
                title = (title_v.value if hasattr(title_v, "value") else str(title_v)) if title_v else ""

                artist_v = metadata.get("xesam:artist")
                artists = (artist_v.value if hasattr(artist_v, "value") else []) if artist_v else []
                artist = artists[0] if artists else ""

                if title:
                    return NowPlaying(title=title, artist=artist)

            except Exception as exc:
                logger.debug("MPRIS query failed for %s: %s", service, exc)
                proxies.pop(service, None)

        return None
