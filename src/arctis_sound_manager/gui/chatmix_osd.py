# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
ChatMix on-screen display: the mixer page's ChatMix bar, floated at the
bottom of the screen for a moment whenever the mix moves — the headset dial
turned mid-game, with ASM's own window nowhere in sight.

Driven by the mix streams themselves rather than by the headset's status
frame: the dial lands on the channels' loopback output streams through the
daemon's set_mix whatever the family, and the OSD then shows exactly what the
mixer page's bar would.

Placement is the whole difficulty. Wayland lets no client position its own
window, so the OSD becomes a wlr-layer-shell surface on the *overlay* layer,
anchored to the bottom edge: the compositor keeps it clear of the panel's
exclusive zone (above the taskbar) and draws it over fullscreen games too.
LayerShellQt has no Python bindings, so its few C++ entry points are called
through ctypes. On X11 the window places itself. GNOME's Wayland session has
no layer-shell and only the Shell itself can draw over a game there, so the
bar is handed to a small GNOME Shell extension (desktop/gnome-shell/) over
D-Bus instead; without it the OSD stays off rather than open a
focus-stealing window in the middle of a game.
"""
import ast
import ctypes
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from threading import Thread

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QWidget

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.fader_slider import FADER_HANDLE_QSS_H
from arctis_sound_manager.gui.home_page import (_CHATMIX_CHANNEL_COLOR_KEYS,
                                                _ChatMixSlider,
                                                _make_chatmix_bar_qss,
                                                chatmix_bar_position,
                                                chatmix_bar_track_css)
from arctis_sound_manager.constants import (DBUS_BUS_NAME, DBUS_OBJECT_BASE_PATH,
                                            DBUS_STATUS_INTERFACE_NAME,
                                            DBUS_STATUS_OBJECT_PATH)
from arctis_sound_manager.i18n import I18n
from arctis_sound_manager.pactl import mix_stream_levels

logger = logging.getLogger("ChatMixOsd")

# How long the OSD stays up after the last movement.
OSD_HIDE_DELAY_MS = 1500
# Gap between the OSD and whatever the compositor reserves at the bottom
# (the panel), or the screen edge when nothing does.
OSD_BOTTOM_MARGIN = 24
OSD_WIDTH = 440

# LayerShellQt::Window enums (LayerShellQt/window.h).
_ANCHOR_BOTTOM = 2
_LAYER_OVERLAY = 3
_KEYBOARD_INTERACTIVITY_NONE = 0

# The GNOME Shell extension (desktop/gnome-shell/<uuid>/extension.js).
GNOME_EXTENSION_UUID = "asm-chatmix-osd@loteran.github.com"
GNOME_OSD_BUS_NAME = f"{DBUS_BUS_NAME}.ChatMixOsd"
GNOME_OSD_OBJECT_PATH = f"{DBUS_OBJECT_BASE_PATH}/ChatMixOsd"

_LAYER_SHELL_SONAME = "libLayerShellQtInterface.so.6"
_SYM_GET = "_ZN12LayerShellQt6Window3getEP7QWindow"
_SYM_SET_ANCHORS = "_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE"
_SYM_SET_LAYER = "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE"
_SYM_SET_MARGINS = "_ZN12LayerShellQt6Window10setMarginsERK8QMargins"
_SYM_SET_KEYBOARD = "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE"
_SYM_SET_ACTIVATE_ON_SHOW = "_ZN12LayerShellQt6Window17setActivateOnShowEb"
_SYM_SET_ACTIVE_SCREEN = "_ZN12LayerShellQt6Window26setWantsToBeOnActiveScreenEb"


class _QMargins(ctypes.Structure):
    # QMargins is four ints: left, top, right, bottom.
    _fields_ = [("left", ctypes.c_int), ("top", ctypes.c_int),
                ("right", ctypes.c_int), ("bottom", ctypes.c_int)]


def _loaded_qtcore_dir() -> Path | None:
    """Directory of the libQt6Core this process actually runs on."""
    try:
        for line in Path("/proc/self/maps").read_text().splitlines():
            path = line.split()[-1]
            if "/libQt6Core.so" in path:
                return Path(path).resolve().parent
    except OSError:
        pass
    return None


def _load_layer_shell() -> ctypes.CDLL | None:
    """LayerShellQt, if it is installed *and* built against the Qt we run on.

    A PySide6 wheel bundles its own Qt; loading the system's LayerShellQt
    next to it would bring a second QtCore into the process, which crashes
    rather than fails. Only accept the library when it sits beside the
    QtCore already loaded — the distribution-packaged PySide6 case.
    """
    qt_dir = _loaded_qtcore_dir()
    if qt_dir is None:
        return None
    candidate = qt_dir / _LAYER_SHELL_SONAME
    if not candidate.exists():
        return None
    try:
        lib = ctypes.CDLL(str(candidate))
        for sym in (_SYM_GET, _SYM_SET_ANCHORS, _SYM_SET_LAYER, _SYM_SET_MARGINS, _SYM_SET_KEYBOARD):
            getattr(lib, sym)
    except (OSError, AttributeError) as exc:
        logger.info("LayerShellQt unusable: %s", exc)
        return None
    return lib


def _make_layer_surface(widget: QWidget, lib: ctypes.CDLL) -> bool:
    """Turn *widget*'s window into a bottom-anchored overlay layer surface.

    Must run before the first show(): LayerShellQt swaps the window's shell
    integration, which is only possible while no shell surface exists yet.
    """
    import shiboken6

    widget.winId()  # creates the QWindow (not yet its shell surface)
    handle = widget.windowHandle()
    if handle is None:
        return False

    get = lib[_SYM_GET]
    get.restype = ctypes.c_void_p
    get.argtypes = [ctypes.c_void_p]
    window = get(ctypes.c_void_p(shiboken6.getCppPointer(handle)[0]))
    if not window:
        return False
    this = ctypes.c_void_p(window)

    def call(sym, *args):
        fn = lib[sym]
        fn.restype = None
        fn(this, *args)

    call(_SYM_SET_LAYER, ctypes.c_int(_LAYER_OVERLAY))
    # Bottom only: centred horizontally, stacked above any exclusive zone
    # (the panel) the compositor already reserves on that edge.
    call(_SYM_SET_ANCHORS, ctypes.c_int(_ANCHOR_BOTTOM))
    margins = _QMargins(0, 0, 0, OSD_BOTTOM_MARGIN)
    call(_SYM_SET_MARGINS, ctypes.byref(margins))
    # Never take the keyboard from the game.
    call(_SYM_SET_KEYBOARD, ctypes.c_int(_KEYBOARD_INTERACTIVITY_NONE))
    # Newer LayerShellQt only; older ones already behave this way.
    for sym, value in ((_SYM_SET_ACTIVATE_ON_SHOW, False), (_SYM_SET_ACTIVE_SCREEN, True)):
        if hasattr(lib, sym):
            call(sym, ctypes.c_bool(value))
    return True


def osd_style(channels: list[str]) -> dict:
    """Same labels and colours as the mixer page's bar, re-read each time so
    a theme or channel change made in the meantime shows up."""
    included = [ch for ch in ("game", "media", "aux") if ch in channels] or ["game"]
    return {
        "left_label": ", ".join(I18n.translate("ui", ch) for ch in included),
        "right_label": I18n.translate("ui", "chat"),
        "left_colors": [_theme.c(_CHATMIX_CHANNEL_COLOR_KEYS[ch]) for ch in included],
        "chat_color": _theme.c("COLOR_CHAT"),
        "text_color": _theme.c("TEXT_SECONDARY"),
        "bg_color": _theme.c("BG_CARD"),
        "border_color": _theme.c("BORDER"),
        "cap_color": _theme.c("TEXT_PRIMARY"),
    }


def master_osd_style(percent: int) -> dict:
    """The DAC wheel in the ChatMix bar's frame: same panel, same place, the
    track filled up to the level in Master's colour (`fill`) — the reading
    shown beside the Master card on the Channels page (#268)."""
    return {
        **osd_style([]),
        "left_label": I18n.translate("ui", "dac_wheel"),
        "right_label": f"{percent}%",
        "left_colors": [_theme.c("COLOR_MASTER")],
        "chat_color": _theme.c("BG_BUTTON"),
        "fill": True,
    }


def _master_bar_qss(accent: str, groove: str) -> str:
    return f"""
        QSlider::groove:horizontal {{
            height: 6px;
            background: {groove};
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {accent};
            border-radius: 3px;
        }}
        QSlider::add-page:horizontal {{
            background: transparent;
        }}
    """ + FADER_HANDLE_QSS_H


class ChatMixOsd(QWidget):
    """The floating bar itself. Input-transparent and never focused: it must
    not eat a click or a keypress meant for the game underneath."""

    def __init__(self):
        super().__init__(None)
        self._usable = True
        self._x11 = QGuiApplication.platformName() == "xcb"

        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.WindowTransparentForInput)
        if self._x11:
            flags |= Qt.WindowType.ToolTip | Qt.WindowType.X11BypassWindowManagerHint
        else:
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(OSD_WIDTH)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._panel = QFrame()
        self._panel.setObjectName("chatmixOsdPanel")
        outer.addWidget(self._panel)

        row = QHBoxLayout(self._panel)
        row.setContentsMargins(18, 10, 18, 10)
        row.setSpacing(10)

        self._channels_lbl = QLabel()
        row.addWidget(self._channels_lbl)

        self._bar = _ChatMixSlider(Qt.Orientation.Horizontal)
        self._bar.setRange(0, 100)
        self._bar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self._bar, stretch=1)

        self._chat_lbl = QLabel()
        row.addWidget(self._chat_lbl)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(OSD_HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self.hide)

        self.adjustSize()
        if QGuiApplication.platformName() == "wayland":
            lib = _load_layer_shell()
            self._usable = lib is not None and _make_layer_surface(self, lib)
            if not self._usable:
                logger.info("No layer-shell on this Wayland session: ChatMix OSD disabled")

    @property
    def usable(self) -> bool:
        return self._usable

    def show_position(self, position: int, channels: list[str]) -> None:
        if not self._usable:
            return
        style = osd_style(channels)
        self._restyle(style)
        self._bar.show_centre_tick = True
        self._bar.setStyleSheet(
            _make_chatmix_bar_qss(chatmix_bar_track_css(style["left_colors"], style["chat_color"]))
        )
        self._pop(position)

    def show_volume(self, percent: int) -> None:
        if not self._usable:
            return
        style = master_osd_style(percent)
        self._restyle(style)
        self._bar.show_centre_tick = False
        self._bar.setStyleSheet(_master_bar_qss(style["left_colors"][0], style["chat_color"]))
        self._pop(percent)

    def _pop(self, value: int) -> None:
        self._bar.setValue(value)
        self._bar.update()
        if self._x11:
            self._place_x11()
        self.show()
        self._hide_timer.start()

    def _restyle(self, style: dict) -> None:
        label_qss = f"color: {style['text_color']}; font-size: 9pt; background: transparent;"
        self._channels_lbl.setText(style["left_label"])
        self._channels_lbl.setStyleSheet(label_qss)
        self._chat_lbl.setText(style["right_label"])
        self._chat_lbl.setStyleSheet(label_qss)
        self._panel.setStyleSheet(
            f"QFrame#chatmixOsdPanel {{ background-color: {style['bg_color']};"
            f" border: 1px solid {style['border_color']}; border-radius: 12px; }}"
        )

    def _place_x11(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.adjustSize()
        area = screen.availableGeometry()  # excludes the panel (_NET_WORKAREA)
        self.move(area.x() + (area.width() - self.width()) // 2,
                  area.bottom() - self.height() - OSD_BOTTOM_MARGIN)


class GnomeShellOsd:
    """Hands the bar to the GNOME Shell extension, which draws it. Silent
    when the extension is not running (not installed, not enabled yet, or
    another desktop): there is nothing else that could show it."""

    usable = True

    def show_position(self, position: int, channels: list[str]) -> None:
        self._show({"position": position, **osd_style(channels)})

    def show_volume(self, percent: int) -> None:
        self._show({"position": percent, **master_osd_style(percent)})

    def _show(self, state: dict) -> None:
        # QtDBus is a separate package on Debian/Ubuntu (python3-pyside6.qtdbus):
        # imported here so a missing module only costs the GNOME OSD, not the GUI (#306).
        try:
            from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage
        except ImportError:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected() or not bus.interface().isServiceRegistered(GNOME_OSD_BUS_NAME).value():
            return
        message = QDBusMessage.createMethodCall(
            GNOME_OSD_BUS_NAME, GNOME_OSD_OBJECT_PATH, GNOME_OSD_BUS_NAME, "Show")
        message.setArguments([json.dumps(state)])
        bus.call(message, QDBus.CallMode.NoBlock)


def is_gnome_session() -> bool:
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _make_osd():
    osd = ChatMixOsd()
    if osd.usable or not is_gnome_session():
        return osd
    osd.deleteLater()
    return GnomeShellOsd()


def _gnome_extension_installed() -> bool:
    data_dirs = [os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")]
    data_dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return any((Path(d) / "gnome-shell" / "extensions" / GNOME_EXTENSION_UUID).is_dir()
               for d in data_dirs if d)


def _gnome_extension_marker() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "arctis-sound-manager" / "gnome-chatmix-osd-enabled"


def enable_gnome_extension_once() -> None:
    """Turn the packaged extension on the first time ASM runs on GNOME.

    Written to org.gnome.shell's enabled-extensions rather than through
    `gnome-extensions enable`: a Shell started before the package was
    installed does not know the extension yet and would refuse; the key
    is picked up at the next login either way. Only ever once, so a user
    who switches it off in the Extensions app is not overruled.
    """
    marker = _gnome_extension_marker()
    if not is_gnome_session() or marker.exists() or not _gnome_extension_installed():
        return
    try:
        raw = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        enabled = ast.literal_eval(raw.removeprefix("@as").strip()) or []
        if GNOME_EXTENSION_UUID not in enabled:
            subprocess.run(
                ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                 str([*enabled, GNOME_EXTENSION_UUID])],
                timeout=5, check=True,
            )
            # Live if the running Shell already knows it; harmless otherwise.
            subprocess.run(["gnome-extensions", "enable", GNOME_EXTENSION_UUID],
                           capture_output=True, timeout=5)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError) as exc:
        logger.info("Could not enable the GNOME ChatMix OSD extension: %r", exc)


