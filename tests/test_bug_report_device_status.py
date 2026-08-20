# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""A bug report must say what the daemon decodes from the device.

A Discord report — "in the custom DAC settings under battery it simply shows
offline on my DAC" — could not be settled from a bug report. The USB section
says which device is plugged in, but nothing said what ASM reads out of it,
and the difference between "this model has no battery" and "the battery is not
being decoded" is the entire answer.

So the report now carries the daemon's GetStatus payload. It is levels and
mode names — no identifiers, nothing to redact.
"""
from __future__ import annotations

import json

from arctis_sound_manager import bug_reporter


_PAYLOAD = {
    "headset": {
        "headset_power_status": {"value": "online", "type": "label"},
        "headset_battery_charge": {"value": 90, "type": "percentage"},
    }
}


def test_the_status_is_unwrapped_from_busctl_json(monkeypatch):
    """busctl --json=short answers {"type":"s","data":["<json>"]}. Printing
    that raw would bury the status under two layers of quoting."""
    monkeypatch.setattr(bug_reporter, "_run_out",
                        lambda *a, **k: json.dumps(
                            {"type": "s", "data": [json.dumps(_PAYLOAD)]}))

    dump = bug_reporter._device_status_dump()

    assert json.loads(dump) == _PAYLOAD
    assert "headset_battery_charge" in dump


def test_a_silent_daemon_is_reported_not_swallowed(monkeypatch):
    """A daemon that is not running is itself the answer to a lot of reports,
    so the absence has to be written down rather than left blank."""
    monkeypatch.setattr(bug_reporter, "_run_out", lambda *a, **k: "")

    assert "daemon" in bug_reporter._device_status_dump()


def test_an_unexpected_reply_is_passed_through(monkeypatch):
    """Whatever busctl said is more useful than an exception or an empty
    section — a changed output format must not cost the whole report."""
    monkeypatch.setattr(bug_reporter, "_run_out", lambda *a, **k: "not json at all")

    assert bug_reporter._device_status_dump() == "not json at all"


def test_the_section_reaches_the_rendered_report(monkeypatch):
    """Collected is not the same as printed: the value has to survive
    format_bug_report and land under a heading someone will read."""
    monkeypatch.setattr(bug_reporter, "collect_system_info",
                        lambda: {"device_status": json.dumps(_PAYLOAD, indent=2)})

    report = bug_reporter.format_bug_report()

    assert "Device status" in report
    assert "headset_battery_charge" in report
