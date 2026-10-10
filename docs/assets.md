# The asset library

A script can mention anything: a dragon, a village, a rickshaw. The engine draws a fixed set of characters and
backgrounds itself; the **asset library** is how everything else gets in. An asset is a picture or drawing plus a few facts
about it. There are three kinds:

| kind        | what it is                                   | where a spec uses it                         |
|-------------|----------------------------------------------|----------------------------------------------|
| `object`    | a thing in a scene: car, tree, cake, phone   | `scenes[].objects[]` (`"asset": "car"`)       |
| `character` | a creature or person that acts: cat, dragon  | `characters[].archetype` (`"archetype": "cat"`) |
| `place`     | a whole set: beach, village, classroom       | `scenes[].background.template` (`"template": "beach"`) |

Reel ships a built-in library (`reel assets list`; the full list is in [reference/assets.md](reference/assets.md)) and you
add your own next to it. Your assets work exactly like the built-in ones: the planners (the LLM and the offline one) see
them, the linter checks them, all three styles draw them, and the cache knows when their art changes.

```json
{
  "characters": [{"id": "pal", "archetype": "cat"}],
  "scenes": [{
    "id": "s1", "duration_sec": 6,
    "background": {"template": "beach", "params": {"time_of_day": "dusk"}},
    "objects": [
      {"asset": "tree", "position": [0.16, 0.80], "scale": 0.9},
      {"asset": "car", "position": [0.5, 0.82], "motions": [{"type": "move", "t0": 1, "t1": 4, "to": [1.2, 0.82]}]}
    ],
    "layers": [{"character": "pal", "position": "center", "actions": [{"name": "walk", "t0": 0, "t1": 4}]}]
  }]
}
```

## Adding your own

Any of these works; they stack (a later one wins when two use the same name).

1. **Reel Studio:** Library → *Add asset*. Drop a file, pick the kind, check the three previews, save. It goes in
   the workspace's `assets/` folder and is available at once.
2. **A folder:** put files in `assets/` next to your spec (or `assets/characters`, `assets/objects`, `assets/places` to set
   the kind by folder). `reel render`, `lint`, `storyboard`, `build` and `serve` read it without any flag.
3. **`--assets DIR`** (repeatable) or **`REEL_ASSETS=dir1:dir2`** for a shared library.
4. **`reel assets add FILE --kind character --name unicorn --tags "unicorn,यूनिकॉर्न"`** copies the file into `./assets`
   with a small sidecar and tells you how to use it.

Supported art: **SVG** (best: it is redrawn natively by every style and can be recoloured) and **PNG / JPG / WebP**
(up to 12 MB). A picture with a plain background (white, grey, any flat colour) gets that background removed
automatically; a PNG with transparency is used as it is.

`reel assets check` reads every asset and tells you what is wrong or will not be drawn; `reel assets preview` draws a
contact sheet in the three styles (add `--true-scale` to see each next to a person at the size a scene gives it).

### The sidecar

Next to `unicorn.svg` an optional `unicorn.json` holds the facts. Every field is optional:

```json
{
  "name": "unicorn",
  "kind": "character",
  "summary": "A white cartoon unicorn with a rainbow mane, side view",
  "tags": ["unicorn", "unicorns", "यूनिकॉर्न", "yunikorn"],
  "height": 420,
  "anchor": [0.5, 0.94],
  "facing": "right",
  "credit": "drawn by me, CC0"
}
```

| field     | meaning |
|-----------|---------|
| `name`    | the id used in specs: lower-case letters, digits, underscores, starting with a letter (default: the file name) |
| `kind`    | `character`, `object` or `place` (default: the folder it is in, else `object`) |
| `summary` | one line, what it is. The LLM planner reads it, so say what the thing is, not how it was drawn |
| `tags`    | the words a script may use for it: synonyms, plurals, other languages. **The planners match on these.** |
| `height`  | how tall it is drawn, in design px at scale 1. **A person is 575.** Think "metres × 340": a chair 300, a door 700, a phone in a hand 140, a coin 70 |
| `anchor`  | the point that sits on the position you give it, as fractions of the art's box. `[0.5, 1]` (default) is the middle of the bottom edge (things that stand); `[0.5, 0.5]` is the centre (things that float) |
| `facing`  | which way the art looks: `right` (default), `left`, or `none` for symmetric art. The engine mirrors it to face the other way |
| `fit`     | SVG only: take the art's box from what is drawn (`content`, default) or from the `viewBox` |
| `cutout`  | pictures only: `true`/`false` forces or forbids removing a plain background (default: only when there is no transparency) |
| `shadow`  | paper cutout: does it cast a soft shadow? Default: yes, unless it floats (its `anchor` is high in the picture: the sun, a balloon) |
| `credit`  | who made it, and the licence |
| `place`   | places only, see below |

A place's `place` block says where characters stand:

```json
"place": {
  "ground_y": 0.80,
  "horizon": 0.56,
  "perspective": 1.0,
  "slots": {"left": [0.27, 0.80], "center": [0.5, 0.82], "right": [0.73, 0.80], "well": [0.62, 0.78]}
}
```

