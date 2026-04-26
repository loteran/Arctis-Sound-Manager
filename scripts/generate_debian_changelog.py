#!/usr/bin/env python3
"""Generate debian/changelog from CHANGELOG.md.

Reads CHANGELOG.md (Keep-a-Changelog format) and writes a Debian-style
changelog to stdout (or --output / --in-place). Used by debian/rules at
build time so the package's changelog is always in sync with the project's,
instead of being maintained by hand (it had drifted by 39 versions).

Usage:
    python3 scripts/generate_debian_changelog.py [--in-place] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, time, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = ROOT / 'CHANGELOG.md'
DEFAULT_DEBIAN_CHANGELOG = ROOT / 'debian' / 'changelog'

VERSION_HEADER = re.compile(r'^##\s*\[(?P<version>\d+\.\d+\.\d+)\]\s*-\s*(?P<date>.+)$')

DEBIAN_DISTRIBUTION = 'noble'  # Ubuntu LTS that the .deb targets
DEBIAN_URGENCY = 'medium'
DEBIAN_MAINTAINER = 'loteran <axel.valadon@gmail.com>'


def parse_changelog(changelog_path: Path) -> list[tuple[str, str, list[str]]]:
    """Return a list of (version, iso_date, [bullet_lines]) tuples, newest first."""
    entries: list[tuple[str, str, list[str]]] = []
    current_version: str | None = None
    current_date: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_version and current_date:
            bullets = _extract_bullets(current_lines)
            if not bullets:
                bullets = ['Maintenance release.']
            entries.append((current_version, current_date, bullets))

    for raw in changelog_path.read_text(encoding='utf-8').splitlines():
        m = VERSION_HEADER.match(raw.strip())
        if m:
            flush()
            current_version = m.group('version')
            current_date = _normalize_date(m.group('date').strip())
            current_lines = []
        elif current_version is not None:
            current_lines.append(raw)
    flush()
    return entries


def _normalize_date(raw: str) -> str:
    for fmt in ('%d %B %Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return datetime.utcnow().strftime('%Y-%m-%d')


def _extract_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    for line in lines:
        s = line.strip()
        if not s.startswith('- '):
            continue
        text = s[2:]
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = text.strip()
        if text:
            bullets.append(text)
    return bullets


def _wrap_bullet(text: str, first_indent: str = '  * ', cont_indent: str = '    ', width: int = 76) -> list[str]:
    """Word-wrap a bullet point to fit Debian's typical 80-col policy.

    Long URLs and tokens are kept intact (no mid-word break)."""
    out: list[str] = []
    line = first_indent
    indent = first_indent
    for word in text.split():
        candidate = (line + word) if line.endswith(' ') or line == indent else (line + ' ' + word)
        if len(candidate) > width and line != indent:
            out.append(line.rstrip())
            line = cont_indent + word
            indent = cont_indent
        else:
            line = candidate
    if line.strip():
        out.append(line.rstrip())
    return out


def render_debian_changelog(entries: list[tuple[str, str, list[str]]], package: str = 'arctis-sound-manager') -> str:
    blocks: list[str] = []
    for version, iso_date, bullets in entries:
        try:
            d = datetime.strptime(iso_date, '%Y-%m-%d')
        except ValueError:
            d = datetime.utcnow()
        # Debian wants RFC 5322 dates. We don't track per-release timestamps in
        # the source CHANGELOG so use 00:00 UTC for determinism.
        dt = datetime.combine(d.date(), time(0, 0), tzinfo=timezone.utc)
        date_str = dt.strftime('%a, %d %b %Y %H:%M:%S +0000')

        lines = [f'{package} ({version}-1) {DEBIAN_DISTRIBUTION}; urgency={DEBIAN_URGENCY}', '']
        for bullet in bullets:
            lines.extend(_wrap_bullet(bullet))
        lines.extend(['', f' -- {DEBIAN_MAINTAINER}  {date_str}', ''])
        blocks.append('\n'.join(lines))
    return '\n'.join(blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--changelog', type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--in-place', action='store_true',
                        help='Overwrite debian/changelog with the generated content.')
    parser.add_argument('--limit', type=int, default=None,
                        help='Keep only the N most recent versions (default: all).')
    parser.add_argument('--package', default='arctis-sound-manager')
    args = parser.parse_args()

    if not args.changelog.is_file():
        print(f'error: changelog not found: {args.changelog}', file=sys.stderr)
        return 1

    entries = parse_changelog(args.changelog)
    if not entries:
        print('error: no version entries parsed from changelog', file=sys.stderr)
        return 1
    if args.limit is not None:
        entries = entries[: args.limit]

    rendered = render_debian_changelog(entries, package=args.package)

    if args.in_place:
        DEFAULT_DEBIAN_CHANGELOG.write_text(rendered, encoding='utf-8')
    elif args.output is None or str(args.output) == '-':
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
