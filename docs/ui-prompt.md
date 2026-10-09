# Reel Studio: the brief the UI was built from

> **Status:** this brief has been built: run `reel serve --open` (see [docs/ui.md](ui.md) for what exists). It is kept as the
> original specification, and as a ready-to-paste prompt if you want a *different* front end on the same engine.

This file is a ready-to-paste prompt for an AI builder (Claude Code, Cursor, v0, Lovable, Bolt ...) that
produces a beautiful local web UI for the `reel` engine, which on its own is a command line (`reel build|render|lint|doctor|voices ...`)
plus a static HTML storyboard (`reel storyboard`).

* **Tool that can run Python and read this repo** (Claude Code, Cursor): paste **THE PROMPT** below; it builds the frontend and a thin
  `reel serve` API on top of the existing library.
* **Design / front-end-only tool** (v0, Lovable, Bolt, Claude artifacts): paste the same prompt; it builds the whole UI against a built-in
  mock adapter (section 7), so every screen works without a backend, and the real API can be wired in afterwards.
* **Small prompt limit**: use **THE SHORT VERSION** at the end.
* If your tool accepts images, attach `docs/img/three_styles.jpg` (the same spec in the three styles) as a visual reference.

---

## THE PROMPT

You are a senior product designer and full-stack engineer. Build **Reel Studio**: a polished, production-quality, local-first web app that
lets a non-technical creator turn a plain-text script into a 45-60 second vertical (9:16) animated reel, fine-tune it on a timeline with a
live preview, give it voices, and export an MP4. The animation engine already exists (a Python CLI + library called `reel`); you are building
the UI and the thin local API between them. If you cannot run Python, build the frontend against the mock adapter in section 7 so that every
screen and state is fully demonstrable; the real API can be wired in afterwards.

### 1. What `reel` is (design the right things)

* **Input** a JSON "scene spec"; **output** a 1080x1920, 30 fps H.264 MP4 with sound, 45-60 s. The same spec renders in three visual
  styles: `paper_cutout` (layered paper, shadows, wobble), `stickman` (marker line art on whiteboards), `flat_vector` (bold cel shading).
* **Pipeline**: script text -> planner (an instant offline planner, or an LLM: local Ollama, OpenAI, Claude) -> spec -> lint (returns a list of
  issues with hints; never crashes) -> render (deterministic; unchanged 2-second chunks come from a cache, so edits re-render fast) -> audio
  (voice-over from a TTS engine, procedural music, 41 sound effects, ducking, loudness -16 LUFS).
* **Offline by default.** Two things are online and strictly opt-in: LLM planning (OpenAI / Claude) and ElevenLabs voices (billed per
  character). API keys live only in environment variables or a git-ignored `.env`; the UI never asks for, stores or shows a key.
* **Measured speeds, so design for them**: one preview frame renders in 20-140 ms (scrubbing can be live); a 50 s draft (360x640) renders in
  about 9 s cold and under 1 s when cached; Full HD takes 20-60 s cold depending on the style; progress is reported per frame.

### 2. Users and jobs

1. "I have a script. Make me a reel." Paste, pick a style, get a first cut in under a minute.
2. "Make it better." Live preview + timeline + inspector; fix problems; swap characters, backgrounds, actions; tune camera and captions.
3. "Give it a voice and ship it." Choose a voice engine, assign voices per character, see what will be billed (online engines), export a draft or full HD.

The primary user is a creator, teacher or marketer, not an engineer. Power users get a JSON tab and keyboard shortcuts.

### 3. Deliverables and stack

**Part A, frontend (required).** React 18 + TypeScript (strict) + Vite + Tailwind CSS + Radix UI primitives (shadcn/ui style) + Zustand
(state) + TanStack Query (server cache) + dnd-kit (drag) + CodeMirror 6 (JSON tab, lazy-loaded) + Lucide icons. Fonts bundled locally (Inter
variable, JetBrains Mono): no CDN, no telemetry, runs fully offline. A typed API client with an `HttpAdapter` and a `MockAdapter` (section 7).
Vitest tests for the timeline math and the spec reducer; a Playwright smoke test of the main flow against the mock.

**Part B, local API (if you can run Python).** A new command `reel serve [--port 8765] [--open] [--workspace DIR]` (FastAPI + uvicorn, optional
extra: `pip install -e ".[ui]"`) that serves the built frontend and the `/api` routes of section 7 by calling the existing library
(`lint_data`, `generate_spec`, `Renderer`, `prepare_audio`, `get_tts_engine`, `DiskCache`). Bind to 127.0.0.1 only, per-launch random token,
Host-header allow-list, no CORS, one render at a time, every render in its own OS process (so Cancel works and the server stays responsive).

