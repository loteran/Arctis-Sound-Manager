# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #202: a partly configured headset is still a present headset.

configure_virtual_sinks() calls init_device() and then, at the very end, sets
_device_ready. init_device() handles USB errors per command and carries on, but
anything else it raises escaped — and _device_ready gates the status sentinel
that tells the GUI "connected" for profiles with no status block at all
(gamebuds.yaml is exactly that: `status` is None, so GetStatus is empty by
design).

The result on the reporter's GameBuds X — a PID whose profile documents its
protocol as assumed rather than captured — was audio working, settings
applying, and every GUI surface saying "No device detected".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration


def _gamebuds() -> DeviceConfiguration:
    raw = YAML(typ="safe").load(
        (Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager"
         / "devices" / "gamebuds.yaml").read_text()
    )
    return DeviceConfiguration(raw)


def test_the_profile_really_has_no_status_block():
    """The premise: without this, GetStatus has nothing to report and the
    sentinel is the only thing standing between the user and "no device"."""
    cfg = _gamebuds()
    assert cfg.status is None
    assert cfg.online_status is None


def test_the_status_sentinel_reports_online_for_such_a_device():
    from arctis_sound_manager.dbus_service import ArctisManagerDbusStatusService as Svc

    service = Svc.__new__(Svc)
    service.core_engine = MagicMock()
    service.core_engine._device_ready = True
    service.core_engine.device_status = {}
    service.core_engine.device_config = _gamebuds()

    import json
    # dbus_next's @method wraps the function; call the real one.
    result = json.loads(Svc.get_status.__wrapped__(service))

    assert result["headset"]["headset_power_status"]["value"] == "online"


def test_init_device_failing_does_not_mark_the_device_absent():
    """The fix: an exception out of init_device() must not skip the line that
    records the device as ready."""
    import inspect

    from arctis_sound_manager.core import CoreEngine

    src = inspect.getsource(CoreEngine.configure_virtual_sinks)
    init_at = src.index("self.init_device()")
    ready_at = src.rindex("self._device_ready = True")
    guarded = src[:init_at].rstrip().endswith("try:")

    assert guarded, "init_device() must be called inside a try"
    assert init_at < ready_at


def test_a_device_that_is_not_ready_yet_reports_nothing():
    """The sentinel must not claim "online" before the daemon has actually
    brought the device up — that is the #198 lesson: inventing a reading is
    what made a broken command channel invisible."""
    import json
    from unittest.mock import MagicMock

    from arctis_sound_manager.dbus_service import \
        ArctisManagerDbusStatusService as Svc

    service = Svc.__new__(Svc)
    service.core_engine = MagicMock()
    service.core_engine._device_ready = False
    service.core_engine.device_status = {}
    service.core_engine.device_config = _gamebuds()

    assert json.loads(Svc.get_status.__wrapped__(service)) == {}


def test_a_device_that_does_report_its_power_state_is_untouched():
    """Profiles WITH a status block keep the #198 behaviour: an empty parse
    means "we know nothing", not "online"."""
    import json
    from unittest.mock import MagicMock

    from arctis_sound_manager.dbus_service import \
        ArctisManagerDbusStatusService as Svc

    raw = YAML(typ="safe").load(
        (Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager"
         / "devices" / "nova_pro_wireless.yaml").read_text()
    )
    service = Svc.__new__(Svc)
    service.core_engine = MagicMock()
    service.core_engine._device_ready = True
    service.core_engine.device_status = {}
    service.core_engine.device_config = DeviceConfiguration(raw)

    result = json.loads(Svc.get_status.__wrapped__(service))
    assert result == {}, "a silent command channel must stay visible as silent"


@pytest.mark.parametrize("profile", sorted(
    p.name for p in (Path(__file__).resolve().parents[1] / "src"
                     / "arctis_sound_manager" / "devices").glob("*.yaml")))
def test_every_profile_reports_something_once_the_device_is_ready(profile):
    """The generic guard #202 was missing.

    A profile is free to expose no battery, no ANC, no OLED — but once the
    daemon has brought the device up, the GUI must never be told "nothing",
    because every surface reads that as "No device detected". This is checked
    across all profiles rather than for GameBuds alone: the poorest profile is
    the one most likely to fall through a code path written with a rich one in
    mind, and it is the case nobody's own hardware reproduces.
    """
    import json
    from unittest.mock import MagicMock

    from arctis_sound_manager.dbus_service import \
        ArctisManagerDbusStatusService as Svc

    raw = YAML(typ="safe").load(
        (Path(__file__).resolve().parents[1] / "src" / "arctis_sound_manager"
         / "devices" / profile).read_text()
    )
    config = DeviceConfiguration(raw)
    if config.status is not None:
        pytest.skip("has a status block: covered by the #198 behaviour instead")

    service = Svc.__new__(Svc)
    service.core_engine = MagicMock()
    service.core_engine._device_ready = True
    service.core_engine.device_status = {}
    service.core_engine.device_config = config

    result = json.loads(Svc.get_status.__wrapped__(service))
    assert result, (
        f"{profile} reports an empty status with the device ready — every GUI "
        f"surface renders that as 'No device detected' (#202)"
    )
