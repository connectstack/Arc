"""Finding assets on disk and putting them in the catalog.

A library folder holds art files and, optionally, a sidecar ``<name>.json`` beside each (an `AssetManifest`).  Files may be
sorted into ``characters/``, ``objects/`` and ``places/`` (that sets the kind); a loose file is an object unless its sidecar
says otherwise.  The built-in library is such a folder inside the package; users add more folders with ``REEL_ASSETS``,
``--assets``, an ``assets/`` folder next to a spec, or the workspace's ``assets/`` folder in Reel Studio.

Where a name appears twice, the later folder wins (so a project can replace a built-in), but an asset never replaces one of
the engine's own backgrounds or characters: those names are taken.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from reel.assets.model import (
    ART_EXT,
    KIND_FOLDERS,
    KIND_HEIGHT,
    MAX_ART_BYTES,
    NAME_RE,
    RASTER_EXT,
    SVG_EXT,
    AssetDef,
    AssetKind,
    AssetManifest,
    PlaceSpec,
    slug,
)

BUILTIN_DIR = Path(__file__).resolve().parent / "library"
ASSETS_ENV = "REEL_ASSETS"
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}


@dataclass
class Problem:
    """Something wrong with one file of a library, said so a person can fix it."""

    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"


@dataclass
class Found:
    assets: list[AssetDef] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


def asset_dirs_from_env() -> list[str]:
    return [p for p in os.environ.get(ASSETS_ENV, "").split(os.pathsep) if p.strip()]


def _kind_from_path(path: Path, root: Path) -> AssetKind | None:
    for part in reversed(path.relative_to(root).parts[:-1]):
        if part.lower() in KIND_FOLDERS:
            return KIND_FOLDERS[part.lower()]
    return None


def read_manifest(sidecar: Path) -> AssetManifest:
    return AssetManifest.model_validate(json.loads(sidecar.read_text(encoding="utf-8")))


def asset_from_files(
    art: Path,
    *,
    root: Path,
    origin: str,
    manifest_path: Path | None = None,
    kind_hint: AssetKind | None = None,
) -> AssetDef:
    """Build the `AssetDef` for one art file (and its sidecar); raises ``ValueError`` saying what is wrong."""
    manifest = AssetManifest()
    if manifest_path is not None and manifest_path.is_file():
        try:
            manifest = read_manifest(manifest_path)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"the sidecar {manifest_path.name} is not valid JSON ({exc.msg}, line {exc.lineno})"
            ) from exc
        except RecursionError as exc:
            raise ValueError(f"the sidecar {manifest_path.name} is nested too deeply") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "the sidecar"
            raise ValueError(f"{manifest_path.name}: {loc}: {first['msg']}") from exc
    if manifest.file:
        where = manifest_path.name if manifest_path else "the sidecar"
        if Path(manifest.file).name != manifest.file or manifest.file in (".", ".."):
            raise ValueError(
                f"{where}: file: must be the name of a file next to the sidecar, not a path"
            )
        art = (manifest_path.parent if manifest_path else art.parent) / manifest.file
    if art.is_symlink():
        raise ValueError(f"{art.name} is a symbolic link: copy the file into the folder instead")
    ext = art.suffix.lower()
    if ext not in ART_EXT:
        raise ValueError(
            f"{art.name} is not a picture or drawing (use one of: {', '.join(ART_EXT)})"
        )
    if not art.is_file():
        raise ValueError(f"the art file {art.name} is missing")
    size = art.stat().st_size
    if size > MAX_ART_BYTES:
        raise ValueError(
            f"{art.name} is {size // (1024 * 1024)} MB: the limit is {MAX_ART_BYTES // (1024 * 1024)} MB"
        )
    name = manifest.name or slug(art.stem)
    if not NAME_RE.match(name):
        raise ValueError(
            f"{name!r} is not a usable name: lower-case letters, digits and underscores, starting with a letter"
        )
    kind: AssetKind = manifest.kind or kind_hint or "object"
    if kind == "place" and manifest.place is None:
        manifest = manifest.model_copy(update={"place": PlaceSpec()})
    height = manifest.height or KIND_HEIGHT[kind]
    return AssetDef(
        name=name,
        kind=kind,
        summary=manifest.summary or name.replace("_", " "),
        tags=tuple(dict.fromkeys([name.replace("_", " "), *manifest.tags])),
        path=art,
        fmt="svg" if ext in SVG_EXT else "raster",
        height=float(height),
        anchor=(float(manifest.anchor[0]), float(manifest.anchor[1])),
        facing=manifest.facing,
        fit=manifest.fit,
        cutout=manifest.cutout,
        credit=manifest.credit,
        origin="builtin" if origin == "builtin" else "user",
        manifest=manifest_path if manifest_path and manifest_path.is_file() else None,
        place=manifest.place if kind == "place" else None,
        shadow=manifest.shadow,
    )


def discover(root: Path, *, origin: str = "user") -> Found:
    """Every asset below ``root`` (problems are collected, never raised: one bad file must not hide the others)."""
    out = Found()
    root = root.expanduser()
    if not root.is_dir():
        out.problems.append(Problem(str(root), "this assets folder does not exist"))
        return out
    seen: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            p = Path(dirpath, fn)
            if p.suffix.lower() not in ART_EXT or fn.startswith("."):
                continue
            rel = str(p.relative_to(root))
            sidecar = p.with_suffix(".json")
            try:
                a = asset_from_files(
                    p,
                    root=root,
                    origin=origin,
                    manifest_path=sidecar if sidecar.is_file() else None,
                    kind_hint=_kind_from_path(p, root),
                )
            except (
                ValueError,
                OSError,
                RecursionError,
            ) as exc:  # one bad file must never hide the others
                out.problems.append(Problem(rel, str(exc)))
                continue
            if a.name in seen:
                out.problems.append(
                    Problem(
                        rel,
                        f"the name {a.name!r} is already used by {seen[a.name].name}: rename one of them",
                    )
                )
                continue
            seen[a.name] = p
            out.assets.append(a)
    return out


def load_dirs(
    dirs: Iterable[str | Path], catalog: object | None = None, *, origin: str = "user"
) -> Found:
    """Discover and register every asset of the given folders (a missing or broken one is reported, not raised).

    The art of the user's assets is read first, as the server does at its start: an asset that cannot be read is reported and
    left out (so it never replaces a built-in one), whoever loads the folder: a command, a job, the server."""
    from reel.assets.art import ArtError, load_art
    from reel.core.catalog import CATALOG

    cat = catalog or CATALOG
    total = Found()
    for d in dirs:
        if not str(d).strip():
            continue
        found = discover(Path(d), origin=origin)
        total.problems += found.problems
        for a in found.assets:
            if origin != "builtin":
                try:
                    load_art(a)
                except ArtError as exc:
                    total.problems.append(Problem(a.path.name, str(exc)))
                    continue
                except Exception as exc:  # a file that breaks the importer in an unforeseen way: reported, not a crash
                    total.problems.append(
                        Problem(a.path.name, f"cannot be read ({type(exc).__name__})")
                    )
                    continue
            try:
                register_asset(a, cat, replace=True)
                total.assets.append(a)
            except ValueError as exc:
                total.problems.append(Problem(a.path.name, str(exc)))
    return total


def register_asset(asset: AssetDef, catalog: object, *, replace: bool = False) -> None:
    """Put an asset in the catalog: an object in ``objects``, a character as a sprite archetype, a place as a background."""
    from reel.assets.places import place_background
    from reel.assets.sprite import sprite_archetype
    from reel.core.catalog import Catalog

    assert isinstance(catalog, Catalog)
    cat = catalog
    taken = _taken_by_engine(cat, asset)
    if taken:
        raise ValueError(
            f"the name {asset.name!r} is taken by the engine's own {taken}: choose another name"
        )
    if asset.kind == "object":
        cat.objects.register(
            asset.name, asset, replace=replace, summary=asset.summary, tags=asset.tags
        )
    elif asset.kind == "character":
        cat.archetypes.register(
            asset.name, sprite_archetype(asset), replace=replace, summary=asset.summary
        )
    else:
        cat.backgrounds.register(
            asset.name, place_background(asset), replace=replace, summary=asset.summary
        )
    cat.assets.register(asset.name, asset, replace=True, summary=asset.summary, tags=asset.tags)


def unregister_asset(catalog: object, name: str) -> None:
    from reel.core.catalog import Catalog

    assert isinstance(catalog, Catalog)
    if name not in catalog.assets:
        return
    a = catalog.assets.get(name)
    reg = {
        "object": catalog.objects,
        "character": catalog.archetypes,
        "place": catalog.backgrounds,
    }[a.kind]
    reg.unregister(name)
    catalog.assets.unregister(name)


def reset_assets(
    catalog: object, user_dirs: Iterable[str | Path] = (), *, validate: bool = False
) -> Found:
    """Make the catalog's library what a fresh start would build: the built-in assets, then ``user_dirs`` in order (a later
    folder wins).  What a deleted or renamed file left behind goes, and a built-in that a user asset had replaced comes
    back.  The new set is worked out first and swapped in entry by entry, so a render running meanwhile never finds the
    library empty.  With ``validate`` the art of every asset of the user's is read first: one that cannot be read is
    reported and left out (the built-in asset it would have replaced stays)."""
    from reel.core.catalog import Catalog

    assert isinstance(catalog, Catalog)
    builtin = discover(BUILTIN_DIR, origin="builtin")
    wanted: dict[str, AssetDef] = {a.name: a for a in builtin.assets}
    problems = list(builtin.problems)
    for d in user_dirs:
        if not str(d).strip():
            continue
        found = discover(Path(d), origin="user")
        problems += found.problems
        for a in found.assets:
            if validate:
                from reel.assets.art import ArtError, forget, load_art

                try:
                    load_art(a)
                    forget(a)
                except ArtError as exc:
                    problems.append(Problem(a.path.name, str(exc)))
                    continue
                except Exception as exc:  # a file that breaks the importer in an unforeseen way must not stop the start
                    problems.append(Problem(a.path.name, f"cannot be read ({type(exc).__name__})"))
                    continue
            wanted[a.name] = a
    for name in list(catalog.assets.names()):
        if name not in wanted:
            unregister_asset(catalog, name)
    kept: list[AssetDef] = []
    for a in wanted.values():
        try:
            register_asset(a, catalog, replace=True)
            kept.append(a)
        except ValueError as exc:
            problems.append(Problem(a.path.name, str(exc)))
    return Found(kept, problems)


def library_fingerprint(catalog: object | None = None) -> str:
    """A digest of the user's assets (their names and what is in their files): part of the key of every picture that may show
    one (a preview, a thumbnail), so a changed or replaced asset is never shown from an old picture.  The built-in art is part of
    the engine's own fingerprint."""
    import hashlib

    from reel.core.catalog import CATALOG

    cat = catalog or CATALOG
    h = hashlib.sha256()
    for e in cat.assets.entries():  # type: ignore[attr-defined]
        a = e.obj
        if a.origin == "user":
            try:
                h.update(f"{a.name}:{a.content_hash}\n".encode())
            except OSError:  # the file went away since the last reload
                h.update(f"{a.name}:gone\n".encode())
    return h.hexdigest()[:12]


def _taken_by_engine(cat: object, asset: AssetDef) -> str | None:
    """The engine's own background / character / object of that name (an asset may replace another asset, not these)."""
    from reel.core.catalog import Catalog

    assert isinstance(cat, Catalog)
    for kind, reg in (
        ("object", cat.objects),
        ("character", cat.archetypes),
        ("place", cat.backgrounds),
    ):
        if asset.name in reg and asset.name not in cat.assets:
            return f"{'background' if kind == 'place' else kind} {asset.name!r}"
    if asset.name in cat.assets:
        other = cat.assets.get(asset.name)
        if (
            other.kind != asset.kind
        ):  # replacing a place by a character of the same name would leave a stale entry
            unregister_asset(cat, asset.name)
    return None


__all__ = [
    "ASSETS_ENV",
    "BUILTIN_DIR",
    "RASTER_EXT",
    "Found",
    "Problem",
    "asset_dirs_from_env",
    "asset_from_files",
    "discover",
    "load_dirs",
    "read_manifest",
    "register_asset",
    "reset_assets",
    "unregister_asset",
]
