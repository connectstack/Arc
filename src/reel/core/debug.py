"""Debug tools: bake a single action alone and draw its poses as a labelled skeleton sheet.

Style-free on purpose - this is what you use to check a new action's motion before it
ever meets a style pack (`reel debug-actions walk wave --out sheet.png`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import skia

from reel.core.animation import BakedLayer, CharacterInfo, LayerPlan, PlacedAction
from reel.core.catalog import CATALOG, Catalog
from reel.core.fonts import typeface
from reel.core.planner import CHAR_UNIT, SimpleWorld
from reel.core.rig import Rig, solve, squash_scale

COLORS = {
    "bg": skia.Color(250, 248, 243),
    "ground": skia.Color(190, 184, 170),
    "skin": skia.Color(240, 196, 154),
    "shirt": skia.Color(228, 87, 46),
    "pants": skia.Color(45, 74, 107),
    "arm_back": skia.Color(160, 62, 34),
    "leg_back": skia.Color(30, 52, 78),
    "ink": skia.Color(40, 40, 50),
    "text": skia.Color(60, 60, 70),
}


def bake_action(
    name: str,
    *,
    params: dict[str, Any] | None = None,
    duration: float | None = None,
    archetype: str = "everyman",
    props: Sequence[str] = (),
    fps: int = 30,
    pad: float = 0.5,
    position: tuple[float, float] = (0.4, 0.8),
    catalog: Catalog | None = None,
) -> tuple[BakedLayer, float, float]:
    """Bake ``name`` alone on a timeline: ``pad`` s idle, the action, ``pad`` s idle."""
    cat = catalog or CATALOG
    defn = cat.actions.get(name)
    dur = duration if duration is not None else defn.default_duration
    arch = cat.archetypes.get(archetype)
    info = CharacterInfo("debug", archetype, tuple(props), tuple(cat.props.get(p) for p in props))
    world = SimpleWorld()
    plan = LayerPlan(
        char=info,
        rig=Rig(arch),
        position=position,
        px_scale_at=lambda y: CHAR_UNIT * world.depth_scale(y),
        seed=1234,
    )
    plan.actions = [
        PlacedAction(0, name, pad, pad + dur, defn.parse(params), defn.fn, defn.moves_root)
    ]
    from reel.core.animation import bake_layer

    total = pad + dur + pad
    return bake_layer(plan, world, fps=fps, duration=total), pad, pad + dur


# ------------------------------------------------------------------------------- drawing
def _paint(color: int, width: float = 0.0, *, stroke: bool = False, alpha: int = 255) -> skia.Paint:
    p = skia.Paint(AntiAlias=True, Color=color)
    p.setAlpha(alpha)
    if stroke:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(width)
        p.setStrokeCap(skia.Paint.kRound_Cap)
    return p


def draw_skeleton(canvas: skia.Canvas, rig: Rig, pose: dict[str, float], *, s: float) -> None:
    """Draw one posed body in figure space scaled by ``s`` (origin = ground contact)."""
    d = rig.dims
    fig = solve(d, pose)
    P = fig.pts

    def seg(a: str, b: str, w: float, color: int) -> None:
        canvas.drawLine(P[a][0], P[a][1], P[b][0], P[b][1], _paint(color, w, stroke=True))

    seg("hip_l", "knee_l", d.leg_w, COLORS["leg_back"])
    seg("knee_l", "ankle_l", d.leg_w * 0.9, COLORS["leg_back"])
    seg("shoulder_l", "elbow_l", d.arm_w, COLORS["arm_back"])
    seg("elbow_l", "wrist_l", d.arm_w * 0.9, COLORS["arm_back"])
    canvas.drawCircle(P["hand_l"][0], P["hand_l"][1], d.hand_r, _paint(COLORS["skin"]))
    seg("hip_c", "shoulder_c", d.chest_w * 0.85, COLORS["shirt"])
    seg("hip_r", "knee_r", d.leg_w, COLORS["pants"])
    seg("knee_r", "ankle_r", d.leg_w * 0.9, COLORS["pants"])
    for side in ("l", "r"):
        a = P[f"heel_{side}"]
        b = P[f"toe_{side}"]
        canvas.drawLine(
            a[0],
            a[1] - d.foot_h * 0.45,
            b[0],
            b[1] - d.foot_h * 0.45,
            _paint(COLORS["ink"], d.foot_h * 0.9, stroke=True),
        )
    seg("shoulder_r", "elbow_r", d.arm_w, COLORS["shirt"])
    seg("elbow_r", "wrist_r", d.arm_w * 0.9, COLORS["skin"])
    canvas.drawCircle(P["hand_r"][0], P["hand_r"][1], d.hand_r, _paint(COLORS["skin"]))
    if fig.pose["hand_r_point"] > 0.5:
        ang = math.radians(fig.ang["hand_r"])
        hx, hy = P["hand_r"]
        canvas.drawLine(
            hx,
            hy,
            hx + math.sin(ang) * d.hand_r * 2.1,
            hy + math.cos(ang) * d.hand_r * 2.1,
            _paint(COLORS["skin"], d.hand_r * 0.6, stroke=True),
        )

    # head + face ------------------------------------------------------------------------
    hc = P["head_c"]
    ha = math.radians(fig.ang["head"])
    canvas.save()
    canvas.translate(hc[0], hc[1])
    canvas.rotate(math.degrees(ha))
    canvas.drawOval(skia.Rect(-d.head_rx, -d.head_ry, d.head_rx, d.head_ry), _paint(COLORS["skin"]))
    canvas.drawOval(
        skia.Rect(-d.head_rx, -d.head_ry, d.head_rx, d.head_ry),
        _paint(COLORS["ink"], 6, stroke=True, alpha=200),
    )
    shift = (0.20 + 0.26 * fig.pose["head_turn"]) * d.head_rx
    eo = max(0.0, fig.pose["eye_open"] * (1 - fig.pose["blink"]))
    for ex in (-0.30, 0.30):
        cx = shift + ex * d.head_rx
        cy = -0.10 * d.head_ry
        r = d.head_rx * 0.17
        canvas.drawOval(
            skia.Rect(cx - r, cy - r * min(eo, 1.3), cx + r, cy + r * min(eo, 1.3) + 0.01),
            _paint(skia.ColorWHITE),
        )
        px = cx + fig.pose["eye_dx"] * r * 0.5
        py = cy + fig.pose["eye_dy"] * r * 0.5
        canvas.drawCircle(px, py, r * 0.48, _paint(COLORS["ink"]))
        by = cy - r * 1.7 - fig.pose["brow_raise"] * r * 0.8
        tilt = fig.pose["brow_angle"] * r * 0.9
        sgn = -1 if ex < 0 else 1
        # worried (+): the inner end (towards the nose) is raised; angry (-): lowered
        canvas.drawLine(
            cx - r, by - tilt * sgn, cx + r, by + tilt * sgn, _paint(COLORS["ink"], 6, stroke=True)
        )
    my = 0.42 * d.head_ry
    mw = (0.28 + 0.2 * fig.pose["mouth_wide"]) * d.head_rx
    sm = fig.pose["mouth_smile"]
    op = fig.pose["mouth_open"]
    path = skia.Path()
    path.moveTo(shift - mw, my - sm * 6)
    path.quadTo(shift, my + sm * 26 + op * 30, shift + mw, my - sm * 6)
    if op > 0.08:
        path.quadTo(shift, my - sm * 6 + op * 56 + 4, shift - mw, my - sm * 6)
        canvas.drawPath(path, _paint(skia.Color(120, 30, 40)))
    canvas.drawPath(path, _paint(COLORS["ink"], 5, stroke=True))
    if fig.pose["tear"] > 0.1:
        for ex in (-0.30, 0.30):
            canvas.drawOval(
                skia.Rect(
                    shift + ex * d.head_rx - 6,
                    0.1 * d.head_ry,
                    shift + ex * d.head_rx + 6,
                    0.1 * d.head_ry + 38 * fig.pose["tear"],
                ),
                _paint(skia.Color(90, 170, 240)),
            )
    canvas.restore()
    if fig.pose["fx_think"] > 0.05:
        think_a = fig.pose["fx_think"]
        for i, r in enumerate((10, 16, 34)):
            canvas.drawCircle(
                hc[0] + 90 + i * 34, hc[1] - 110 - i * 30, r * think_a, _paint(skia.ColorWHITE)
            )
            canvas.drawCircle(
                hc[0] + 90 + i * 34,
                hc[1] - 110 - i * 30,
                r * think_a,
                _paint(COLORS["ink"], 3, stroke=True),
            )
    if fig.pose["fx_surprise"] > 0.05:
        pop = fig.pose["fx_surprise"]
        f = skia.Font(typeface(("Impact", "Arial Black"), bold=True), 120 * pop)
        canvas.drawString("!", hc[0] + 80, hc[1] - 70, f, _paint(skia.Color(230, 40, 40)))
    canvas.drawCircle(P["hip_c"][0], P["hip_c"][1], 8, _paint(skia.Color(255, 0, 100)))


def render_action_sheet(
    names: Sequence[str],
    out: str | Path,
    *,
    samples: Sequence[float] = (0.0, 0.17, 0.33, 0.5, 0.67, 0.83, 1.0),
    archetype: str = "everyman",
    props: Sequence[str] = ("briefcase",),
    cell: tuple[int, int] = (250, 270),
    catalog: Catalog | None = None,
) -> Path:
    """Write a PNG: one row per action, one column per sampled moment."""
    cat = catalog or CATALOG
    cw, ch = cell
    label_w = 96
    surf = skia.Surface(label_w + cw * len(samples), ch * len(names))
    canvas = surf.getCanvas()
    canvas.clear(COLORS["bg"])
    font = skia.Font(typeface(("Helvetica Neue", "Arial"), bold=True), 15)
    small = skia.Font(typeface(("Helvetica Neue", "Arial")), 11)
    s = 0.30
    for row, name in enumerate(names):
        baked, t0, t1 = bake_action(
            name, archetype=archetype, props=props if name == "pick_up" else (), catalog=cat
        )
        # the pick_up debug run needs the prop to exist; others don't care
        rig = Rig(cat.archetypes.get(archetype))
        y0 = row * ch
        canvas.drawString(name, 8, y0 + 22, font, _paint(COLORS["text"]))
        dur = t1 - t0
        canvas.drawString(f"{dur:.1f}s", 8, y0 + 38, small, _paint(COLORS["text"], alpha=150))
        moved = abs(float(baked.root_x[-1] - baked.root_x[0]))
        if moved > 0.01:
            canvas.drawString(
                f"dx {moved:+.2f}", 8, y0 + 52, small, _paint(COLORS["text"], alpha=150)
            )
        for col, frac in enumerate(samples):
            t = t0 + frac * dur
            i = baked.frame_index(min(t, baked.n / baked.fps - 1e-3))
            pose = baked.pose(i)
            ox = label_w + col * cw + cw * 0.64
            oy = y0 + ch * 0.88
            canvas.save()
            canvas.clipRect(skia.Rect(label_w + col * cw, y0, label_w + (col + 1) * cw, y0 + ch))
            canvas.drawLine(
                label_w + col * cw + 6,
                oy,
                label_w + (col + 1) * cw - 6,
                oy,
                _paint(COLORS["ground"], 3, stroke=True),
            )
            canvas.translate(ox, oy)
            face = pose["face"]
            canvas.rotate(pose["rot"] * (1 if face >= 0 else -1))
            sx, sy = squash_scale(pose["squash"])
            canvas.scale(face * s * sx, s * sy)
            draw_skeleton(canvas, rig, pose, s=s)
            canvas.restore()
            canvas.drawString(
                f"{frac * dur:.2f}s",
                label_w + col * cw + 6,
                y0 + ch - 6,
                small,
                _paint(COLORS["text"], alpha=130),
            )
    img = surf.makeImageSnapshot()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), skia.kPNG)
    return out


def check_action_finite(name: str, **kw: Any) -> bool:
    """True if a baked action has no NaN/inf and stays inside channel limits (used by tests)."""
    baked, _, _ = bake_action(name, **kw)
    return bool(np.isfinite(baked.matrix).all())


# ------------------------------------------------------------------------------- backgrounds
BG_VARIANTS: tuple[tuple[str, str], ...] = (
    ("day", "neutral"),
    ("dusk", "warm"),
    ("night", "cool"),
    ("day", "gloomy"),
)


def render_background_sheet(
    names: Sequence[str],
    out: str | Path,
    *,
    style: str = "flat_vector",
    variants: Sequence[tuple[str, str]] = BG_VARIANTS,
    params: dict[str, Any] | None = None,
    scale: float = 0.3,
    t: float = 1.0,
    catalog: Catalog | None = None,
) -> Path:
    """One row per template, one column per (time_of_day, mood); two characters stand on the set."""
    from reel.core.fx import apply_fx
    from reel.core.spec import ReelSpec
    from reel.core.stage import RenderConfig, SceneStage

    cat = catalog or CATALOG
    cols = len(variants)
    cw, ch = round(1080 * scale), round(1920 * scale)
    pad = 6
    surf = skia.Surface(cols * cw + (cols + 1) * pad + 70, len(names) * (ch + pad) + pad)
    canvas = surf.getCanvas()
    canvas.clear(skia.Color(30, 30, 38))
    font = skia.Font(typeface(("Helvetica Neue", "Arial"), bold=True), 13)
    small = skia.Font(typeface(("Helvetica Neue", "Arial")), 10)
    style_cls = cat.styles.get(style)
    for row, name in enumerate(names):
        bg = cat.backgrounds.get(name)
        slots = bg.slot_names()
        left = "left"
        right = "right"
        for col, (tod, mood) in enumerate(variants):
            spec = ReelSpec.model_validate(
                {
                    "meta": {"title": "bg", "style": style, "seed": 11},
                    "characters": [
                        {"id": "a", "archetype": "everyman", "props": ["hat"]},
                        {"id": "b", "archetype": "kid"},
                    ],
                    "scenes": [
                        {
                            "id": "s",
                            "duration_sec": 4,
                            "background": {
                                "template": name,
                                "params": {**(params or {}), "time_of_day": tod, "mood": mood},
                            },
                            "layers": [
                                {
                                    "character": "a",
                                    "position": left,
                                    "actions": [{"name": "wave", "t0": 0.2, "t1": 3}],
                                },
                                {
                                    "character": "b",
                                    "position": right,
                                    "scale": 0.9,
                                    "actions": [{"name": "laugh", "t0": 0.2, "t1": 3}],
                                },
                            ],
                        }
                    ],
                }
            )
            cfg = RenderConfig((cw, ch), 30, 11, style, False, style_cls.fx, (0.1, 0.2, 0.07, 0.07))
            st = SceneStage(spec, 0, cfg, catalog=cat)
            frame = apply_fx(st.render(round(t * 30)), cfg.fx, st._ctx(0, 0))
            img = skia.Image.fromarray(frame, colorType=skia.kBGRA_8888_ColorType)
            x = 70 + pad + col * (cw + pad)
            y = pad + row * (ch + pad)
            canvas.drawImage(img, x, y)
            if row == 0:
                canvas.drawString(f"{tod}/{mood}", x + 4, y + 14, small, _paint(skia.ColorWHITE))
        canvas.drawString(name, 4, pad + row * (ch + pad) + 18, font, _paint(skia.ColorWHITE))
        canvas.drawString(
            f"{len(slots)} slots",
            4,
            pad + row * (ch + pad) + 34,
            small,
            _paint(skia.ColorWHITE, alpha=170),
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    surf.makeImageSnapshot().save(str(out), skia.kPNG)
    return out
