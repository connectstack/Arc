# Audio

Sound is generated **offline and deterministically**: voice-over from a local TTS engine, a generated music bed, procedurally
synthesised sound effects, all mixed with ducking and loudness normalisation, then muxed into the MP4 next to the (never
re-encoded) video. Nothing is downloaded at runtime and the same spec + seed always produces the same samples.

```
spec ──► prepare_audio() ──► audio.wav (48 kHz stereo, ≈ -16 LUFS) ──► ffmpeg mux (AAC 192k) ──► reel.mp4
         ├─ voice-over   one TTS clip per spoken caption, trimmed + levelled, placed at the caption's start
         ├─ music        a file, or procedural[:mood]
         ├─ sfx          scene `sfx` events (+ footsteps/landings from the actions when `auto_sfx`)
         └─ mix          ducking under speech · BS.1770 loudness · look-ahead limiter at -1 dBFS
```

`reel render` / `reel build` run this automatically when the spec has any audio (`--no-audio` skips it, `--tts ENGINE` picks the
speech engine). A failure in the audio stage never loses the picture: the video is written without sound and the reason is printed.

## The `audio` block

```json
"audio": {
  "music": "procedural:playful",   // a file path, "procedural", "procedural:<mood>" or null
  "voiceover": "tts",              // "tts" | "file" | "none"
  "ducking": true,                 // lower the music under speech
  "auto_sfx": true,                // footsteps / landings / gasps ... emitted by the actions themselves
  "tts_voice": null,               // default voice (engine specific), see below
  "music_gain_db": -16, "voice_gain_db": 0, "sfx_gain_db": -8
}
```

