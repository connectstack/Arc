# The scene spec

A reel is one JSON document. It is validated by Pydantic v2 models
([`src/reel/core/spec.py`](../src/reel/core/spec.py)), the JSON Schema
[`schema/scene_spec.schema.json`](../schema/scene_spec.schema.json) is generated from them
(`reel schema -o schema/scene_spec.schema.json`; `reel schema --enums` pins every registry-bound name to
what is registered *right now*, which is the flavour the LLM prompt embeds), and `reel lint` checks what a
schema cannot: that the things the spec names exist, timing, readability and the 45-60 s budget.

Add `"$schema": "../schema/scene_spec.schema.json"` to get validation and completion in your editor.

## Conventions

* **Time** is in seconds and **scene-local** (`t0 = 0` is the start of that scene).
* **Positions** are screen fractions: `[0, 0]` top-left, `[1, 1]` bottom-right. A character's position is where
  its **feet** touch the ground; `y ≈ 0.62-0.76` keeps captions (bottom 25 %) clear of faces. A string such as
  `"left"` / `"center"` / `"right"` / `"far_left"` / `"far_right"` / `"off_left"` / `"off_right"` or a
  background slot (`"sofa"`, `"crosswalk"`...) may be used instead of coordinates.
* **A layer's `position` is where the character stands at rest**: with `enter_from` it is the *destination*
  (they start off-screen), with `walk`/`run` it is the *starting point*.
* **Transitions overlap** the two scenes: scene *i+1* starts `transition_out.duration` seconds before scene
  *i* ends. Total length = `sum(duration_sec) - sum(overlaps)`, and that must be 45-60 s.
* Names that live in a registry are plain strings; the linter reports the exact list of missing ones.

## Document

```jsonc
{
  "$schema": "../schema/scene_spec.schema.json",          // optional
  "version": "1.0",
  "meta": {
    "title": "…",
    "style": "paper_cutout",                              // paper_cutout | stickman | flat_vector | any registered style
    "fps": 30, "resolution": [1080, 1920], "aspect": "9:16",
    "seed": 42,                                           // same spec + seed => byte-identical video
    "target_duration_sec": 50,                            // 45-60
    "fx": { "grain": 0.5, "vignette": 0.3, "bloom": 0.2, "chromatic_aberration": 0.5,
            "saturation": 1.0, "contrast": 1.0, "warmth": 0.0, "letterbox": 0.0 },   // optional overrides of the style's post-FX
    "safe_area": { "top": 0.10, "bottom": 0.20, "left": 0.07, "right": 0.07 }          // optional caption margins
  },
  "characters": [
    { "id": "mia", "archetype": "kid", "palette": { "shirt": "#2a9d8f" }, "props": ["backpack"],
      "name": "Mia", "voice": "Samantha" }                // name and voice are optional, see below
  ],
  "scenes": [
    {
      "id": "intro", "duration_sec": 5.5,
      "background": { "template": "street", "params": { "time_of_day": "dusk", "mood": "gloomy", "density": 0.6 } },
      "camera": { "moves": [ { "type": "dolly", "from": 0, "to": 0.25, "t0": 0, "t1": 5.5, "ease": "ease_in_out" } ] },
      "layers": [ { "character": "mia", "position": [0.34, 0.70], "scale": 0.92, "depth": "mid", "facing": "auto",
                    "actions": [ { "name": "enter_from", "t0": 0.4, "t1": 2.2, "params": { "side": "left" } } ] } ],
      "captions": [ { "text": "Rainy days are for adventures!", "t0": 3.1, "t1": 5.2, "style": "subtitle", "speaker": "mia" } ],
      "sfx": [ { "name": "chime", "t": 3.1, "volume": 1.0 } ],
      "transition_out": { "type": "crossfade", "duration": 0.6, "params": {} }
    }
  ],
  "audio": { "music": "procedural:playful", "voiceover": "tts", "ducking": true,
             "music_gain_db": -16, "voice_gain_db": 0, "sfx_gain_db": -8, "auto_sfx": true }
}
```

### `meta`
| field | notes |
|---|---|
| `style` | which style pack paints the spec; `reel render --style X` overrides it with no other change |
| `seed` | seeds blinks, gaze, grain, jitter, background scatter, camera shake... |
| `fx` | multipliers/overrides of the style's post-FX; `0` switches an effect off, `letterbox` is the fraction of the height blanked top and bottom |
| `library_gaps` | what the script needed that the [asset library](assets.md) lacked when the spec was planned, one `{kind, name, scenes, character?, stand_in?}` per thing (`kind` is `character`, `object` or `place`; `stand_in` is what is shown instead, absent when it was left out). The renderer ignores it; the linter notes it, Reel Studio lists it with an Add button, and `reel assets fill` swaps in an asset added later |