`ground_y` is the line the characters' feet are on (a fraction of the frame height), `horizon` where the sky meets the
ground, `perspective` how much characters grow towards the camera, and `slots` are named standing spots a script can use
(`"position": "well"`). `left`, `center` and `right` always exist.

## Drawing assets that look right

The importer reads the practical subset of SVG that icon sets and editors export: `path` (every command, arcs too),
`rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, groups, `transform`, `use`, solid colours, opacity, linear
gradients (two colours), and CSS in `style=""` or a `<style>` block. It **skips text, filters, masks, clip paths,
embedded pictures** and says so in `reel assets check` (convert text to outlines in your editor).

Because the drawing becomes the engine's own shape list, the three styles repaint it: paper cutout adds layered paper,
shadows and a texture; flat vector fills it; stickman turns it into marker outlines on white. A few attributes steer that.
They can sit on any element and are inherited by what is inside it.

| attribute | effect |
|-----------|--------|
| `data-role="body"` | this colour can be changed per use: `"palette": {"body": "#2a6fdb"}` on the object or character. Pieces of one role may differ a little (a slightly darker leg): each keeps its own step from the role's first colour. A role named `body_dark` or `body_light` follows `body` (same step darker or lighter, the new hue), unless the palette names it too |
| `data-stroke-role="rays"` | the same for the colour of a stroke |
| `data-lod="1"` | fine detail, left out by the stickman style. Anything small is marked automatically; the 8 biggest shapes never are. `data-lod="0"` forces a small thing to stay (**eyes**: an animal without eyes looks dead in line art) |
| `data-shadow="false"` | no paper shadow under this piece (stripes, windows, spots painted on a bigger shape) |
| `data-elev="2"` | how far above the one below it this piece floats in paper cutout (default 1.5, details 0.6) |
| `data-material="emissive"` | a light source: a lamp, the sun, a screen. It is not darkened at night |
| `data-tag`, `data-plane` | places: see below |

Rules of thumb that make assets look like they belong to the built-in ones:

* **Flat, friendly shapes.** 10 to 35 pieces, back to front: the big silhouette first, then the details. No outlines; use a
  stroke only for thin things (whiskers, branches, strings) with `stroke-linecap="round"`.
* **Two tones per material.** A base colour, plus a darker piece for the shaded side and a lighter one for a highlight.
  Make the base, its dark and its light roles (`body`, `body_dark`, `body_light`) so the shading survives a recolour.
* **A small palette.** 4 to 8 colours, saturated but not neon, with `#23262e` as the "ink" for eyes, wheels and tyres.
* **The identity must survive line art.** The stickman style draws only the lod-0 shapes with outlines, so the silhouette
  and the main parts must be lod 0; only decoration is lod 1.
* **Face right.** Draw side views looking to the right (or set `"facing": "left"`).
* **Size it against a person** (575 px) with `reel assets preview NAME --true-scale`.
* **Keep it simple to recolour.** Mark the 1 to 3 colours a script might change ("a red car", "a blue shirt"): `body`,
  `accent`, and for people `skin`, `hair`, `shirt`, `pants`.

### Characters

A character asset becomes a **sprite**: the whole picture is posed as one piece. The existing actions drive it: walking
rocks and hops it with the stride, `jump` lifts it, talking and laughing squash it, `face` mirrors it, captions with its id
as the speaker make it talk. What a body does and a picture cannot (waving an arm, pointing, holding a prop) takes its time
but the character keeps still, and `reel lint` says so. Draw a **whole body standing on the ground**, with the anchor at the
feet (`[0.5, 0.94]`). Recolourable roles are the ones the drawing marks.

Pictures come in every width, so a planner makes room for them: when the pictures of a scene would stand on one another (a farmer, a
cow and a dog in one row), the outer two move to the `far_left` / `far_right` slots and, if that is not enough, the whole scene is
scaled down together by `layers[].scale` (never below half, so the farmer stays taller than the dog). A picture that is wider than the
frame is narrowed. This applies to the offline planner and to a model's plan alike (`normalize_spec`), and to a stand-in swapped for a
wider asset by `reel assets fill`. Scenes of engine bodies are never touched, and a spec you write by hand is yours: set `scale` yourself.

### Objects

Objects sit in `scenes[].objects[]` with `position` (where the anchor goes), `scale`, `depth` (`background` | `mid` |
`foreground`), `layer` (`behind` | `front` of the characters), `facing`, `rotation`, `alpha`, `t0`/`t1` (visible from/until)
and `palette`. They can move: `move`, `hop`, `float`, `spin`, `pulse`, `fade`, `grow`, `shake` (see `docs/spec.md`).

### Places

