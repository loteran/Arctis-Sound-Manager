# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import shutil
import subprocess
import time

import pulsectl

from arctis_sound_manager.constants import (PULSE_CHAT_NODE_NAME,
                                            PULSE_MEDIA_NODE_NAME,
                                            STEELSERIES_VENDOR_ID)

ONLY_PHYSICAL = 1
ONLY_VIRTUAL = 2
ALL_SINKS = 3


def _pid_matches(product_id_attr: str, product_id: 'int | list[int] | None') -> bool:
    """Compare a proplist PID value (any hex format) against an int or list of ints.

    PipeWire/PulseAudio may expose `device.product.id` as "0x22a1", "22a1",
    "0X22A1", etc. depending on version and backend. Parsing as int makes the
    match robust to any of these variations.
    """
    if product_id is None:
        return True
    if not product_id_attr:
        return False
    try:
        attr_int = int(product_id_attr, 16)
    except ValueError:
        return False
    lst = product_id if isinstance(product_id, list) else [product_id]
    return attr_int in lst

class TypedPulseSinkInfo(pulsectl.PulseSinkInfo):
    name: str


class PulseAudioManager:
    _instance: 'PulseAudioManager|None' = None

    @staticmethod
    def get_instance() -> 'PulseAudioManager':
        if PulseAudioManager._instance is None:
            PulseAudioManager._instance = PulseAudioManager()

        return PulseAudioManager._instance

    # PipeWire/PulseAudio sometimes isn't ready when the daemon starts
    # (boot race, user session not yet up). Retry with exponential-ish
    # backoff before giving up on the constructor.
    _CONNECT_MAX_ATTEMPTS = 12
    _CONNECT_INITIAL_DELAY_S = 0.5
    _CONNECT_MAX_DELAY_S = 4.0

    def __init__(self):
        self.logger = logging.getLogger('PulseAudioManager')
        self.pulse = self._connect_with_retry()

    def _connect_with_retry(self) -> 'pulsectl.Pulse':
        delay = self._CONNECT_INITIAL_DELAY_S
        last_err: Exception | None = None
        for attempt in range(1, self._CONNECT_MAX_ATTEMPTS + 1):
            try:
                client = pulsectl.Pulse('arctis-sound-manager')
                if attempt > 1:
                    self.logger.info(f'Connected to PulseAudio/PipeWire on attempt {attempt}.')
                return client
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f'PulseAudio connect attempt {attempt}/{self._CONNECT_MAX_ATTEMPTS} '
                    f'failed: {e!r}; retrying in {delay:.1f}s.'
                )
                time.sleep(delay)
                delay = min(delay * 1.5, self._CONNECT_MAX_DELAY_S)
        raise RuntimeError(
            f'Could not connect to PulseAudio/PipeWire after '
            f'{self._CONNECT_MAX_ATTEMPTS} attempts: {last_err!r}. '
            'Is pipewire-pulse / pulseaudio running for this user?'
        )

    def _reconnect(self):
        try:
            self.pulse.disconnect()
        except Exception:
            pass
        self.pulse = self._connect_with_retry()

    def sink_list_wrapper(self) -> list[TypedPulseSinkInfo]:
        max_attempts = 15

        sinks: list[TypedPulseSinkInfo] = []
        for attempt in range(max_attempts):
            try:
                sinks = self.pulse.sink_list()
                break
            except Exception as e:
                if attempt >= 2:
                    self.logger.error(f'Error while getting sink list (attempt {attempt + 1}): {e}')
                else:
                    self.logger.debug(f'Sink list not ready yet (attempt {attempt + 1}), retrying...')
                self._reconnect()
                time.sleep(1)

        sinks: list[TypedPulseSinkInfo] = sinks if type(sinks) is list else [sinks] # pyright: ignore[reportAssignmentType]

        return sinks
    
    def get_arctis_sinks_classified(
        self,
        vendor_id: int = STEELSERIES_VENDOR_ID,
        product_id: int | list[int] | None = None,
    ) -> tuple['TypedPulseSinkInfo | None', 'TypedPulseSinkInfo | None']:
        """Return (game_sink, chat_sink) for devices with two ALSA PCMs.

        Distinguishes by node.name suffix (pro-output-1 / pro-output-0) and
        channel count (2ch stereo = game, 1ch mono = chat).  For single-output
        devices both elements point to the same sink.
        """
        def vendor_matches(s) -> bool:
            try:
                return int(s.proplist.get('device.vendor.id', ''), 16) == vendor_id
            except ValueError:
                return False

        sinks = [
            s for s in self.sink_list_wrapper()
            if vendor_matches(s) and _pid_matches(s.proplist.get('device.product.id', ''), product_id)
        ]

        if not sinks:
            return None, None

        # Prefer explicit node.name suffix, fall back to channel count
        game = next(
            (s for s in sinks if s.proplist.get('node.name', '').endswith('pro-output-1')),
            None,
        ) or next((s for s in sinks if getattr(s, 'channel_count', 0) == 2), None)

        chat = next(
            (s for s in sinks if s.proplist.get('node.name', '').endswith('pro-output-0')),
            None,
        ) or next((s for s in sinks if getattr(s, 'channel_count', 0) == 1), None)

        # Fallbacks for single-output devices or when properties are absent
        if game is None and len(sinks) >= 2:
            game = sinks[1]
        if chat is None and sinks:
            chat = sinks[0]
        if game is None:
            game = chat
        if chat is None:
            chat = game

        return game, chat

    def get_arctis_sinks(self, mode: int = ALL_SINKS, vendor_id: int = STEELSERIES_VENDOR_ID, product_id: int|list[int]|None = None) -> list[TypedPulseSinkInfo]:
        sinks = self.sink_list_wrapper()

        def vendor_matches(s) -> bool:
            try:
                return int(s.proplist.get('device.vendor.id', ''), 16) == vendor_id
            except ValueError:
                return False

        physical = [s for s in sinks if vendor_matches(s) and _pid_matches(s.proplist.get('device.product.id', ''), product_id)]
        virtual = [s for s in sinks if s.proplist.get('node.name', '') in (PULSE_MEDIA_NODE_NAME, PULSE_CHAT_NODE_NAME)]

        if mode == ONLY_PHYSICAL:
            sinks = physical
        elif mode == ONLY_VIRTUAL:
            sinks = virtual
        else:
            sinks = physical + virtual

        return sinks

    # Fragments that identify sinks owned by ASM. Streams stuck on any of
    # these when the headset powers off will be silently dead — we migrate
    # them to the new default sink so the user never has to touch "Channels".
    _ASM_SINK_FRAGMENTS = (
        'Arctis_Game', 'Arctis_Chat', 'Arctis_Media',
        'effect_input.sonar-game-eq',
        'effect_input.sonar-chat-eq',
        'effect_input.sonar-media-eq',
        'effect_input.sonar-output-eq',
        'effect_input.virtual-surround-7.1-hesuvi',
    )

    _REDIRECT_MAX_ATTEMPTS = 5
    _REDIRECT_RETRY_DELAY_S = 0.4

    @staticmethod
    def _routing_overrides() -> dict:
        """The user's saved app→sink pins; empty dict if unreadable."""
        try:
            from arctis_sound_manager.pw_utils import _load_overrides
            return _load_overrides() or {}
        except Exception:
            return {}

    @staticmethod
    def _stream_is_where_the_user_put_it(si, current_name: str, overrides: dict) -> bool:
        """True if *si* sits on the sink its app is pinned to.

        Keys are matched exactly as the GUI and the media router write them —
        through ``app_override_key`` (application.name + binary, issue #108) —
        with a fallback to the bare application.name for pins saved before that
        composite key existed.
        """
        if not overrides:
            return False
        try:
            from arctis_sound_manager.pw_utils import app_override_key
            app = si.proplist.get('application.name', '') or ''
            binary = si.proplist.get('application.process.binary', '') or ''
            pinned = overrides.get(app_override_key(app, binary)) or overrides.get(app)
        except Exception:
            return False
        if not pinned:
            return False
        # Pins are stored as a sink *name fragment* (e.g. "Arctis_Media"), which
        # is how every other consumer of this file matches them.
        return pinned in current_name

    def redirect_audio(self, output_sink_node_name: str) -> None:
        self.logger.info(f'Redirecting audio to {output_sink_node_name}...')

        sinks: list[TypedPulseSinkInfo] = []
        sink = None
        for attempt in range(1, self._REDIRECT_MAX_ATTEMPTS + 1):
            sinks = self.sink_list_wrapper()
            sink = next(
                (s for s in sinks
                 if s.proplist.get('node.nick', '') == output_sink_node_name
                 or s.proplist.get('node.name', '') == output_sink_node_name),
                None,
            )
            if sink is not None:
                break
            if attempt < self._REDIRECT_MAX_ATTEMPTS:
                self.logger.debug(
                    f'Sink {output_sink_node_name} not yet visible (attempt {attempt}/'
                    f'{self._REDIRECT_MAX_ATTEMPTS}), retrying in {self._REDIRECT_RETRY_DELAY_S}s...'
                )
                time.sleep(self._REDIRECT_RETRY_DELAY_S)

        if sink is None:
            self.logger.warning(
                f'Sink {output_sink_node_name} not found after {self._REDIRECT_MAX_ATTEMPTS} '
                f'attempt(s) — loopbacks may not have registered yet or failed to start'
            )
            return

        try:
            self.pulse.default_set(sink)
        except Exception as e:
            self.logger.warning(f'pulse.default_set failed: {e!r}')

        # On PipeWire, also persist via pw-metadata so the change survives
        # daemon restarts and isn't overridden by WirePlumber shortly after.
        target_name = sink.proplist.get('node.name', '') or sink.name
        pw_metadata = shutil.which('pw-metadata')
        if target_name and pw_metadata:
            payload = json.dumps({'name': target_name})
            for key in ('default.configured.audio.sink', 'default.audio.sink'):
                try:
                    # Absolute path + close_fds=False keep this on the posix_spawn
                    # path so the daemon never fork()s while libusb I/O is in
                    # flight in a sibling thread (issue #123).
                    subprocess.run(
                        [pw_metadata, '0', key, payload],
                        check=False, timeout=3, close_fds=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception as e:
                    self.logger.debug(f'pw-metadata {key} failed: {e!r}')

        # Migrate every stream currently parked on an ASM-owned sink to the
        # new target. Without this, apps that opened a stream while the headset
        # was alive stay glued to a dead loopback (issue #50).
        try:
            overrides = self._routing_overrides()
            sink_by_index = {s.index: s for s in sinks}
            for si in self.pulse.sink_input_list():
                current = sink_by_index.get(si.sink)
                if current is None or current.index == sink.index:
                    continue
                current_name = current.proplist.get('node.name', '') or current.name or ''
                if self._stream_is_where_the_user_put_it(si, current_name, overrides):
                    # The user pinned this app to the sink it is already on, and
                    # that sink is alive (it is in the enumeration above). Moving
                    # it would undo an explicit choice — and the media router
                    # would then read our own move as a manual one and persist
                    # the wrong sink as the app's override. The migration below
                    # exists to rescue streams stuck on a *dead* loopback
                    # (issue #50), which is not this case.
                    continue
                if any(frag in current_name for frag in self._ASM_SINK_FRAGMENTS):
                    try:
                        self.pulse.sink_input_move(si.index, sink.index)
                    except Exception as e:
                        app = si.proplist.get('application.name', '?')
                        self.logger.warning(
                            f'Failed to move stream {si.index} ({app}) '
                            f'from {current_name} to {output_sink_node_name}: {e!r}'
                        )
        except Exception as e:
            self.logger.warning(f'Could not enumerate streams for migration: {e!r}')
    
    def get_default_device(self) -> TypedPulseSinkInfo|None:
        try:
            server_info = self.pulse.server_info()
        except Exception:
            return None
        default_sink_name: str|None = getattr(server_info, 'default_sink_name', None)

        if default_sink_name is None:
            return None
        
        sink = next((s for s in self.sink_list_wrapper() if s.proplist.get('node.name', '') == default_sink_name), None)

        return sink

    def get_physical_source(self, vendor_id: int = STEELSERIES_VENDOR_ID, product_id: int | list[int] | None = None) -> 'pulsectl.PulseSourceInfo | None':
        """Return the first physical ALSA source (microphone) matching the given device IDs."""
        retry_attempts = 15
        sources: list = []
        while retry_attempts > 0:
            try:
                sources = self.pulse.source_list()
                break
            except Exception as e:
                self.logger.error(f'Error getting source list: {e}')
                retry_attempts -= 1
                self._reconnect()
                time.sleep(1)

        def vendor_matches(s) -> bool:
            try:
                return int(s.proplist.get('device.vendor.id', ''), 16) == vendor_id
            except ValueError:
                return False

        physical = [s for s in sources
                    if vendor_matches(s)
                    and _pid_matches(s.proplist.get('device.product.id', ''), product_id)
                    and s.proplist.get('device.class', '') != 'monitor']
        return physical[0] if physical else None

    def set_default_source(self, name: str) -> None:
        """Set the default PipeWire/PulseAudio source by node name."""
        try:
            self.pulse.source_default_set(name)
            self.logger.info("Default source set to %s", name)
        except Exception as e:
            self.logger.warning("Failed to set default source to %s: %r", name, e)

    def set_sink_volume_by_node(self, node_name: str, pct: int) -> bool:
        """Set the volume of the sink whose ``node.name`` is *node_name*.

        Used by the daemon to re-assert a user-saved virtual-sink level after a
        loopback is (re)created (issue #134). Unlike :meth:`set_mix`, this looks
        the sink up by node name across the full sink list — the Media sink
        (``Arctis_Media``) is not part of ``ONLY_VIRTUAL`` — so it can restore
        any of the three virtual sinks.

        Returns ``True`` if the sink was found and the volume applied, ``False``
        if the sink is not present yet (so the caller can retry on a later tick).
        """
        pct = max(0, min(100, int(pct)))
        try:
            sink = next(
                (s for s in self.sink_list_wrapper()
                 if s.proplist.get('node.name', '') == node_name),
                None,
            )
        except Exception as exc:
            self.logger.warning("set_sink_volume_by_node(%s): list failed: %r", node_name, exc)
            return False
        if sink is None:
            return False
        try:
            self.pulse.volume_set_all_chans(sink, pct / 100)
        except Exception as exc:
            self.logger.warning("set_sink_volume_by_node(%s): set failed: %r", node_name, exc)
            return False
        return True

    def set_mix(self, media_mix: int, chat_mix: int):
        if media_mix > 100:
            media_mix = 100
        if chat_mix > 100:
            chat_mix = 100

        sinks = self.get_arctis_sinks(ONLY_VIRTUAL)

        media = next((s for s in sinks if s.proplist.get('node.name', '') == PULSE_MEDIA_NODE_NAME), None)
        chat = next((s for s in sinks if s.proplist.get('node.name', '') == PULSE_CHAT_NODE_NAME), None)

        if media:
            self.pulse.volume_set_all_chans(media, media_mix / 100)
        if chat:
            self.pulse.volume_set_all_chans(chat, chat_mix / 100)

