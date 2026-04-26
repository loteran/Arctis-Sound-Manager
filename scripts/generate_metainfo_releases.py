#!/usr/bin/env python3
"""Inject AppStream <release> entries into the metainfo XML from CHANGELOG.md.

Reads CHANGELOG.md (Keep-a-Changelog format) and writes the metainfo XML to
stdout (or --output) with a freshly generated <releases> block. Limits to the
N most recent versions to keep the file under AppStream best-practice size.

Usage:
    python3 scripts/generate_metainfo_releases.py [--limit 30] [--output PATH]

Used by packaging (PKGBUILD, RPM spec, debian/rules) so users see up-to-date
release info in GNOME Software / KDE Discover instead of a frozen list from
release 1.0.4.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent.parent
DEFAULT_CHANGELOG = ROOT / 'CHANGELOG.md'
DEFAULT_METAINFO = ROOT / 'src' / 'arctis_sound_manager' / 'desktop' / 'com.github.loteran.arctis-sound-manager.metainfo.xml'

VERSION_HEADER = re.compile(r'^##\s*\[(?P<version>\d+\.\d+\.\d+)\]\s*-\s*(?P<date>.+)$')


def parse_changelog(changelog_path: Path) -> list[tuple[str, str, str]]:
    """Return a list of (version, iso_date, summary) tuples, newest first."""
    entries: list[tuple[str, str, str]] = []
    current_version: str | None = None
    current_date: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_version and current_date:
            summary = _summarize(current_lines)
            entries.append((current_version, current_date, summary))

    for raw_line in changelog_path.read_text(encoding='utf-8').splitlines():
        m = VERSION_HEADER.match(raw_line.strip())
        if m:
            flush()
            current_version = m.group('version')
            current_date = _normalize_date(m.group('date').strip())
            current_lines = []
        elif current_version is not None:
            current_lines.append(raw_line)
    flush()
    return entries


def _normalize_date(raw: str) -> str:
    for fmt in ('%d %B %Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    # Best-effort fallback: keep raw text inside an attribute is invalid, so emit today.
    return datetime.utcnow().strftime('%Y-%m-%d')


def _summarize(lines: list[str]) -> str:
    """Pick a single short paragraph describing the release."""
    bullets: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith('- '):
            text = s[2:]
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            text = re.sub(r'`([^`]+)`', r'\1', text)
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
            bullets.append(text)
            if len(bullets) >= 3:
                break
    if not bullets:
        return 'See changelog for details.'
    if len(bullets) == 1:
        return bullets[0]
    return ' '.join(b.rstrip('.') + '.' for b in bullets)


def render_releases(entries: list[tuple[str, str, str]], base_indent: str) -> str:
    out = [f'{base_indent}<releases>']
    for version, date, summary in entries:
        out.append(f'{base_indent}  <release version="{escape(version)}" date="{escape(date)}">')
        out.append(f'{base_indent}    <description>')
        out.append(f'{base_indent}      <p>{escape(summary)}</p>')
        out.append(f'{base_indent}    </description>')
        out.append(f'{base_indent}  </release>')
    out.append(f'{base_indent}</releases>')
    return '\n'.join(out)


def inject(metainfo_xml: str, entries: list[tuple[str, str, str]]) -> str:
    # Match the existing <releases>…</releases> block and replace it, keeping
    # whatever leading indentation it currently has so the file stays clean.
    pattern = re.compile(r'(^[ \t]*)<releases>.*?</releases>', re.DOTALL | re.MULTILINE)
    m = pattern.search(metainfo_xml)
    if not m:
        raise RuntimeError('No <releases> block found in metainfo XML — aborting.')
    leading = m.group(1)
    new_block = render_releases(entries, base_indent=leading)
    return pattern.sub(lambda _m: new_block, metainfo_xml, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--changelog', type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument('--metainfo', type=Path, default=DEFAULT_METAINFO)
    parser.add_argument('--limit', type=int, default=30, help='Max releases to include (default: 30)')
    parser.add_argument('--output', type=Path, default=None,
                        help='Write to this path instead of stdout (use - for stdout)')
    parser.add_argument('--in-place', action='store_true',
                        help='Overwrite --metainfo with the generated content')
    args = parser.parse_args()

    if not args.changelog.is_file():
        print(f'error: changelog not found: {args.changelog}', file=sys.stderr)
        return 1
    if not args.metainfo.is_file():
        print(f'error: metainfo not found: {args.metainfo}', file=sys.stderr)
        return 1

    entries = parse_changelog(args.changelog)
    if not entries:
        print('error: no version entries parsed from changelog', file=sys.stderr)
        return 1
    entries = entries[: args.limit]

    metainfo_xml = args.metainfo.read_text(encoding='utf-8')
    new_xml = inject(metainfo_xml, entries)

    target: Path | None = args.metainfo if args.in_place else args.output
    if target is None or str(target) == '-':
        sys.stdout.write(new_xml)
    else:
        target.write_text(new_xml, encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
