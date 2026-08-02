# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Screen + multi-track audio capture feeding a rolling clip buffer.

The GStreamer/portal half of the clip feature. It keeps a live capture running
and hands encoded frames to :mod:`clip_buffer`, so that "save the last N
seconds" is answered from memory rather than by starting a recording after the
fact — the moment worth clipping has always already happened.

GStreamer and PyGObject are an optional dependency: ASM works exactly as before
without them, and only the clip feature is unavailable. Nothing here is
imported at ASM start-up for that reason.

Four things about this pipeline were established by measurement on real
hardware, and each of them fails in a way that points somewhere else:

* **The portal session must last as long as the capture.** It is bound to the
  D-Bus client that created it; drop that and the node id stays valid-looking
  while its stream is dead. pipewiresrc then reports "target not found". It
  must also be closed once the capture is over — see ScreenCastPortal.close().
  Releasing the reference is not closing it, and the compositor draws a
  recording indicator for every session still open.
* **Downstream caps must be concrete.** With an ANY-caps sink, pipewiresrc has
  no format to offer, connects with "no format given", and the core rejects it
  as — again — "target not found", a message about the target for what is
  really an empty format list.
* **Never connect without a target.** With autoconnect and no path,
  pipewiresrc binds whatever source it finds first; on a machine with a
  capture card that means silently recording the webcam instead of the game.
* **The frame never needs to leave the GPU.** nvh264enc accepts GLMemory
  directly, so glupload → glcolorconvert → encoder avoids a full-resolution
  copy per frame.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections import deque
from pathlib import Path

from arctis_sound_manager.clip_buffer import NANOSECONDS, ClipBuffer, Frame

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "arctis_manager"
TOKEN_FILE = CONFIG_DIR / "clip_screencast_token.json"
CLIP_DIR = Path.home() / "Videos" / "ASM Clips"

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"

# Encoders in preference order: (element, extra props, needs GL upload).
# The GL forms keep the frame on the GPU; x264enc is the portable last resort
# and the only one that costs real CPU.
ENCODERS = [
    ("nvh264enc", "preset=low-latency-hq gop-size={gop} bitrate={kbps}", True),
    ("vah264enc", "key-int-max={gop} bitrate={kbps}", False),
    ("vah264lpenc", "key-int-max={gop} bitrate={kbps}", False),
    ("x264enc", "speed-preset=veryfast tune=zerolatency key-int-max={gop} bitrate={kbps}", False),
]

# Apps that play audio but are never what a clip is about, as the *words* their
# stream names are built from. Matching whole names was the mistake: PulseAudio
# reports "Google Chrome", not "chrome", and "OBS Studio", not "obs" — so an
# exact-membership test passed both straight through and clips came out labelled
# after the browser the user happened to have open.
_NOT_A_GAME = {
    "firefox", "chromium", "chrome", "brave", "vivaldi", "librewolf", "epiphany",
    "discord", "vesktop", "armcord", "spotify", "vlc", "mpv", "telegram",
    "speech-dispatcher", "obs", "asm-gui", "plasmashell", "kdeconnect", "zapzap",
    # Names that only mean anything as a phrase. "webrtc" and "voiceengine" are
    # each too generic to block on their own; together they are what every
    # Chromium tab calls its audio stream.
    "webrtc voiceengine",
    # The rest of the desktop's own noise, which is never the subject of a clip.
    "asm", "arctis", "notification", "notifications", "canberra", "libcanberra",
}


def _is_not_a_game(name: str) -> bool:
    """Whether *name* is one of the apps a clip is never about.

    Split into words first, then tested word by word, because the names that
    reach the audio graph are display names: "Google Chrome", "OBS Studio",
    "Brave Browser", "WEBRTC_VoiceEngine". A whole-string test only ever caught
    the bare binary name, which is the one form users do not see.

    Word matching rather than a plain substring so a game is not blocked for
    containing a blocked word inside a longer one — "Chromatic" is not Chrome.
    Multi-word entries are matched against the normalised name as a phrase.
    """
    words = [w for w in "".join(
        c if c.isalnum() else " " for c in name.lower()).split() if w]
    if not words:
        return True
    normalised = " ".join(words)
    for blocked in _NOT_A_GAME:
        if " " in blocked or "-" in blocked:
            if blocked.replace("-", " ") in normalised:
                return True
        elif blocked in words:
            return True
    return False

# The Game channel, by the node names a stream can be sitting on: the virtual
# sink when the Sonar EQ is off, its filter-chain input when it is on.
_GAME_SINK_NAMES = ("Arctis_Game", "effect_input.sonar-game-eq")

# How much recent history the live rate read-outs average over. Long enough to
# be steady, short enough that a stall shows up while it is still happening.
_RATE_WINDOW_S = 4.0

# Capture rates offered, and the one used unless asked otherwise.
#
# 30 rather than 60: the portal stream only produces a frame when the screen
# changes, so the ceiling is a ceiling and nothing more — asking for 60 does not
# make a compositor send 60. What it does do is set the keyframe interval (gop
# == fps) and the encoder's workload against a rate that is rarely reached,
# which costs bitrate and CPU for frames that never arrive. 60 is kept for the
# machines that genuinely sustain it.
FPS_CHOICES = (30, 60)
DEFAULT_FPS = 30

SONAR_MONITORS = [
    ("game", "Arctis_Game.monitor"),
    ("chat", "Arctis_Chat.monitor"),
    ("media", "Arctis_Media.monitor"),
]


