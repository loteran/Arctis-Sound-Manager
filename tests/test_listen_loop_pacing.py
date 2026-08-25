# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Issue #211: the ChatMix dial was paced by the quietest interface.

loop() used to await gather() over one listen task per interface, and each of
those tasks performs a single read before returning. gather() waits for all of
them, so the slowest read set the pace for every interface at once.

On a Nova 7 that is the whole bug. The dial pushes its ChatMix frames on one
interface while another only answers a polled status frame and therefore blocks
for its full 1 s read timeout. Every dial frame arriving inside that second was
read late or not at all, which the reporter saw as the wheel jumping straight
from 100 to 53 instead of moving through the values.

Waiting on FIRST_COMPLETED and resubmitting only what finished lets the dial run
at its own pace. That change also removed an accidental brake: gather() held the
turn until the slowest interface returned, which paced the *failing* paths too.
A listen task that returns without awaiting anything is now resubmitted
immediately and would spin a core, so every early return has to cost time. That
is what the second half of this file is about.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import arctis_sound_manager.core as core_mod

SRC = Path(core_mod.__file__).read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    raise AssertionError(f"{name}() not found: this test needs updating")


def test_the_loop_does_not_wait_for_every_interface():
    """gather() here is the bug, not a style choice."""
    body = _function_body("loop")
    assert "asyncio.gather(*listen_coroutines" not in body, (
        "loop() is back to gather()ing the listen tasks, so the slowest "
        "interface again sets the pace for the ChatMix dial (#211)"
    )
    assert "FIRST_COMPLETED" in body, (
        "loop() must wake on the first completed read, not on all of them"
    )


def test_only_the_finished_interface_is_resubmitted():
    """Resubmitting all of them on every wake would restart reads that are
    still in flight, which is how you lose frames rather than gain them."""
    body = _function_body("loop")
    assert "listen_tasks" in body, "the per-interface task map is gone"
    assert re.search(r"if task is None or task\.done\(\)", body), (
        "loop() no longer checks whether an interface's read has finished "
        "before resubmitting it"
    )


def test_every_early_return_in_the_listen_loop_awaits_first():
    """The one that bites: an interface named in a profile but absent on the
    hardware used to be paced by gather(). Now it comes straight back, and a
    bare `return` would spin a core at 100%.

    Walks the real AST rather than grepping, so a new early return added later
    is caught even if it is worded differently.
    """
    body = _function_body("listen_endpoint_loop")

    # Only the part before the read: once `await usb_device.read(...)` has run,
    # the turn has already been yielded and a later return costs nothing. The
    # early exits are the ones that can spin, and they all live above it.
    head, sep, _ = body.partition("await asyncio.to_thread(usb_device.read")
    assert sep, "the read moved: this test needs updating"

    lines = [ln.strip() for ln in head.splitlines()]
    offenders = [
        (i, ln) for i, ln in enumerate(lines)
        if ln == "return" and not any(
            "await" in prev for prev in lines[max(0, i - 3):i]
        )
    ]

    assert not offenders, (
        "listen_endpoint_loop() returns without awaiting anything first, at "
        f"line(s) {[i for i, _ in offenders]} of the function. loop() "
        "resubmits a finished listen task immediately, so this spins a core "
        "(#211). Await a back-off before returning."
    )


def test_the_idle_backoff_is_a_real_wait():
    """A back-off of 0 would satisfy the check above and fix nothing."""
    assert core_mod._LISTEN_IDLE_BACKOFF_S >= 0.1, (
        "the idle back-off is too short to keep a failing interface from "
        "monopolising the loop"
    )


def test_nova_7_listens_on_the_dial_interface():
    """The other half of #211: the dial pushes 0x45 frames on interface 5,
    while interface 3 only carries the polled 0xb0 status frame. Listening on
    3 alone left the dial to the runtime detection getting lucky once."""
    from arctis_sound_manager.config import load_device_configurations

    for c in load_device_configurations():
        if 0x22A1 in getattr(c, "product_ids", []):
            assert 5 in c.listen_interface_indexes, (
                f"{c.name} no longer listens on interface 5, where the ChatMix "
                f"dial actually pushes its frames (#211)"
            )
            break
    else:
        raise AssertionError("no profile claims PID 0x22a1 any more")


def test_nova_5_listens_on_the_dial_interface_too():
    """The same gap, found while checking #211 and older than it.

    upstream (elegos/Linux-Arctis-Manager) lists [3, 5] for the Nova 5. ASM's
    profile was written with [3] when the family was added on 2026-04-03, so
    the 5 was never there: a value lost in the copy rather than a decision.
    Restored, not measured, so this test names its source instead of claiming
    hardware it was never checked against.
    """
    from arctis_sound_manager.config import load_device_configurations

    for c in load_device_configurations():
        if 0x2232 in getattr(c, "product_ids", []):
            assert 5 in c.listen_interface_indexes, (
                f"{c.name} lost interface 5 again. Upstream lists [3, 5]; the "
                f"dial pushes there while 3 carries the polled status frame"
            )
            break
    else:
        raise AssertionError("no profile claims PID 0x2232 any more")