If you can read the repository, README.md, docs/spec.md, docs/audio.md, docs/reference/*.md, schema/scene_spec.schema.json and
src/reel/templates/manifest.json are authoritative; this prompt summarises them.

### 4. Design language: "calm, precise creative tool"

Think Linear's clarity x Figma's panels x a good video editor's timeline. The content is colourful, the chrome is quiet. Dark by default plus a
light theme (follow the system, with a toggle).

* **Tokens** (CSS variables; Tailwind maps to them).
  Dark: bg `#0B0D12`, panel `#12151C`, raised `#181C25`, hover `#1E232E`, border `#262C38`, border-strong `#343C4B`, text `#E8EBF2`, muted `#9099AA`, faint `#666F80`.
  Light: bg `#F5F6FA`, panel `#FFFFFF`, raised `#FFFFFF`, hover `#EEF0F6`, border `#E2E5EE`, border-strong `#CDD2DF`, text `#12151C`, muted `#5B6477`, faint `#8A93A5`.
  **One accent**: violet `#7C6CFF` (dark) / `#5B4BE0` (light); hover a touch lighter; selection/focus tint = accent at 14 % opacity.
  Status: success `#34D399`, warning `#FBBF24`, error `#F87171`, info `#60A5FA`, always paired with an icon and text, never colour alone.
* **Type**: Inter, 13 px base UI, 12 px secondary, 14/16 in forms and dialogs, 20/28 headings (600, -0.01em tracking). JetBrains Mono for
  timecodes, JSON and paths. `tabular-nums` on every number that changes.
* **Shape and space**: 4-pt grid; controls 32 px high (36 in dialogs); radii 6 chips, 8 controls, 12 cards/panels, 28 phone frame; 1 px hairline
  borders; elevation only on floating layers (popover, menu, dialog), soft and static; thin overlay scrollbars.
* **Craft details**: primary button = accent fill, white text, 1 px inner top highlight; secondary = raised fill + border; toolbar buttons ghost.
  Panel headers 40 px with an 11 px/600 uppercase muted label. Chips 22 px high with a 14 %-tinted fill and an icon. Focus ring 2 px accent,
  2 px offset. Timeline clips: 28 px high, 6 px radius, a 3 px colour bar on the left edge, 12 px/500 label with icon; hover +6 % lighter;
  selected = 2 px accent outline with resize handles. Playhead: 1.5 px accent line with a flag showing the timecode. Style cards: 9:16 thumbnails,
  12 px radius, selected = 2 px accent ring + check badge. Behind the phone stage, a faint radial vignette in the panel colour.
* **Motion**: functional only: progress bars, the playhead, 120-160 ms ease-out on hover/press/open. **No decorative or idle animation** (no
  looping gradients, floating shapes, shimmer, parallax, confetti); skeleton placeholders are static. Honour `prefers-reduced-motion`.
* **Imagery**: the product's own frames are the hero (style cards and project thumbnails are real engine renders). Empty states use small flat,
  single-colour SVG illustrations in the accent tint. No stock photos, no emoji as icons.
* **Voice**: friendly, short, concrete. An error says what happened and what to do (reuse the engine's `hint`).

### 5. Screens

**App shell.** Slim left rail (Projects, Studio, Voice & Audio, Library, Health) with a project switcher. Top bar: editable title, saved/unsaved
indicator, style switcher (three segmented thumbnails), Undo/Redo, a **Render** split button (Draft, Standard, Full HD, Custom...), theme
toggle, command palette (Cmd/Ctrl+K: search scenes, characters, actions, commands).

**5.1 Projects (home).** Grid of project cards: real frame thumbnail (9:16), title, style chip, duration chip (red outside 45-60 s), lint badge
(ok / n errors / n warnings), last edited, a menu (duplicate, rename, export JSON, delete with confirm). A prominent **New reel** with three
entries: *From a script* (wizard), *From an example* (a 50 s story, a 45 s explainer), *Open a spec file* (drop a .json). A recent-renders strip.

**5.2 New-reel wizard** (three steps).
1. *Script*: a large editor with live counters (words, about 2.5 words per second -> estimated seconds), a "Try an example" menu (English and
   Hindi samples), a hint that `Name: line` writes dialogue.
2. *Look and planner*: (a) **Style**: three large cards showing the same sample frame rendered in each style; (b) length slider 45-60 s;
   (c) **Planner** segmented: Offline planner (default, instant) | Ollama (list the installed local models) | OpenAI | Claude. An unavailable
   option is disabled with the exact reason ("no key found: set OPENAI_API_KEY in your shell or .env"), never a key field. Online options show
   "sends your script to api.openai.com" with a first-use consent checkbox. (d) Advanced: seed (dice), repair attempts 0-5, "enrich" switch.
3. *Generating*: a calm progress view with the engine's notes as a checklist ("Planned 10 scenes, 3 characters", "Rescaled scene durations 54.3 -> 50.0 s"),
   then it opens the result in Studio. On failure: the lint report and "Edit the JSON manually".

**5.3 Studio** (the core). Desktop layout: left panel 280 px, centre (preview over timeline), right inspector 340 px; collapsible, resizable,
layout remembered.

* **Preview**: a 9:16 stage with a soft bezel (no fake notch). Frames come from the API at 0.5 scale (540x960), crisp on retina. Overlay toggles:
  safe area (the spec's `safe_area`, shaded to show platform-UI zones), thirds grid, character handles. **Scrubbing is live**: debounce, abort stale
  requests, keep showing the last frame until the next arrives (no flicker), a tiny "rendering..." chip after 300 ms. Transport: play/pause
  (Space), step 1 frame (Left/Right), 1 s (Shift+Left/Right), previous/next scene (Up/Down), loop between In/Out, timecode `00:12.40 / 00:50.00`
  and the frame number in mono. *Play* is a muted draft playback (about 12 fps from prefetched frames); a **Render preview with sound** button
  renders the 360x640 MP4 with audio and plays it in a `<video>` synced to the playhead.
  Direct manipulation: drag a character to set `position` (feet), snapping to the background's named slots (left, center, right, far_left,
  far_right, plus set-specific ones such as door, window, sofa) with visible guides; a corner handle sets `scale`; depth toggle
  (background / mid / foreground); facing (auto / left / right). Actions that move the character (walk, run, enter_from, exit_to, jump) draw their
  path as a dashed arrow.
* **Timeline** (bottom, resizable): a global time ruler; zoom with Cmd+wheel / pinch, pan with wheel/drag; draggable playhead; snapping to 0.1 s,
  to clip edges and scene boundaries (hold Alt to disable). Lanes: **Scenes** (blocks tinted by background template, name + duration, draggable
  edges, transition chips between them: click to edit type/duration); **Camera** (pan, zoom, dolly, shake, rack_focus clips); one **Character lane per
  layer** of the selected scene, **tinted with that character's shirt colour** (action clips show name and a params summary; resize = t0/t1, never
  shorter than the action's minimum; striped overlap warning); **Captions** (text clips, speaker avatar, style icon); **SFX** (markers; click
  auditions); **Audio** (music bar with its mood; voice and SFX waveforms after audio is prepared; ducking as a dip curve).
  A **duration budget bar** above the ruler spans 0-60 s with a marked 45 s minimum: green inside 45-60, amber within 3 s of an edge, red outside,
  and a one-click **Fit to 50 s** that scales scene durations proportionally.
  Editing: click to select, Shift multi-select, drag to move (across lanes where valid), duplicate (Cmd+D), split at playhead (S), delete,
  nudge (Alt+Left/Right = 1 frame), copy/paste between characters, right-click menu, unlimited undo/redo (Cmd+Z / Shift+Cmd+Z) over every spec
  change with a history popover.
* **Left panel** tabs: *Scenes* (reorderable, real mid-scene thumbnails, add/duplicate/delete); *Cast* (character cards with archetype art, name,
  props; add character); *Script* (the original script, each line linked to its caption); *Library* (5.6).
* **Inspector** (right), context-aware and **generated from the catalog** (section 6) so every parameter gets the right control: enum -> segmented
  control (4 options or fewer) or select; number with min/max -> slider + numeric field; boolean -> switch; vector -> picker on the stage; string -> text
  field; the catalog description as one-line helper text; a reset-to-default per field. It edits:
  *Reel* (title, style, fps 12-60, seed, post-FX sliders: grain, vignette, bloom, chromatic aberration, saturation, contrast, warmth -1..+1, letterbox
  0-0.3, each with a "style default" state; safe-area margins) | *Scene* (id, duration, background as 7 visual cards with day/dusk/night thumbnails + its
  params, notes, transition out) | *Character* (archetype as 6 cards, a colour picker per palette role: skin, hair, shirt, shirt2, pants, shoes, accent, eye,
  outline; props as 12 chips; name; voice) | *Layer / action clip* (character, position, scale, depth, facing; action picker grouped by category
  with one-line summaries, then its params) | *Caption* (text with a live count, style subtitle/title/shout each with a mini preview, speaker, speak-aloud
  switch, anchor; text may be Hindi, Arabic, Hebrew, Thai, CJK or emoji, so use `dir="auto"`) | *Camera move / SFX / Transition* (type, from/to controls
  that fit the type, easing from 17 curves with a small curve preview, SFX name with a play button and volume).
* **Problems** (bottom drawer, badge in the status bar): live lint (debounced 400 ms), grouped by severity; each row = icon, message, the JSON path
  as a breadcrumb ("Scene 2 > Pip > talk"), the engine's `hint` as a second line, and a **Go to** button that selects the offending item. A missing
  registry name offers "Replace with..." (closest names).
* **JSON** tab: a schema-validated editor (the JSON Schema comes from the API), two-way synced with the visual editor (valid edits apply, errors show
  inline), diff against the last save, copy/download.
* **Status bar**: engine health dot, workspace path, lint summary, render queue.

**5.4 Voice & Audio.**
* *Engines* (radio cards): **Piper** (neural, needs a voice model), **macOS Say**, **Placeholder** (a synthetic babble for lip-sync tests),
  **ElevenLabs** (online). Each shows a live status chip: "ready", "needs a voice model", "no key found", "key OK - plan creator - 98,800 characters
  left". The ElevenLabs card carries an **Online - sends dialogue to api.elevenlabs.io - billed by ElevenLabs** badge, a model select
  (eleven_multilingual_v2 by default; eleven_v4, eleven_flash_v2_5) and a **Check account** button (it spends no credits).
* *Cast voices*: a table, character -> voice select (names and traits from the API; the automatic default reads "Auto, fits Mia") with a play
  button to audition; for ElevenLabs the button shows its cost ("about 26 characters") and only runs on an explicit click.
* *Cost and cache preview* (online engines): "12 lines, 9 already generated (free), 3 new -> 418 characters will be billed", updating live as
  captions change. **Generate voice** runs a job, then shows voice / music / SFX lanes with waveforms, LUFS and peak, warnings (overlaps, speech running
  past its scene) and the **retimed-captions diff** ("Caption 3 now ends at 4.1 s").
* *Music and mix*: none | procedural with a mood picker (upbeat, calm, playful, tense, epic, mysterious, sad, corporate; 10 s audition) | file path;
  gains (music -16 dB, voice 0, SFX -8; range -60..+12), ducking switch, auto-SFX switch.

**5.5 Export.** A dialog from the Render split button. Presets: **Draft** 360x640 (fast), **Standard** 540x960, **Full HD** 1080x1920;
advanced: range from the In/Out markers, quality (CRF 14-30), size cap, workers, seed, strict vs lenient lint, include audio. Before it starts it shows
the plan ("31 of 35 chunks cached, about 4 s"). Progress view: frames done/total, ETA, chunk counters, a live log, **Cancel** (confirm; nothing
half-written is left). Result card: a `<video>` player, size, bitrate, duration, chunks reused, audio notes (for example billed characters), Download,
Reveal in Finder / copy path, Render again. A render-history list.

**5.6 Library.** Search + filter across **Actions** (16, by category: locomotion, gesture, expression, posture; minimum and typical duration; a
"moves the character" badge; params), **Backgrounds** (7, day/dusk/night thumbnails), **Characters** (6 archetypes), **Props** (12), **SFX** (41,
play to audition), **Camera moves** (5), **Transitions** (4), **Caption styles** (3), **Easings** (17, curve preview). Drag any item onto the
timeline or stage, or use "Add to scene".

**5.7 Health & Settings.**
* *Health* (from `reel doctor`): ffmpeg version, skia, fonts found per role, speech engines, LLM providers; **Test** buttons that make ONE tiny
  request and say what it costs ("OpenAI: about 100 tokens", "ElevenLabs: no credits spent"), with the result in plain words.
* *Keys*: only whether each variable is set and where ("found in shell" / "found in .env"); never a value. A copyable `.env` snippet with empty
  values and where the file goes. (Stretch: a write-only field that saves to `~/.config/reel/.env` with mode 0600 and never echoes it back.)
* *Cache*: sizes for frames / chunks / speech / tmp with Clear buttons; clearing **speech** warns that ElevenLabs lines would have to be paid for again.
* *Preferences*: theme, density, workspace folder, default style and planner, workers, plugin paths, reduced motion.

### 6. The data the UI edits (domain reference)

Times inside a scene are **scene-local seconds**. Total duration = sum of scene durations minus the transition overlaps; it must be **45-60 s**
(`DURATION_BUDGET` error otherwise). A scene is at most 30 s. Scene i starts at sum over j < i of (duration_j - transition_j).

```ts
type Seconds = number; type Vec2 = [number, number];
interface ReelSpec { version: "1.0"; meta: Meta; characters: Character[]; scenes: Scene[]; audio: Audio }
interface Meta { title: string; style: "paper_cutout"|"stickman"|"flat_vector"|string; fps: number /*12-60, default 30*/;
  resolution: [number, number] /*[1080,1920]*/; seed: number; target_duration_sec: number /*45-60*/; aspect: "9:16";
  fx?: Partial<Record<"grain"|"vignette"|"bloom"|"chromatic_aberration"|"saturation"|"contrast", number /*0-2*/>> & { warmth?: number /*-1..1*/; letterbox?: number /*0-0.3*/ };
  safe_area?: { top: number /*.10*/; bottom: number /*.20*/; left: number /*.07*/; right: number /*.07*/ } }