class ClipCaptureUnavailable(RuntimeError):
    """Raised when the machine cannot support clip capture at all."""


def announce_clip(path: Path, seconds: float, game: str | None = None) -> None:
    """Say a clip was taken, with a sound and a desktop notification.

    A shortcut pressed mid-game gives no other feedback: the window is not on
    screen, the log is not either, and the only way to know whether it worked
    is to alt-tab and look in a folder — by which point the moment is gone and
    the user has pressed it three more times. Both channels are best-effort;
    neither is worth failing a save over.
    """
    import subprocess

    title = "Clip saved"
    body = f"{seconds:.0f}s · {game}" if game else f"{seconds:.0f}s"

    try:
        subprocess.Popen(
            ["notify-send", "--app-name=Arctis Sound Manager",
             "--icon=media-record", "--expire-time=4000",
             title, f"{body}\n{path.name}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.debug("no desktop notification: %s", exc)

    # A concrete file first, not a sound-theme id. canberra-gtk-play exits 0
    # whether or not the theme actually contains "screen-capture", so asking for
    # an id gives a success that plays nothing — which is how the shortcut ended
    # up silent while appearing to work.
    for path_candidate in (
        "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
        "/usr/share/sounds/freedesktop/stereo/screen-capture.oga",
        "/usr/share/sounds/freedesktop/stereo/message.oga",
    ):
        if not Path(path_candidate).exists():
            continue
        for player in ("paplay", "pw-play", "canberra-gtk-play"):
            argv = ([player, "-f", path_candidate] if player == "canberra-gtk-play"
                    else [player, path_candidate])
            try:
                subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    log.debug("no sound player or cue file available for the clip cue")


def _mark_default_audio_track(path: Path, order: list[str],
                              frames: dict) -> None:
    """Leave exactly one audio track flagged as the default one to play.

    Matroska's FlagDefault is 1 when it is not written, and matroskamux never
    writes it — so a clip carrying five channels tells every player that all
    five are the default. The player then picks by its own tie-break, and since
    most channels in a normal session are empty (nothing is routed to Chat, the
    microphone is muted), what it usually picks is silence. The clip looks like
    it recorded no audio when two of its tracks are perfectly loud, which is
    exactly the report this fixes.

    Which track to keep is decided by how many encoded bytes it holds. Opus
    compresses silence to almost nothing — in the reported clip the three empty
    channels came to ~15 kB each while the two real ones were 30 kB and 151 kB
    — so the answer is already sitting in the buffer and costs nothing. It
    beats picking the first channel, which is the Game channel and is empty for
    anyone playing through the headset directly.

    A stream copy, so it is a remux and not a re-encode: ~50 ms on a 30 s clip.
    Any failure leaves the original file exactly as it was — a clip with an
    awkward default flag is worth far more than no clip.
    """
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    audio = [t for t in order if t != "video"]
    if ffmpeg is None or len(audio) < 2:
        return

    loudest = max(range(len(audio)),
                  key=lambda i: sum(len(f.payload) for f in frames[audio[i]]))
    temp = path.with_name(path.name + ".default.mkv")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
             "-map", "0", "-c", "copy",
             "-disposition:a", "0", f"-disposition:a:{loudest}", "default",
             str(temp)],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not temp.exists() or temp.stat().st_size == 0:
            log.debug("could not set the default audio track: %s",
                      (result.stderr or "").strip()[:200])
            temp.unlink(missing_ok=True)
            return
        temp.replace(path)
        log.info("clip default audio track: %s", audio[loudest])
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("could not set the default audio track: %s", exc)
        temp.unlink(missing_ok=True)


def _require_gst():
    """Import GStreamer, turning a missing optional dependency into a clear error."""
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gio, GLib, Gst  # noqa: F401
    except (ImportError, ValueError) as exc:
        raise ClipCaptureUnavailable(
            "Clip capture needs PyGObject and GStreamer "
            "(python-gobject, gst-plugins-base/good/bad, gst-plugin-pipewire)."
        ) from exc
    return Gio, GLib, Gst


# Runtimes a game is launched through. A process living under one of these is
# a game far more reliably than one that merely fails a blocklist test — which
# is why these are checked first, and why the feature is not limited to titles
# anyone thought to list.
_GAME_RUNTIME_HINTS = (
    "steamapps", "proton", "wine", "lutris", "heroic", "bottles",
    "/steam/", "gamescope", "vkd3d", "dxvk",
)


def _process_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except OSError:
        return ""


def _process_environ(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            return fh.read().replace(b"\0", b"\n").decode("utf-8", "replace").lower()
    except OSError:
        return ""       # not ours to read; only a hint is lost


def detect_game() -> str | None:
    """Name the app a clip is most likely about, for labelling it.

    This is a *name for the clip*, not the screen being captured. What is on
    screen was chosen in the portal picker and Wayland never tells us what it
    was — so anything built on this must say "Game", never "Recording": a user
    who picked their OBS window and read "Recording: Google Chrome" has been
    told the capture is pointed somewhere it is not.

    Wayland forbids enumerating other apps' windows, so the screen cannot say
    what is on it. The audio graph can, and ASM already watches it.

    The first question asked is the one only this app can answer: *which stream
    did the user put on the Game channel?* That is a routing decision they made
    themselves (or that video_router made for them), and it beats every
    heuristic — no list to maintain, and it is right by construction. It is also
    what kept mislabelling clips: with a browser playing and a game running,
    name-based detection picked whichever stream came first and wrote
    "WEBRTC_VoiceEngine" onto a Genshin clip.

    Identification is then positive first and negative second. A blocklist alone
    ("anything that is not a browser is a game") only works for titles someone
    remembered to exclude, and mislabels every unlisted app — so a stream whose
    process runs under Steam, Proton, Wine, Lutris, Heroic, Bottles or gamescope
    is taken as the game outright, whatever it is called. Only when nothing
    matches does the blocklist decide, which keeps ordinary native games working
    without needing to be known in advance.
    """
    try:
        import pulsectl
    except ImportError:
        return None

    def label(si) -> str:
        name = (si.proplist.get("application.name")
                or si.proplist.get("application.process.binary") or "")
        return name.removesuffix(".exe").strip()

    try:
        with pulsectl.Pulse("asm-clip-detect") as pulse:
            streams = pulse.sink_input_list()

            # 0. Whatever the user routed to the Game channel.
            game_sinks = {
                sink.index for sink in pulse.sink_list()
                if ((getattr(sink, "proplist", None) or {}).get("node.name")
                    or getattr(sink, "name", "")) in _GAME_SINK_NAMES}
            for si in streams:
                if si.sink not in game_sinks:
                    continue
                name = label(si)
                if name and not _is_not_a_game(name):
                    return name

            # 1. A stream running under a known game runtime.
            for si in streams:
                try:
                    pid = int(si.proplist.get("application.process.id", ""))
                except (TypeError, ValueError):
                    continue
                haystack = _process_cmdline(pid) + _process_environ(pid)
                if any(hint in haystack for hint in _GAME_RUNTIME_HINTS):
                    if (name := label(si)):
                        return name

            # 2. Fall back to "a playback stream that is not obviously not a game".
            for si in streams:
                name = label(si)
                if name and not _is_not_a_game(name):
                    return name
    except Exception as exc:
        log.debug("game detection failed: %s", exc)
    return None


def resolve_audio_sources() -> list[tuple[str, str]]:
    """Choose which monitors to record, as (track name, PulseAudio source).

    Every Sonar channel that exists is recorded — game, chat and media — plus
    any non-Sonar sink that has application streams on it, plus the mic.

    Which channels are *in use* deliberately does not come into it. This runs
    once, when capture starts, and capture is started long before the thing
    worth clipping happens: launch the game after that and its channel was
    empty at the moment the pipeline was built, so the branch was never
    created and the clip comes out with no game audio at all. Nothing can
    recover it afterwards — the buffer only holds what was wired up. An idle
    channel costs one Opus encoder on silence; a missing one costs the clip.

    The non-Sonar sinks are what covers a game left on the headset directly or
    on a Bluetooth headset, where the Sonar monitors would genuinely be silent.
    """
    try:
        import pulsectl
    except ImportError:
        return list(SONAR_MONITORS)

    sonar = {name for _, m in SONAR_MONITORS for name in (m.removesuffix(".monitor"),)}
    tracks: list[tuple[str, str]] = []
    try:
        with pulsectl.Pulse("asm-clip-sources") as pulse:
            sinks = {s.index: s for s in pulse.sink_list()}
            present = {s.name for s in sinks.values()}
            apps: dict[int, list[str]] = {}
            for si in pulse.sink_input_list():
                name = (si.proplist.get("application.name")
                        or si.proplist.get("application.process.binary"))
                if name and si.sink in sinks:
                    apps.setdefault(si.sink, []).append(name)

            tracks += [(label, mon) for label, mon in SONAR_MONITORS
                       if mon.removesuffix(".monitor") in present]

            for idx, names in apps.items():
                sink = sinks[idx]
                if sink.name in sonar:
                    continue
                tracks.append((_track_label(sorted(set(names))[0]),
                               sink.name + ".monitor"))

            # The microphone is always its own track. Per-channel game/chat
            # separation depends on those apps being routed through the Sonar
            # channels, which a user listening on Bluetooth earbuds is not doing
            # at all — but the mic is a separate capture device regardless, so
            # this is the one split that always works. It is also the split
            # people actually want: being able to mute yourself in a clip after
            # the fact, without touching the game audio.
            if (mic := _default_microphone(pulse)):
                tracks.append(("mic", mic))
    except Exception as exc:
        log.warning("could not resolve audio sources: %s", exc)
        return list(SONAR_MONITORS)

    return _unique_tracks(tracks) or list(SONAR_MONITORS)


def _track_label(app_name: str) -> str:
    """Turn an application name into something usable as a track name.

    The label ends up in a GStreamer element name (``appsink name=audio_x``)
    and in a Matroska track title, and parse_launch rejects anything outside
    ``[A-Za-z0-9_-]`` — an app called "Rocket League (Steam)" would take the
    whole pipeline down with a parse error, which reads as "clips are broken"
    rather than "that app has brackets in its name".
    """
    cleaned = "".join(
        c if c.isalnum() else "_"
        for c in app_name.removesuffix(".exe").strip().lower())
    return "_".join(part for part in cleaned.split("_") if part) or "app"


def _unique_tracks(tracks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop repeats and make the names distinct.

    Two sinks can carry the same application, and two appsinks cannot carry the
    same element name: the duplicate makes parse_launch fail and takes every
    track down with it, including the Sonar ones that were fine.
    """
    seen_sources: set[str] = set()
    seen_labels: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, source in tracks:
        if source in seen_sources:
            continue
        seen_sources.add(source)
        unique, suffix = label, 2
        while unique in seen_labels:
            unique, suffix = f"{label}_{suffix}", suffix + 1
        seen_labels.add(unique)
        out.append((unique, source))
    return out


def _default_microphone(pulse) -> str | None:
    """The source ASM's Micro EQ is pinned to, else the system default input.

    Honours micro_input_source when the user has chosen one, so a clip records
    the same microphone they hear themselves on. Monitors are never candidates:
    they are a sink's own output, not an input.

    A Bluetooth input is never selected automatically. Opening one forces the
    card off A2DP and onto HSP/HFP, because Bluetooth audio cannot do
    high-quality playback and microphone capture at the same time. The user
    hears their music collapse to 16 kHz mono the instant capture starts, and
    on some headsets the transport switch drops the link outright. Recording a
    microphone is never worth silently wrecking playback, so a Bluetooth mic is
    used only when explicitly chosen — at which point the trade is the user's
    to make, not ours.
    """
    chosen = None
    try:
        from arctis_sound_manager.settings import GeneralSettings
        chosen = getattr(GeneralSettings.read_from_file(), "micro_input_source", None)
    except Exception:
        chosen = None

    sources = [s for s in pulse.source_list()
               if not s.name.endswith(".monitor")
               and s.proplist.get("device.class", "") != "monitor"
               and not s.name.startswith("effect_")]
    if not sources:
        return None

    if chosen and chosen not in ("__auto__", "__manual__"):
        for source in sources:
            if chosen in (source.name, source.proplist.get("node.name", "")):
                return source.name

    def is_bluetooth(source) -> bool:
        return (source.name.startswith("bluez_")
                or str(source.proplist.get("device.api", "")) == "bluez5"
                or str(source.proplist.get("device.bus", "")) == "bluetooth")

    wired = [s for s in sources if not is_bluetooth(s)]
    if not wired:
        log.info("only Bluetooth microphones available — leaving the mic track "
                 "out rather than forcing the headset off A2DP")
        return None

    try:
        default = pulse.server_info().default_source_name
    except Exception:
        default = None
    for source in wired:
        if source.name == default:
            return source.name
    return wired[0].name


class ScreenCastPortal:
    """xdg-desktop-portal ScreenCast client.

    Instances must be kept alive for as long as the stream is used: the portal
    ties the session to its creating client and tears it down when that client
    disappears, leaving a node id whose stream no longer produces anything.
    """

    def __init__(self):
        self._Gio, self._GLib, _ = _require_gst()
        self.bus = self._Gio.bus_get_sync(self._Gio.BusType.SESSION, None)
        self.unique = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session: str | None = None
        self.closed = False
        self._result: tuple[int, dict] | None = None
        self._token = 0
        # Kept so close() can drop it: a subscription outlives the session it
        # was made for, and the callback holds this object alive with it.
        self._closed_sub: int | None = None

    def _call(self, method: str, signature: str, pre_args: tuple, options: dict) -> dict:
        GLib, Gio = self._GLib, self._Gio
        self._token += 1
        token = f"asm_clip_{os.getpid()}_{self._token}"
        req_path = f"/org/freedesktop/portal/desktop/request/{self.unique}/{token}"

        loop = GLib.MainLoop()
        self._result = None
        sub = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Request", "Response", req_path, None,
            Gio.DBusSignalFlags.NONE,
            lambda *a: (setattr(self, "_result", a[-1].unpack()), loop.quit()))
        try:
            # handle_token must go in with the other options: an a{sv} needs
            # every value to be a GLib.Variant, and patching it in afterwards
            # is what turns them back into bare Python types.
            opts = dict(options)
            opts["handle_token"] = GLib.Variant("s", token)
            self.bus.call_sync(PORTAL, PORTAL_PATH, SCREENCAST, method,
                               GLib.Variant(signature, (*pre_args, opts)),
                               None, Gio.DBusCallFlags.NONE, -1, None)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)

        code, results = self._result  # type: ignore[misc]
        if code != 0:
            raise ClipCaptureUnavailable(
                f"{method}: portal returned {code}"
                f"{' (cancelled)' if code == 1 else ''}")
        return results

    def open(self, window: bool = False) -> tuple[int, int]:
        """Handshake through to a live stream; returns (pipewire fd, node id).

        A saved restore_token makes the picker appear only the first time ever.
        Wayland requires that consent once and gives no way around it — every
        screen recorder on the platform has the same one-off prompt.
        """
        GLib, Gio = self._GLib, self._Gio

        res = self._call("CreateSession", "(a{sv})", (), {
            "session_handle_token": GLib.Variant("s", f"asm_clip_{os.getpid()}")})
        self.session = res["session_handle"]

        self._closed_sub = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Session", "Closed", self.session,
            None, Gio.DBusSignalFlags.NONE,
            lambda *a: setattr(self, "closed", True))

        select = {
            "types": GLib.Variant("u", 2 if window else 1 | 2),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 2),
            "persist_mode": GLib.Variant("u", 2),
        }
        if (saved := self._load_token()):
            select["restore_token"] = GLib.Variant("s", saved)

        self._call("SelectSources", "(oa{sv})", (self.session,), select)
        res = self._call("Start", "(osa{sv})", (self.session, ""), {})

        if res.get("restore_token"):
            self._save_token(res["restore_token"])

        streams = res.get("streams") or []
        if not streams:
            raise ClipCaptureUnavailable("portal returned no stream")
        node_id = streams[0][0]

        reply, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
        return fds.get(reply.unpack()[0]), node_id

    def close(self) -> None:
        """Close the portal session on the bus, not just this reference to it.

        Dropping the Python object does nothing: the portal keeps a session
        alive until its client calls Close or disconnects from the bus, and the
        GUI does neither when a capture stops — it goes on running. The
        compositor draws one recording indicator per live session, so every
        Stop/Start cycle and every restart() left another indicator on screen,
        stacked in the corner and pointing at a session with no pipeline behind
        it. Reported as three overlapping record symbols.

        Never raises: this runs on the way out of a capture, and a session that
        cannot be closed is not a reason to fail the stop.
        """
        if self.session is None:
            return
        if self._closed_sub is not None:
            try:
                self.bus.signal_unsubscribe(self._closed_sub)
            except Exception:  # noqa: BLE001
                pass
            self._closed_sub = None
        try:
            self.bus.call_sync(
                PORTAL, self.session, "org.freedesktop.portal.Session", "Close",
                None, None, self._Gio.DBusCallFlags.NONE, -1, None)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not close the screencast session: %s", exc)
        self.session = None
        self.closed = True

    def forget(self) -> None:
        TOKEN_FILE.unlink(missing_ok=True)

    @staticmethod
    def _load_token() -> str | None:
        try:
            return json.loads(TOKEN_FILE.read_text()).get("restore_token")
        except Exception:
            return None

    @staticmethod
    def _save_token(token: str) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(json.dumps({"restore_token": token}))
        except OSError as exc:
            log.warning("could not persist the screencast token: %s", exc)


def pick_encoder(Gst, gop: int, kbps: int) -> tuple[str, bool]:
    """Return (encoder description, needs_gl) for the best available encoder."""
    for element, props, needs_gl in ENCODERS:
        if Gst.ElementFactory.find(element):
            return f"{element} {props.format(gop=gop, kbps=kbps)}", needs_gl
    raise ClipCaptureUnavailable(
        "No usable H.264 encoder (looked for nvh264enc, vah264enc, x264enc).")


class ClipCapture:
    """Runs the capture and answers save_clip() from the rolling buffer."""

    def __init__(self, history_s: float = 90.0, fps: int = DEFAULT_FPS,
                 bitrate_kbps: int = 20000, window: bool = False):
        self._Gio, self._GLib, self._Gst = _require_gst()
        self._Gst.init(None)

        # The ceiling handed to videorate — not the rate being achieved. What
        # capture is actually managing is measured from the buffer and read off
        # the `fps` property below.
        self.max_fps = fps
        # What the last saved clip actually measured, so a save can report the
        # number that ended up in the file rather than leaving it to be
        # discovered in a player. See `fps` for why the two differ.
        self.last_clip_fps = 0.0
        self.bitrate_kbps = bitrate_kbps
        self.window = window
        self.buffer: ClipBuffer = ClipBuffer(window_s=history_s)
        self.portal: ScreenCastPortal | None = None
        self.pipeline = None
        self.caps: dict[str, object] = {}
        self.audio_tracks: list[tuple[str, str]] = []
        self._pts_offset: dict[str, int] = {}
        self._convert_chain: list[str] = []
        self._convert_index = 0
        self._started_at = 0.0
        # Arrival times of the last few seconds of portal frames — see
        # _watch_source_rate(). A deque with a maxlen cannot be used: the window
        # is a duration, not a count, and the count is what is being measured.
        self._source_stamps: deque[float] = deque()

    # ── capture ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        Gst = self._Gst

        # A session already here means a previous start was never stopped.
        # Overwriting the reference would strand it on the bus, still drawing
        # its own recording indicator, with nothing left able to close it.
        if self.portal is not None:
            self.portal.close()
            self.portal = None

        self.portal = ScreenCastPortal()
        try:
            fd, node_id = self.portal.open(window=self.window)
        except BaseException:
            # CreateSession may well have succeeded before whatever failed
            # here — a cancelled picker, a refused stream. That half-open
            # session is exactly as visible to the compositor as a working one.
            self.portal.close()
            self.portal = None
            raise
        log.info("screencast node %s on fd %s", node_id, fd)

        # gop == fps gives a keyframe every second, which bounds how far the
        # clip start can be rounded back (see clip_buffer).
        encoder, needs_gl = pick_encoder(Gst, gop=self.max_fps, kbps=self.bitrate_kbps)
        # Keeping the frame on the GPU is worth a lot at 1440p, but glupload
        # cannot always import what the portal hands out (driver and modifier
        # dependent). The system-memory path always negotiates, so it is held in
        # reserve: without it a GL failure takes the video branch down silently
        # and the clip comes out audio-only.
        if not self._convert_chain:
            self._convert_chain = [
                "glupload ! glcolorconvert ! video/x-raw(memory:GLMemory),format=NV12",
                "videoconvert ! video/x-raw,format=NV12",
            ] if needs_gl else ["videoconvert ! video/x-raw,format=NV12"]
        convert = self._convert_chain[self._convert_index]

        self.audio_tracks = resolve_audio_sources()
        log.info("audio tracks: %s", ", ".join(n for n, _ in self.audio_tracks) or "none")

        # No `video/x-raw` filter and no forced framerate here. Naming plain
        # video/x-raw pins the stream to system memory, so every frame is pulled
        # off the GPU and pushed straight back up by glupload — at 1440p that
        # copy alone is enough to starve the encoder. And forcing an exact rate
        # onto a variable-rate portal stream makes videorate drop frames to fit
        # the cadence rather than encode what arrived; between them the result
        # was a recording at a fraction of the real rate, with frames held long
        # enough to look frozen.
        #
        # The rate is capped, not fixed, so a busy scene cannot run the encoder
        # past what it can sustain. Clip lengths stay correct regardless: the
        # buffer measures time from buffer timestamps, never from a frame count.
        parts = [
            f"pipewiresrc name=screen fd={fd} path={node_id} "
            f"do-timestamp=true keepalive-time=1000",
            f"! videorate max-rate={self.max_fps} drop-only=true",
            f"! {convert} ! {encoder}",
            "! h264parse config-interval=-1",
            # Pin the byte format here. appsink accepts anything, so h264parse
            # would settle on byte-stream — and matroskamux only takes avc, so
            # the saved clip's appsrc then fails to negotiate and the file
            # comes out empty. Deciding it at capture time means the caps we
            # cache are already the ones the muxer will accept.
            "! video/x-h264,stream-format=avc,alignment=au",
            "! appsink name=video emit-signals=true sync=false max-buffers=0 drop=false",
        ]
        for name, source in self.audio_tracks:
            parts.append(
                f"pulsesrc device={source} provide-clock=false "
                f"! audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=2 "
                f"! opusenc bitrate=128000 "
                f"! appsink name=audio_{name} emit-signals=true sync=false "
                f"max-buffers=0 drop=false")

        desc = " ".join(parts)
        log.info("video path: %s | encoder: %s", convert.split(" !")[0], encoder.split()[0])
        log.debug("capture pipeline: %s", desc)
        self.pipeline = Gst.parse_launch(desc)

        self._watch_source_rate()

        self.buffer.add_video("video")
        self._wire_sink("video", "video")
        for name, _ in self.audio_tracks:
            self.buffer.add_audio(name)
            self._wire_sink(f"audio_{name}", name)

        # Watch the bus. A branch that fails to negotiate takes itself down
        # quietly while the rest of the pipeline keeps running, and the only
        # visible symptom is a clip that is missing a track — which looks like
        # a buffer bug and is not one.
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        self.pipeline.set_state(Gst.State.PLAYING)
        self._started_at = time.monotonic()

    def _on_bus_message(self, _bus, message) -> None:
        Gst = self._Gst
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            source = message.src.get_name() if message.src else "?"
            log.error("capture error from %s: %s", source, err.message)
            if debug:
                log.debug("%s", debug)
            if self._convert_index + 1 < len(self._convert_chain):
                self._convert_index += 1
                log.warning("video path failed — retrying on the system-memory "
                            "path (%s)", self._convert_chain[self._convert_index])
                try:
                    self.restart()
                except Exception:
                    log.exception("could not restart capture on the fallback path")
        elif message.type == Gst.MessageType.WARNING:
            warn, _ = message.parse_warning()
            log.warning("capture warning from %s: %s",
                        message.src.get_name() if message.src else "?", warn.message)

    def _wire_sink(self, element: str, track: str) -> None:
        """Route one appsink's samples into its track in the buffer."""
        Gst = self._Gst
        sink = self.pipeline.get_by_name(element)
        if sink is None:
            log.warning("appsink %s missing from the pipeline", element)
            return

        is_video = track == "video"

        def on_sample(appsink):
            sample = appsink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            buf = sample.get_buffer()
            if track not in self.caps:
                self.caps[track] = sample.get_caps()

            pts = buf.pts if buf.pts != Gst.CLOCK_TIME_NONE else 0
            pts = self._to_running_time(track, pts)
            keyframe = True
            if is_video:
                keyframe = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)

            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.OK
            try:
                payload = bytes(info.data)
            finally:
                buf.unmap(info)

            target = self.buffer.video if is_video else self.buffer.audio.get(track)
            if target is not None:
                target.push(Frame(pts=pts, payload=payload, keyframe=keyframe,
                                  duration=buf.duration if buf.duration != Gst.CLOCK_TIME_NONE else 0))
            return Gst.FlowReturn.OK

        sink.connect("new-sample", on_sample)

    def _to_running_time(self, track: str, pts: int) -> int:
        """Map a track's own timestamps onto the pipeline's running time.

        The branches do not agree on an epoch: pipewiresrc hands out the
        portal stream's clock, which starts around 3.6e15 ns (1000 hours),
        while pulsesrc starts near zero. Left alone, the two tracks never
        overlap — every cut finds frames on one and nothing on the other, so
        clips silently come out video-only or audio-only.

        The first buffer of each track defines its offset against the running
        time observed at that moment; everything after is shifted by the same
        amount, which preserves each track's internal timing and lines the
        tracks up with each other to within one buffer of arrival jitter.
        """
        if track not in self._pts_offset:
            running = pts
            if self.pipeline is not None and (clock := self.pipeline.get_clock()):
                running = clock.get_time() - self.pipeline.get_base_time()
            self._pts_offset[track] = pts - running
            if self._pts_offset[track]:
                log.debug("track %s: rebasing timestamps by %.3fs",
                          track, self._pts_offset[track] / NANOSECONDS)
        return max(pts - self._pts_offset[track], 0)

    def _with_measured_framerate(self, caps, frames):
        """Stamp the clip's real frame rate onto the video caps.

        The portal stream is variable-rate, so its caps carry framerate 0/1 with
        a max-framerate of whatever the compositor can manage — 239 on this
        display. Written through unchanged, the file claims 239 fps while
        holding a fraction of that many frames, and every player believes the
        header: playback stutters and looks frozen, and the clip reads as a
        broken low-frame-rate recording when the frames themselves are fine.

        Measuring the rate from the frames actually captured makes the header
        describe the file. Nothing is re-encoded; only what the container
        declares changes.
        """
        Gst = self._Gst
        if len(frames) < 2:
            return caps
        span_ns = frames[-1].pts - frames[0].pts
        if span_ns <= 0:
            return caps

        fps = (len(frames) - 1) * NANOSECONDS / span_ns
        # Keep it to a sane range: a wildly wrong measurement (a stalled
        # capture, a single burst) must not produce a header worse than the one
        # being replaced.
        if not (1.0 <= fps <= 240.0):
            log.debug("measured %.1f fps — leaving the caps alone", fps)
            return caps
        self.last_clip_fps = fps

        # Built through the caps string rather than Gst.Fraction(num, den):
        # PyGObject 3.56 (GStreamer 1.28) ships Gst.Fraction as a plain
        # introspected struct with no Python constructor, so the two-argument
        # form raises "Fraction() takes no arguments" — which aborted every
        # single save, by button and by shortcut alike. The serialised form is
        # understood by every version.
        out = Gst.Caps.from_string(
            f"{caps.to_string()}, framerate=(fraction){round(fps * 1000)}/1000")
        if out is None:
            log.debug("could not restamp the framerate — leaving the caps alone")
            return caps
        log.info("clip video: %.1f fps measured over %.1fs",
                 fps, span_ns / NANOSECONDS)
        return out

    def restart(self) -> None:
        """Tear the pipeline down and build it again on the next video path.

        Used when a branch fails to negotiate. start() opens a fresh session
        from the saved restore token, so the picker stays out of the user's way
        for a failure they did not cause — but the old session still has to be
        closed on the bus first. Leaving it open is what stacked a second and
        third recording indicator in the corner, each one a session whose
        pipeline had already been torn down.

        The buffer is cleared because its timestamps belong to the pipeline
        that produced them.
        """
        Gst = self._Gst
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self.buffer.clear()
        self.caps.clear()
        self._pts_offset.clear()
        if self.portal is not None:
            self.portal.close()
        self.portal = None
        self.start()

    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(self._Gst.State.NULL)
            self.pipeline = None
        if self.portal is not None:
            # Closed, not just dropped — otherwise the session outlives the
            # capture and the compositor keeps showing it as recording.
            self.portal.close()
        self.portal = None

    @property
    def ready_s(self) -> float:
        return self.buffer.ready_s()

    @property
    def fps(self) -> float:
        """Frames per second capture is managing *right now*.

        Surfaced because the recording rate is invisible until a clip is played
        back, and a capture quietly running at a third of the display rate looks
        like a broken clip rather than a slow pipeline.

        A few seconds wide on purpose, which is also why it reads higher than
        the rate a saved clip comes out at: the portal only sends a frame when
        the screen changes, so a live 20 fps during activity and a 12 fps clip
        average over thirty seconds that included idle moments are the same
        capture, honestly measured over different spans. :attr:`buffered_fps`
        is the one that predicts the file.
        """
        video = self.buffer.video
        return video.rate_hz() if video is not None else 0.0

    @property
    def buffered_fps(self) -> float:
        """Average rate across everything still buffered — what a clip will claim.

        The live rate above answers "is capture keeping up"; this answers "what
        will the file say", and on a damage-driven screencast the two differ by
        more than anyone expects. Showing only the first is what makes a status
        bar reading 20 fps produce a 12 fps clip with no explanation.
        """
        video = self.buffer.video
        if video is None or len(video.frames) < 2:
            return 0.0
        span_ns = video.span_ns
        if span_ns <= 0:
            return 0.0
        return (len(video.frames) - 1) * NANOSECONDS / span_ns

    def _watch_source_rate(self) -> None:
        """Count frames as they leave the portal, before anything of ours.

        A clip recorded at 15 fps has two completely different explanations —
        the compositor is only producing 15 (fullscreen games on some
        compositors starve the screencast), or our own branch is dropping them
        — and they need opposite fixes. Measuring at the source settles it
        instead of leaving the number to be argued about.
        """
        Gst = self._Gst
        source = self.pipeline.get_by_name("screen") if self.pipeline else None
        pad = source.get_static_pad("src") if source is not None else None
        if pad is None:                              # pragma: no cover - env dependent
            log.debug("no pipewiresrc pad to measure the source rate on")
            return

        def on_buffer(_pad, _info):
            now = time.monotonic()
            stamps = self._source_stamps
            stamps.append(now)
            # A rolling window: an average over the whole session would hide a
            # stall, which is the thing worth seeing while it happens.
            while stamps and now - stamps[0] > _RATE_WINDOW_S:
                stamps.popleft()
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, on_buffer)

    @property
    def source_fps(self) -> float:
        """Frames per second the compositor is handing out, before our pipeline.

        Compare with :attr:`fps`: equal means the screencast itself is slow;
        higher means the frames are being lost on our side.
        """
        stamps = self._source_stamps
        if len(stamps) < 2:
            return 0.0
        span = stamps[-1] - stamps[0]
        return (len(stamps) - 1) / span if span > 0 else 0.0

    @property
    def video_path_label(self) -> str:
        """Which conversion path is live — GPU or system memory."""
        if not self._convert_chain:
            return ""
        return ("GPU" if self._convert_chain[self._convert_index].startswith("glupload")
                else "CPU")

    # ── saving ────────────────────────────────────────────────────────────────

    def save_clip(self, seconds: float = 30.0, path: Path | None = None) -> Path | None:
        """Write the last *seconds* to an .mkv with one audio track per source.

        Returns the file written, or None when there is nothing buffered yet.
        """
        Gst = self._Gst
        frames, actual = self.buffer.take(seconds)
        if not frames:
            log.warning("nothing buffered yet — clip not saved")
            return None

        game = detect_game()
        if path is None:
            CLIP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            label = f"_{game.replace(' ', '_')}" if game else ""
            path = CLIP_DIR / f"clip_{stamp}{label}.mkv"

        order = [t for t in ("video", *(n for n, _ in self.audio_tracks)) if t in frames]

        # Built element by element rather than through parse_launch: the clip
        # directory is "ASM Clips", and a launch string cannot carry a path
        # with a space in it — the parser reads the second word as an element
        # and fails with `no element "Clips"`. Setting location as a property
        # sidesteps quoting entirely, for this and any other user-chosen path.
        writer = Gst.Pipeline.new("clip-writer")
        mux = Gst.ElementFactory.make("matroskamux", "mux")
        sink = Gst.ElementFactory.make("filesink", "sink")
        if mux is None or sink is None:
            log.error("matroskamux/filesink unavailable — cannot write the clip")
            return None
        sink.set_property("location", str(path))
        writer.add(mux)
        writer.add(sink)
        if not mux.link(sink):
            log.error("could not link muxer to file")
            return None

        sources: dict[str, object] = {}
        for track in order:
            src = Gst.ElementFactory.make("appsrc", f"src_{track}")
            queue = Gst.ElementFactory.make("queue", f"q_{track}")
            if src is None or queue is None:
                log.error("appsrc/queue unavailable — cannot write the clip")
                return None
            src.set_property("format", Gst.Format.TIME)
            src.set_property("is-live", False)
            if (caps := self.caps.get(track)) is not None:
                if track == "video":
                    caps = self._with_measured_framerate(caps, frames[track])
                src.set_property("caps", caps)
            writer.add(src)
            writer.add(queue)
            if not src.link(queue):
                log.error("could not link appsrc for track '%s'", track)
                return None

            # Ask the muxer for the pad this track actually needs. Letting
            # Element.link() choose picks a template by compatibility, and at
            # link time the queue has no caps yet — so an audio branch can be
            # handed a video_%u pad and only fail later, as a bare
            # "not-negotiated" on the appsrc with nothing pointing at the mixup.
            template = "video_%u" if track == "video" else "audio_%u"
            request = (mux.request_pad_simple(template)
                       if hasattr(mux, "request_pad_simple")
                       else mux.get_request_pad(template))
            if request is None:
                log.error("muxer refused a '%s' pad for track '%s'", template, track)
                return None
            if queue.get_static_pad("src").link(request) != Gst.PadLinkReturn.OK:
                log.error("could not link track '%s' into the muxer", track)
                return None
            sources[track] = src

        if writer.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            log.error("clip writer refused to start")
            writer.set_state(Gst.State.NULL)
            return None

        # Rebase every track onto a common zero so the muxer sees a clip that
        # starts at 0; keeping the capture-clock pts would leave minutes of
        # empty timeline at the front of the file.
        base = min(f[0].pts for f in frames.values())
        for track in order:
            src = sources[track]
            for frame in frames[track]:
                buf = Gst.Buffer.new_wrapped(frame.payload)
                buf.pts = max(frame.pts - base, 0)
                buf.duration = frame.duration
                if not frame.keyframe:
                    buf.set_flags(Gst.BufferFlags.DELTA_UNIT)
                if (ret := src.emit("push-buffer", buf)) != Gst.FlowReturn.OK:
                    log.error("track '%s': push rejected (%s) — clip truncated",
                              track, ret)
                    break
            src.emit("end-of-stream")

        bus = writer.get_bus()
        message = bus.timed_pop_filtered(
            30 * NANOSECONDS, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        writer.set_state(Gst.State.NULL)

        # The muxer only commits the file on EOS; giving up early (or on an
        # error) leaves a zero-byte clip behind, which is worse than no clip
        # because it looks like a successful save.
        if message is None:
            log.error("clip writer timed out before EOS — %s is incomplete", path)
            return None
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log.error("clip writer failed: %s", err.message)
            if debug:
                log.debug("%s", debug)
            path.unlink(missing_ok=True)
            return None

        size = path.stat().st_size if path.exists() else 0
        if size == 0:
            log.error("clip writer produced an empty file — %s", path)
            path.unlink(missing_ok=True)
            return None

        _mark_default_audio_track(path, order, frames)

        log.info("clip saved: %s (%.1fs, %d tracks, %.1f MB%s)",
                 path, actual, len(order), size / (1024 * 1024),
                 f", {game}" if game else "")
        # Track names beside the clip. matroskamux takes titles from stream
        # tags, which appsrc does not carry, so every track landed in the editor
        # as "Audio" — three identical rows with no way to tell the game from
        # the microphone, which is exactly the choice the editor exists to make.
        try:
            path.with_suffix(".tracks.json").write_text(json.dumps({
                "tracks": order,
                "game": game,
                "seconds": round(actual, 2),
            }, indent=2))
        except OSError as exc:
            log.debug("could not write the track sidecar: %s", exc)

        announce_clip(path, actual, game)
        return path
