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
