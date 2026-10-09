from __future__ import annotations

import pytest

from reel.core.registry import DuplicateEntryError, Registry, UnknownEntryError


def test_register_and_lookup() -> None:
    r: Registry[int] = Registry("thing")
    r.register("a", 1, summary="one")
    assert "a" in r and r.get("a") == 1 and r["a"] == 1
    assert r.names() == ["a"] and len(r) == 1
    assert r.entry("a").meta["summary"] == "one"


def test_duplicate_is_rejected_but_idempotent_for_same_object() -> None:
    r: Registry[object] = Registry("thing")
    obj = object()
    r.register("a", obj)
    r.register("a", obj)  # same object again is fine (module re-import)
    with pytest.raises(DuplicateEntryError):
        r.register("a", object())
    r.register("a", object(), replace=True)


def test_unknown_lookup_has_suggestions() -> None:
    r: Registry[int] = Registry("action")
    for n in ("walk", "run", "wave"):
        r.register(n, 0)
    with pytest.raises(UnknownEntryError) as ei:
        r.get("wlak")
    assert "walk" in ei.value.suggestions
    assert "did you mean" in str(ei.value)
    assert r.suggest("wal") and r.suggest("wal")[0] == "walk"


def test_decorator_form() -> None:
    r: Registry[object] = Registry("thing")

    @r.decorator("x", summary="hi")
    def fn() -> None: ...

    assert r.get("x") is fn


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        Registry("thing").register("", 1)