interface Character { id: string; archetype: "boss"|"elder"|"everyman"|"hero"|"kid"|"robot"; name?: string; voice?: string; props: string[];
  palette: Partial<Record<"skin"|"hair"|"shirt"|"shirt2"|"pants"|"shoes"|"accent"|"eye"|"outline", string /*#hex*/>> }
interface Scene { id: string; duration_sec: number; background: { template: string; params: Record<string, unknown> };
  camera: { moves: CameraMove[] }; layers: Layer[]; captions: Caption[]; sfx: { name: string; t: Seconds; volume: number /*0-2*/ }[];
  transition_out: { type: "cut"|"crossfade"|"wipe"|"page_flip"; duration: number /*0-3*/; params: Record<string, unknown> }; notes?: string }
interface Layer { character: string; position: Vec2 | string /*feet, screen fractions, or a slot name*/; scale: number /*0-4*/;
  depth: "background"|"mid"|"foreground"; facing: "auto"|"left"|"right"; actions: { name: string; t0: Seconds; t1: Seconds; params: Record<string, unknown> }[] }
interface Caption { text: string; t0: Seconds; t1: Seconds; style: "subtitle"|"title"|"shout"; speaker?: string /*character id: drives lip-sync*/;
  speak?: boolean /*default true for subtitles*/; anchor: "auto"|"top"|"center"|"bottom" }
