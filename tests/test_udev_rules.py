# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
test_udev_rules.py — udev rules generation from device YAMLs.

Covers the regressions behind #146:
  1. A user override in ~/.config/arctis_manager/devices is MERGED with the
     bundled YAML of the same family (union of product ids), so a hand-added PID
     reaches the rules file AND a stale override can't hide a newer packaged PID.
  2. Every PID declared in the bundled YAMLs ends up in the rendered rules.
  3. Running elevated (sudo / pkexec) still reads the invoking user's override
     folder instead of /root's.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from arctis_sound_manager.constants import (UDEV_RULES_PATHS,
                                            UDEV_UACCESS_RULES_NAME,
                                            UDEV_UACCESS_RULES_PATHS)
from arctis_sound_manager.udev_rules import (generate_rules,
                                              generate_uaccess_rules,
                                              load_devices)

SRC_DEVICES = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"

_OVERRIDE_YAML = """\
device:
  name: SteelSeries Arctis Nova 7P (Gen 2)
  vendor_id: 0x1038
  product_ids:
    - 0x22a7
    - 0xdead
"""


def _write_override(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "nova_7p_perc_battery.yaml"
    path.write_text(_OVERRIDE_YAML)
    return path


def test_home_override_merges_with_bundled_yaml(tmp_path: Path):
    """The HOME copy and the packaged profile of a family are unioned (#146):
    a hand-added PID reaches the rules AND the bundled PIDs are still there."""
    home = _write_override(tmp_path / "devices").parent

    rules = generate_rules([home, SRC_DEVICES])

    # The override's hand-added PID is present…
    assert 'ATTRS{idProduct}=="dead"' in rules
    # …and so is a PID that only the bundled profile carries (0x2298), which
    # first-match-wins used to drop when the override lacked it.
    assert 'ATTRS{idProduct}=="2298"' in rules
    # The family is still emitted once — union, not a duplicate device block.
    assert rules.count("# SteelSeries Arctis Nova 7P (Gen 2)") == 1


def test_stale_override_does_not_hide_new_packaged_pid(tmp_path: Path):
    """The core #146 trap: a pre-existing HOME copy written before 0x2298 was
    added must not shadow it. With union, the packaged PID survives even though
    the stale copy never listed it."""
    stale = tmp_path / "devices"
    stale.mkdir(parents=True)
    # A copy of the family from before 0x2298 existed — only the old PID.
    (stale / "nova_7p_perc_battery.yaml").write_text(
        "device:\n"
        "  name: SteelSeries Arctis Nova 7P (Gen 2)\n"
        "  vendor_id: 0x1038\n"
        "  product_ids:\n"
        "    - 0x22a7\n"
    )

    rules = generate_rules([stale, SRC_DEVICES])

    assert 'ATTRS{idProduct}=="22a7"' in rules
    assert 'ATTRS{idProduct}=="2298"' in rules, "stale override must not hide 0x2298"
    assert rules.count("# SteelSeries Arctis Nova 7P (Gen 2)") == 1


def test_bundled_pids_all_rendered():
    """Every PID declared in the shipped YAMLs makes it into the rules file."""
    devices = load_devices([SRC_DEVICES])
    rules = generate_rules([SRC_DEVICES])

    for _vid, pids, name in devices:
        for pid in pids:
            assert f'ATTRS{{idProduct}}=="{pid:04x}"' in rules, f"{name}: 0x{pid:04x} missing"


def test_nova_7p_gen2_pids_present():
    """#146: 0x2298 (Arctis Nova 7P Gen 2) is declared alongside 0x22a7."""
    rules = generate_rules([SRC_DEVICES])

    assert 'ATTRS{idProduct}=="2298"' in rules
    assert 'ATTRS{idProduct}=="22a7"' in rules


def test_uaccess_file_sorts_before_seat_late():
    """#297: since systemd 258 the uaccess ACL is only applied for tags set
    before 73-seat-late.rules runs — a 91- file's tag is silently ignored."""
    prefix = int(UDEV_UACCESS_RULES_NAME.split('-', 1)[0])
    assert prefix < 73


def test_uaccess_rules_cover_every_pid_and_only_tag():
    """The 70- file tags exactly the PIDs of the 91- file and nothing else:
    MODE and the power attributes must stay in the late file so they keep
    overriding earlier rules."""
    devices = load_devices([SRC_DEVICES])
    rules = generate_uaccess_rules([SRC_DEVICES])

    for _vid, pids, name in devices:
        for pid in pids:
            assert f'ATTRS{{idProduct}}=="{pid:04x}", TAG+="uaccess"' in rules, \
                f"{name}: 0x{pid:04x} missing"
    assert 'MODE=' not in rules
    assert 'ATTR{power/' not in rules
    assert 'ACTION=="remove", GOTO="local_end"' in rules


def test_uaccess_paths_follow_the_main_rules_paths():
    """Diagnostics and the checker must search the 70- companion beside each
    91- location so a packaged or /etc installation gets the same verdict."""
    assert UDEV_UACCESS_RULES_PATHS == [
        str(Path(path).with_name(UDEV_UACCESS_RULES_NAME))
        for path in UDEV_RULES_PATHS
    ]


def test_checker_requires_current_uaccess_companion(monkeypatch):
    """#297: a current 91- file alone does not grant session ACLs on systemd
    258+, so the setup dialog must install the new 70- companion."""
    from arctis_sound_manager import udev_checker

    main = ('91 rules', 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1038", '
            'ATTRS{idProduct}=="beef", MODE="0666", TAG+="uaccess"\n'
            'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1038", '
            'ATTRS{idProduct}=="beef", MODE="0666"')
    uaccess = ('70 rules', 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1038", '
               'ATTRS{idProduct}=="beef", TAG+="uaccess"')
    monkeypatch.setattr(udev_checker, '_expected_pids', lambda: [(0x1038, 0xbeef)])
    monkeypatch.setattr(udev_checker, '_host_rules_contents', lambda _paths: [])
    monkeypatch.setattr(
        udev_checker, '_local_rules_contents',
        lambda paths: [main] if paths == UDEV_RULES_PATHS else [],
    )
    assert udev_checker.get_udev_rules_status() == 'outdated'

    monkeypatch.setattr(
        udev_checker, '_local_rules_contents',
        lambda paths: [main] if paths == UDEV_RULES_PATHS else [uaccess],
    )
    assert udev_checker.get_udev_rules_status() == 'ok'


def test_power_persist_disabled_alongside_power_control():
    """#238: power/persist="0" must sit next to power/control="on" on every
    rule line, so a resumed device is not assumed still-connected across
    suspend and forced to re-enumerate instead."""
    rules = generate_rules([SRC_DEVICES])

    for line in rules.splitlines():
        if 'ATTR{power/control}="on"' in line:
            assert 'ATTR{power/persist}="0"' in line


def test_elevated_run_reads_invoking_user_devices(tmp_path: Path, monkeypatch):
    """Under sudo, DEVICES_CONFIG_FOLDER must include the real user's folder."""
    import pwd

    fake_home = tmp_path / "home" / "alberto"
    _write_override(fake_home / ".config" / "arctis_manager" / "devices")

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.delenv("PKEXEC_UID", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/root")))
    monkeypatch.setattr(
        pwd, "getpwuid", lambda uid: pwd.struct_passwd(
            ("alberto", "x", 1000, 1000, "", str(fake_home), "/bin/sh")
        )
    )

    import arctis_sound_manager.constants as constants
    constants = importlib.reload(constants)
    try:
        expected = fake_home / ".config" / "arctis_manager" / "devices"
        assert constants.INVOKING_USER_CONFIG_FOLDER == expected
        assert constants.DEVICES_CONFIG_FOLDER[0] == expected

        rules = generate_rules(constants.DEVICES_CONFIG_FOLDER)
        assert 'ATTRS{idProduct}=="dead"' in rules
    finally:
        # Leave the module in its unelevated state for the rest of the suite.
        monkeypatch.undo()
        importlib.reload(constants)


def test_unelevated_run_has_no_invoking_user_folder(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setenv("SUDO_UID", "1000")

    import arctis_sound_manager.constants as constants
    constants = importlib.reload(constants)
    try:
        assert constants.INVOKING_USER_CONFIG_FOLDER is None
        assert constants.DEVICES_CONFIG_FOLDER[0] == constants.HOME_CONFIG_FOLDER
    finally:
        monkeypatch.undo()
        importlib.reload(constants)


def _rule(pid: str, tag_only: bool = False, hidraw: bool = True) -> str:
    extra = '' if tag_only else 'MODE="0666", '
    line = (f'SUBSYSTEM=="usb", ATTRS{{idVendor}}=="1038", '
            f'ATTRS{{idProduct}}=="{pid}", {extra}TAG+="uaccess"')
    if hidraw and not tag_only:
        line += (f'\nKERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="1038", '
                 f'ATTRS{{idProduct}}=="{pid}", MODE="0666"')
    return line


def test_stale_etc_copy_shadows_a_current_packaged_file(monkeypatch):
    """#217: /etc replaces the same-named packaged file in /usr/lib. A stale
    /etc copy must read as outdated even when /usr/lib is complete, or
    asm-setup never rewrites it and the new PID stays without access."""
    from arctis_sound_manager import udev_checker

    etc, usr = UDEV_RULES_PATHS[0], UDEV_RULES_PATHS[1]
    etc_ua, usr_ua = UDEV_UACCESS_RULES_PATHS[0], UDEV_UACCESS_RULES_PATHS[1]
    files = {
        etc: _rule('2317'), usr: _rule('2317') + '\n' + _rule('231a'),
        etc_ua: _rule('2317', True), usr_ua: _rule('2317', True) + '\n' + _rule('231a', True),
    }
    monkeypatch.setattr(udev_checker, '_expected_pids',
                        lambda: [(0x1038, 0x2317), (0x1038, 0x231a)])
    monkeypatch.setattr(udev_checker, '_host_rules_contents', lambda _paths: [])
    monkeypatch.setattr(udev_checker, '_local_rules_contents',
                        lambda paths: [(p, files[p]) for p in paths if p in files])
    assert udev_checker.get_udev_rules_status() == 'outdated'

    # Without the /etc copies the packaged files are the ones udev reads.
    for p in (etc, etc_ua):
        del files[p]
    assert udev_checker.get_udev_rules_status() == 'ok'


def test_run_overlay_outranks_the_packaged_file():
    """udev's order is /etc, then /run, then /usr/lib — not the list order."""
    from arctis_sound_manager import udev_checker

    found = [('/usr/lib/udev/rules.d/x.rules', 'a'), ('/run/udev/rules.d/x.rules', 'b')]
    assert udev_checker._effective(found) == [('/run/udev/rules.d/x.rules', 'b')]
    assert udev_checker._effective([]) == []


def test_expected_pids_include_the_charging_case():
    """#217: the case PIDs are written to the rules (udev_rules.py), so a file
    without them must not pass the check."""
    from arctis_sound_manager import udev_checker

    pids = {pid for _, pid in udev_checker._expected_pids()}
    assert {0x2317, 0x230c, 0x231a} <= pids


def _fake_rules(monkeypatch, files: dict[str, str]):
    from arctis_sound_manager import udev_checker
    monkeypatch.setattr(udev_checker, '_host_rules_contents', lambda _paths: [])
    monkeypatch.setattr(udev_checker, '_local_rules_contents',
                        lambda paths: [(p, files[p]) for p in paths if p in files])
    return udev_checker


def test_rules_without_hidraw_lines_are_outdated(monkeypatch):
    """#342: an /etc copy written before 1.7.0 covers every PID on its usb
    lines but has no hidraw line; it shadows the packaged file, the hidraw
    nodes stay 0600 and ASM silently falls back to libusb. It must read as
    outdated so the setup dialog rewrites it."""
    etc, etc_ua = UDEV_RULES_PATHS[0], UDEV_UACCESS_RULES_PATHS[0]
    files = {etc: _rule('12ad', hidraw=False), etc_ua: _rule('12ad', True)}
    udev_checker = _fake_rules(monkeypatch, files)
    monkeypatch.setattr(udev_checker, '_expected_pids', lambda: [(0x1038, 0x12ad)])
    assert udev_checker.get_udev_rules_status() == 'outdated'

    files[etc] = _rule('12ad')
    assert udev_checker.get_udev_rules_status() == 'ok'


def test_hidraw_rules_cover_reads_the_file_udev_reads(monkeypatch):
    """#342: the stale /etc copy decides, not the current packaged one."""
    etc, usr = UDEV_RULES_PATHS[0], UDEV_RULES_PATHS[1]
    files = {etc: _rule('12ad', hidraw=False), usr: _rule('12ad')}
    udev_checker = _fake_rules(monkeypatch, files)
    assert udev_checker.hidraw_rules_cover(0x12ad) is False

    del files[etc]
    assert udev_checker.hidraw_rules_cover(0x12ad) is True
    assert udev_checker.hidraw_rules_cover(0x2232) is False

    files.clear()
    assert udev_checker.hidraw_rules_cover(0x12ad) is None


def test_generated_rules_pass_the_hidraw_check():
    """What udev_rules.py writes must satisfy the checker's hidraw test."""
    from arctis_sound_manager import udev_checker
    rules = generate_rules([SRC_DEVICES])
    usb = udev_checker._pids_in_rules(rules)
    assert usb and usb <= udev_checker._pids_in_rules(rules, 'hidraw')
