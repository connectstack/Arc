"""Characters made from art: a cat, a dragon, a mascot.

A sprite character is an ordinary archetype to the rest of the engine: the planner places it, the existing actions (walk,
run, jump, talk, laugh ...) drive it, captions with its id as speaker make it talk.  Only the drawing differs: instead of a
jointed body the whole picture is posed by the channels the actions write.  Walking rocks and hops it with the stride, a jump
lifts it, laughing and talking squash it, a lean tips it, and ``face`` mirrors it.  The picture keeps its own colours; the parts
its drawing marks with ``data-role`` can be recoloured per character through ``characters[].palette``.

Things a body can do that a picture cannot (wave an arm, point, hold a prop) are skipped: the action still takes its time and
the character keeps still, and the linter says so.
"""

from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

from reel.assets.art import load_art, recolor, silhouette
from reel.assets.model import ARMS_ONLY, Art, AssetDef
from reel.core.archetypes import Archetype, PropDef, RigDims
from reel.core.figure import SB, SPRITE_Z, FigureBuild, ShadowInfo
from reel.core.geometry import Rot2D, clamp
from reel.core.ir import PathG, Shape
from reel.core.rig import PosedFigure

#: a person's height in design px: sprite motion written for a body is scaled by (sprite height / this)
PERSON_HEIGHT = RigDims().height


def sprite_archetype(asset: AssetDef) -> Archetype:
    """The archetype a character asset registers: a person's skeleton scaled to the sprite's height.

    The skeleton is never drawn; it is what the planner reads gaze targets, reach and landmarks from, so a sprite behaves
    like a body of its height everywhere except in how it is painted.
    """
    base = RigDims()
    k = asset.height / base.height
    dims = RigDims(**{f.name: getattr(base, f.name) * k for f in fields(RigDims)})
    return Archetype(
        asset.name,
        asset.summary,
        dims,
        {},
        {"sprite": True, "asset": asset},
        {},
        "sprite",
        asset=asset.name,
        tags=asset.tags,
        height_px=asset.height,
    )


def _art_shapes(art: Art, palette: dict[str, str], casts_shadow: bool = True) -> list[Shape]:
    shapes = recolor(art.shapes, art.roles, palette) if art.roles else list(art.shapes)
    if art.fmt == "svg":
        shapes = [replace(s, shadow=False) for s in shapes]
        outline = silhouette(art) if casts_shadow else ()
        if outline:
            # one soft shadow from the whole outline instead of one per piece (paper cutout); other styles ignore it
            caster = Shape(
                PathG(outline), None, z=-1.0, elev=3.2, shadow=True, tag="shadow_caster", lod=0
            )
            shapes = [caster, *shapes]
    return shapes


def build_sprite(
    fig: PosedFigure, arch: Archetype, palette: dict[str, str], props: Any = (), t: float = 0.0
) -> FigureBuild:
    """The posed sprite as shapes in figure space (origin at the feet, +x forward), plus its ground shadow."""
    asset: AssetDef = arch.features["asset"]
    art = load_art(asset)
    pose = fig.pose
    ks = clamp(
        asset.height / PERSON_HEIGHT, 0.3, 1.5
    )  # motion channels are written for a person's size

    stride = max(
        abs(pose["leg_r_hip"]), abs(pose["leg_l_hip"])
    )  # how far the (invisible) legs swing: the gait's clock
    hop = 0.0011 * asset.height * stride
    dy = -(pose["lift"] * ks + pose["bob"] * ks + hop)
    dx = pose["hip_dx"] * ks
    rock = 0.09 * pose["leg_r_hip"]
    rot = 0.8 * pose["torso_lean"] + 0.25 * pose["head_tilt"] + 0.5 * pose["rot_hip"] + rock
    stretch = 1.0 + 0.02 * pose["breath"] + 0.035 * pose["mouth_open"]
    xf = (dx, dy, rot, 1.0, stretch, 0.0, 0.0)
    shapes = [
        replace(s, xf=xf, z=SPRITE_Z + i)
        for i, s in enumerate(_art_shapes(art, palette, asset.casts_shadow))
    ]

    # thought bubble / exclamation mark, drawn by the same code as for a body, near the top of the sprite
    sb = SB(SPRITE_Z + 500)
    x0, y0, x1, y1 = art.bounds
    hc = (x0 + 0.72 * (x1 - x0), y0 + 0.12 * (y1 - y0))
    from reel.core import props as prop_lib

    prop_lib.fx_markers(sb, Rot2D(hc, 0.0), hc, 0.12 * (x1 - x0), 0.1 * (y1 - y0), pose, t, palette)
    s = clamp(ks * 1.6, 0.5, 1.1)
    shapes += [replace(m, xf=(dx, dy, 0.0, s, s, hc[0], hc[1])) for m in sb.shapes]

    lift = max(0.0, pose["lift"] * ks)
    rx = max(30.0, 0.46 * (x1 - x0))
    shadow = ShadowInfo(
        (x0 + x1) / 2,
        rx,
        max(8.0, rx * 0.17),
        clamp(0.42 / (1.0 + lift / (0.3 * asset.height)), 0.05, 0.42),
    )
    return FigureBuild(
        shapes, shadow, (x0, y0, x1, y1), {"palette": palette, "arch": arch, "sprite": True}
    )


def sprite_notes(arch: Archetype, props: list[PropDef], used_actions: set[str]) -> list[str]:
    """What a sprite cannot do among what the spec asks of it (for the linter)."""
    notes: list[str] = []
    if props:
        notes.append("props are not drawn on a picture character")
    body_only = sorted(used_actions & ARMS_ONLY)
    if body_only:
        notes.append(
            "a picture character does not use its arms: " + ", ".join(body_only) + " only take time"
        )
    return notes