interface CameraMove { type: "pan"|"zoom"|"dolly"|"shake"|"rack_focus"; from?: number|string|Vec2; to?: number|string|Vec2; t0: Seconds; t1: Seconds;
  ease: string; params: Record<string, unknown> }   // pan [x,y] | zoom scale | dolly 0..1 | shake 0..1 | rack_focus "background"|"midground"|"foreground"|0..1
interface Audio { music: string|null /*"procedural" | "procedural:<mood>" | file path*/; voiceover: "tts"|"file"|"none"; ducking: boolean;
  voiceover_file?: string; tts_voice?: string; music_gain_db: number /*-16*/; voice_gain_db: number /*0*/; sfx_gain_db: number /*-8*/; auto_sfx: boolean }
```

Catalog (the API returns it; every inspector form is generated from it):

```ts
interface Param { name: string; type: "string"|"number"|"integer"|"boolean"|"array"; required: boolean; default?: unknown;
  enum?: string[]; minimum?: number; maximum?: number; description: string }
interface CatalogEntry { name: string; summary: string; params?: Param[]; category?: string; moves_root?: boolean; min_duration?: number; default_duration?: number }
type Catalog = Record<"styles"|"backgrounds"|"actions"|"transitions"|"camera_moves"|"caption_styles"|"archetypes"|"props"|"sfx"|"easings", CatalogEntry[]> & { music_moods: string[] }
```

Registered today (the catalog can grow with plugins, so never hard-code):
* **styles** (3): paper_cutout, stickman, flat_vector.
* **backgrounds** (7): abstract, forest, rooftop, room, stage, street, whiteboard. All take `time_of_day` (dawn|day|dusk|night), `mood`
  (neutral|warm|cool|dramatic|playful|gloomy), `density` 0-1, plus their own (street: weather, district, shops; forest: season, trees, fog, path; ...).
* **actions** (16): locomotion enter_from, exit_to, fall, jump, run, walk; gesture look_at, pick_up, point, wave; expression cry, laugh, surprise, talk,
  think; posture idle. Each has `min_duration` / `default_duration`; walk/run/jump/enter_from/exit_to have `moves_root`.
* **transitions** (4): cut, crossfade, wipe (direction left|right|up|down, softness, angle), page_flip (direction, radius).
* **camera moves** (5): pan, zoom, dolly, shake, rack_focus. **caption styles** (3): subtitle (bottom, spoken word highlighted), title (upper third),
  shout (huge, above the heads). **archetypes** (6). **props** (12): backpack, balloon, book, briefcase, cap, coffee, flower, glasses, hat, phone, scarf,
  umbrella. **sfx** (41), **easings** (17), **music moods** (8).
* Speech engines: `piper` (needs a voice model), `say` (macOS), `babble` (placeholder), `elevenlabs` (online, opt-in, billed). Planners: `offline`,
  `ollama:<model>` (local), `openai`, `anthropic`.

### 7. API contract (and the mock)

JSON over HTTP; `Authorization: Bearer <token>` or the HttpOnly SameSite=Strict cookie set on first load (so `<img>` and `<video>` work). Long work is a
**job** with a Server-Sent-Events stream. Implement the frontend against `interface Api` with two adapters: `HttpAdapter` and `MockAdapter` (chosen by
`VITE_API=mock|http`). **The mock must simulate latency, job progress, lint issues, offline/online availability and errors**, so every state can be
reviewed without a backend; give it the fixtures below.

| Route | Purpose (engine function behind it) |
|---|---|
| `GET /api/health` | `{version, python, ffmpeg, skia, workers}` |
| `GET /api/catalog` | the Catalog above (`reel manifest` + `music_moods`) |
| `GET /api/schema` | the JSON Schema of `ReelSpec` (for the JSON tab) |
| `GET /api/examples` | example specs and scripts |
| `GET/POST /api/projects`, `GET/PUT/DELETE /api/projects/{id}` | spec files in the workspace; `PUT` uses `If-Match` (409 on an on-disk change); list items: `{id, title, style, duration_sec, scenes, lint:{errors,warnings}, updated_at}`; `GET /api/projects/{id}/thumb` -> image |
| `GET /api/library/thumb/{kind}/{name}?time_of_day=` | catalog thumbnails rendered by the engine; kind is style, background or archetype (a style card = the same sample frame in that style; a background card = the set with two characters at day, dusk or night); cache them |
| `POST /api/lint` `{spec, style_override?}` | `LintReport` (`lint_data`) |
| `POST /api/generate` `{script, style, target_duration, seed, planner, enrich, repairs}` -> `{job_id}` | script -> spec (`generate_spec`); done = `{spec, lint, attempts, notes, generator}` |
| `POST /api/preview` `{spec, scale?}` -> `{preview_id, fps, total_frames, width, height}`; `GET /api/preview/{id}/frame/{n}` -> `image/webp` | live frames (`Renderer.frame`); keep the last 4 renderers; content-keyed cache makes unchanged frames instant |
| `POST /api/audio/estimate` `{spec, engine}` | `{lines, cached_lines, new_lines, billable_characters, destination}` (destination is null for offline engines), without synthesising |
| `POST /api/audio/prepare` `{spec, engine?}` -> `{job_id}` | `prepare_audio`; done = `{wav_url, spec /*retimed*/, word_timings, warnings, notes, report:{duration,peak,approx_lufs,ducked_seconds,n_clips}, peaks:{voice,music,sfx}}` |
| `GET /api/tts/engines`; `GET /api/tts/voices?engine=`; `POST /api/tts/sample` `{engine, voice, text}` | status (`{name, available, online, detail, account?}`), voices (`{id, name, traits}`), a short WAV |
| `GET /api/sfx/{name}.wav`; `GET /api/music/{mood}.wav?seconds=10&seed=` | auditions |
| `POST /api/render/plan` `{spec, preset, range?}` | `{frames, segments_total, segments_cached, est_seconds}` |
| `POST /api/render` `{spec or project_id, preset, scale?, range?, crf?, maxrate?, workers?, lenient?, no_audio?, tts?, seed?}` -> `{job_id}` (preset is draft, standard, full or custom) | render; done = `{video_url, path, size_bytes, width, height, duration_sec, frames, seconds, segments:{total,encoded,reused}, warnings, notes}` |
| `GET /api/jobs/{id}/events` (SSE); `DELETE /api/jobs/{id}` | events below; DELETE cancels |
| `GET /api/renders`; `GET /api/renders/{id}/video` (HTTP range) | history and playback |
| `GET /api/doctor`; `POST /api/doctor/test` `{kind, target}` (kind is llm or tts) | structured health; ONE tiny request, with its cost stated |
| `GET /api/cache`; `DELETE /api/cache/{kind}` | `{root, frames:{files,bytes}, ..., total_bytes}`; kind is frames, segments, tts or tmp |

```ts
type JobEvent =
  | { type: "progress"; phase: "plan"|"audio"|"frames"|"mux"; done: number; total: number; eta_sec?: number }
  | { type: "note" | "warning" | "log"; message: string }
  | { type: "done"; result: unknown }
  | { type: "error"; message: string; hint?: string; lint?: LintReport };
