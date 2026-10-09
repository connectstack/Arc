# Architecture

`reel` turns a **JSON scene spec** into a finished vertical reel, entirely offline. There are two
decoupled stages, and the second one never needs the first:

```
 script.txt ──► SpecGenerator ──► spec.json ──► lint ──► Renderer ──► reel.mp4
  (optional)    LLM | manual |     (the one      │         │
                heuristic          contract)     │         ├─ SceneStage  (one per scene: template + animation + camera)
                                                 │         ├─ StylePack   (how everything looks)
                                  fails with a   │         ├─ post-FX, transitions, captions
                                  precise report └──► registries (actions, backgrounds, styles, ...)
                                                           └─ audio: TTS + SFX + music bed → mix → mux
```

## The contract: the scene spec

`reel.core.spec` defines the Pydantic v2 models (and `schema/scene_spec.schema.json` is generated
from them). Every string that names something extensible (an action, a background template, a
transition, a style...) is checked against a **registry** by the linter, never hard-coded in the
schema, so plugins can add names without touching schema code. Strict models (`extra="forbid"`)
turn typos into errors instead of silent no-ops.

Time is scene-local seconds; positions are screen fractions where the character's *feet* stand;
transitions **overlap** neighbouring scenes, and the reel length is
`sum(scene durations) - sum(overlaps)` (that is what the 45-60 s budget checks).

## "Templates describe, styles render"

The key design decision. Nothing in a template or an action knows how it will be drawn:

| layer | produces | knows about |
|---|---|---|
| **Action** (`reel.actions`) | per-channel curves over time (`Clip`) | the rig's channels, never a style |
| **Rig** (`reel.core.rig`) | solved skeleton (joint positions/angles) | body proportions (archetype) |
| **Figure builder** (`reel.core.figure`, `props`) | style-agnostic **shape IR** for a posed character | anatomy, outfits, props, expressions |
| **Background template** (`reel.templates`) | shape IR on depth planes + slots + lighting scheme | the set, time of day, mood |
| **Style pack** (`reel.styles`) | pixels | how a *shape* is painted (fill, paper, marker...) |

The IR (`reel.core.ir`) is a flat list of `Shape`s carrying *semantics* - colour **roles**, a
**material**, a **tag** (`head`, `window`, ...), a level of detail and an **elevation** - never pixels.
`flat_vector` fills them, `paper_cutout` adds shadows, cut edges and fibre texture driven by
`elev`/`material`, `stickman` turns characters into marker strokes and backgrounds into line art.
So **one spec renders in every style with no edits** (`reel render spec.json --style stickman`).

## From spec to a frame

1. **Plan** (`reel.core.planner`): for each layer resolve the archetype, props, position (slot or
   `[x, y]`), validate every action's params against its own schema; unknown action → `idle` with a
   warning in lenient mode. Captions that name a `speaker` make that character talk.
2. **Bake** (`reel.core.animation.bake_layer`): lay actions on the timeline, cross-blend them, chain
   root motion (walks continue from where the last one ended), add secondary motion (follow-through on
   hands and head, cloth/hair sway) and return dense per-frame arrays. A frame is then a pure lookup.
3. **Stage** (`reel.core.stage.SceneStage`): build the background graph, then for each frame draw the
   depth planes back to front with **parallax** (`sky 0.1x, far 0.4x, back 0.72x, mid 1.0x, near 1.55x`
   under pans, different growth under dolly), depth-of-field / **rack-focus** blur per plane, characters
   between `mid_back` and `mid_front`. Static plates are rendered once per scene; only characters and
   animated shapes are redrawn.
4. **Transition** (`reel.core.transitions`, style-overridable): blend two worlds during an overlap.
5. **Post-FX** (`reel.core.fx`): bloom → chromatic aberration → colour grade (per-channel LUT built
   from contrast/gamma/lift/gain/warmth) → vignette. This is the part that is **cached**.
6. **Finish**: film grain (animated, seeded) and optional letterbox, then **captions** are drawn on top
   (so they stay crisp), inside the platform safe area, with kinetic-typography presets.

## Rendering and caching

`reel.core.render.Renderer` splits the timeline into **segments** of ≤2 s that never straddle a scene
or transition boundary. Each segment has a key derived from the digest of every frame in it; the
digest of a frame covers everything its pixels depend on (the engine's own source fingerprint, style,
resolution, background spec, camera state, every character's baked pose row, time, caption state...).

* Missing segments are rendered in parallel worker processes (`spawn`), each streaming raw BGRA into
  its own `ffmpeg -c:v libx264` process (closed GOPs, no B-frames, in-band headers).
* Segments land in `~/.cache/reel/segments`, finished world frames in `~/.cache/reel/frames`
  (lossless zlib). Re-rendering after editing one caption therefore re-renders and re-encodes only
  the chunks that show it; unchanged chunks are concatenated byte-for-byte and remuxed into the MP4.
* **Deterministic**: every random choice comes from `sha256`-derived seeds of `(spec seed, stable path)`;
  the same spec + seed produces byte-identical MP4s regardless of worker count.

## Extension points (registries)

Everything extensible lives in a `Registry` (`reel.core.registry`) held by one `Catalog`:
`actions`, `backgrounds`, `styles`, `transitions`, `easings`, `camera_moves`, `sfx`, `archetypes`,
`props`, `caption_styles`, plus `objects` and `assets` for the [asset library](assets.md) (a library character registers as a picture archetype, a place as a
background, an object in `objects`). Decorators register into the global `CATALOG`; plugins (a module, a `.py`
file or a folder; `--plugin PATH` or `REEL_PLUGINS`) are imported the same way. The linter reports exactly
which names a spec uses that are not registered, and `reel manifest` dumps the catalog the LLM prompt reads.

See [adding-an-action.md](adding-an-action.md), [adding-a-style.md](adding-a-style.md) and
[adding-a-background.md](adding-a-background.md).

## Module map

```
src/reel/
  core/        spec · schema · lint · timeline · registry/catalog · easing · rig · animation · planner
               ir · figure · props · stage · camera · captions · transitions · fx · render · cache · ffmpeg
               matchcut · shaping (non-Latin captions) · manifest · docgen · debug
  actions/     locomotion · gestures · expressions  (+ base: @register_action, idle baseline, speech envelope)
  styles/      base (StylePack) · flat_vector/ · paper_cutout/ · stickman/
  templates/   base (BuildContext, @register_background) · palette · abstract · room · street · forest
               rooftop · stage · whiteboard  (+ manifest.json: the catalog the LLM prompt reads)
  audio/       sfx · music · tts · align · mix · pipeline          (see audio.md)
  assets/      model · svg + raster (importers) · art · library (discovery, registration) · sprite · objects · places · preview
               match (words -> assets) · coverage (what a script needs) · gaps · layout (room for wide pictures) · lexicon.json · library/ (the built-in art)   (see assets.md)
  llm/         base (SpecGenerator, Manual) · client (Ollama/OpenAI/Anthropic) · prompt · generator · heuristic · library_plan   (see llm.md)
  cli/         main (typer) · build · pipeline (the shared render flow) · report · scaffold
```
