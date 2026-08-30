# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for recovering from a link PipeWire refuses on permissions (#181).

Background: PipeWire denies a link when the client owning one end cannot see
the node at the other end. On a system where ASM's clients come up as
`access=restricted` (seen on SteamOS), every link ASM needs is refused, the
channels reach nothing and there is no audio at all. The user cannot work
around it either: running `pw-link` by hand fails the same way.

`pw-cli` talks to the manager socket, which is unrestricted, so ASM can raise
the permission for the clients it owns and retry. These tests pin the parts
that make that safe: it only fires on an actual refusal, only touches the two
clients at the ends of that link, and gives up rather than looping.

They also pin the shape of the command itself, which is where this repair
spent a release doing nothing at all: `-1` (every object) is eaten by pw-cli's
getopt as an option unless `--` comes first, and pw-cli reports a command that
failed on stderr while still exiting 0. Every assertion here mocks `_pw_run`,
so nothing in this file can notice pw-cli rejecting its own arguments — that
is what let the bug through, and why the argv shape is asserted explicitly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from arctis_sound_manager import pw_utils


def _dump() -> list:
    """Two ports on two nodes owned by two different clients."""
    return [
        {'id': 10, 'type': 'PipeWire:Interface:Port',
         'info': {'props': {'node.id': 100}}},
        {'id': 20, 'type': 'PipeWire:Interface:Port',
         'info': {'props': {'node.id': 200}}},
        {'id': 100, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'Arctis_Game_sink_out', 'client.id': '232'}}},
        {'id': 200, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'effect_input.sonar-game-eq',
                            'client.id': '184'}}},
        # Daemon-owned: no client.id at all.
        {'id': 30, 'type': 'PipeWire:Interface:Port',
         'info': {'props': {'node.id': 300}}},
        {'id': 300, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'effect_output.sonar-game-eq'}}},
    ]


def _ok(*_a, **_k):
    return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')


def setup_function():
    pw_utils._perm_repair_attempted.clear()
    pw_utils._perm_repair_attempted_props.clear()


def test_grants_only_the_two_clients_at_the_ends():
    """Not a blanket grant: only the clients owning this link's endpoints."""
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is True

    targets = sorted(c[3] for c in calls if c[:3] == ['pw-cli', '--', 'permissions'])
    assert targets == ['184', '232']
    # The linking flag is the point: "rwxm" alone would not help (#181).
    assert all(c[-1] == 'rwxml' for c in calls)


def test_third_party_clients_are_never_granted_anything():
    """The far end of a link is often the physical sink, owned by
    WirePlumber. Raising permissions there would be an elevation on a client
    ASM does not own — not ours to do, even inside the user's own session.
    Only clients behind nodes ASM created are ever touched.
    """
    dump = _dump() + [
        {'id': 40, 'type': 'PipeWire:Interface:Port',
         'info': {'props': {'node.id': 400}}},
        {'id': 400, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'alsa_output.usb-SteelSeries_Arctis_7_-00',
                            'client.id': '56'}}},
    ]
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', lambda: dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        # Our node at one end, the device at the other.
        assert pw_utils.grant_link_permissions(10, 40) is True

    targets = [c[3] for c in calls if c[:3] == ['pw-cli', '--', 'permissions']]
    assert targets == ['232'], 'only the ASM-owned end may be granted'
    assert '56' not in targets, "WirePlumber's client must be left alone"


def test_daemon_owned_ends_are_left_alone():
    """A node with no owning client is exempt from the check already, so a
    refusal there came from somewhere else and this must not pretend to fix
    it."""
    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', _ok), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(30, 30) is False


def test_repair_is_attempted_once_per_pair():
    """The watchdog retries every few seconds. Without this it would run
    pw-cli forever on a system where the grant does not help."""
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is True
        first = len(calls)
        assert pw_utils.grant_link_permissions(10, 20) is False
        assert len(calls) == first, 'second attempt must not re-run pw-cli'


def test_no_pw_cli_is_not_an_error():
    with patch.object(pw_utils.shutil, 'which', lambda _: None):
        assert pw_utils.grant_link_permissions(10, 20) is False


