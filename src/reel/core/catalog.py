"""The catalog: every registry in one place, plus builtin/plugin loading.

The module-level :data:`CATALOG` is what decorators like ``@register_action`` write
to and what the linter/renderer read from.  Tests (and embedders) can build their
own :class:`Catalog` and pass it explicitly, so nothing depends on global state.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from reel.core.registry import Registry

#: Singular kind name -> attribute on Catalog (used by the linter and CLI)
KINDS: dict[str, str] = {
    "action": "actions",
    "background": "backgrounds",
    "style": "styles",
    "transition": "transitions",
    "easing": "easings",
    "camera_move": "camera_moves",
    "sfx": "sfx",
    "archetype": "archetypes",
    "prop": "props",
    "caption_style": "caption_styles",
}

#: Plural CLI word -> kind
PLURALS: dict[str, str] = {
    "actions": "action",
    "backgrounds": "background",
    "styles": "style",
    "transitions": "transition",
    "easings": "easing",
    "camera": "camera_move",
    "cameras": "camera_move",
    "camera_moves": "camera_move",
    "sfx": "sfx",
    "archetypes": "archetype",
    "props": "prop",
    "captions": "caption_style",
    "caption_styles": "caption_style",
}

#: Modules imported (once) to populate the default catalog.  Each one only needs to
#: run its ``@register_*`` decorators at import time.
BUILTIN_MODULES: tuple[str, ...] = (
    "reel.core.easing",
    "reel.core.archetypes",
    "reel.core.camera",
    "reel.core.captions",
    "reel.core.transitions",
    "reel.actions",
    "reel.templates",
    "reel.styles",
    "reel.audio.sfx",
)

PLUGIN_ENV = "REEL_PLUGINS"


class Catalog:
    """Bundle of registries.  ``Catalog()`` is empty; ``CATALOG`` is the live default."""

    def __init__(self, *, default: bool = False) -> None:
        loader = self.load_builtins if default else None
        self.actions: Registry[Any] = Registry("action", loader=loader)
        self.backgrounds: Registry[Any] = Registry("background", loader=loader)
        self.styles: Registry[Any] = Registry("style", loader=loader)
        self.transitions: Registry[Any] = Registry("transition", loader=loader)
        self.easings: Registry[Any] = Registry("easing", loader=loader)
        self.camera_moves: Registry[Any] = Registry("camera_move", loader=loader)
        self.sfx: Registry[Any] = Registry("sfx", loader=loader)
        self.archetypes: Registry[Any] = Registry("archetype", loader=loader)
        self.props: Registry[Any] = Registry("prop", loader=loader)
        self.caption_styles: Registry[Any] = Registry("caption_style", loader=loader)
        self._default = default
        self._loaded = False
        self.plugin_files: list[
            str
        ] = []  # source files of loaded plugins (part of the render cache fingerprint)

    def registry(self, kind: str) -> Registry[Any]:
        """Look a registry up by singular ('action') or plural ('actions') name."""
        kind = PLURALS.get(kind, kind)
        try:
            reg: Registry[Any] = getattr(self, KINDS[kind])
            return reg
        except KeyError:
            raise KeyError(f"unknown registry kind {kind!r}; known: {sorted(KINDS)}") from None

    def all_registries(self) -> dict[str, Registry[Any]]:
        return {k: getattr(self, attr) for k, attr in KINDS.items()}

    # -- loading ---------------------------------------------------------------------------
    def load_builtins(self) -> None:
        """Import the builtin modules (their decorators register into CATALOG), then plugins."""
        if self._loaded or not self._default:
            return
        self._loaded = True
        for mod in BUILTIN_MODULES:
            importlib.import_module(mod)
        self.load_plugins(os.environ.get(PLUGIN_ENV, "").split(os.pathsep))

    def load_plugins(self, specs: Iterable[str]) -> list[str]:
        """Load plugin modules/files/directories.  Returns the module names imported."""
        loaded: list[str] = []
        for raw in specs:
            spec = raw.strip()
            if not spec:
                continue
            path = Path(spec).expanduser()
            if path.is_dir():
                self._track(path)
                if (path / "__init__.py").exists():
                    loaded.append(_import_path(path / "__init__.py", path.name))
                else:
                    for child in sorted(path.iterdir()):
                        if child.suffix == ".py" and not child.name.startswith("_"):
                            loaded.append(_import_path(child, f"reel_plugin_{child.stem}"))
                        elif child.is_dir() and (child / "__init__.py").exists():
                            loaded.append(_import_path(child / "__init__.py", child.name))
            elif path.is_file() and path.suffix == ".py":
                self._track(path)
                loaded.append(_import_path(path, f"reel_plugin_{path.stem}"))
            else:
                importlib.import_module(spec)
                mod = importlib.util.find_spec(spec)
                if mod and mod.origin:
                    self._track(
                        Path(mod.origin).parent
                        if mod.submodule_search_locations
                        else Path(mod.origin)
                    )
                loaded.append(spec)
        return loaded

    def _track(self, path: Path) -> None:
        files = (
            [path]
            if path.is_file()
            else sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        )
        for f in files:
            name = str(f.resolve())
            if name not in self.plugin_files:
                self.plugin_files.append(name)


def _import_path(file: Path, name: str) -> str:
    """Import ``file`` as module ``name`` (package-aware), registering it in sys.modules."""
    if name in sys.modules:
        return name
    is_pkg = file.name == "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, file, submodule_search_locations=[str(file.parent)] if is_pkg else None
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import plugin {file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name


CATALOG = Catalog(default=True)


def get_catalog() -> Catalog:
    return CATALOG
