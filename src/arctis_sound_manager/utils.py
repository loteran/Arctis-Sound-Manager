# Copyright (C) 2022 Giacomo Furlan (elegos) — original work
# Copyright (C) 2026 loteran — modifications
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABC
from enum import Enum
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar


def project_version() -> str:
    """Return the version of the distribution that ACTUALLY provides the running
    arctis_sound_manager package.

    On Fedora (and similar setups) there can be a stale ``pip install --user``
    copy in ``~/.local`` that shadows the RPM install on ``sys.path``.
    ``importlib.metadata.version()`` returns the FIRST metadata directory found,
    which may belong to that stale user copy rather than the system-installed
    package.  Instead, we locate the running package on disk and walk the
    installed distributions to find the one whose installation root contains our
    package directory — that is the correct version to report.

    Falls back to the name-based lookup, then ``"dev"`` if everything fails.
    """
    try:
        import arctis_sound_manager as _pkg
        pkg_dir = Path(_pkg.__file__).resolve().parent

        for dist in distributions():
            try:
                # direct_url.json / RECORD both live under dist.locate_file(".")
                dist_root = Path(dist.locate_file(".")).resolve()
                if pkg_dir.is_relative_to(dist_root):
                    return dist.version
            except Exception:
                continue
    except Exception:
        pass

    # Name-based fallback (may return wrong version when shadowed)
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