### `characters`
`archetype` (body/face template: `everyman kid elder hero robot boss`), `palette` (colour overrides by role:
`skin hair shirt shirt2 pants shoes accent eye outline`, hex), `props` (`hat cap glasses scarf backpack briefcase
umbrella phone book coffee flower balloon`), `name`/`voice` (TTS voice selection: `voice` is an engine-specific voice name such as a macOS
`say -v ?` voice or a Piper model; without it `say` picks one that suits the archetype and the name, see [audio.md](audio.md)).

A character may also be one of the library's **pictures** (`cat`, `dragon`, `doctor`, `farmer` ...: see [reference/assets.md](reference/assets.md)):
a drawing posed as one piece. The same actions drive it (walking rocks and hops it, `jump` lifts it, talking squashes it, `face` mirrors it) but
what needs a jointed body (waving, pointing, holding a prop) only takes time, and `reel lint` says so (`PICTURE_LIMIT`). Its `palette` roles are
the ones its drawing marks (`fur`, `skin`, `shirt` ...), listed in the catalog.

### `scenes[].layers[]`
`character`, `position`, `scale` (0-4, 1 ≈ a third of the frame height), `depth` (`background|mid|foreground`: which
parallax plane the character lives on), `facing`, `actions`.
Actions are the registered action library ([reference/actions.md](reference/actions.md)); times are scene-local and
may be clamped by the scene end (a warning). Two root-moving actions that overlap warn: the later one wins.

### `scenes[].objects[]`
Things from the [asset library](assets.md) placed in a scene: a tree, a car, a cake.

| field | notes |
|---|---|
| `asset` | the library object (`reel assets list --kind object`) |
| `position` | `[x, y]` screen fractions of the point it **stands on** (bottom centre of its art unless its anchor says otherwise), or a slot name of the background |
| `scale` | 0-6; at 1 an object is drawn at its own `height` (a person is 575), and the catalog's `size` says how tall that is next to a person |
| `depth`, `layer` | the parallax plane (`background mid foreground`); at `mid`, `behind` (default) or `front` of the characters |
| `facing`, `rotation`, `alpha` | `auto left right` (art is drawn facing right; `left` mirrors it), degrees clockwise, 0-1 |
| `t0`, `t1` | on screen from / until, scene-local seconds (`t1` absent: to the end of the scene) |
| `palette` | colours of the roles its drawing marks (`{"body": "#2a6fdb"}`: a blue car); shades like `body_dark` follow |
| `motions[]` | `{type, t0, t1, from?, to?, amount?, count?, ease}`: `move` (to `[x,y]` or a slot, then stays), `hop` (`count`, `amount` of its height), `float`, `spin` (`amount` turns), `pulse`, `fade` (`from`/`to` opacity), `grow` (`from`/`to` times its size), `shake` |

Unknown names are `REGISTRY_MISSING` errors with the way to add them; `--lenient` leaves an unknown object out. Planners that cannot find
a thing in the library leave it out (or use a stand-in) and record it in `meta.library_gaps`.

### `scenes[].camera.moves[]`
`type` is `pan | zoom | shake | dolly | rack_focus`; `from`/`to` meaning depends on the type
([reference/camera.md](reference/camera.md)). Each move holds its end value afterwards; moves apply in order.
A `rack_focus` move's lens already sits on its `from` depth before `t0` (no snap when the move starts), and any scene with one
renders with depth of field, so the planes away from the focus are soft for the whole scene.

### Match cuts (`reel match-cut`)

A match cut is a hard cut that hides itself: the subject keeps the same screen position, size and camera framing across it. You do not
compute coordinates by hand:

```bash
reel match-cut spec.json walk_in kitchen mia -o spec.matched.json
```

bakes the outgoing scene (`walk_in`), reads where `mia` ends up and what the camera is doing, rewrites the incoming scene (`kitchen`)'s
layer position/scale and opening camera so they start from exactly that, and makes the transition a plain `cut`. The two scenes must be
adjacent (`--no-camera` leaves the camera alone); scenes can be given by id or index. Run `reel lint` on the result as usual.

