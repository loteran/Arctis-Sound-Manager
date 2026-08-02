# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""The translation files have to parse, and a duplicate key means they do not.

Found by adding a `clips_shortcut` that already existed twenty lines up.
configparser is strict about duplicate options: it raises part-way through the
file, and everything *after* the duplicate is lost. So one repeated key does not
cost you that key — it costs every string below it, which showed up as raw
`ui.clips_source` labels in the interface while the keys above kept working.

Nothing about that failure points at the real cause, so it is worth a test that
names it.

Parsed the way the app parses: through `sanitize_ini_text`, which folds the
un-indented continuation lines the translated files are full of. Checking the
raw text instead would fail on tr.ini for a condition the loader is built to
absorb — a test that reports a handled case as breakage teaches people to
ignore it. Duplicates survive sanitising, which is the point.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from arctis_sound_manager.lang_sanitize import sanitize_ini_text

LANG_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "lang"
LANG_FILES = sorted(LANG_DIR.glob("*.ini"))


def _parse(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(strict=True)
    parser.read_string(sanitize_ini_text(path.read_text(encoding="utf-8")),
                       source=str(path))
    return parser


def test_there_are_language_files_to_check():
    """A glob that quietly matches nothing would make every test below pass."""
    assert LANG_FILES, f"no .ini files under {LANG_DIR}"


@pytest.mark.parametrize("path", LANG_FILES, ids=lambda p: p.name)
def test_the_file_parses_the_way_the_app_parses_it(path: Path):
    """Strict is what the app uses. A file that only parses leniently is a file
    whose tail silently stops existing."""
    _parse(path)  # raises on a duplicate key or section


@pytest.mark.parametrize("path", LANG_FILES, ids=lambda p: p.name)
def test_no_key_is_defined_twice_in_a_section(path: Path):
    """Same defect as above, reported usefully: strict parsing says a duplicate
    exists, this says which key and which line."""
    section: str | None = None
    seen: dict[tuple[str, str], int] = {}
    duplicates: list[str] = []

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line or section is None:
            continue
        key = line.split("=", 1)[0].strip()
        first = seen.get((section, key))
        if first is not None:
            duplicates.append(f"[{section}] {key}: line {first} and line {number}")
        else:
            seen[(section, key)] = number

    assert not duplicates, "duplicate keys:\n  " + "\n  ".join(duplicates)


def test_every_language_offers_the_same_keys_as_english():
    """A key missing from a translation falls back to English, which is fine.
    A key *only* in a translation is a typo or a rename that was not carried
    across, and it will never be read.
    """
    def _keys(path: Path) -> set[tuple[str, str]]:
        parser = _parse(path)
        return {(s, k) for s in parser.sections() for k in parser[s]}

    english = LANG_DIR / "en.ini"
    if english not in LANG_FILES:
        pytest.skip("no en.ini to compare against")

    base = _keys(english)
    for path in LANG_FILES:
        if path == english:
            continue
        orphans = _keys(path) - base
        assert not orphans, (
            f"{path.name} defines keys English does not: "
            + ", ".join(f"[{s}] {k}" for s, k in sorted(orphans)))
