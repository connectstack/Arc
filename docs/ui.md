# Reel Studio: the web app

Reel Studio is a local web app for the engine: write or paste a script, get a first cut, edit it on a timeline with a live
preview, give it a voice, and export the MP4. It is a thin client over the same code the CLI runs, so a project is a plain
`*.reel.json` file you can also `reel lint` and `reel render` from a terminal.

```bash
reel serve --open        # or: make serve
```

`reel serve` prints a link with a one-time access token, opens it, and keeps everything on this machine (127.0.0.1). The
first run creates a `studio/` folder (or `--workspace`) with the two example reels in it.

The built web app is committed under `src/reel/server/static/`, so none of this needs Node. Node 20+ is only for changing the
web app itself (see [Developing](#developing)).

## What is in it

| Screen | What it does |
|---|---|
| **Projects** | Your reels as cards with a real thumbnail, length, style and a lint badge. Start from a script, from an example, or by dropping a spec file. |
| **New reel** | Three steps: the script, the look and the planner (offline, Ollama, OpenAI, Claude), then a generating checklist that ends in the studio. A hosted planner is only offered when its key exists, and asks before the script leaves the machine. |
| **Studio** | Live preview (the engine's own frames, cached) that plays **with the voice**, a multi-lane timeline (scenes, camera, one lane per character, captions, sound effects, audio with the music's dip under speech), an inspector generated from the engine's catalog, a Script tab that checks the reel against your script, Problems (lint with "fix it" shortcuts) and the JSON itself. Drag characters on the stage, drag or Shift-select clips on the timeline, copy and paste them onto another character, `⌘K` for every command, `?` for every shortcut. |
| **Voice & Audio** | Pick the speech engine (Piper, macOS Say, a placeholder, ElevenLabs), a voice per character with an audition button, the music mood and the mix. States exactly what a generation will cost before anything is sent. |
| **Export** | Draft, Standard, Full HD or custom. Shows how many chunks are already cached, runs the render as a job with progress and Cancel (it keeps going if you reload the page), then plays the result and offers the download. Earlier renders are listed and can be deleted. |
| **Library** | Every action, background, character, prop, sound, camera move, transition, caption style and easing, with thumbnails drawn by the renderer in the style you choose. |
| **Health & settings** | `reel doctor` as a page: ffmpeg, skia, fonts, speech engines, planners, which keys exist (never their values), the cache with a Clear button per kind, theme, density and defaults. |

## Rules it keeps

* **Offline unless you say so.** The default planner, the default voice and every preview run on this machine. An online
  service is used only after you pick it *and* click through a dialog that names the host and what it will cost.
* **Billing is never a surprise.** An online voice shows its billable characters before you can press the button; lines
  already generated are reused for free; clearing the voice cache says it can re-bill. Nothing paid runs without a click.
* **The UI never touches an API key.** Keys come from your shell or a git-ignored `.env`; the app shows whether one was
  *found* and where, and has no field to type one into. The server never returns a key, and scrubs it from every message.
* **Nothing decorative moves.** The only continuous motion is a live level meter and a spinner while something is working.
  `prefers-reduced-motion` is honoured.
* **Everything is reachable from the keyboard**, and the contrast of every text colour is tested (below).

## In the studio

**Hear it before you export.** Press Play: the first time (and again after the words change) the app makes the soundtrack with the
speech engine you chose in Voice & Audio, then plays the preview with the voice, the music and the effects. The audio is the clock the
picture follows, so the mouths and the captions stay on the words, and the preview shows the reel *as the render will draw it*
(captions retimed to the speech, with a one-click "Apply to the timeline"). The speaker button next to the transport turns the sound off.
An online voice asks first and states what it will bill; an offline one (macOS Say, Piper, the placeholder) just runs. A text in another
script (Hindi, Arabic, Japanese ...) is read by an installed voice for that language; when a language has only one voice, each character keeps
a pitch of their own so you can still tell who speaks.

**Your script, exactly.** The Script tab shows how much of your script the captions say ("every line of the script is in the captions, in
order", or what is missing, what is only a speaker's name, what the script does not contain), with **Make the captions follow my script**
to fix it in one undo step. The New reel wizard keeps the script's own words unless you switch that off for a long script (see
[llm.md](llm.md#your-script-exactly)).

**Editing**

* **Select several clips** with Shift+click on the timeline; drag one to move them all, arrow keys nudge them, Delete removes them,
  `⌘D` duplicates them.
* **Copy, cut, paste** (`⌘C` `⌘X` `⌘V`, or right-click a clip): paste lands at the playhead, in the scene it is in. Click a character's
  lane name (or right-click it and choose *Paste onto …*) to paste actions onto *that* character, who gets a lane in the scene if they had none.
  The clipboard survives switching projects.
* **Undo history** (the clock button next to Undo): every step is named by what it changed ("Moved walk", "Edited caption …", "Scene 2:
  length 6 → 8 s"); click one to go back to it, or forward again.
* **Switch project** from the folder button at the foot of the rail; it opens the other project on the same screen (Studio or Voice & Audio).
* **An action the catalog does not know** (from a plugin that is not loaded) shows as a *custom action*: its parameters are a raw JSON box, and
  *Replace with idle* is one click. A strict render stops on it; a lenient render and the preview draw *idle* in its place.
* **The audio lane** draws the voice, the music and the effects, and the music's dip under speech as a curve (the mixer's own ducking).
* **Cancelling** a render leaves nothing half-written; **a render survives a reload**: the page picks it up again (progress, then the
  "Render finished · Watch" notice).

## Architecture

```
ui/                       React 19 + TypeScript + Vite + Tailwind v4 + Radix primitives
  src/api/                the Api interface; HttpAdapter (reel serve) and MockAdapter (in memory, for demos and tests)
  src/store/              project (undo/redo with gesture coalescing), studio view state, jobs, audio, lint, preferences
  src/lib/                timeline maths (mirrors reel/core/timeline.py), snapping, frame pipeline, spec defaults
  src/features/           projects · wizard · studio · audio · export · library · health
src/reel/server/          FastAPI: security middleware, workspace files, jobs, previews, routes
src/reel/server/static/   the production build of ui/ (committed)
```

**Spec files stay small.** The engine fills in every default (`fps`, `depth`, `facing`, empty `params`, ...). The app does
too when it opens a file (`normalizeSpec`) and takes the defaults out again when it saves (`stripDefaults`), so a project
file stays as readable as one written by hand. Both are round-trip tested.

**Live preview** is a server-side `Renderer` per spec (an LRU keyed by the spec's hash). The app asks for frames
(`/api/preview/<id>/frame/<n>`, WebP, immutable cache headers); at most three are in flight and the newest request wins, so
scrubbing never queues up stale frames. The preview *is* the render: same code, same cache.

**Jobs** (render, audio, script → spec) are separate processes with their own process group. The server keeps answering
frames while a render runs; **Cancel** is a SIGINT to the group (the engine already shuts down cleanly on Ctrl-C: workers
stopped, no half-written file) with a watchdog SIGKILL; a crash cannot take the server down. One heavy job runs at a time.
Progress is a stream of small JSON events over server-sent events, resumable with `Last-Event-ID`, and the server replays them to a
late listener, so a job survives a page reload (the tab remembers which renders were running and follows them again).

**The workspace is plain files**: `projects/<id>.reel.json`, an optional `<id>.script.txt`, and `renders/*.mp4` with a
`.json` beside each. Saves carry the file's etag (`If-Match`); if something else changed the file, the app offers to load
the disk version or keep yours.

### The API

Everything is under `/api`, JSON unless noted, and needs the token.

| | |
|---|---|
| `GET /health` · `/catalog` · `/schema` · `/examples` · `/doctor` · `/cache` | what is installed, what a script can ask for, the JSON Schema, example reels, `reel doctor`, cache sizes |
| `POST /doctor/test` · `DELETE /cache/{kind}` | one explicit check of a hosted service (cost stated by the UI); clear one kind of cache |
| `GET/POST /projects` · `GET/PUT/DELETE /projects/{id}` · `GET /projects/{id}/thumb` | project files; `PUT` needs `If-Match` |
| `POST /lint` · `POST /generate` (job) | lint a spec; script → spec (`verbatim` keeps the script's own words) |
| `POST /script/check` · `POST /script/lock` | how much of a script a spec's captions say; the spec made to follow the script (with notes and the new coverage) |
| `POST /preview` · `GET /preview/{id}/frame/{n}` | start a preview of a spec (optionally with the word timings of a voice, so mouths follow the speech); one frame as WebP |
| `GET /library/thumb/{kind}/{name}` | a thumbnail drawn by the renderer |
| `GET /tts/engines` · `/tts/voices` · `POST /tts/sample` | speech engines and voices; an audition clip (an online engine needs `confirm_billing`) |
| `POST /audio/estimate` · `/audio/prepare` (job) · `GET /audio/{id}.wav` | lines to speak, how many are cached, billable characters; make the soundtrack (the result carries the retimed spec, word timings, waveforms and the music's ducking curve) |
| `GET /sfx/{name}.wav` · `/music/{mood}.wav` | audition a sound effect or a music bed |
| `POST /render/plan` · `POST /render` (job) | what a render would reuse and cost; start it |
| `GET /jobs` · `GET/DELETE /jobs/{id}` · `GET /jobs/{id}/events` (SSE) | observe and cancel jobs |
| `GET /renders` · `GET /renders/{id}/video` · `DELETE /renders/{id}` | finished videos |

## Security

The server is for the person who started it:

1. **Host check.** Only the loopback names and the configured port are accepted; a web page on another site that resolves its
   own domain to 127.0.0.1 sends *its* host name and gets a 421 (DNS rebinding).
2. **Token.** Every API call needs the per-launch token: as `Authorization: Bearer ...` or as the `reel_token` cookie. The
   launch link sets that cookie (`HttpOnly; SameSite=Strict`), which is also what lets `<img>` and `<video>` load frames
   without any script handling the secret.
3. **Origin check** on every unsafe method.
4. A strict **Content-Security-Policy**: the app loads nothing from any other origin (fonts and icons are bundled).

`--host` other than this machine needs `--allow-remote`, and the token is still required. `--no-token` exists for
development only.

## Developing

```bash
make ui-install                                   # npm ci in ui/
.venv/bin/reel serve --no-token --port 8765       # the engine (no token: the dev server proxies /api to it)
make ui-dev                                       # the app with hot reload on http://127.0.0.1:5173
make ui-test                                      # unit + component tests (Vitest + Testing Library, on the mock engine)
make ui-build                                     # the production build into src/reel/server/static
make ui-check                                     # type check + tests + build
```

The dev server forwards `/api` to `http://127.0.0.1:8765`; `REEL_API=http://127.0.0.1:8767 make ui-dev` points it at another
engine (handy when one `reel serve` is already running and you do not want to share its workspace).

`VITE_API=mock npm run dev` (or `?mock` in the address) runs the whole app against the in-memory `MockAdapter`: every
screen, every state (offline and online voices, job progress, lint, errors) with no server at all. `?mock=eleven`
pretends an ElevenLabs key exists, to review the billing flows without one.

Python side: `tests/test_server.py` covers the security rules, projects and conflicts, lint, previews, jobs (a real
subprocess render, cancel mid-render, SSE resume), the audio endpoints and the packaged build.

### Accessibility

The target is WCAG 2.2 AA.

* `ui/src/lib/contrast.test.ts` reads the colour tokens in `index.css` and fails if any text colour drops under 4.5:1 on any
  surface, on a selected row, or as a status colour on its tinted chip, in either theme. Change a token and it tells you.
* Every screen was audited with [axe-core](https://github.com/dequelabs/axe-core) in both themes, with its dialogs, drawers
  and inspector states open (no violations). To repeat it: open the page in the dev server, load
  `/node_modules/axe-core/axe.min.js`, switch `data-theme`, and run `axe.run(document)` (disable CSS transitions first, or
  colours are measured mid-fade).
* Status is never colour alone (an icon and a word go with it), focus is a 2 px ring, the timeline and its clips are
  keyboard-operable (`Tab` to a clip, `←`/`→` nudges by 0.1 s, `⇧` by 1 s, `Alt` by one frame, `Delete`, `Enter` opens the
  inspector, and every change is announced), and character handles on the stage have an equivalent in the inspector's
  Position fields.

### Responsive

Wide (≥1280 px): scenes, stage and inspector side by side. Below that the two side panels open as drawers from the
transport bar. On a phone (<768 px) the navigation rail becomes a bottom bar and the timeline gutter narrows.
