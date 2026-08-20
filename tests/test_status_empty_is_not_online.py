# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""An empty status must not be reported as a connected headset (#198).

GetStatus injected `{"headset_power_status": "online"}` whenever the result was
empty and the daemon had initialised the device. The intent — stated in its own
comment — was to cover devices that have *no way* to report a power state, like
the always-connected Nova 3. The condition never checked for that.

So on a Nova Pro Wireless whose command channel answered nothing, the window
said Connected while the OLED on the same device said Offline (it reads
core.device_status directly, and saw the empty dict it really is), with no
battery on either. Two surfaces, opposite answers, same state — and the
sentinel was the one lying.

The reporter's whole payload was, character for character, that literal. It is
worth recognising on sight: it means nothing was read at all.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arctis_sound_manager.dbus_service import ArctisManagerDbusStatusService


def _config(*, has_online_status: bool):
    """A device config whose representation yields nothing — the empty-result
    case both branches are about."""
    online = (SimpleNamespace(status_variable='headset_power_status',
                              online_value='online')
              if has_online_status else None)
    return SimpleNamespace(
        status=SimpleNamespace(representation={}),
        status_parse={},
        online_status=online,
    )


def _service(config, *, device_ready: bool = True):
    core = MagicMock()
    core.device_config = config
    core.device_status = {}
    core._device_ready = device_ready

    service = ArctisManagerDbusStatusService.__new__(ArctisManagerDbusStatusService)
    service.core_engine = core
    return service


def _status_of(service) -> dict:
    """Call the real method body.

    dbus_next's @method decorator replaces get_status with a wrapper that
    returns None when called outside a bus; `__wrapped__` is the function that
    actually computes the reply.
    """
    fn = getattr(type(service).get_status, '__wrapped__', type(service).get_status)
    return json.loads(fn(service))


def test_a_headset_that_reports_power_is_not_faked_online():
    """The #198 case. Nothing was read; saying "online" invents a reading."""
    payload = _status_of(_service(_config(has_online_status=True)))

    assert payload == {}, "an empty status must stay empty"


def test_a_device_with_no_power_reporting_still_gets_the_sentinel():
    """The case the sentinel was written for: an always-connected wired
    headset has nothing to report and is nonetheless plainly connected."""
    payload = _status_of(_service(_config(has_online_status=False)))

    assert payload["headset"]["headset_power_status"]["value"] == "online"


def test_nothing_is_claimed_before_the_device_is_ready():
    payload = _status_of(
        _service(_config(has_online_status=False), device_ready=False))

    assert payload == {}
