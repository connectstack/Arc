"""What an asset is: a picture (SVG or PNG/JPG) plus the few facts the renderer and the planners need about it.

An asset is one of three kinds, and the kind decides where it plugs into the engine:

* ``object``    - a thing placed in a scene (a car, a tree, a cake): ``scenes[].objects[]``
* ``character`` - a creature or person that acts (a cat, a dragon): ``characters[].archetype``; the existing
  walk / jump / talk actions move it as a sprite
* ``place``     - a whole set (a beach, a village): ``scenes[].background.template``

On disk an asset is the art file with an optional sidecar ``<name>.json`` (an :class:`AssetManifest`); a folder of
assets may sort them into ``characters/``, ``objects/`` and ``places/``, which then sets the kind.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from reel.core.ir import Shape

AssetKind = Literal["character", "object", "place"]
KINDS: tuple[AssetKind, ...] = ("character", "object", "place")
#: sub-folder name -> kind (a library may sort its files like this; the kind of a loose file is "object")
KIND_FOLDERS: dict[str, AssetKind] = {
    "characters": "character",
    "objects": "object",
    "places": "place",
    "backgrounds": "place",
}
#: what only a jointed body does: on a picture character these take their time and nothing moves (``look_at`` still turns
#: it to face its target)
ARMS_ONLY = frozenset({"wave", "point", "pick_up"})
SVG_EXT = (".svg",)
RASTER_EXT = (".png", ".jpg", ".jpeg", ".webp")
ART_EXT = SVG_EXT + RASTER_EXT
MAX_ART_BYTES = 12 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")  # \Z, not $: "cat\n" is not a name

#: depth planes a layered SVG place may put its groups on (``<g data-plane="far">``), back to front
PLACE_PLANES = ("sky", "far", "back", "mid_back", "mid_front", "near")


def _finite_pair(v: list[float]) -> list[float]:
    if not all(math.isfinite(x) and abs(x) <= 10 for x in v):
        raise ValueError("these must be ordinary numbers (fractions of the picture or the frame)")
    return v


Vec2 = Annotated[list[float], Field(min_length=2, max_length=2), AfterValidator(_finite_pair)]


def slug(text: str) -> str:
    """A file or display name as an asset name: lower case, letters/digits/underscores, starting with a letter."""
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    if not s:  # nothing Latin in it ("कुत्ता"): a name that is the same for the same text and differs between texts
        digest = hashlib.sha1(text.strip().casefold().encode("utf-8")).hexdigest()[:6]
        return f"asset_{digest}" if text.strip() else "asset"
    if not s[0].isalpha():
        s = f"a_{s}"
    return s[:40].rstrip("_") or "asset"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlaceSpec(_Strict):
    """What a place needs beyond its picture: where the characters' feet go, and where they can stand."""

    ground_y: float = Field(
        0.80, ge=0.3, le=1.0, description="feet line, as a fraction of the frame height"
    )
    horizon: float = Field(
        0.45, ge=0.0, le=1.0, description="where sky meets ground (for perspective)"
    )
    perspective: float = Field(
        1.0, ge=0.0, le=3.0, description="how much characters grow towards the camera"
    )
    slots: dict[str, Vec2] = Field(
        default_factory=dict,
        description="named standing spots, [x, y] in frame fractions (left, center, right are always there)",
    )


class AssetManifest(_Strict):
    """The sidecar ``<name>.json`` of an asset.  Every field but ``name`` and ``file`` has a default."""

    name: str | None = Field(
        None, description="lower-case id used in specs; defaults to the file name"
    )
    kind: AssetKind | None = Field(
        None, description="character | object | place (default: from the folder, else object)"
    )
    summary: str = Field("", description="one line: what it is")
    tags: list[str] = Field(
        default_factory=list,
        description="the words a script may use for it (synonyms and other languages): the planner matches on these",
    )
    file: str | None = Field(
        None, description="the art file next to this one (default: <name>.svg / .png ...)"
    )
    height: float | None = Field(
        None,
        gt=0,
        le=4000,
        description="how tall it is drawn, in design px at scale 1 (a person is 575)",
    )
    anchor: Vec2 = Field(
        default_factory=lambda: [0.5, 1.0],
        description="where it stands, as fractions of the art's box: [0.5, 1] is the middle of the bottom edge",
    )
    facing: Literal["right", "left", "none"] = Field(
        "right", description="which way the art looks; the engine mirrors it for the other side"
    )
    fit: Literal["content", "viewbox"] = Field(
        "content", description="SVG: take the art's box from what is drawn, or from the viewBox"
    )
    cutout: bool | None = Field(
        None,
        description="picture: make the plain background transparent (default: yes when it has no transparency)",
    )
    credit: str = Field("", description="who drew it, and the licence")
    shadow: bool | None = Field(
        None,
        description="paper cutout: does it cast a shadow? default: yes, unless it floats (its anchor is high in the picture)",
    )
    place: PlaceSpec | None = Field(None, description="places only")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is not None and not NAME_RE.match(v):
            raise ValueError(
                f"{v!r} is not a usable name: use lower-case letters, digits and underscores, starting with a letter"
            )
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = " ".join(str(t).strip().lower().split())
            if t and t not in out:
                out.append(t)
        return out[:40]


