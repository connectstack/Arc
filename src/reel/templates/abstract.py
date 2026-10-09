"""abstract: soft gradient stage with floating shapes - for explainers, titles and moods."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from reel.core.geometry import adjust, mix
from reel.core.ir import Anim, Particles
from reel.templates.base import X0, X1, BgParams, BuildContext, register_background

PALETTES: dict[str, tuple[str, str, str, str, str]] = {
    # bg_top, bg_bottom, shape1, shape2, shape3
    "sunrise": ("#ffb88c", "#ffe9c7", "#ff6f61", "#ffd166", "#6c5ce7"),
    "ocean": ("#4fc3f7", "#d9f4ff", "#0288d1", "#26c6da", "#ffd54f"),
    "candy": ("#ff9ecb", "#ffe3f1", "#7c4dff", "#00e5ff", "#ffeb3b"),
    "forest": ("#7bc67e", "#e4f5d6", "#2e7d32", "#ffca28", "#ef6c00"),
    "mono": ("#cfd8e3", "#f4f6fa", "#4a5568", "#8892a2", "#2d3748"),
    "violet": ("#8e7bff", "#e6e0ff", "#ff6b9d", "#ffd166", "#06d6a0"),
}


class AbstractParams(BgParams):
    pattern: Literal["blobs", "circles", "blocks", "waves", "rays"] = Field(
        "blobs", description="kind of floating shapes"
    )
    palette: Literal["auto", "sunrise", "ocean", "candy", "forest", "mono", "violet"] = Field(
        "auto", description="colour family (auto follows mood)"
    )
    floor: bool = Field(True, description="draw a soft stage floor under the characters")


_AUTO = {
    "neutral": "violet",
    "warm": "sunrise",
    "cool": "ocean",
    "dramatic": "violet",
    "playful": "candy",
    "gloomy": "mono",
}


@register_background(
    "abstract",
    params_schema=AbstractParams,
    summary="Soft gradient stage with floating geometric shapes; good for explainers, titles and moods",
    slots={"stage_left": (0.28, 0.74), "stage_center": (0.5, 0.74), "stage_right": (0.72, 0.74)},
    tags=("indoor-neutral", "explainer"),
    ground_y=0.74,
    perspective=0.0,
    horizon=0.58,
)
def abstract(ctx: BuildContext, p: AbstractParams) -> None:
    name = p.palette if p.palette != "auto" else _AUTO[p.mood]
    top, bot, c1, c2, c3 = PALETTES[name]
    night = ctx.time_of_day == "night"
    if night:
        top, bot = mix(top, "#0b1030", 0.78), mix(bot, "#1a2457", 0.7)
        c1, c2, c3 = (adjust(c, sat=1.1, light=0.9) for c in (c1, c2, c3))
    elif ctx.time_of_day in ("dawn", "dusk"):
        top = mix(top, "#ff8f6b" if ctx.time_of_day == "dusk" else "#ffc2a0", 0.25)
    ctx.graph.scheme = ctx.scheme.with_roles(
        abs_top=top,
        abs_bot=bot,
        abs1=c1,
        abs2=c2,
        abs3=c3,
        abs_floor=mix(bot, c1, 0.18),
        abs_floor2=mix(bot, c1, 0.30),
    )

    ctx.rect(
        "sky",
        X0,
        -300,
        X1 - X0,
        2520,
        "abs_top",
        material="emissive",
        shadow=False,
        tag="sky",
        gradient=("abs_top", "abs_bot", 90.0),
    )
    rng = ctx.rng
    n = int(4 + 8 * ctx.density)

    if p.pattern == "rays":
        for i in range(14):
            a = -90 + (i - 6.5) * 10.5
            ctx.poly(
                "far",
                [
                    (540, 760),
                    (
                        540 + 1900 * math.cos(math.radians(a - 3.2)),
                        760 + 1900 * math.sin(math.radians(a - 3.2)),
                    ),
                    (
                        540 + 1900 * math.cos(math.radians(a + 3.2)),
                        760 + 1900 * math.sin(math.radians(a + 3.2)),
                    ),
                ],
                "#ffffff",
                material="emissive",
                alpha=0.10 + 0.05 * (i % 2),
                shadow=False,
                tag="ray",
                lod=1,
                anim=Anim("twinkle", amp=0.5, freq=0.12, phase=i),
            )
    for i in range(n):
        x = float(rng.uniform(-420, 1500))
        y = float(rng.uniform(100, 1250))
        r = float(rng.uniform(70, 230))
        col = ("abs1", "abs2", "abs3")[i % 3]
        plane = "far" if i % 2 == 0 else "back"
        a = 0.22 if plane == "far" else 0.32
        if p.pattern in ("blobs", "circles"):
            if p.pattern == "blobs":
                pts = [
                    (
                        x + r * math.cos(k * math.pi / 4) * (1 + 0.18 * math.sin(k * 1.7 + i)),
                        y + r * math.sin(k * math.pi / 4) * (1 + 0.18 * math.cos(k * 2.1 + i)),
                    )
                    for k in range(8)
                ]
                s = ctx.poly(
                    plane,
                    pts,
                    col,
                    curve=True,
                    alpha=a,
                    material="paper",
                    elev=2,
                    tag="blob",
                    lod=1,
                )
            else:
                s = ctx.ellipse(
                    plane, x, y, r, r, col, alpha=a, material="paper", elev=2, tag="circle", lod=1
                )
        elif p.pattern == "blocks":
            s = ctx.rect(
                plane,
                x - r / 2,
                y - r / 2,
                r,
                r,
                col,
                r=r * 0.18,
                alpha=a + 0.1,
                material="paper",
                elev=2,
                tag="block",
                lod=1,
            )
            s.xf = (0.0, 0.0, float(rng.uniform(-25, 25)), 1.0, 1.0, x, y)
        else:  # waves
            pts = [
                (X0 + k * 160, y + math.sin(k * 0.9 + i) * r * 0.35)
                for k in range(int((X1 - X0) / 160) + 2)
            ]
            pts += [(X1 + 160, 2400), (X0 - 160, 2400)]
            s = ctx.poly(
                plane,
                pts,
                col,
                curve=False,
                smooth=0.0,
                alpha=a * 0.8,
                material="paper",
                elev=2,
                tag="wave",
                lod=1,
            )
        s.anim = Anim(
            "bob",
            amp=float(rng.uniform(8, 22)),
            freq=float(rng.uniform(0.08, 0.2)),
            phase=float(rng.uniform(0, 6.28)),
        )
    if p.floor:
        ctx.ellipse(
            "mid_back",
            540,
            1568,
            700,
            120,
            "abs_floor",
            material="paper",
            elev=2,
            tag="floor",
            shadow=True,
        )
        ctx.ellipse(
            "mid_back",
            540,
            1560,
            640,
            100,
            "abs_floor2",
            material="paper",
            elev=2,
            tag="floor",
            lod=1,
            shadow=False,
        )
        ctx.rect(
            "mid_back",
            X0,
            1568,
            X1 - X0,
            760,
            "abs_floor",
            tag="ground",
            shadow=False,
            gradient=("abs_floor", "abs_floor2", 90.0),
        )
    ctx.emit(
        "near",
        Particles(
            "sparkle",
            (X0, 0, X1, 1500),
            int(8 + 26 * ctx.density),
            size=9,
            color="#ffffff",
            alpha=0.7,
            seed=ctx.seed,
            material="emissive",
        ),
    )
    for i in range(3):  # big soft bokeh in the foreground corners
        x = (-250, 1250, 520)[i]
        y = (1500, 1150, 2050)[i]
        ctx.ellipse(
            "near",
            x,
            y,
            190,
            190,
            "#ffffff",
            alpha=0.10,
            material="emissive",
            shadow=False,
            tag="bokeh",
            lod=1,
            anim=Anim("bob", amp=18, freq=0.07, phase=i * 2),
        )