interface LintIssue { severity: "error"|"warning"|"info"; code: string; path: string /*"scenes[1].layers[0].actions[0].name"*/; message: string; hint?: string; kind?: string; name?: string }
interface LintReport { ok: boolean; total_sec: number|null; n_scenes: number|null; counts: { errors: number; warnings: number; infos: number };
  missing: Record<string, Record<string, string[]>>; issues: LintIssue[] }
```

**Fixtures for the mock** (use them verbatim; extend the spec to 8-10 scenes for the demo project):

```json
{
  "version": "1.0",
  "meta": {"title": "The Lost Umbrella", "style": "paper_cutout", "fps": 30, "resolution": [1080, 1920], "seed": 2026, "target_duration_sec": 50},
  "characters": [
    {"id": "mia", "archetype": "kid", "name": "Mia", "props": ["backpack"], "palette": {"shirt": "#2a9d8f", "hair": "#d9822b"}},
    {"id": "pip", "archetype": "elder", "name": "Mr. Pip", "props": ["scarf"]}
  ],
  "scenes": [
    {"id": "rainy_street", "duration_sec": 5.9,
     "background": {"template": "street", "params": {"time_of_day": "day", "mood": "gloomy", "weather": "rain"}},
     "camera": {"moves": [{"type": "pan", "from": [0, 0], "to": [0.05, 0], "t0": 0, "t1": 5.9, "ease": "linear"}]},
     "layers": [
       {"character": "mia", "position": [0.27, 0.7], "scale": 1.1, "actions": [{"name": "look_at", "t0": 0.4, "t1": 3.4, "params": {"target": "pip"}}]},
       {"character": "pip", "position": "right", "actions": [{"name": "surprise", "t0": 0.5, "t1": 1.8}, {"name": "talk", "t0": 0.9, "t1": 3.5}]}],
     "captions": [{"text": "Oh no! Where is my umbrella?", "t0": 0.9, "t1": 3.5, "style": "subtitle", "speaker": "pip"}],
     "sfx": [{"name": "gasp", "t": 0.6}],
     "transition_out": {"type": "wipe", "duration": 0.6, "params": {"direction": "left"}}},
    {"id": "the_search", "duration_sec": 5.5,
     "background": {"template": "forest", "params": {"time_of_day": "dusk", "season": "autumn"}},
     "layers": [{"character": "mia", "position": "left", "actions": [{"name": "walk", "t0": 0.2, "t1": 3.0, "params": {"to": "center"}}, {"name": "point", "t0": 3.2, "t1": 4.6}]}],
     "captions": [{"text": "Don't worry, Mr. Pip!", "t0": 0.8, "t1": 3.0, "style": "subtitle", "speaker": "mia"}],
     "transition_out": {"type": "crossfade", "duration": 0.5}}
  ],
  "audio": {"music": "procedural:playful", "voiceover": "tts", "ducking": true, "auto_sfx": true}
}
```

```json
{"ok": false, "total_sec": 10.8, "n_scenes": 2, "counts": {"errors": 1, "warnings": 0, "infos": 1}, "missing": {},
 "issues": [
  {"severity": "error", "code": "DURATION_BUDGET", "path": "scenes", "message": "total duration is 10.80s (scenes 11.40s - transition overlaps 0.60s); the budget is 45-60s", "hint": "add about 34.2s of scene time"},
  {"severity": "info", "code": "TRANSITION_LAST", "path": "scenes[1].transition_out", "message": "transition_out on the last scene is ignored"}]}