A place is a 1080×1920 background. Make the SVG's `viewBox` `0 0 1080 1920` and group the drawing by depth so the camera
gets real parallax: `<g data-plane="sky">`, `far`, `back`, `mid_back`, `mid_front`, `near` (back to front; anything outside
a group goes on `far`). Leave the lower-middle (the characters' ground, roughly y 1300 to 1750) calm: characters stand
there and subtitles sit over it. Keep the top fifth quiet too; titles go there. A single flat picture (PNG, JPG) also
works as a place; it sits on the far plane.

Places take the same `params` as the built-in sets for the time of day: the scene's lighting tints the art.

## Names

Names are unique across the three kinds and may not reuse one of the engine's own backgrounds or characters
(`forest`, `room`, `hero`, ...): `reel assets check` says so. An asset may replace another *asset* of the same name, so a
project can swap a built-in for its own version.

## How a script finds assets

Every planner reads the library through the assets' **tags** (and names), so whatever you add is found the same way as what ships.

* **A model (OpenAI, Claude, Ollama)** gets the library in its prompt: each object, picture character and place with its size next to a person, the
  colours it can change and the words a script may call it (Hindi included), so a script that says "गाँव" or "gaay" lands on `village` or `cow`.
  The prompt says not to invent names: when the script needs something the library lacks, the model uses the closest entry (or leaves the object out)
  and reports it in `meta.library_gaps`. An object name the model gets wrong is resolved through the tags (`"automobile"` becomes `car`) or recorded as a gap,
  without spending a repair round.
* **The offline planner** matches the same words: a place named in a scene is used, a creature or role the library draws ("Max the dog", "a farmer") becomes
  that character, objects a scene names are placed clear of the cast (a vehicle in a sentence that says it drives crosses the frame). A word several things
  answer to ("fruit", "vehicle") is never guessed, and a word that names a place ("school", "hospital") draws the place and not also
  the building object of the same word (unless the text says "school building"). In a script that is not in English (Hindi, say) there is no "the dog" for the rules to read, so a
  character it names ("किसान", "गाय") is cast as the asset that answers to the word, under the same rule as English role nouns: it counts when it
  recurs, or when nothing else is named.
* **What the library lacks.** `reel assets coverage script.txt` (and the New reel page) reads a script before anything is planned and lists what the
  library can draw and what it cannot ("not in the library: castle, sword"), using a lexicon of everyday visual things in English, Hindi and Hinglish
  (`src/reel/assets/lexicon.json`). Planned specs carry the same list in `meta.library_gaps`, and `reel lint` prints it with the command that adds each one.
  It is advice, never a gate: a word the lexicon does not know is never reported.
* **Adding it later.** Add the asset (`reel assets add`, or the Add asset button next to each missing item in Reel Studio), then `reel assets fill spec.json --write`
  swaps it into the spec: the stand-in character becomes the new one, the scenes get the new place, the missing object is placed on a free spot.

## In Reel Studio

* **Library** shows the characters, objects and places (Objects is its own tab; Characters and Backgrounds list the engine's own and the library's) with a
  *From* filter (all, built in, yours), a search that also matches the words (Hindi too), and pictures drawn by the same renderer the video uses, in the style you pick.
  **Add asset** (or drop a file on the page) reads the file, shows it in all three styles and next to a person at true size while you set its kind, name, words,
  size, anchor and facing, then keeps it in the workspace's `assets/` folder. Assets of your own can be edited and deleted there; built-in ones are read-only
  (add one of yours with the same name to override it).
* **New reel** checks the script as you type and lists what the library can draw and what it cannot, each missing item with an *Add asset* button pre-filled with its words.
  After planning, anything still missing is shown again; adding it swaps it into the reel before the studio opens.
* **Build from scratch** (Projects, or under the script on New reel) starts an empty reel and opens the studio on its **Build** tab: pick a background, cast characters,
  add objects, then give them actions, sounds and words, one scene at a time, all from the library (your own assets included). See [ui.md](ui.md#in-the-studio).
* **The studio** treats objects like characters: add them from the library panel (or drag them onto the stage), drag and size them on the stage, set when they appear
  and how they move on the timeline (one lane per object, a clip per motion), and recolour the parts their drawing marks.
  A picture character is cast from the *Add a character* menu (its pictures are listed under their own heading); its inspector offers only the colours its
  drawing marks and no props, and swapping a body for a picture drops the colours and props that no longer apply.

## Limits

* A **picture character** is one piece: it cannot wave an arm, point or hold a prop (the action takes its time and the linter says so). For those, the jointed built-in people
  are the right tool.
* SVG **text, filters, masks, clip paths, patterns, radial gradients and embedded pictures** are not drawn (`reel assets check` says so). Convert text to outlines.
* Paper cutout shows each piece with a slight hand-cut wobble: thin tall filled shapes (poles, slats) look better drawn as strokes, and round things from circles; a single path
  made of several separate pieces is warped as one, so draw windows or wheels as separate shapes.
* A **photo** works as a character or object once its plain background is removed; a busy background is not removed (use a PNG with transparency).
* Names are unique across the three kinds, and the engine's own names (`forest`, `hero`, ...) are taken.
* Files are read defensively, so that someone else's asset folder cannot harm a run: a file is at most 12 MB, a picture at most 40 megapixels, an SVG at most 20,000
  elements nested 24 deep, with at most 64 KB / 1,000 rules of CSS (`<style>`); a symbolic link is not followed, a sidecar's `file` must be the name of a file next to it,
  and a file that cannot be read is reported (`reel assets check`, the Library page) and left out, never allowed to stop a start or to replace a built-in asset.
