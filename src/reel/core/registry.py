"""Generic named registry: the extensibility backbone.

Actions, style packs, background templates, transitions, easings, camera moves,
SFX, archetypes, props and caption styles all live in a `Registry`.  Every
lookup failure raises :class:`UnknownEntryError` carrying "did you mean"
candidates, and the linter uses the same registries to say *exactly* what is
missing from a spec.
"""

from __future__ import annotations

import difflib
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class UnknownEntryError(KeyError):
    """A name was looked up that nobody registered."""

    def __init__(self, kind: str, name: str, suggestions: list[str], known: list[str]) -> None:
        self.kind = kind
        self.name = name
        self.suggestions = suggestions
        self.known = known
        msg = f"unknown {kind} {name!r}"
        if suggestions:
            msg += " (did you mean " + ", ".join(repr(s) for s in suggestions) + "?)"
        super().__init__(msg)

    def __str__(self) -> str:
        return str(self.args[0])


class DuplicateEntryError(ValueError):
    """Two different things tried to claim the same name."""


@dataclass(frozen=True)
class Entry(Generic[T]):
    name: str
    obj: T
    meta: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""  # module that registered it (handy when debugging plugins)


class Registry(Generic[T]):
    """Name -> object map with metadata, suggestions and lazy builtin loading."""

    def __init__(self, kind: str, *, loader: Callable[[], None] | None = None) -> None:
        self.kind = kind
        self._entries: dict[str, Entry[T]] = {}
        self._loader = loader
        self._loading = False

    # -- loading ---------------------------------------------------------------------------
    def _ensure(self) -> None:
        if self._loader is not None and not self._loading:
            self._loading = True
            try:
                self._loader()
            finally:
                self._loading = False

    # -- registration ----------------------------------------------------------------------
    def register(self, name: str, obj: T, *, replace: bool = False, **meta: Any) -> T:
        if not name or not isinstance(name, str):
            raise ValueError(f"{self.kind} name must be a non-empty string, got {name!r}")
        if name in self._entries and not replace:
            prev = self._entries[name]
            if prev.obj is obj:
                return obj
            raise DuplicateEntryError(
                f"{self.kind} {name!r} is already registered by {prev.origin or 'another module'}; "
                "pass replace=True to override it deliberately"
            )
        origin = sys._getframe(1).f_globals.get("__name__", "")
        self._entries[name] = Entry(name=name, obj=obj, meta=dict(meta), origin=origin)
        return obj

    def decorator(self, name: str, **meta: Any) -> Callable[[T], T]:
        def wrap(obj: T) -> T:
            self.register(name, obj, **meta)
            return obj

        return wrap

    def unregister(self, name: str) -> None:
        self._entries.pop(name, None)

    # -- lookup ----------------------------------------------------------------------------
    def entry(self, name: str) -> Entry[T]:
        self._ensure()
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownEntryError(
                self.kind, name, self.suggest(name), sorted(self._entries)
            ) from None

    def get(self, name: str) -> T:
        return self.entry(name).obj

    def __getitem__(self, name: str) -> T:
        return self.get(name)

    def __contains__(self, name: object) -> bool:
        self._ensure()
        return name in self._entries

    def __iter__(self) -> Iterator[str]:
        self._ensure()
        return iter(sorted(self._entries))

    def __len__(self) -> int:
        self._ensure()
        return len(self._entries)

    def names(self) -> list[str]:
        self._ensure()
        return sorted(self._entries)

    def items(self) -> list[tuple[str, T]]:
        self._ensure()
        return [(n, self._entries[n].obj) for n in sorted(self._entries)]

    def entries(self) -> list[Entry[T]]:
        self._ensure()
        return [self._entries[n] for n in sorted(self._entries)]

    def suggest(self, name: str, n: int = 3, cutoff: float = 0.55) -> list[str]:
        known = list(self._entries)
        close = difflib.get_close_matches(name, known, n=n, cutoff=cutoff)
        # substring matches are good hints too ("moon" -> "moonwalk")
        for k in known:
            if (name in k or k in name) and k not in close and len(close) < n:
                close.append(k)
        return close

    def copy(self) -> Registry[T]:
        clone: Registry[T] = Registry(self.kind)
        clone._entries = dict(self._entries)
        return clone