```

```json
{"name": "walk", "category": "locomotion", "moves_root": true, "min_duration": 0.6, "default_duration": 2.5,
 "summary": "Walk to a point or slot (or on the spot); gait style via `style`",
 "params": [
  {"name": "to", "type": "array", "required": false, "description": "Destination: [x,y] screen fractions, a slot name or a character id"},
  {"name": "style", "type": "string", "required": false, "default": "normal", "enum": ["normal","sneak","tired","happy","stomp","march","strut"], "description": "gait"},
  {"name": "in_place", "type": "boolean", "required": false, "default": false, "description": "walk on the spot (the camera or background moves instead)"}]}
```

Job stream to simulate: `progress{phase:"plan"}`, then `progress{phase:"audio"}`, `note "ElevenLabs (eleven_multilingual_v2): 418 characters in 14
line(s) were sent to api.elevenlabs.io and billed"`, then `progress{phase:"frames", done: 0..1491}` at about 120 frames/s, `progress{phase:"mux"}`, `done`.

### 8. States and edge cases

Backend unreachable: a banner, auto-retry with backoff, read-only until it is back. Invalid JSON: inline error, last valid spec kept. Lint errors do not block
editing; they block Render unless "Render anyway (lenient)" is chosen, which states what falls back (unknown action -> idle, unknown background -> abstract).
An unknown (plugin) action shows as a "custom action" with a raw-params JSON editor and a "Replace with idle" fix. `position` is a `Vec2` or a slot
name: slot chips on the stage; dragging converts to a `Vec2` unless it snaps to a slot. Unsaved changes + a file changed on disk: a conflict dialog with a diff.
Long jobs survive a page reload (job id in the URL); one render at a time, others queue with their position shown; a completion toast. Disk or cache problems show
the engine's warning text. Time values round to 1 ms and display with 2 decimals. Every empty state (no projects, no renders, no voice engine, no problems)
has an illustration, one sentence and one primary action.

