# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Clips page has to be able to ask for a window, not only a screen.

Reported from use: a clip of a game that was not covering the screen came out
with the desktop panel drawn across the bottom of it. Nothing was misbehaving —
the GUI simply never asked the portal for a window. It built `ClipCapture()`
without the `window` argument, so `types` went out as MONITOR|WINDOW every
time and whatever was picked on the first run was frozen into the restore
token. There was no setting, anywhere, that could produce `types=WINDOW`.

`open()` is exercised with stand-ins for the two GObject namespaces rather
than the real ones: `gi` is not installed in the test environment, and the
only thing under test here is which number lands in the `types` option. The
fakes therefore implement exactly what `open()` touches — `GLib.Variant`,
`GLib.VariantType`, two flag enums, and the three bus calls — and record
their arguments.
"""
from __future__ import annotations

from arctis_sound_manager.clip_capture import ScreenCastPortal

# The portal's SelectSources source types, from the ScreenCast interface.
MONITOR = 1
WINDOW = 2


class _Variant:
    """Stands in for GLib.Variant, keeping the value reachable for asserts."""

    def __init__(self, signature, value):
        self.signature = signature
        self.value = value


class _FakeGLib:
    Variant = _Variant

    @staticmethod
    def VariantType(signature):
        return signature


class _Flags:
    NONE = 0


class _FakeGio:
    DBusSignalFlags = _Flags
    DBusCallFlags = _Flags


class _FakeReply:
    def unpack(self):
        return (0,)


class _FakeFdList:
    def get(self, index):
        return 7        # the pipewire fd open() is expected to hand back


class _FakeBus:
    def signal_subscribe(self, *args, **kwargs):
        return 1

    def call_with_unix_fd_list_sync(self, *args, **kwargs):
        return _FakeReply(), _FakeFdList()


def _portal(saved_token: str | None = None) -> tuple[ScreenCastPortal, dict]:
    """A portal whose `_call` records instead of talking to the bus.

    Built with `__new__` for the same reason the session tests do it: going
    through `__init__` would mean a live D-Bus connection and PyGObject for a
    method that only assembles a dictionary.
    """
    portal = ScreenCastPortal.__new__(ScreenCastPortal)
    portal._GLib = _FakeGLib
    portal._Gio = _FakeGio
    portal.bus = _FakeBus()
    portal.session = None
    portal.closed = False
    portal._closed_sub = None

    recorded: dict = {}

    def _call(method, signature, pre_args, options):
        recorded[method] = options
        if method == "CreateSession":
            return {"session_handle": "/org/freedesktop/portal/desktop/session/1"}
        if method == "Start":
            return {"streams": [(42, {})], "restore_token": "new-token"}
        return {}

    portal._call = _call
    portal._load_token = lambda window=False: saved_token
    portal._save_token = lambda token, window=False: recorded.setdefault(
        "saved", (token, window))
    return portal, recorded


def _types(recorded: dict) -> int:
    return recorded["SelectSources"]["types"].value


def test_screen_capture_offers_monitor_or_window():
    """The default keeps both on the table, so the picker can offer either."""
    portal, recorded = _portal()

    portal.open(window=False)

    assert _types(recorded) == MONITOR | WINDOW


def test_window_capture_asks_for_windows_only():
    """The whole point of the setting: a screen must not be offered at all.

    Leaving MONITOR in the mask would let the picker hand back a screen again,
    which is the state the user was already stuck in.
    """
    portal, recorded = _portal()

    portal.open(window=True)

    assert _types(recorded) == WINDOW
    assert not _types(recorded) & MONITOR


def test_saved_token_is_replayed():
    """Unchanged behaviour, pinned because the new setting deletes the token.

    `_on_source_kind_changed` calls `forget()` precisely because a token
    replayed here outranks the `types` mask — the portal restores what the
    token names and never asks.
    """
    portal, recorded = _portal(saved_token="old-token")

    portal.open()

    assert recorded["SelectSources"]["restore_token"].value == "old-token"


def test_capture_window_setting_round_trips():
    """The GUI's choice has to survive a restart, or it is not a setting.

    Writes for real: conftest gives the whole suite a throwaway HOME before
    any module is imported, so SETTINGS_FOLDER already points inside it.
    """
    from arctis_sound_manager import settings as settings_mod

    loaded = settings_mod.GeneralSettings.read_from_file()
    # Default: a screen — granted once, restored silently ever after, so the
    # buffer arms itself on detection with nobody asked anything.
    assert loaded.clips_capture_window is False

    loaded.clips_capture_window = True
    loaded.write_to_file()

    assert settings_mod.GeneralSettings.read_from_file().clips_capture_window is True


def test_token_from_the_other_source_kind_is_not_replayed(tmp_path, monkeypatch):
    """A window token replayed in screen mode restores the window — and once
    that window is gone, the picker on every launch. The kind is stored with
    the token and a mismatch reads as "no token"."""
    from arctis_sound_manager import clip_capture as cc
    monkeypatch.setattr(cc, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cc, "TOKEN_FILE", tmp_path / "tok.json")

    cc.ScreenCastPortal._save_token("win-token", window=True)

    assert cc.ScreenCastPortal._load_token(window=True) == "win-token"
    assert cc.ScreenCastPortal._load_token(window=False) is None


def test_legacy_token_file_is_not_trusted(tmp_path, monkeypatch):
    """A file written before the kind was recorded could be either — the
    window-default builds minted window tokens into it — so it is dropped
    for both, and the picker asked once for the right thing."""
    from arctis_sound_manager import clip_capture as cc
    monkeypatch.setattr(cc, "TOKEN_FILE", tmp_path / "tok.json")
    (tmp_path / "tok.json").write_text('{"restore_token": "old"}')

    assert cc.ScreenCastPortal._load_token(window=False) is None
    assert cc.ScreenCastPortal._load_token(window=True) is None
