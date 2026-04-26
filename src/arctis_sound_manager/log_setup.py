"""Shared logging bootstrap honouring ARCTIS_LOG_LEVEL.

All entry points (daemon, GUI, CLI, video router) call configure_logging()
so users can opt into verbose logs for bug reports without rebuilding the
package or passing flags. Example:

    ARCTIS_LOG_LEVEL=debug asm-daemon
"""
from __future__ import annotations

import logging
import os

_DEFAULT_FORMAT = '[%(levelname)7s] %(name)20s: %(message)s'

_LEVEL_ALIASES: dict[str, int] = {
    'critical': logging.CRITICAL,
    'crit':     logging.CRITICAL,
    'error':    logging.ERROR,
    'warning':  logging.WARNING,
    'warn':     logging.WARNING,
    'info':     logging.INFO,
    'debug':    logging.DEBUG,
    'trace':    logging.DEBUG,
}


def resolve_level(default: int = logging.INFO) -> int:
    """Resolve the effective level from ARCTIS_LOG_LEVEL, or fall back to *default*.

    Accepts both names ('debug') and numeric strings ('10'). Falls back to
    *default* when the env var is unset, blank, or unparseable.
    """
    raw = os.environ.get('ARCTIS_LOG_LEVEL', '').strip().lower()
    if not raw:
        return default
    if raw in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[raw]
    if raw.isdigit():
        return int(raw)
    return default


def configure_logging(default: int = logging.INFO, fmt: str = _DEFAULT_FORMAT) -> int:
    """Apply logging.basicConfig honouring ARCTIS_LOG_LEVEL. Returns the level used."""
    level = resolve_level(default)
    logging.basicConfig(level=level, format=fmt)
    return level