### 9. Privacy and safety (hard requirements)

1. Never display, log, store or transmit an API key; there is no key input field (the stretch option is write-only).
2. The server listens on 127.0.0.1 with a per-launch token and a Host allow-list; remote access needs an explicit flag and a warning.
3. Any online call (LLM planner, ElevenLabs) shows the provider host and exactly what is sent, and needs a one-time consent per provider.
4. **Billing transparency**: show the billable character count before any ElevenLabs generation; never run a paid action (audition, regenerate) without an explicit click.
5. Destructive actions confirm; clearing the speech cache warns about re-billing.
6. No telemetry and no external requests at runtime (fonts and icons are bundled); CSP `default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:`.
7. File access is confined to the workspace folder (no path traversal); only `.json` specs and rendered videos are served.

### 10. Accessibility, performance, responsiveness

* WCAG 2.2 AA: text contrast 4.5:1, UI 3:1, visible 2 px focus rings, every icon button labelled, status never by colour alone, `prefers-reduced-motion`.
  The timeline is fully keyboard operable (arrows move a clip by 0.1 s, Alt = 1 frame, Enter opens the inspector) and announces changes in a live region ("Moved Walk to 2.30-4.10 s").
* First paint under 1 s locally; the timeline stays at 60 fps with 300 clips (virtualise lanes); at most one frame request in flight plus the latest queued; prefetch
  +-12 frames during draft playback; initial JS under 500 KB gzipped (lazy-load the JSON editor).
