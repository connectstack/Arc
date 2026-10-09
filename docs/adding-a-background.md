# How to add a background template

A background is a function that fills a **scene graph** with style-agnostic shapes. It *adapts* to its
parameters (time of day, colour mood, density, anything you add) - the LLM or the author fills the
parameters, the template does the rest. Because it emits shapes (not pixels), the same template is
painted as flat vector, layered paper or marker line art.

```python
# src/reel/templates/lighthouse.py  (auto-discovered) - or any folder passed with --plugin
from typing import Literal
from pydantic import Field
from reel.templates.base import BgParams, BuildContext, register_background, X0, X1


class LighthouseParams(BgParams):                       # time_of_day, mood, density are inherited
    sea: Literal["calm", "stormy"] = Field("calm", description="sea state")
    beam: bool = Field(True, description="sweep a light beam from the lantern at dusk/night")


@register_background(
    "lighthouse",
    params_schema=LighthouseParams,
    summary="A lighthouse on a rocky shore with sea and sky; the beam sweeps at night",
    slots={"rocks": (0.28, 0.74), "door": (0.74, 0.74)},      # named positions a spec can use: "position": "door"
    ground_y=0.74,                                            # where feet go by default (leave room for captions)
)
def lighthouse(ctx: BuildContext, p: LighthouseParams) -> None:
    ctx.sky(horizon_y=1250)                                   # gradient, sun/moon, stars, drifting clouds (time-aware)
    ctx.rect("far", X0, 1250, X1 - X0, 700, "water", tag="sea", gradient=("water", "sky_bottom", 90))
    ctx.rect("mid_back", X0, 1380, X1 - X0, 900, "sand", tag="ground")           # the floor must reach the frame bottom
    ctx.rect("back", 700, 380, 200, 900, "paper", elev=3, tag="tower")           # roles: colour names, not hex
    lamp = ctx.rect("back", 730, 300, 140, 90, "glow", material="emissive", glow=60, tag="lantern")
    # ...
```

## The rules of the stage

* **Planes** back to front: `sky`, `far`, `back`, `mid_back`, *(characters)*, `mid_front`, `near`. Under a camera pan they move at
  `0.1× / 0.4× / 0.72× / 1× / 1× / 1.55×`, so put distant things in `far`, the floor and set pieces in `mid_back`, and framing
  foliage/lamp posts at the edges of `near`. `mid_front` is for the rare low object in front of the characters.
* **Coordinates** are design pixels in the 1080×1920 frame; the **stage is bigger** (`x -540…1620`, `y -300…2220`) so
  pans and dollies never reveal the void - a test (`tests/test_templates.py`) fails if you leave a gap.
* **Characters** stand with their feet on `ground_y` (use `0.74`: the bottom quarter is for captions) and are ≈640 px tall at scale 1,
  so keep the centre zone behind them (x 150-930, y 600-1450) calm and put detail at the edges and in the far planes. The floor must
  extend from just above the feet line to past the bottom of the frame.
* **Colours are roles** (`"wall"`, `"floor"`, `"foliage"`, `"glass"`, `"sky_top"` ... see `templates/base.py: BASE_ROLES`), or hex derived from
  `ctx.scheme.base(role)` with `darken()/lighten()/mix()`. The scheme already encodes `time_of_day` and `mood`; ambient light is applied
  at paint time to every non-emissive shape. Use `material="emissive"` (and `glow=`) for lit windows, lamps, screens, neon: they ignore ambient.
  Check `ctx.graph.lamps_on` to decide whether windows/lamps are lit.
* **Shape hints** for the styles: `elev` (px above the backdrop: paper shadows), `material`, `tag`, `lod` (0 essential / 1 detail / 2 fine:
  the stickman style draws only `lod 0` as outlines, so the silhouette goes at 0 and texture strokes at 1+).
* **Life**: `anim=Anim("sway"|"bob"|"drift"|"flicker"|"twinkle"|"pulse"|"spin", ...)` on a shape, or
  `ctx.emit(plane, Particles("rain"|"snow"|"dust"|"leaves"|"sparkle"|"embers"|"bubbles"|"confetti", area, count, ...))`.
  Static shapes are drawn once per scene as a cached plate; keep **animated shapes under ~60**.
* **Randomness** only via `ctx.rng` (seeded by the spec seed + scene id) so renders stay deterministic.

## Gotchas the shipped templates ran into

* **Animated shapes draw after the static ones of the same plane**, whatever their `z`: static shapes are baked into a per-scene plate and
  animated ones are drawn over it each frame. A swaying curtain can therefore never be hidden by a static valance in the same plane: put the
  occluder in a nearer plane (`mid_front`), or make everything in front of the animation animated too.
* **No alpha gradients, and soft glows band.** Stacking many low-alpha discs rounds to 1/255 steps and shows rings; use few, wider layers
  (the shipped `room` and `stage` templates have a `halo()` helper, and `street`/`rooftop` a `glow()` one, with exact 1/255 alpha steps: copy it), or an `emissive` shape with `glow=`.
  `glow=` also draws the sharp body, so mist and soft light are nested translucent shapes. Hex colours with an alpha suffix work in
  gradients only for `material="emissive"` (`ColorScheme.lit()` drops the alpha for other materials).
* **Frame-wide `near` elements lose coverage** beyond about ±0.3 of pan and shrink under a negative dolly, so keep framing elements that must
  always cover the frame in `mid_front`, and let backdrop rects reach the bottom of the stage (`Y1`), otherwise a vertical pan exposes the clear colour
  between planes.
* **`ctx.sky()` fixes the sun and moon** where buildings or trees may hide them: call it with `sun=False` and draw your own, and clamp
  `ctx.density` around the call if the star count matters.
* **`density` should only add or remove clutter.** Seed the layout independently of `time_of_day`, `density`, `weather` and `season`
  (use a `ctx.rng` keyed on the template name, not on those params), so changing a mood never moves the furniture.
* **Keep a pull-back margin.** Backdrops are authored for the stage rect (`y` up to 2220); an extreme camera pull-back (dolly below -0.7 or
  zoom under about 0.76) shows its edge.

## Preview and test

```bash
reel debug-backgrounds lighthouse -o sheet.png --scale 0.4     # day / dusk+warm / night+cool / gloomy, two characters on the set
reel debug-backgrounds lighthouse --params '{"sea": "stormy"}' -o stormy.png
pytest tests/test_templates.py -k lighthouse                   # contract: every time x mood builds, deterministic, covers the stage, renders in all styles
```

`tests/test_templates.py` automatically covers every registered template. Then `reel manifest -o src/reel/templates/manifest.json` so the
LLM prompt can offer it (the prompt only ever names things that exist in the manifest), and `reel reference -o docs/reference` for the docs.

### Checklist
- [ ] summary, params (with defaults and `description=`), 3-6 slots, `ground_y=0.74`
- [ ] looks right at dawn, day, dusk and night and in every mood (`reel debug-backgrounds`)
- [ ] calm centre zone, interest at the edges, clear depth layering
- [ ] essential structure at `lod 0` with descriptive `tag`s so the stickman style reads
