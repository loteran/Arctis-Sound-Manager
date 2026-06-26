# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

import re
from abc import ABC
from enum import Enum
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

_DIST_NAME = "arctis-sound-manager"


def _normalize_dist_name(dist) -> str:
    """PEP 503-normalized distribution name, or '' if unavailable."""
    try:
        name = dist.metadata["Name"] or ""
    except Exception:
        return ""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _dist_owns_dir(dist, pkg_dir: Path) -> bool:
    """True if any file recorded by *dist* lives inside *pkg_dir*.

    This is what actually proves a distribution provides the running package,
    as opposed to merely sharing the same ``site-packages`` root with it.
    """
    try:
        files = dist.files or []
    except Exception:
        return False
    for f in files:
        try:
            p = Path(dist.locate_file(f)).resolve()
        except Exception:
            continue
        if p == pkg_dir or pkg_dir in p.parents:
            return True
    return False


def project_version() -> str:
    """Return the version of the distribution that ACTUALLY provides the running
    arctis_sound_manager package.

    On Fedora/Arch (and similar) there can be a stale ``pip install --user`` copy
    in ``~/.local`` that shadows the system install on ``sys.path``. We must
    report the version of whichever copy is *actually imported*.

    The naive ``importlib.metadata.version("arctis-sound-manager")`` returns the
    first metadata directory found, which may be the wrong copy. A previous
    heuristic checked whether the running package dir was *under a distribution's
    root* — but in a normal system install every distribution shares the same
    ``site-packages`` root, so that test matched the FIRST distribution
    enumerated (any random sibling package) and reported its version. That is why
    bug reports showed nonsense versions like ``1.9.0`` or ``0.1`` for a
    ``1.1.83`` install.

    Correct approach: among the distributions that call themselves
    ``arctis-sound-manager``, pick the one whose recorded files are the ones being
    imported (``_dist_owns_dir``). Falls back to the name-based lookup, then
    ``"dev"``.
    """
    try:
        import arctis_sound_manager as _pkg
        pkg_dir = Path(_pkg.__file__).resolve().parent

        named = [d for d in distributions() if _normalize_dist_name(d) == _DIST_NAME]

        # Prefer the arctis-sound-manager distribution whose installed files are
        # the ones actually being imported (resolves a ~/.local vs system clash).
        for dist in named:
            if _dist_owns_dir(dist, pkg_dir):
                return dist.version

        # No location match (editable install, unusual layout): any distribution
        # that names itself arctis-sound-manager beats a random site-packages
        # sibling.
        if named:
            return named[0].version
    except Exception:
        pass

    # Name-based fallback.
    try:
        return version("arctis-sound-manager")
    except PackageNotFoundError:
        pass

    return "dev"


class JsonSerializable(ABC):
    _js_exclude_fields: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        def serialize(value: Any) -> Any:
            if isinstance(value, JsonSerializable):
                return value.to_dict()
            if isinstance(value, list):
                return [serialize(item) for item in value]

            if isinstance(value, Enum):
                return value.value

            return value
        
        if isinstance(self, dict):
            return { k: serialize(v) for k, v in self.items() }
        
        cls = type(self)
        fields = getattr(cls, '__annotations__', {}).keys()

        return { field: serialize(getattr(self, field)) for field in fields if type(getattr(self, field)) != callable and field not in [*self._js_exclude_fields, '_js_exclude_fields']}



K = TypeVar('K')
V = TypeVar('V')

class ObservableDict(dict[K, V], Generic[K, V], JsonSerializable):
    _js_exclude_fields = ['_observers']
    _observers: list[Callable[[K, V], None]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._observers = []

    def add_observer(self, observer: Callable[[K, V], None]):
        self._observers.append(observer)

    def __setitem__(self, key, value):
        old_value = self.get(key, None)

        super().__setitem__(key, value)
        if old_value != value:
            for observer in self._observers:
                observer(key, value)
    
    def update(self, *args, **kwargs):
        if args:
            if len(args) != 1:
                raise TypeError("update expected exactly 1 argument")
            other = dict(args[0])
            for k, v in other.items():
                self[k] = v

        for k, v in kwargs.items():
            self[k] = v