* Desktop-first (1280 px and up, three columns); 1024-1279: the inspector becomes a drawer; 768-1023: both side panels are drawers and the timeline is compact; under 768:
  Projects, preview, export and status only, with "Editing needs a larger screen" in Studio.

### 11. Acceptance criteria

1. Launching shows Projects with two example reels and real thumbnails (mock: static renders).
2. The wizard turns the sample script into a lint-clean 50 s spec and opens Studio in under 10 s (offline planner).
3. Scrubbing the whole reel updates the preview live (under 150 ms per frame at 540x960).
4. Dragging an action clip's edge updates preview and lint; Undo restores it.
5. Changing a character's shirt colour updates the stage and the timeline lane tint.
6. Setting a scene to 30 s (total over 60) shows `DURATION_BUDGET` with its hint in Problems; **Fit to 50 s** resolves it.
7. Export Draft shows progress and plays the result with audio in the result card.
8. The ElevenLabs card without a key shows the exact reason and no key field; with a key it shows plan and credits; the cost estimate follows caption edits; nothing paid runs without a click.
9. A keyboard-only pass through the main flow works; axe-core reports no serious violations in dark and light; no console errors; Playwright asserts that no request leaves localhost.

### 12. Do not

Do not invent engine features (no AI image generation, stock footage, cloud rendering, accounts or payments). No decorative animation, gradient text,
heavy glassmorphism or emoji icons. Do not hide the JSON. Do not block the UI on slow work (use jobs and streaming). Do not use `localStorage` for projects
(workspace files are the source of truth; it is only for UI preferences).

### Output

First a one-page design spec (tokens, component inventory, screen map), then the code, then run instructions (`npm run dev` with the mock; `reel serve --open`
with the API), and screenshots of every screen in dark and light via Playwright.

---

## THE SHORT VERSION

Build **Reel Studio**, a beautiful local-first web UI (React + TypeScript + Vite + Tailwind + Radix, Lucide icons, bundled Inter/JetBrains Mono, dark + light)
for `reel`, an offline engine that turns a script into a 45-60 s vertical 9:16 animated reel (JSON scene spec -> MP4; styles paper_cutout, stickman,
flat_vector). Calm, precise creative-tool look (Linear x Figma x a video editor): quiet chrome, the product's own frames as the hero, one violet accent
(`#7C6CFF` dark / `#5B4BE0` light), dark bg `#0B0D12` / panel `#12151C`, 13 px Inter, 4-pt grid, 8/12 px radii, hairline borders, tabular numbers, no decorative
animation (progress and playhead only), WCAG AA.

Screens: **Projects** (thumbnail cards, New reel from script / example / .json), a **3-step wizard** (script editor; style as three cards showing the same frame;
planner Offline | Ollama | OpenAI | Claude with disabled-with-reason states and consent for online ones; generating checklist), **Studio** (centre 9:16 live preview with
safe-area overlay and drag-to-position characters; bottom multi-lane timeline - scenes, camera, one lane per character tinted by shirt colour, captions, SFX, audio -
with a 45-60 s duration-budget bar, snapping, split/duplicate/undo; right inspector generated from a catalog of params (enum -> segmented/select, number -> slider,
bool -> switch); left tabs Scenes / Cast / Script / Library; a Problems drawer from lint issues `{severity, code, path, message, hint}` with Go-to; a JSON tab),
**Voice & Audio** (engine cards Piper / macOS Say / Placeholder / ElevenLabs with status chips, per-character voice table with audition, a billable-characters
estimate for online engines, waveforms, music mood, gains), **Export** (Draft 360x640 / Standard 540x960 / Full HD; plan "31 of 35 chunks cached"; progress,
cancel, result player), **Library** (16 actions, 7 backgrounds, 6 archetypes, 12 props, 41 SFX, ...), **Health & Settings** (doctor, key *status* only, cache sizes).

Data: `ReelSpec {meta, characters[], scenes[{background, camera.moves, layers[{character, position, actions[]}], captions[], sfx[], transition_out}], audio}`;
scene-local seconds; total = sum(scenes) - transition overlaps must be 45-60 s. Use a typed `Api` with a `MockAdapter` (latency, SSE-style job progress, lint issues,
online/offline states) and an `HttpAdapter` for `/api/*` (catalog, projects, lint, generate, preview frames, audio, render jobs + SSE, doctor, cache).

Hard rules: never show or ask for an API key (status only); localhost only; consent before any online call; show billable characters before any paid ElevenLabs
action and never run one without a click; no telemetry or external requests; confirm destructive actions; keyboard-operable timeline; dark and light screenshots.