def test_failed_grant_reports_false():
    """pw-cli refusing too means the caller must not bother retrying."""
    def run(argv, **_k):
        return SimpleNamespace(returncode=1, stdout=b'', stderr=b'denied')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is False


def test_command_separates_options_from_the_subcommand():
    """`--` before the sub-command, or the repair never runs (#181).

    pw-cli hands its arguments to getopt before parsing the command, so `-1`
    reads as an option: without the separator it exits with
    `invalid option -- '1'` and the grant fails on every system, restricted or
    not. Reproduced on pipewire 1.6.8, and by the reporter typing the same
    command by hand.
    """
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is True

    for argv in calls:
        assert argv[1] == '--', f'{argv}: pw-cli would read -1 as an option'
        assert argv.index('--') < argv.index('-1')


def test_stderr_beats_a_zero_exit_status():
    """pw-cli exits 0 on a command that failed, saying so only on stderr.

    Trusting the status alone would report a grant that never happened, and the
    caller would retry a link that is still refused.
    """
    def run(argv, **_k):
        return SimpleNamespace(
            returncode=0, stdout=b'',
            stderr=b'Error: "permissions: unknown global \'232\'"')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is False


# ---------------------------------------------------------------------------
# Retrying the repair, and telling a refusal apart from a missing node (#181)
# ---------------------------------------------------------------------------

def test_repair_is_retried_after_the_cooldown_not_before():
    """One attempt per pair for the life of the daemon was too few.

    Port ids live as long as their loopback, so a pair that failed its single
    attempt — session manager still coming up, a transient pw-cli error — could
    never be repaired again, and its link stayed refused for the whole session.
    """
    calls: list[list[str]] = []
    clock = {'now': 1000.0}

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.time, 'monotonic', lambda: clock['now']), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_link_permissions(10, 20) is True
        first = len(calls)

        # A second later: refused, so pw-cli is not hammered on every tick.
        clock['now'] += 1.0
        assert pw_utils.grant_link_permissions(10, 20) is False
        assert len(calls) == first

        # Past the cooldown, the same pair is repairable again — the port ids
        # have not changed and never will while the loopback lives.
        clock['now'] += pw_utils._PERM_REPAIR_RETRY_S
        assert pw_utils.grant_link_permissions(10, 20) is True
        assert len(calls) > first


def _graph_dump():
    """A real-shaped pw-dump: the Game loopback and the Game EQ, no links yet.

    Built from the same fields ensure_loopback_link reads (node.name, node.id,
    port.direction, audio.channel) rather than by patching its internals, so
    the test still fails if the way it walks the graph changes.
    """
    objs = [
        {'id': 500, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'Arctis_Game_sink_out',
                            'client.id': '232'}}},
        {'id': 600, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'effect_input.sonar-game-eq',
                            'client.id': '184'}}},
    ]
    for port_id, channel in ((10, 'FL'), (11, 'FR')):
        objs.append({'id': port_id, 'type': 'PipeWire:Interface:Port',
                     'info': {'props': {'node.id': 500, 'port.direction': 'out',
                                        'audio.channel': channel}}})
    for port_id, channel in ((20, 'FL'), (21, 'FR')):
        objs.append({'id': port_id, 'type': 'PipeWire:Interface:Port',
                     'info': {'props': {'node.id': 600, 'port.direction': 'in',
                                        'audio.channel': channel}}})
    return objs


