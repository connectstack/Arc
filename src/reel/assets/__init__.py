"""The asset library: pictures and drawings the engine can put on screen as characters, objects and places.

The built-in library lives in ``reel/assets/library`` and is registered by the catalog when it loads; your own folders are
added with ``REEL_ASSETS`` (a path list), ``--assets DIR``, an ``assets/`` folder next to a spec, or the workspace's
``assets/`` folder in Reel Studio.  See ``docs/assets.md``.
"""

from reel.assets.library import (
    ASSETS_ENV,
    BUILTIN_DIR,
    Found,
    Problem,
    asset_dirs_from_env,
    discover,
    load_dirs,
    register_asset,
    unregister_asset,
)
from reel.assets.model import KINDS, Art, AssetDef, AssetKind, AssetManifest, PlaceSpec, slug

__all__ = [
    "ASSETS_ENV",
    "BUILTIN_DIR",
    "KINDS",
    "Art",
    "AssetDef",
    "AssetKind",
    "AssetManifest",
    "Found",
    "PlaceSpec",
    "Problem",
    "asset_dirs_from_env",
    "discover",
    "load_dirs",
    "register_asset",
    "slug",
    "unregister_asset",
]
