"""Action library.  Importing this package registers every action module in it.

Adding an action = dropping one file in this folder (see docs/adding-an-action.md) -
`reel new-action <name>` scaffolds it.
"""

from __future__ import annotations

import importlib
import pkgutil

from reel.actions.base import ActionDef, NoParams, ParamsBase, register_action

__all__ = ["ActionDef", "NoParams", "ParamsBase", "register_action"]

for _m in pkgutil.iter_modules(__path__):
    if not _m.name.startswith("_") and _m.name != "base":
        importlib.import_module(f"{__name__}.{_m.name}")
