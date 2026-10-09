# Script → JSON (the optional first stage)

The renderer only ever reads a JSON scene spec. This stage is the *optional* way to get one from plain text, and it is swappable: three
generators implement one interface, and whatever any of them returns has already passed the linter.

```
script.txt ─┬─► LLMSpecGenerator        ask a model (Ollama / OpenAI-compatible / Anthropic), normalise, lint, repair
            ├─► HeuristicSpecGenerator  rule-based, fully offline, no model at all  (the default of `reel build`)
            └─► ManualSpecGenerator     "write the JSON yourself": load, pin style/seed, lint (what `reel lint` tells you)
                         │
                         └─► GenerationResult(spec, lint, attempts, notes)  ──►  reel render
```

```bash
reel build examples/scripts/lost_umbrella.txt                          # offline planner (default)
reel build script.txt --llm ollama:llama3.1                            # a local model through Ollama
reel build script.txt --llm openai                                     # OpenAI, default model gpt-4o-mini (needs OPENAI_API_KEY)
reel build script.txt --llm openai:gpt-4o                              # any OpenAI model
reel build script.txt --llm anthropic                                  # Claude, default claude-sonnet-5-5 (needs ANTHROPIC_API_KEY)
reel build script.txt --llm anthropic:claude-opus-5-5                  # any Claude model id
reel build script.txt --llm openai:my-model@http://localhost:1234/v1   # LM Studio, vLLM, llama.cpp server, a proxy, ...
REEL_LLM=openai reel build script.txt                                  # the model can come from the environment (or .env)
reel build script.txt --spec-only -o out/mine.mp4                      # stop after writing out/mine.spec.json, edit it, `reel render` it
```

### Your own OpenAI or Claude key

