# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Global shortcut for saving a clip, via the xdg-desktop-portal GlobalShortcuts API.

On Wayland an application cannot grab a key combination for itself — the same
boundary that stops it reading the screen unasked. The portal is the sanctioned
route: the app declares the shortcut it wants and a preferred trigger, the
compositor owns the binding, and the user can rebind it in their own system
settings (:meth:`ClipShortcut.configure` opens that UI).

This means the default below is a *request*, not a guarantee: if Alt+F is
already taken the compositor may hand out something else, and the binding the
user actually has is whatever :meth:`ClipShortcut.current_trigger` reports.
The UI must show that value rather than the requested one, or it will confidently
display a shortcut that does nothing.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

log = logging.getLogger(__name__)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"

SHORTCUT_ID = "save-clip"
# Portal trigger syntax: modifiers joined by '+', see the GlobalShortcuts spec.
DEFAULT_TRIGGER = "ALT+f"


class ClipShortcut:
    """Binds one global shortcut and calls *on_activate* when it fires."""

    def __init__(self, on_activate: Callable[[], None],
                 trigger: str = DEFAULT_TRIGGER,
                 description: str = "Save the last seconds as a clip"):
        self._on_activate = on_activate
        self._trigger = trigger
        self._description = description
        self.session: str | None = None
        self.available = False
        self._shortcuts: dict[str, str] = {}
        self._token = 0

        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as exc:
            log.info("global shortcuts unavailable (no PyGObject): %s", exc)
            self._Gio = self._GLib = self.bus = None
            return

        self._Gio, self._GLib = Gio, GLib
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            log.warning("no session bus for global shortcuts: %s", exc)
            self.bus = None

    # ── portal plumbing ───────────────────────────────────────────────────────

    def _call(self, method: str, signature: str, pre_args: tuple,
              options: dict) -> dict:
        GLib, Gio = self._GLib, self._Gio
        self._token += 1
        token = f"asm_sc_{os.getpid()}_{self._token}"
        req_path = (f"/org/freedesktop/portal/desktop/request/"
                    f"{self.bus.get_unique_name()[1:].replace('.', '_')}/{token}")

        loop = GLib.MainLoop()
        result: dict = {}

        def on_response(*args):
            code, values = args[-1].unpack()
            result["code"] = code
            result["values"] = values
            loop.quit()

        sub = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Request", "Response", req_path,
            None, Gio.DBusSignalFlags.NONE, on_response)
        try:
            opts = dict(options)
            opts["handle_token"] = GLib.Variant("s", token)
            self.bus.call_sync(PORTAL, PORTAL_PATH, SHORTCUTS, method,
                               GLib.Variant(signature, (*pre_args, opts)),
                               None, Gio.DBusCallFlags.NONE, -1, None)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)

        if result.get("code") != 0:
            raise RuntimeError(f"{method}: portal returned {result.get('code')}")
        return result.get("values", {})

    def bind(self) -> bool:
        """Create the session and bind the shortcut. Returns True on success."""
        if self.bus is None:
            return False

        GLib, Gio = self._GLib, self._Gio
        try:
            res = self._call("CreateSession", "(a{sv})", (), {
                "session_handle_token": GLib.Variant("s", f"asm_sc_{os.getpid()}")})
            self.session = res["session_handle"]

            self.bus.signal_subscribe(
                PORTAL, SHORTCUTS, "Activated", None, None,
                Gio.DBusSignalFlags.NONE, self._on_activated)
            self.bus.signal_subscribe(
                PORTAL, SHORTCUTS, "ShortcutsChanged", None, None,
                Gio.DBusSignalFlags.NONE, self._on_changed)

            # A plain list, not a pre-built Variant: the outer GLib.Variant()
            # call below packs this whole tuple from raw Python values, and
            # handing it an already-packed child is what raises "Expected
            # GLib.Variant, but got str". Only the a{sv} *values* are Variants,
            # because 'v' is the one type that has to carry its own signature.
            shortcuts = [(
                SHORTCUT_ID,
                {"description": GLib.Variant("s", self._description),
                 "preferred_trigger": GLib.Variant("s", self._trigger)},
            )]
            res = self._call("BindShortcuts", "(oa(sa{sv})sa{sv})",
                             (self.session, shortcuts, ""), {})
            self._remember(res.get("shortcuts") or [])
            self.available = True
            log.info("clip shortcut bound: %s", self.current_trigger() or "(pending)")
            return True
        except Exception as exc:
            log.warning("could not bind the clip shortcut: %s", exc)
            self.available = False
            return False

    def _remember(self, shortcuts) -> None:
        """Record what the compositor actually bound, per shortcut id."""
        for entry in shortcuts:
            try:
                sid, props = entry[0], entry[1]
            except (TypeError, IndexError):
                continue
            trigger = props.get("trigger_description") or props.get("trigger")
            if isinstance(trigger, str):
                self._shortcuts[sid] = trigger

    def _on_activated(self, _c, _s, _p, _i, _sig, params) -> None:
        try:
            session, shortcut_id = params.unpack()[0], params.unpack()[1]
        except Exception:
            return
        if self.session and session != self.session:
            return
        if shortcut_id != SHORTCUT_ID:
            return
        try:
            self._on_activate()
        except Exception:
            log.exception("clip shortcut handler failed")

    def _on_changed(self, _c, _s, _p, _i, _sig, params) -> None:
        try:
            session, shortcuts = params.unpack()
        except Exception:
            return
        if self.session and session != self.session:
            return
        self._remember(shortcuts)
        log.info("clip shortcut rebound to %s", self.current_trigger())

    # ── queries ───────────────────────────────────────────────────────────────

    def current_trigger(self) -> str | None:
        """What the compositor bound, or None if it has not said yet.

        Never fall back to the requested trigger here: showing "Alt+F" when the
        compositor refused it is worse than showing nothing, because the user
        then blames the feature for not firing.
        """
        return self._shortcuts.get(SHORTCUT_ID)

    def configure(self) -> bool:
        """Open the desktop's own shortcut editor for this session."""
        if self.bus is None or self.session is None:
            return False
        GLib, Gio = self._GLib, self._Gio
        try:
            self.bus.call_sync(
                PORTAL, PORTAL_PATH, SHORTCUTS, "ConfigureShortcuts",
                GLib.Variant("(osa{sv})", (self.session, "", {})),
                None, Gio.DBusCallFlags.NONE, -1, None)
            return True
        except Exception as exc:
            log.warning("could not open the shortcut editor: %s", exc)
            return False

    def close(self) -> None:
        if self.bus is None or self.session is None:
            return
        try:
            self.bus.call_sync(
                PORTAL, self.session, "org.freedesktop.portal.Session", "Close",
                None, None, self._Gio.DBusCallFlags.NONE, -1, None)
        except Exception:
            pass
        self.session = None
        self.available = False