| field | meaning |
|---|---|
| `music` | `procedural` picks the style's mood (`paper_cutout` → playful, otherwise upbeat); `procedural:<mood>` with `upbeat calm playful tense epic mysterious sad corporate`; any other string is an audio file (anything ffmpeg decodes), resolved relative to the spec. `reel lint` checks the mood name and the file |
| `voiceover` | `tts`: every *spoken* caption is synthesised (captions of style `subtitle` by default; a caption's `speak: true/false` overrides). `file`: one recording at `voiceover_file`, placed at t = 0, from which the word timings are recovered inside each caption's window. `none`: silent |
| `ducking` | music dips under speech (look-ahead, hold and raised-cosine ramps, so it never pumps) |
| `auto_sfx` | adds the sounds the actions emit (`footstep`, `jump`, `land`, `thud`, `gasp`, `laugh`, `pickup`) |
| `*_gain_db` | trims, −60 … +12 dB |

Scene-level effects are plain data: `"sfx": [{"name": "boing", "t": 1.2}]` (`t` is scene-local seconds; an optional `volume`, 0-2).

## Voice-over and captions

For each spoken caption the pipeline synthesises the text, trims the silence, levels the clip to a fixed speech loudness and places
it at the caption's global start. Then two things follow from the *actual* speech, not from your estimate:

* **the caption is retimed** to stay on screen while it is spoken (plus a short tail), and a long sentence that would overflow its scene
  is reported as a warning rather than cut;
* **word timings** come from the engine when it has them (`elevenlabs`, `babble`), else they are estimated from the audio alone (a 5 ms energy envelope, pause detection, and a small dynamic programme that
  matches pauses to word boundaries by syllable count): they drive the spoken-word highlight in the subtitle style and the
  lip-flap of the character named in `speaker` (the `talk` action reads the same timings).

Per-speaker voices: the character's own `voice` (`characters[].voice`), else `audio.tts_voice`, else the engine's own choice for that
speaker:

* `say` picks from the installed macOS voices by the character's **archetype** (kid → Junior, or Flo for a girl's name; elder → Grandpa,
  or Grandma for a woman's name; robot → Fred; boss → Ralph; hero → Daniel), preferring the English variant, and otherwise a stable pick
  (hashed from the character id) among a few natural voices, so a story does not sound like one person doing every part. A voice that is not
  installed is skipped.
* `babble` derives a stable pitch from the character id.
* `piper` speaks every character that has no explicit `voice` with its default model; name a model per character (`"voice": "en_US-ryan-high"`)
  if they should differ.

### Language follows the script

A caption in Devanagari, Arabic, Hebrew, Thai, kana/kanji, Hangul, Cyrillic or Greek is detected from its script, and `say` switches to an installed
voice for that language (Hindi → Lekha, Arabic → Majed, Japanese → a Japanese voice, ...) instead of letting an English voice spell it out. If the
machine has no voice for that language, `prepare_audio` says so once as a warning and uses the English voice (Piper needs a model per language:
set `characters[].voice` or `audio.tts_voice` to one). The syllable count that drives word timing and lip-flap also handles these scripts
(one per ideograph, one per Indic consonant or vowel not joined by a virama, an estimate elsewhere).

Most languages have a single installed voice, so a whole cast would sound alike. When `say` swaps a character's voice for the language's, it keeps a
**pitch of its own** for that character (say's `[[pbas N]]` baseline pitch, taken from the voice the character would have had in English: a kid high, an
elder lower, a robot lowest), so Mia, Pip and Bolt are still told apart by ear. Narration, and a character whose `voice` is already in the right
language, are left alone; the pitch is part of the speech-cache key, and `characters[].voice` can carry one explicitly: `"Lekha|pbas=60"`.

### TTS engines

| engine | needs | notes |
|---|---|---|
| `piper` | the `piper` command (or the `piper-tts` package) **and a voice model** (`.onnx` + `.onnx.json`) | neural, best quality. Voices are looked up as `<name>.onnx` in `$PIPER_VOICES`, `~/.local/share/piper` and `./voices`; `voice:3` picks speaker 3 of a multi-speaker model; `$PIPER_VOICE` names a default |
| `say` | macOS | the system voices (`say -v ?`), converted through ffmpeg |
| `babble` | nothing | a deterministic syllable-timed formant "babble" so lip-sync, tests and CI work on any machine; not meant to be intelligible. Words in other scripts and numbers get syllables too (stable per word), so a Hindi or Chinese line is not silent |
| `elevenlabs` | `ELEVENLABS_API_KEY` and a network | the **online** engine: natural voices in 30-90 languages, exact word timings. Opt-in, billed by ElevenLabs, see below |

`--tts auto` (the default) is `piper` if it can run, else `say` on macOS, else `babble`; it **never** picks `elevenlabs`. Naming an engine
that cannot run is an error that says what is missing, and it stops *before* anything is rendered (no silent video instead). Every engine caches its clips on disk (`<cache dir>/tts`, keyed by engine,
model/settings, voice, speed and text), so re-rendering never re-synthesises, and `reel cache` pruning never deletes speech clips (only frames and chunks).

A voice that produces no sound at all for a line (a voice without phonemes for that script, an engine that "succeeds" with silence) is
replaced by `babble` for that line with a warning, so the character is never silently mute; a caption with nothing to say (`...`) stays silent without one.

### ElevenLabs (optional, online)

Everything else in reel runs on your machine. This engine sends each *spoken line* (the caption text and nothing else) to `api.elevenlabs.io`
and uses your ElevenLabs credits for it, so it is built to be predictable:

* **Opt-in.** `--tts elevenlabs`, or `REEL_TTS=elevenlabs` to make it the default when no `--tts` is given. `auto` never chooses it.
* **Your key stays yours.** Put it in your shell or in a git-ignored `.env` (see [`.env.example`](../.env.example)); never in a spec, a
  script, a commit or a chat. reel sends it only in the `xi-api-key` header, scrubs it from every message and `repr`, and refuses a base URL that is not
  `https` (a cloned repository's `.env` can set a key but cannot set `ELEVENLABS_BASE_URL`, so it cannot redirect yours).
* **Pay once per line.** Each clip is cached on disk by (model, settings, voice, text): re-rendering, changing the picture or the music, or editing
  *other* lines costs nothing, and cached lines need no network (or even a key). The CLI prints what a run billed
  (`audio: elevenlabs (...): 33 characters in 2 line(s) were sent to api.elevenlabs.io and billed`). `reel cache clear tts` deletes the clips, and with them
  what you paid for.
* **Exact word timings.** The `/with-timestamps` endpoint returns per-character times, so the spoken-word highlight and the lip-flap follow the
  real speech instead of an estimate from the audio. If the alignment ever disagrees with the text, the usual estimate takes over.

```bash
export ELEVENLABS_API_KEY=...                  # or put it in a git-ignored .env
reel doctor --tts elevenlabs                   # checks key, plan, credits left, model and voice list; synthesises nothing, so no credits
reel voices --tts elevenlabs                   # names, ids and traits of your voices
reel build script.txt --tts elevenlabs -o reel.mp4
export REEL_TTS=elevenlabs                     # optional: make it the default
```

| variable | meaning |
|---|---|
| `ELEVENLABS_API_KEY` | the key (required) |
| `ELEVENLABS_MODEL` | model id; default `eleven_multilingual_v2` (every plan, 29 languages). Newer ones such as `eleven_v4` (90+ languages, most expressive) or the cheaper `eleven_flash_v2_5` work if your plan has them: `reel doctor --tts elevenlabs` tells you whether the model is in your list |
| `ELEVENLABS_VOICE` | one voice (a name or an id) for every speaker that has no `voice` of its own |
| `ELEVENLABS_BASE_URL` | another region (`https://api.eu.residency.elevenlabs.io`, ...); shell or a file you named with `REEL_ENV_FILE` only |

Voices: `characters[].voice` / `audio.tts_voice` may be a voice **id** or a **name** from your account (case-insensitive, a prefix works). Without
one, each speaker gets a fitting voice from your account's *premade* voices (never a clone): a woman's name picks a female voice, the
`kid`/`hero` archetypes a young voice, `elder` an old one, and two speakers never share a voice while there are unused ones. The choice is stable between runs. A voice name that is not an ElevenLabs
voice (a spec written for another engine says `"voice": "high"`) is replaced by an automatic pick with one warning. The multilingual models
read Hindi, Arabic, Japanese ... with any voice, so there is no per-language voice to choose.

Failures never lose the picture: a rejected key or an empty account is reported once and the remaining lines use `babble`; a rate limit or server error is retried with a
pause (and the finished lines are cached, so a re-run only asks for the rest).

**Getting a Piper voice.** Piper needs a voice file from the [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
collection (for example `en_US-lessac-medium.onnx` and its `.onnx.json`). reel never downloads one for you: put the two files in
`~/.local/share/piper/` (or anywhere on `$PIPER_VOICES`) and `reel render --tts piper` will use it. `reel doctor` shows which engines are usable and which Piper voices it found.

Adding an engine is one class: implement `TTSEngine` (`available()`, `synthesize(text, voice, speed) -> TTSClip`, `list_voices()`), or
subclass `CachedEngine` and implement only `_render(text, voice, speed)` to get the on-disk clip cache for free, then add it to `get_tts_engine`.
Two optional hooks let an engine talk to the user: `take_warnings()` (shown as `audio:` warnings) and `usage()` (a one-line note such as what was billed).

## Music

`generate_music(mood, duration, seed)` is a tiny arranger: from the (mood, seed) pair it picks a key, tempo and chord progression,
voice-leads the chords and writes a pad, a bass line, a plucked/bell arpeggio, a short hook and light drums with an energy curve
(intro → build → body with a lift every second phrase → outro). Instruments are cheap vectorised synths (band-limited wavetables,
FM, modal bells, noise drums) with a stereo reverb and a ping-pong delay; a 60 s bed takes about a second. The result depends only on
`(mood, duration, seed)`, so a preview and the full render start the same way. A music *file* is decoded through ffmpeg, looped with 1 s equal-power crossfades (or trimmed) to the reel length, and faded in (0.5 s) and out (1.5 s).

## Sound effects

About forty effects ship, all synthesised (numpy only) at 48 kHz mono: UI (`click`, `ding`, `pop`, `typing`), cartoon (`boing`, `whoosh`,
`swoosh`, `record_scratch`, `tada`), body and foley (`footstep`, `jump`, `land`, `thud`, `gasp`, `laugh`, `slap`, `door_knock`), nature and
ambience (`wind`, `splash`, `bubble`), and stingers (`success`, `fail`, `sparkle`, `magic`, `drumroll`, `applause`). `reel list sfx` shows
them all and [reference/sfx.md](reference/sfx.md) is generated from the registry.

* **Swap in recordings, no code:** a file `<name>.wav` in `$REEL_SFX_DIR` or `./assets/sfx/` replaces the synthesised sound of that name,
  and a wav with a *new* name registers a new effect that specs and the linter can use. Both directories are read when `reel.audio.sfx`
  is imported (`./assets/sfx` is relative to the current directory); when rendering, `<spec dir>/assets/sfx` is searched as well, so
  there a recording is found for playback even if the linter, run from another directory, has not registered its name.
* **Add a synthesised effect:** a function `fn(rng, sr) -> float array` decorated with `@register_sfx("name", summary=...)`, in
  `reel/audio/sfx.py` or in any [plugin](adding-an-action.md) (`--plugin PATH`). It must be deterministic (draw randomness from the `rng`
  it is handed); the module has a small DSP toolbox (envelopes, oscillators, filters, band noise, modal resonators, a vocal-tract formant
  source, a reverb).

## The mix

Each voice clip is first levelled to a speech loudness of -20 dBFS RMS and the music bed to -14 dBFS RMS (effects are loudness-matched
when they are synthesised), so the `*_gain_db` fields are trims relative to that. The music is ducked by 12 dB under speech (effects by
3 dB), the sum is normalised to about **-16 LUFS** (ITU-R BS.1770-4 / EBU R128 integrated loudness, K-weighted, gated) and finally limited to -1 dBFS by a
look-ahead soft-knee limiter that never hard-clips. The mixer is plain numpy: resampling, panning, ducking, loudness and the limiter
are each usable on their own (`reel.audio.mix`).

## From Python

```python
from pathlib import Path
from reel.audio import prepare_audio
from reel.core.spec import ReelSpec

spec = ReelSpec.from_file("examples/story_50s.json")
plan = prepare_audio(spec, base_dir=Path("examples"), work_dir=Path("/tmp/audio"), engine="auto")
plan.wav            # the finished soundtrack, or None when the spec has no audio at all
plan.spec           # a copy of the spec with captions retimed to the speech: render THIS one
plan.word_timings   # {scene: {caption: [(t0, t1, word), ...]}} for highlights and lip-flap
plan.warnings       # things that should not stop a render: a missing music file, speech longer than its scene, ...
plan.report         # duration, peak, loudness, seconds ducked, number of clips
```

`prepare_audio` never mutates the spec it is given.

## Troubleshooting

* **No sound in the MP4** - run with `--no-audio` to confirm the picture is fine, then check the printed `audio:` warnings; a spec with
  `voiceover: none`, `music: null`, no `sfx` and `auto_sfx: false` has no audio track by design.
* **`TTS engine 'piper' is not usable`** - the command is installed but no `.onnx` voice was found (see above). `--tts say` or `--tts babble` work meanwhile.
* **Caption disappears before the speech ends** - the speech runs past the end of its scene. The audio is never cut (it keeps playing into
  the next scene and may overlap the next line, which is also warned about), but the on-screen caption cannot outlive its scene;
  lengthen the scene or shorten the text (the warning names the caption).
* **Different machines, different voices** - `say` voices and Piper models differ per machine; `babble` and everything else (music, SFX, mix) are reproducible for a given spec and seed on one machine (floating-point results can
  differ in the last bits between numpy builds or CPUs).