1. **Put the key where it cannot be committed.** Either export it in your shell (`export OPENAI_API_KEY=sk-...`, `export ANTHROPIC_API_KEY=sk-ant-...`),
   or copy [`.env.example`](../.env.example) to `.env` (git-ignored; a pre-commit hook also refuses anything that looks like a key) and uncomment the lines you
   need. A real shell variable always wins over the file; `~/.config/reel/.env` works too, and `REEL_ENV_FILE=path` names another file. Only
   `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `REEL_LLM` and the speech engine's `ELEVENLABS_API_KEY`, `ELEVENLABS_BASE_URL`, `ELEVENLABS_MODEL`, `ELEVENLABS_VOICE`, `REEL_TTS` (see [audio.md](audio.md#elevenlabs-optional-online)) are ever read from it, and no command prints a value. The `.env` in the directory you happen to be in is *untrusted*: it may hold keys and defaults, but a setting that says **where** a key is sent (`OPENAI_BASE_URL`, `ELEVENLABS_BASE_URL`, or a `REEL_LLM` ending in `@url`) is ignored there, so a cloned repository cannot redirect a key you exported in your shell. Put such settings in your shell, in `~/.config/reel/.env`, or in a file you name with `REEL_ENV_FILE`.
2. **Check it with one tiny request:** `reel doctor --llm openai` (or `anthropic`, `openai:gpt-4o`, ...) sends ~100 tokens and reports key, model name and
   connectivity problems in plain words. `reel doctor` alone lists which keys and local models were found (names only).
3. **Build:** `reel build script.txt --llm openai`. Set `REEL_LLM=openai` (shell or `.env`) to make it the default.

What gets sent: the system prompt (rules + the catalog of what the renderer can draw), your script, and the spec schema; nothing else, and nothing from your
files beyond the script. A build is one request of about 6K tokens in and 4K out (the full prompt; up to three more if the linter sends corrections back):
roughly half a cent on a small model, a few cents on a large one: check your provider's current pricing. Azure OpenAI (different URL scheme and headers)
is not supported directly; point `OPENAI_BASE_URL` at a compatible gateway if you have one.

`reel build` always writes the spec next to the video, so the model's work is never opaque: edit the JSON and re-render, the renderer does not
care where it came from. If a model cannot produce a valid spec, the last attempt (`*.spec.failed.json`) and its raw reply (`*.spec.raw.txt`) are
kept for hand-fixing, and `reel doctor` shows which local models and API keys are available. Options: `--repairs N`, `--duration 45..60`, `--seed`,
`--compact/--full-prompt`, `--verbatim/--condense` (see [Your script, exactly](#your-script-exactly)), and every `render` option.

## The prompt

The prompt is a *template* ([`prompts/script_to_spec.md`](../src/reel/llm/prompts/script_to_spec.md)) filled from the **live catalog**, so it can
only ever mention names that exist right now:

| part | content |
|---|---|
| 1 OUTPUT FORMAT | one JSON object, compact, scene-local seconds, positions as screen fractions (feet), no `audio` block (it is added for you) |
| 2 SPEC SHAPE | the schema as a skeleton: CAPITALISED words are catalog names, `[brackets]` are optional |
| 3 HARD RULES | the budget with a worked sum, scene count and length, time windows inside scenes, feet placement and slots, cast, entrances/exits, caption timing, sound, camera, transitions: every rule is one the linter checks |
| 4 DIRECTING | follow the script, hook → turn → final beat, match the set to the story, act the script out |
| 5 CATALOG | rendered from the manifest: every background with its parameters and slots, every action with its parameters and minimum length (`*` marks moving actions), transitions, camera moves, caption styles, SFX, archetypes, props |
| 6 WORKED EXAMPLE | a two-character cast and one scene in the compact form, built from names that exist |
| 7 BEFORE YOU ANSWER | the checklist |

Conditional blocks (`{{#has action enter_from}} ... {{/has}}`) keep a rule only if the thing it names is registered, so removing an action, or
running with a plugin that adds one, changes the prompt consistently; a test fails if the prompt ever names something outside the catalog.
**The exact text, for the default catalog and the `paper_cutout` style, is generated into [`prompts/full.txt`](prompts/full.txt)** (system prompt,
sample user message and the JSON schema; `reel prompt --schema`), so it cannot go stale: `make generated` rewrites it and `reel schema --check`
(a test and a pre-commit hook) fails when it differs from what the code would send. `reel prompt --style stickman --script my.txt` prints it for
any catalog, including your plugins (`--plugin PATH`).

The user message is only the script, fenced so a model cannot mistake it for instructions:

```
Turn this script into the scene spec. Style: "paper_cutout". Target length: 50 s (meta.target_duration_sec = 50). meta.seed = 7.

SCRIPT
<<<
...your text...
>>>

Reply with the JSON object only.
```

## Forcing valid output

Three layers, each cheaper than the next:

1. **Structured output.** The JSON Schema of the spec, with every registry-bound name pinned to what is registered (`enum`s of real action,
   background, transition ... names), goes through the provider's own channel: Ollama's `format`, OpenAI's `response_format: json_schema`
   (falling back to `json_object`, then to plain text when a server rejects it), and for Anthropic a forced `emit_spec` tool whose `input_schema` is the
   schema. A constrained decoder cannot emit a name that does not exist.
2. **Deterministic fixes** (`normalize_spec`): things that are *always* right are repaired in code instead of being asked of the model, which is bad at
   arithmetic: forced `meta.style` / `meta.seed` / target length, scene durations rescaled (with every time inside them) so that
   `sum(durations) - sum(overlaps)` lands on the target, time windows clamped into their scene, zero-length actions dropped, minimum action lengths,
   unique ids, feet kept in the placement band, slips like `duration` → `duration_sec` or colour names → hex. Names that carry meaning (actions,
   backgrounds, ...) are never touched: the linter reports them.
3. **Lint-driven repair.** The linter's report (the same one `reel lint` prints, with its hints) goes back as a user message, "fix these and
   return the FULL corrected JSON", at most `--repairs` times (default 3). JSON extraction is forgiving (markdown fences, prose, `<think>` blocks,
   comments and trailing commas are tolerated).

If it still fails, `SpecGenerationError` carries the last lint report, the raw reply and the last normalised spec.

### Small local models

A 2B model has an 8K-token window, and the full prompt is about 6K tokens. So:

* the client reports its context window (Ollama: read from the model; `num_ctx` is clamped to it; an OpenAI-compatible local server such as LM Studio or
  llama.cpp cannot say, so pass `--compact` for a small model there), and below a threshold the **compact prompt**
  (~1.4K tokens: names and one-line summaries, a smaller schema with caps on scene/layer/action counts) is used automatically; `--compact` /
  `--full-prompt` force it either way ([`prompts/compact.txt`](prompts/compact.txt) is its exact text);
* a reply that is cut off by the length limit is not echoed back (there is no room): the complete scenes it contained are salvaged, the request
  restarts with a demand for a shorter spec, and if the attempts run out the best salvaged spec is returned with a note instead of nothing.

Honest expectations: a 2B model returns a valid, lint-clean spec, but a dull one (see "Enrichment" below); the quality of the story and the staging
scales with the model.

## Your script, exactly

A model asked to "turn this script into a reel" tends to *paraphrase*: it shortens lines, drops a speaker, invents narration, and (the
common case with a hosted model) writes captions that are only the speaker's name (`"Pip:"`). The reel then no longer says what the
script says. So the stage treats the script as the text of the reel and **checks the result against it**, in three steps
([`llm/fidelity.py`](../src/reel/llm/fidelity.py)):

1. **The prompt says so.** When the script fits the budget, the prompt asks for the script's own words, in order, one caption per spoken line
   (`Name: line` is dialogue, other lines are narration).
2. **The lock.** Whatever the model returns is aligned with the script word by word (a token-level diff, so Hindi, Arabic, CJK and
   emoji work the same as English). A caption that is only a speaker's label, or that is the script line with a few words changed, is
   snapped back to the script's exact words; a script line no caption shows is added to the scene it belongs to (or to a new scene that
   reuses that scene's set and cast); a caption that is not in the script at all is removed. The result is re-timed, re-linted and reported
   in the build notes (`kept the script word for word: 3 captions put back to the script's own words, 6 lines added, ...`).
3. **The report.** `ScriptLock.coverage(spec)` says how much of the script the captions show (the UI's *Script* tab prints it, and
   *Make the captions follow my script* applies the lock to a reel you already have, as one undo step).

It only applies while the script can be *read aloud* inside the 45-60 s budget (about 2.5 words a second). A longer script is condensed as
before and says so in the notes; `reel build --condense` (or `generate_spec(..., verbatim=False)`) asks for that on purpose, and in the web
app the wizard has a switch for it.

### Camera moves a model gets wrong

A camera `pan` is a small drift in *frame fractions* (`[0, 0]` to `[0.06, 0]`), not a screen position. Models often write where the camera
should *look* (`from [0.2, 0.5] to [0.8, 0.5]`), which would carry the whole set out of the frame. Three layers keep that from showing:

* the **linter** refuses a pan, zoom, dolly or shake outside the ranges the set can take (`CAMERA_MOVE_INVALID`, whose message names the range);
* the **normaliser** reads a pan written as screen positions as the gentle drift it meant (`[0.2, 0.5] → [0.8, 0.5]` becomes
  `[-0.15, 0] → [0.15, 0]`) and clamps the rest, noting what it did;
* the **renderer** itself never goes past the limits, so a project saved before the limits existed (or a lenient render) still draws
  every layer inside the set instead of a blank band at the edge.

## Enrichment: a weak model still gives a lively video

A small model returns a valid but *static* spec: every character standing in `idle`, no camera, no sound, a caption on screen for 2 s of a 7 s scene.
After the lint-driven repair has accepted a spec, `enrich_spec` fills in what the model left empty (and only that: it never overwrites what the model wrote):

1. **Scene changes**: if every transition is the same type (all cuts, say) they are rotated through crossfade / wipe / page_flip / cut; the reel keeps exactly its length.
2. **Caption windows** are widened to cover the speech (`words / 2.5 + 0.6` s, up to the scene end minus 0.3 s), pushing a later caption of the same style on if the scene has room.
3. **Acting**: a character who only idles gets gestures from its captions (questions → think, exclamations → surprise / jump / laugh, "look", "there" → point, ...),
   the others look at whoever speaks, newcomers `enter_from`, and a "They all ..." line moves the whole cast.
4. **Camera**: one slow move for scenes without any, varied from scene to scene; a `shout` scene gets a shake and a push-in.
5. **Sound**: one or two SFX for scenes without any, at least 0.5 s apart.

It is deterministic (seeded), idempotent (a second pass changes nothing; a rich spec is left alone), and it re-lints the result: if enrichment would add an error or
a warning, the model's own spec is returned with a note instead. The note reads `enriched 8 scenes (actions 16, camera 8, sfx 13, captions 6)`.
Measured on six live runs of `gemma2:2b` (the 2B model that fits an 8K window), layers that stood still went from 18 to 0, camera moves from 0 to 58, SFX from 0 to 91,
and captions long enough for their speech from 17 of 59 to 51 of 59. Turn it off with `reel build --no-enrich` (or `LLMSpecGenerator(enrich=False)`).

Limits: it does not invent `speaker`s for captions that lack one (that would change who is voiced), so a model that omits them gets no lip-flap; and a scene that is
too short for its dialogue stays short (the audio stage retimes the captions and warns).

## Clients

`get_client("provider:model[@base_url]")` returns an `LLMClient` with one method, `complete(system, messages, json_schema=..., temperature=..., seed=...,
max_tokens=...) -> str`:

| spec | client | notes |
|---|---|---|
| `ollama:llama3.1` | `OllamaClient` | `POST /api/chat`, `format` = schema; default `http://localhost:11434` |
| `openai` / `openai:gpt-4o-mini` | `OpenAICompatClient` | OpenAI and anything speaking `/chat/completions`; key from `OPENAI_API_KEY` (optional for non-OpenAI URLs); `OPENAI_BASE_URL` or `@URL` for a proxy or local server |
| `anthropic` / `anthropic:claude-sonnet-5-5` | `AnthropicClient` | Messages API with the forced `emit_spec` tool; key from `ANTHROPIC_API_KEY` |

A bare provider (`--llm openai`, `--llm anthropic`) means its default model, and `claude`, `gpt` and `chatgpt` are accepted as spoken names (`--llm claude` is Anthropic). The clients adapt to what a model refuses instead of failing: OpenAI
reasoning models want `max_completion_tokens` and no `temperature`; a model with a smaller output limit than a spec needs is retried once with the limit its
error message names; a tool schema Anthropic rejects falls back to plain JSON text. These paths are covered by tests against scripted HTTP transports, and
the OpenAI request/response path was also run for real against a local OpenAI-compatible server (Ollama's `/v1`); the hosted APIs themselves need your key,
so `reel doctor --llm ...` is the quickest way to confirm your account works.

**Privacy:** the renderer, the offline planner and `ollama:` models on your own machine keep everything local. The script is sent off-machine only
if you pick `openai:` / `anthropic:` (their APIs) or an Ollama model whose name ends in `-cloud` (Ollama's hosted models, which `reel doctor`
lists like any other); `reel` never chooses one for you.

Network trouble becomes `LLMUnavailable` with a message that says what to try (`ollama serve`, check the key, `ollama pull MODEL`...), transient errors
(429, 5xx, "overloaded") are retried with back-off, and API keys appear only in request headers, never in errors or `repr`s. Adding a provider is one
subclass of `LLMClient`; the clients take an injectable `httpx` transport, so tests never touch the network.

## The offline planner (no model)

`HeuristicSpecGenerator` is a deterministic rule-based screenwriter: `(script, style, seed, target_duration, catalog) -> spec`, the same input
always giving the same output.

1. **parse**: a title line, `Name:` dialogue lines, "quoted" speech with its attribution, `(stage directions)`, plain narration; each sentence is a unit;
2. **cast**: characters from names, `Narrator:` prefixes and kinship/role words (at most 3), an archetype guessed from words like kid / grandma / robot /
   boss / hero (default everyman), distinct stable colours;
3. **plan**: units are grouped into 8-14 scenes sized by reading time (~2.6 words/s + 1 s); short scripts get silent "bridge" scenes, long ones are condensed
   to their most salient sentences; the first scene gets a `title`, the last exclamation a `shout`;
4. **stage**: per scene a background chosen from keywords (only templates that exist), time of day and mood, characters on slots with entrances, actions
   from verbs (walked, jumped, waved, laughed, thought, pointed, fell, picked up, gasped ...), listeners look at the speaker, SFX from the actions, a slow
   camera move, a rotating transition;
5. **fit**: the same `normalize_spec` rescales to the 45-60 s budget, and the linter has the last word.

It understands `Name:` lines for names written in Latin script; other scripts (Hindi, Arabic, CJK ...) are treated as narration, which renders fine
(captions are shaped, the voice follows the language) but gives one narrator instead of a cast: use a model, or write the JSON, for multi-character
dialogue in those scripts.

## Writing and testing without a model

Everything is testable offline with `ReplayClient`, which returns scripted replies in order and records every request:

```python
from reel.llm.client import ReplayClient
from reel.llm.generator import LLMSpecGenerator

client = ReplayClient(["I'm sorry, here is a story:", my_spec_dict])      # a bad first reply, then a good one
result = LLMSpecGenerator(client, max_repairs=2).generate("A cat finds a boat.", style="flat_vector", seed=3)
result.spec, result.lint.ok, result.attempts, result.notes              # attempts == 2: the first reply was repaired
client.requests[0].system        # the exact system prompt that was sent
client.requests[1].messages[-1]  # the correction that was sent back
```

`ManualSpecGenerator("my.json", style="stickman", seed=9).generate("")` is the "I wrote the JSON" path with the same return type, and
`tests/test_llm.py` shows the contract each piece is held to.