def test_outcome_reports_refusals_separately_from_failures():
    """A caller that only knows "did not link" cannot choose what to do next.

    Recreating the loopback is right when the node is missing and wrong when
    the link was refused — the new client is restricted just the same, and the
    recreation drops the channels that were still playing.
    """
    dump = _graph_dump()

    def run(argv, **_k):
        if argv[0] == 'pw-link':
            return SimpleNamespace(
                returncode=1, stdout=b'',
                stderr=b'failed to link ports: Operation not permitted')
        # pw-cli: the grant itself succeeds, so the refusals below are the
        # link's own and not a repair that could not run.
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    outcome: dict = {}
    with patch.object(pw_utils, '_pw_dump', _graph_dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        linked = pw_utils.ensure_loopback_link(
            'Arctis_Game_sink_out', 'effect_input.sonar-game-eq',
            data=dump, outcome=outcome)

    assert linked is False
    assert outcome == {'created': 0, 'total': 2, 'denied': 2}, outcome


def test_outcome_is_clean_when_the_links_go_through():
    """The same call on a system that allows linking reports no refusal, which
    is what keeps the watchdog free to recreate a loopback when it should."""
    dump = _graph_dump()
    outcome: dict = {}

    with patch.object(pw_utils, '_pw_dump', _graph_dump), \
         patch.object(pw_utils, '_pw_run', _ok), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        linked = pw_utils.ensure_loopback_link(
            'Arctis_Game_sink_out', 'effect_input.sonar-game-eq',
            data=dump, outcome=outcome)

    assert linked is True
    assert outcome == {'created': 2, 'total': 2, 'denied': 0}, outcome


# ---------------------------------------------------------------------------
# The same repair, hit from the Props (set-param) call site instead of a link
# (#181, SteamOS EQ/preset case): PipeWire refuses set-param on a filter-chain
# node the same way it refuses pw-link, on the same class of system.
# ---------------------------------------------------------------------------

def test_grant_props_permissions_grants_the_owning_client():
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_props_permissions('effect_input.sonar-game-eq', _dump()) is True

    targets = [c[3] for c in calls if c[:3] == ['pw-cli', '--', 'permissions']]
    assert targets == ['184']
    assert all(c[-1] == 'rwxml' for c in calls)


def test_grant_props_permissions_leaves_non_asm_nodes_alone():
    dump = _dump() + [
        {'id': 400, 'type': 'PipeWire:Interface:Node',
         'info': {'props': {'node.name': 'alsa_output.usb-SteelSeries_Arctis_7_-00',
                            'client.id': '56'}}},
    ]
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_props_permissions(
            'alsa_output.usb-SteelSeries_Arctis_7_-00', dump) is False

    assert calls == [], "a client ASM does not own must never be granted anything"


def test_grant_props_permissions_respects_cooldown():
    calls: list[list[str]] = []

    def run(argv, **_k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        assert pw_utils.grant_props_permissions('effect_input.sonar-game-eq', _dump()) is True
        first = len(calls)
        assert pw_utils.grant_props_permissions('effect_input.sonar-game-eq', _dump()) is False
        assert len(calls) == first, 'second attempt must not re-run pw-cli'


def test_set_filter_controls_retries_after_permission_repair():
    """The live-apply path used by every preset click / EQ slider drag: a
    set-param refused on permissions must be retried once, exactly like a
    refused pw-link, instead of being reported as a failed apply."""
    set_param_calls: list[list[str]] = []
    grant_calls: list[list[str]] = []

    def run(argv, **_k):
        if argv[:2] == ['pw-cli', 'set-param']:
            set_param_calls.append(argv)
            if len(set_param_calls) == 1:
                # set_filter_controls runs pw-cli with text=True.
                return SimpleNamespace(
                    returncode=1, stdout='',
                    stderr='Error: set-param failed: Operation not permitted')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        grant_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        ok = pw_utils.set_filter_controls('effect_input.sonar-game-eq', {'bq0:Gain': 3.0})

    assert ok is True
    assert len(set_param_calls) == 2, 'must retry set-param exactly once after the repair'
    assert any(c[:3] == ['pw-cli', '--', 'permissions'] for c in grant_calls)


def test_set_filter_controls_gives_up_when_repair_does_not_help():
    """When granting the owning client changes nothing, set_filter_controls
    must report the genuine failure rather than retry forever or lie."""
    set_param_calls: list[list[str]] = []

    def run(argv, **_k):
        if argv[:2] == ['pw-cli', 'set-param']:
            set_param_calls.append(argv)
            return SimpleNamespace(
                returncode=1, stdout='',
                stderr='Error: set-param failed: Operation not permitted')
        return SimpleNamespace(returncode=1, stdout=b'', stderr=b'denied')

    with patch.object(pw_utils, '_pw_dump', _dump), \
         patch.object(pw_utils, '_pw_run', run), \
         patch.object(pw_utils.shutil, 'which', lambda _: '/usr/bin/pw-cli'):
        ok = pw_utils.set_filter_controls('effect_input.sonar-game-eq', {'bq0:Gain': 3.0})

    assert ok is False
    assert len(set_param_calls) == 1, 'a failed grant must not be followed by a retry'