#: defaults per kind: how tall the art is drawn (design px at scale 1; a person is 575) and where a character stands
KIND_HEIGHT: dict[str, float] = {"character": 420.0, "object": 300.0, "place": 1920.0}


@dataclass(frozen=True)
class AssetDef:
    """A loaded asset: the registry entry for an object, and the source of a sprite archetype or a place."""

    name: str
    kind: AssetKind
    summary: str
    tags: tuple[str, ...]
    path: Path  # the art
    fmt: Literal["svg", "raster"]
    height: float
    anchor: tuple[float, float]
    facing: Literal["right", "left", "none"]
    fit: Literal["content", "viewbox"]
    cutout: bool | None
    credit: str
    origin: Literal["builtin", "user"]
    manifest: Path | None = None  # the sidecar, when there is one
    place: PlaceSpec | None = None
    shadow: bool | None = None  # None: automatic (a thing that floats casts none)
    warnings: tuple[str, ...] = field(default=(), compare=False)

    @cached_property
    def content_hash(self) -> str:
        """Digest of the art bytes and every placement fact that changes how it looks (not the summary, tags or credit):
        the key that keeps cached frames honest when the art or its settings change.  Computed on first use."""
        h = hashlib.sha256()
        h.update(self.path.read_bytes())
        h.update(
            json.dumps(
                [
                    self.kind,
                    self.height,
                    list(self.anchor),
                    self.facing,
                    self.fit,
                    self.cutout,
                    self.shadow,
                    self.place.model_dump() if self.place else None,
                ],
                sort_keys=True,
            ).encode()
        )
        return h.hexdigest()[:20]

    @cached_property
    def roles(self) -> tuple[str, ...]:
        """The colours a script may change: the roles the drawing marks with ``data-role`` (shades like ``body_dark`` follow
        their base role and are not listed).  Read from the file's text, so listing a library stays cheap."""
        if self.fmt != "svg":
            return ()
        try:
            text = self.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()
        found = dict.fromkeys(re.findall(r"""data-role\s*=\s*["']([^"']+)["']""", text))
        return tuple(r for r in found if not r.endswith(("_dark", "_light")))

    @cached_property
    def aspect(self) -> float:
        """The drawing's width over its height (for the editor's handles around a thing on the stage)."""
        from reel.assets.art import ArtError, load_art

        try:
            art = load_art(self)
        except ArtError:
            return 1.0
        return round(art.width / max(art.height, 1e-6), 4)

    @cached_property
    def role_colors(self) -> dict[str, str]:
        """The colour the drawing gives each recolourable role: what a script's ``palette`` overrides."""
        if not self.roles:
            return {}
        from reel.assets.art import ArtError, load_art

        try:
            art = load_art(self)
        except ArtError:
            return {}
        return {r: art.roles[r] for r in self.roles if r in art.roles}

    @property
    def casts_shadow(self) -> bool:
        """Does a paper-cutout picture of it cast a shadow: what the sidecar says, else only if it stands (a thing that
        floats, like the sun or a balloon, has nothing to cast it on)."""
        return self.shadow if self.shadow is not None else self.anchor[1] > 0.6

    def files(self) -> list[Path]:
        return [p for p in (self.path, self.manifest) if p is not None]

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly facts for the catalog, the API and the CLI (never a file path of the user's disk)."""
        d: dict[str, object] = {
            "name": self.name,
            "kind": self.kind,
            "summary": self.summary,
            "tags": list(self.tags),
            "format": self.fmt,
            "height": self.height,
            "anchor": list(self.anchor),
            "facing": self.facing,
            "origin": self.origin,
            "credit": self.credit,
        }
        if self.roles:
            d["roles"] = list(self.roles)
        if self.fmt == "raster":
            d["cutout"] = self.cutout
        if self.place is not None:
            d["place"] = self.place.model_dump()
        if self.warnings:
            d["warnings"] = list(self.warnings)
        return d


@dataclass
class Art:
    """A drawing or picture as shapes in its own space: the art's box has its anchor at the origin, y down, facing +x."""

    shapes: list[Shape]
    planes: dict[
        str, list[Shape]
    ]  # layered places: plane name -> shapes (``shapes`` holds every shape in order)
    bounds: tuple[float, float, float, float]  # x0, y0, x1, y1 of the art's box in that space
    roles: dict[str, str]  # data-role -> the colour the drawing gives it (recolourable parts)
    warnings: list[str]
    fmt: Literal["svg", "raster"] = "svg"
    viewbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # SVG: the file's own box
    size_px: tuple[int, int] = (0, 0)  # picture: pixels
    notes: list[str] = field(
        default_factory=list
    )  # what the importer did to it ("removed the plain background")
    outline: tuple[tuple[Any, ...], ...] | None = field(
        default=None, repr=False
    )  # the union silhouette of a drawing, worked out once (see art.silhouette)

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]
