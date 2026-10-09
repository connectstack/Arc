"""shrug: an example *plugin* action (see docs/adding-an-action.md).

Load it without touching the repo:

    reel list actions --plugin examples/plugins | grep shrug
    reel debug-actions shrug --plugin examples/plugins -o shrug_sheet.png
    reel render my_spec.json --plugin examples/plugins

or permanently: `export REEL_PLUGINS=examples/plugins`.
"""

from __future__ import annotations

from pydantic import Field

from reel.actions.base import ParamsBase, register_action
from reel.core.animation import ActionContext, Clip


class ShrugParams(ParamsBase):
    intensity: float = Field(1.0, ge=0.3, le=1.6, description="how exaggerated the shrug is")
    hold: float = Field(
        0.6, ge=0.0, le=2.0, description="seconds the shoulders stay up (the comedic hold)"
    )


@register_action(
    "shrug",
    params_schema=ShrugParams,
    category="expression",
    summary="'Who knows?': shoulders up, palms out, head tilt, a comedic hold, then drop",
    min_duration=0.8,
    default_duration=1.6,
)
def shrug(ctx: ActionContext, p: ShrugParams) -> Clip:
    D, k = ctx.duration, p.intensity
    up = min(0.28, 0.25 * D)  # time to reach the pose
    drop = max(up, D - min(0.35, 0.25 * D))  # when the release starts
    top = min(drop, up + p.hold)  # end of the hold (it never runs into the release)
    c = ctx.clip(blend_in=0.1, blend_out=0.25)

    # anticipation (a tiny dip), then up with overshoot, hold, release: the classic "squash, pop, settle"
    c.key(
        "bob", [(0, 0), (0.06 * D, -6), (up, 11 * k, "overshoot"), (top, 9 * k), (D, 0)], mode="add"
    )
    for s, sg in (("r", 1), ("l", -1)):
        c.key(
            f"arm_{s}_sh",
            [
                (0, 5 * sg),
                (0.06 * D, 2 * sg),
                (up, 26 * sg * k, "overshoot"),
                (top, 24 * sg * k),
                (D, 5 * sg),
            ],
        )
        c.key(f"arm_{s}_el", [(0, 10), (up, 98 * k, "overshoot"), (top, 92 * k), (D, 10)])
        c.key(
            f"hand_{s}_wr", [(0, 0), (up, -38 * sg * k), (top, -32 * sg * k), (D, 0)]
        )  # palms turn outward
    c.key("head_tilt", [(0, 0), (up, -9 * k), (top, -8 * k), (D, 0)])
    c.key("head_turn", [(0, 0), (up, -0.5), (top, -0.5), (D, 0)])  # glances at the camera
    c.key("brow_raise", [(0, 0), (up, 0.7 * k), (top, 0.7 * k), (D, 0)])
    c.key("brow_angle", [(0, 0), (up, 0.5 * k), (top, 0.5 * k), (D, 0)])  # worried "no idea" brows
    c.key("mouth_smile", [(0, 0.3), (up, -0.55), (top, -0.55), (D, 0.3)])
    c.key("eye_dx", [(0, 0), (up, 0.5), (top, 0.5), (D, 0)])
    c.event(up, "shrug")
    return c
