# How to add a style pack

A style pack decides **how shapes look**. It never decides *what* is drawn - actions, characters and
backgrounds produce a style-agnostic shape list (the *IR*, see [architecture.md](architecture.md)) - so a
new style automatically renders every existing spec, every action and every background.

> **Adding a style = adding one folder + registering it.**

```bash
reel new-style watercolor            # creates src/reel/styles/watercolor/__init__.py (or ./reel_plugins/watercolor)
reel render examples/story_50s.json --style watercolor --preview
```

Styles in `src/reel/styles/<name>/` are auto-discovered; elsewhere use `--plugin path/to/folder`
(or `REEL_PLUGINS`). That is all the "registration" there is: the `@register_style("watercolor")`
decorator.

## The smallest style

```python
from reel.styles.base import StylePack, register_style

@register_style("blueprint")
class Blueprint(StylePack):
    """White lines on a blue board."""
```

Everything not overridden falls back to the shared defaults (flat fills, a soft contact shadow,
caption presets, default post-FX, the shared transitions), so this already renders. Then override
what makes the look.

## The five hooks (and the brush behind them)

| hook | called for | default |
|---|---|---|
| `draw_background(canvas, shapes, plane, ctx)` | one depth plane of the set (already animated for this frame) | paints each shape through `paint_shape` |
| `draw_character(canvas, build, fig, ctx)` | one posed character, in figure space (positioned, scaled, mirrored, squashed already) | paints `build.shapes` through `paint_shape` |
| `draw_caption(canvas, cap, ctx)` | one caption at one moment | shared kinetic-typography layout with the style's `caption_look` |
| `post_process(frame, ctx, fx)` | the finished world frame (BGRA `uint8`) | the shared FX pipeline with the style's `fx` config |
| `transition(kind, a, b, progress, ctx, params)` | two frames during a scene overlap | the registered transition (`crossfade`, `wipe`, `page_flip`, `cut`) |

Plus one extra: `draw_shadow(canvas, shadow, ctx)` for the ground contact shadow.

The brush that most styles customise is `paint_shape`, which calls four small steps you can
override one at a time: `paint_shadow` (cast shadow), `paint_body` (fill + stroke), `paint_decor`
(texture, shading), and `fill_paint`/`stroke_paint` (the skia paints). A `Shape` carries
the semantics you interpret:

| field | meaning |
|---|---|
| `fill`, `stroke`, `sw` | colour **roles** (`"wall"`, `"skin"`...) or hex; resolve with `self.color(c, shape, ctx)` (applies the scene's lighting; emissive shapes skip it) |
| `material` | `flat paper wood glass metal grass fabric skin emissive water stone` |
| `tag` | what it is: `head torso limb eye mouth window door tree ...` |
| `lod` | 0 essential, 1 detail, 2 fine - a line-art style can draw only `lod == 0` (`max_lod`) |
| `elev` | visual height above the backdrop in px - drives paper shadows |
| `glow`, `gradient`, `alpha`, `xf` | emissive halo, linear gradient, opacity, animated transform |

## Class attributes

```python
class Watercolor(StylePack):
    version = "1"              # bump it when the look changes: it is part of the cache key
    character_fps = 12.0       # sample poses at 12/s for a stop-motion feel (None = every frame)
    dof = 0.3                  # depth-of-field strength (rack_focus moves work in every style)
    base_shake = 0.2           # handheld micro-shake baseline
    max_lod = 2                # lowest level of detail to paint
    fx = FxConfig(grain=0.4, vignette=0.3, bloom=0.2, saturation=0.9, warmth=0.2)   # post-FX defaults
    caption_look = {"subtitle": {"fonts": fonts.MARKER, "fill": "#2b2118", "plate": ("#fbf4e4", 0.95, 0.2)}}
```

`fx` strengths can still be tuned per spec with `meta.fx` (`{"grain": 0, "letterbox": 0.12}`), so a style
picks the default look and an author can dial it.

Captions: a `caption_look` entry only picks fonts, colours, outline, shadow and plate; the shared layout still wraps and animates the
words. It also handles every script: a word your display font cannot draw (Hindi, Arabic, CJK, emoji) is shaped through
`reel.core.shaping.ShapedWord` with a system fallback font. If you override `draw_caption` yourself, call
`draw_caption_default(canvas, cap, look, ctx)` for the text (and decorate around it) so other scripts keep working. The grain strength in
`fx` is also the main driver of the output bitrate (see [performance.md](performance.md#file-size-the-bitrate-cap)): 0.3-0.4 is a film-like
grain that still compresses, 0.55+ costs 2-3x the bits.

## Worked examples in the repo

* **`flat_vector`** (≈50 lines): overrides only `paint_body` to add soft cel shading.
* **`paper_cutout`**: overrides the whole brush - cast shadows from `elev`, a lighter cut edge, per-piece
  tone, a tileable fibre texture (`texture.py`), jittered outlines that "boil" at 12 fps - plus a torn-paper
  `wipe` in `transition()` and a marker/paper caption look.
* **`stickman`**: overrides `draw_character` completely (it draws the rig's joints as marker strokes with a
  double-stroke wobble) and re-interprets backgrounds as line art in `paint_shape`.

## Checklist

- [ ] `reel render examples/story_50s.json --style <name> --preview` works unchanged (no spec edits)
- [ ] output is deterministic: derive jitter from `ctx.seed` / `ctx.tick`, never from global random state
- [ ] expensive per-frame work is avoided (static backgrounds are cached as plates by the stage; only
      characters and animated shapes reach you every frame)
- [ ] `tests/test_render.py` golden frames added for the style (`REEL_UPDATE_GOLDEN=1 pytest -k golden`)