def dac_wheel_level(payload: str) -> int | None:
    """The DAC wheel (`station_volume`) from a StatusChanged payload, 0-100,
    or None when this device has no wheel or did not report it."""
    try:
        wheel = json.loads(payload).get("gamedac", {}).get("station_volume", {})
    except (ValueError, AttributeError):
        return None
    if not isinstance(wheel, dict) or wheel.get("type") != "percentage":
        return None
    value = wheel.get("value")
    return value if isinstance(value, int) else None


class ChatMixWatcher(QObject):
    """Follows the mix streams on PipeWire's own events (no polling), and
    the DAC wheel on the daemon's StatusChanged signal, and brings the OSD
    up when the ChatMix position or the wheel moves. Both share one OSD
    window, so they show in the same place and never on top of each other.

    The first reading after (re)connecting only sets the baseline: startup,
    and PipeWire or the daemon coming back, are not something the user did.
    """

    volumes_changed = Signal(object)
    wheel_changed = Signal(object)

    def __init__(self, is_stopping, is_enabled, channels, is_master_enabled=lambda: False,
                 parent=None):
        super().__init__(parent)
        self._is_stopping = is_stopping
        self._is_enabled = is_enabled
        self._is_master_enabled = is_master_enabled
        self._channels = channels
        self._osd: ChatMixOsd | GnomeShellOsd | None = None
        self._last_position: int | None = None
        self._last_master: int | None = None
        self.volumes_changed.connect(self._on_volumes)
        self.wheel_changed.connect(self._on_wheel)

    def start(self) -> None:
        Thread(target=self._listen, daemon=True, name="chatmix-osd").start()
        Thread(target=self._listen_wheel, daemon=True, name="dac-wheel-osd").start()

    def _listen_wheel(self) -> None:
        try:
            import asyncio

            from dbus_next.aio import MessageBus
        except ImportError:
            return

        async def run() -> None:
            while not self._is_stopping():
                bus = None
                try:
                    # Kept apart from connect(): MessageBus() already opens the
                    # socket, which must be closed even when connecting fails.
                    bus = MessageBus()
                    await bus.connect()
                    intro = await bus.introspect(DBUS_BUS_NAME, DBUS_STATUS_OBJECT_PATH)
                    iface = bus.get_proxy_object(
                        DBUS_BUS_NAME, DBUS_STATUS_OBJECT_PATH, intro,
                    ).get_interface(DBUS_STATUS_INTERFACE_NAME)
                    self.wheel_changed.emit(None)  # new baseline
                    iface.on_status_changed(
                        lambda payload: self.wheel_changed.emit(dac_wheel_level(payload)))
                    while not self._is_stopping() and bus.connected:
                        await asyncio.sleep(1)
                except Exception as exc:  # noqa: BLE001 — daemon not up yet, retry
                    logger.debug("DAC wheel OSD watcher: %r", exc)
                finally:
                    # Always disconnect to free connection
                    if bus is not None:
                        bus.disconnect()
                await asyncio.sleep(2)

        asyncio.run(run())

    def _listen(self) -> None:
        try:
            import pulsectl  # type: ignore
        except ImportError:
            return
        while not self._is_stopping():
            try:
                with pulsectl.Pulse("arctis-manager-osd") as pulse:
                    got_event = [False]

                    def _on_event(_ev):
                        got_event[0] = True
                        raise pulsectl.PulseLoopStop

                    pulse.event_mask_set("sink_input")
                    pulse.event_callback_set(_on_event)
                    self.volumes_changed.emit(None)  # new baseline
                    self.volumes_changed.emit(mix_stream_levels(pulse))
                    while not self._is_stopping():
                        pulse.event_listen(timeout=1)
                        if got_event[0]:
                            got_event[0] = False
                            self.volumes_changed.emit(mix_stream_levels(pulse))
            except Exception as exc:  # noqa: BLE001 — PipeWire restarting, reconnect
                logger.debug("ChatMix OSD watcher: %r", exc)
                time.sleep(2)

    def _on_volumes(self, levels) -> None:
        if levels is None:
            self._last_position = None
            return
        channels = self._channels()
        position = chatmix_bar_position(levels, levels.get("chat"), channels)
        if position is None:
            return
        previous, self._last_position = self._last_position, position
        if previous is None or position == previous or not self._is_enabled():
            return
        if self._can_show():
            self._get_osd().show_position(position, channels)

    def _on_wheel(self, level) -> None:
        if level is None:
            self._last_master = None
            return
        previous, self._last_master = self._last_master, level
        if previous is None or level == previous or not self._is_master_enabled():
            return
        if self._can_show():
            self._get_osd().show_volume(level)

    @staticmethod
    def _can_show() -> bool:
        # Someone moving the mixer page's own sliders is already looking at them.
        return QApplication.activeWindow() is None

    def _get_osd(self):
        if self._osd is None:
            self._osd = _make_osd()
        return self._osd
