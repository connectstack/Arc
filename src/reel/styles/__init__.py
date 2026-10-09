"""Style packs.  Each sub-package registers one `StylePack`; importing this package loads them all.

Adding a style = adding one folder here with a `StylePack` subclass decorated with
`@register_style("name")` (`reel new-style name` scaffolds it).
"""

from __future__ import annotations

import importlib
import pkgutil

from reel.styles.base import StyleContext, StylePack, register_style

__all__ = ["StyleContext", "StylePack", "register_style"]

for _m in pkgutil.iter_modules(__path__):
    if _m.ispkg and not _m.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_m.name}")