### `scenes[].captions[]`
`style` is `subtitle` (on the ground line, just inside the bottom safe margin, so it covers feet and floor rather than faces; words pop
in, the spoken word is highlighted), `title` (upper third, letters drop in) or `shout` (huge slam-in, placed above the characters' heads). `speaker` makes that character talk (lip-flap from the TTS word timings) and `speak`
overrides which captions are read aloud (default: subtitles). `anchor` is `auto | top | center | bottom`. Everything is laid out
inside the safe area and wrapped to fit.

**Any script.** Latin text (accents included) is drawn with the style's display font. A word that font cannot draw (Hindi and other Indic
scripts, Arabic, Hebrew, Thai, CJK, emoji, or a symbol the font lacks) is shaped by skia's Paragraph engine (HarfBuzz + ICU) in a system fallback
font, so conjuncts, vowel signs and Arabic joining come out right; it keeps the same fill, outline, shadow and plate as the rest of the caption.
CJK text has no spaces, so it wraps between characters, and the line-start / line-end rules (kinsoku) hold: closing punctuation, small kana and
the prolonged-sound mark never start a line, an opening bracket never ends one (the previous character moves down with it). Thai, Lao, Khmer
and Myanmar are written without spaces too: a space-separated phrase stays together, and only a phrase wider than the whole line is wrapped,
between clusters (a consonant with its vowel signs, a conjunct, a Thai syllable's prefix vowel), never inside one; there is no dictionary, so such a
break can fall inside a word. Right-to-left text is mirrored per line (first word on the right) and each Hebrew or Arabic word carries its own
punctuation on the correct (left-hand) side. A caption's highlight and pop animation work per whitespace-separated word, so a long CJK or Thai
sentence without spaces is *one* word to them: add spaces (or split the caption) where you want it to light up piece by piece. The fallback fonts
come from the machine, so such captions look slightly different across machines (and the golden-frame tests only use Latin text); installing a font changes the
cache keys, so cached frames never keep the old fallback.

**Text hygiene.** Characters nothing can draw or speak are removed when a spec is loaded: lone UTF-16 surrogates (half an emoji, usually a string
cut by a `slice`) are dropped and control characters become spaces. `reel lint` reports each one as a `BAD_TEXT` warning with its JSON path.

### `audio`
`music`: a file path, `procedural` / `procedural:<mood>` (a generated bed; moods `upbeat calm playful tense epic mysterious sad corporate`,
checked by the linter, plain `procedural` picks one from the style) or `null`. `voiceover`: `tts` (speak the captions with
a local engine), `file` (use `voiceover_file`) or `none`. `ducking` lowers the music under voice. `auto_sfx` adds footsteps/landings
emitted by the actions themselves.

## What `reel lint` checks

Run `reel lint spec.json` (`--json` for machines, `--strict` makes warnings fail, `--style X` lints as if rendering with that style).
It always completes and reports everything at once; registry problems are also collected into a **"missing from the registry"**
list: exactly what to add (`reel new-action <name>`, a template file, ...).

| code | severity | meaning |
|---|---|---|
| `FILE` / `JSON_SYNTAX` | error | the file cannot be read / is not valid JSON (line/column given) |
| `SCHEMA`, `SCHEMA_MISSING`, `SCHEMA_UNKNOWN_FIELD` | error | model validation; unknown fields get "did you mean" |
| `REGISTRY_MISSING` | error | an action / background / transition / style / sfx / archetype / prop / easing / camera move / caption style that is not registered (with the remedy) |
| `PARAMS_INVALID` / `PARAMS_UNUSED` | error / warn | params fail the registered thing's own schema (exact path + allowed names) |
| `DURATION_BUDGET` | error | total length outside 45-60 s (counts transition overlaps) |
| `DURATION_TARGET` | warn | computed length differs from `target_duration_sec` by more than 1.5 s |
| `CHARACTER_UNDEFINED`, `DUPLICATE_ID` | error | unknown character, repeated id |
| `POSITION_SLOT` | error | a named position the background does not provide (lists the available slots) |
| `TIME_RANGE` / `TIME_OVERFLOW` | error / warn | `t1 <= t0`; a window that runs past the scene end |
| `TRANSITION_ZERO` / `TRANSITION_CLAMPED` / `TRANSITION_OVERLAP` / `TRANSITION_LAST` | warn / info | transitions that behave like cuts, get clamped, or are ignored |
| `ACTION_OVERLAP`, `ACTION_TOO_SHORT`, `ACTION_CHECK` | warn | overlapping root motion, a window too short to read, action-specific advice (e.g. `pick_up` of an item the character does not own) |
| `CAPTION_LONG` / `CAPTION_FAST` / `CAPTION_OVERLAP` / `CAPTION_EMPTY` | warn | readability (≤140 chars, ≤5 words/s, no stacked captions) |
| `PALETTE_COLOR` / `PALETTE_ROLE` | error / warn | bad hex colour, unknown colour role (for a library picture: the roles its drawing marks are listed) |
| `OBJECT_MOTION` / `OBJECT_CLUTTER` | error / warn | an unknown object motion or one missing its `to`; more than 14 objects in a scene |
| `PICTURE_LIMIT` | info | a library picture asked for something only a body does (wave, point, hold a prop): it takes the time and keeps still |
| `LIBRARY_GAP` / `LIBRARY_GAP_FILLED` | info | `meta.library_gaps`: the library still lacks it (with how to add it), or has it now (`reel assets fill` swaps it in) |
| `FILE_MISSING`, `AUDIO_CONFIG` | error / warn | referenced music/voiceover file does not exist; inconsistent audio settings or an unknown `procedural:<mood>` (with a did-you-mean) |
| `ASPECT`, `FPS`, `EMPTY_SCENE`, `UNUSED_CHARACTER`, `POSITION_RANGE`, `CAMERA_MOVE_INVALID` | warn / info | housekeeping |

Programmatic use: `from reel.core.lint import lint_file; report = lint_file("spec.json"); report.ok; report.missing()`.
