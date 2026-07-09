# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Repair stray line breaks that translators introduce inside .ini values.

Crowdin translators occasionally press Enter in the middle of a string. In INI
syntax the spilled fragment lands on its own un-indented line with no key
delimiter, so ``configparser`` raises a ``ParsingError`` and the *whole*
language file is discarded — a fully translated locale silently vanishes from
the language menu (this is exactly what happened to ``tr.ini``).

This module folds such spill lines back onto the value they belong to, so a
single bad newline can no longer hide an entire translation. It is used both at
runtime (the i18n loader) and in CI (the Crowdin sync workflow) so the two
stay in lockstep.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# A genuine key line starts with a bare-word key (keys in this project are all
# single tokens like ``udev_reload_body``) followed by a '=' or ':' delimiter.
# A spilled sentence has a space before any delimiter, so it won't match.
_KEY_LINE = re.compile(r'^\s*[\w.\-]+\s*[=:]')


def sanitize_ini_text(text: str) -> str:
    """Fold un-indented continuation lines back onto the preceding value.

    A line is treated as a spill when it is non-blank, not already indented
    (indented lines are valid configparser continuations), not a comment, not a
    section header, and does not look like ``key = value``. Such a line is
    joined to the previous line with a single space, matching how the English
    source keeps these strings on one line.
    """
    # keepends so untouched lines stay byte-identical (line endings, final
    # newline). Only an actual fold changes the text, which keeps CI diffs and
    # sanitize_file() rewrites limited to genuinely broken files.
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        is_spill = (
            bool(out)                             # there is a value to fold into
            and bool(stripped)                    # not a blank line
            and not raw[:1].isspace()             # not an existing continuation
            and not stripped.startswith((';', '#', '['))
            and not _KEY_LINE.match(raw)          # not a real key line
        )
        if is_spill:
            out[-1] = out[-1].rstrip('\r\n').rstrip() + ' ' + raw.lstrip()
        else:
            out.append(raw)
    return ''.join(out)


def sanitize_file(path: Path) -> bool:
    """Rewrite *path* in place if it contained spill lines. Returns True if changed."""
    original = path.read_text(encoding='utf-8')
    fixed = sanitize_ini_text(original)
    if fixed != original:
        path.write_text(fixed, encoding='utf-8')
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m arctis_sound_manager.lang_sanitize <dir-or-file>...``"""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: lang_sanitize <dir-or-file>...', file=sys.stderr)
        return 2

    targets: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            targets.extend(sorted(p.glob('*.ini')))
        else:
            targets.append(p)

    changed = 0
    for path in targets:
        try:
            if sanitize_file(path):
                changed += 1
                print(f'sanitized {path}')
        except Exception as exc:  # pragma: no cover - defensive I/O guard
            print(f'skipped {path}: {exc!r}', file=sys.stderr)
    print(f'{changed} file(s) sanitized out of {len(targets)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
