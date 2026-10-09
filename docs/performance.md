# Performance and caching

`reel` renders frames in parallel worker processes and caches at two levels, so the edit→preview loop
stays fast. (Numbers below are from an Apple M4 Pro, 12 cores.)

## Throughput

Cold render (no cache) of the 49.7 s example story (`examples/story_50s.json`, 1491 frames, 3 characters), 1080x1920, on an Apple M4 Pro
(12 cores, 10 render workers, otherwise idle machine; they run at about 92% utilisation):

| style | wall time | file size | per frame on one core: scene draw | post-FX | frame-cache write | grain | x264 |
|---|---|---|---|---|---|---|---|
| `stickman` | **≈ 22 s** | 25 MB | 17 ms | 27 ms | 16 ms | 20 ms | 20 ms |
| `flat_vector` | **≈ 38 s** | 22 MB | 22 ms | 91 ms | 24 ms | 20 ms | 30 ms |
| `paper_cutout` | **≈ 64 s** | 74 MB | 60 ms | 90 ms | skipped, see below | 20 ms | 70 ms |
| any style, `--preview` (360x640) | 8-17 s | 1-3 MB | | | | | |

The audio stage (TTS with macOS `say`, music, mix) adds about 7 s the first time and nothing on re-renders (clips are cached). Running other heavy
work at the same time slows these figures a lot (the same flat render took 63 s while tests were running): the workers are CPU- and
memory-bandwidth-bound.

The big levers, in order: **static backgrounds are drawn once per scene** (as "plates") and blitted with the camera transform; **depth-of-field
layers are rasterised and blurred at 1/2 or 1/4 of the frame size** (they are blurred anyway: a 2.5x speed-up of the paper style's scene draw
with differences of a fraction of a grey level); post-FX is numpy on `uint8`/`float32` (bloom and chromatic aberration are most of it); characters (the
only thing that changes every frame) are tens of shapes. To see where a render spends its time set `REEL_TRACE=file`: every worker then appends
`pid segment start end` (wall-clock seconds) for each 60-frame chunk it encodes, enough to compute utilisation and spot a straggler.

## File size: the bitrate cap

Film grain and paper fibre are close to incompressible noise, and at a plain `-crf 18` the paper style cost 30-60 Mbps (a 343 MB, 50 s reel).
Full renders therefore use **`-crf 20` with a VBV cap of 10 Mbps** (`--maxrate`, bufsize = one second): `paper_cutout` lands near 12 Mbps
(≈ 75 MB), `flat_vector` and `stickman` at 3-5 Mbps (20-30 MB) are untouched by the cap, and encoding is about 30% faster. The cap applies per
2-second chunk, so it is a ceiling, not an average. Previews are uncapped (CRF 26, `veryfast`). The paper style's grain strength is 0.34
(it was 0.55, which looked heavy at 1:1 and was most of the bitrate). `--maxrate none` (or `0`) lifts the cap, `--maxrate 16M` raises it (`16M`, `16m`, `16000k` and `16000000` all mean 16 Mbit/s: reel normalises the suffix, because ffmpeg reads a lower-case `m` as milli), `--crf` moves quality.

## The two caches

Both live in `~/.cache/reel` (override with `REEL_CACHE_DIR` or `--cache-dir`; `reel cache stats|clear [frames|segments|tts|tmp]`; an LRU prune keeps
it under 8 GB). A third folder, `tts/`, holds synthesised speech clips (keyed by engine, model/settings, voice, speed and text) so re-renders never
re-synthesise; the prune never deletes them (they are tiny, and an online voice charged for each). A fourth, `tmp/`, holds chunks that are still being
written; a failed or interrupted run removes its own, and the next run purges any left over by a kill. Listing or pruning tolerates files that another
process removes meanwhile, an unwritable cache folder switches the cache off with a warning instead of failing the render, and a damaged cache file counts as a miss and is deleted.
The cache keys also cover the installed fonts (the system's families and any `$REEL_FONT_DIR` / `assets/fonts` files), so installing a font a style or a script needs
re-renders what it affects.

### Frame cache (`frames/`)
The "world" frame after post-FX but *before* grain and captions, stored losslessly (zlib level 1 of the BGR bytes, ≈0.3-1.5 MB
per 1080×1920 frame). Its key is a digest of everything that frame's pixels depend on:

* the engine fingerprint (sha256 of the source files that can change a pixel (everything except the web server, the command line and the LLM clients) and any loaded plugin files, so a code change invalidates stale pixels),
* style name + version, resolution, fps, post-FX config, scene seed,
* the scene's background spec (template + params), the characters on stage (archetype, palette, props, scale, depth),
* the **camera state** at that frame, every character's **baked pose row** (all rig channels + root position), the scene-local time,
* for transition frames: both scenes' digests and the blend progress.

Captions, grain and letterbox are *not* in the key because they are applied afterwards, so editing a caption never re-renders pixels (as long as the frames were cached, see below).

**It is adaptive.** Storing a frame costs a zlib pass (≈ 15-25 ms for flat or marker styles, 60 ms for paper) and disk. Frames full of texture
(paper fibre) compress to ~3 MB each, so a cold paper reel would write 4.5 GB, spend 18% more time doing it, and win back little (the world
frame is ≈ 250 ms to recompute). Once 3 frames in a row compress to more than 1.8 MB (per 1080x1920) a cache instance stops storing frames:
paper reels are cached as video chunks only (≈ 75 MB for the story, an identical re-render takes 0.8 s) and a caption edit re-renders the frames
of the chunks it touches; flat and stickman frames (0.3-1 MB) and every preview-size frame are cached as before.

### Segment cache (`segments/`)
The timeline is cut into segments of ≤ 60 frames that never cross a scene/transition boundary and each segment is encoded to its own
raw H.264 chunk (closed GOP, no B-frames, in-band SPS/PPS). A segment's key is the digest of the *finished-frame* keys inside it
(frame key + caption state + grain seed) plus the encoder settings. A re-render concatenates cached chunks byte-for-byte and remuxes -
no re-encode. Because segments are keyed by content, not by position, inserting or lengthening a scene leaves every other scene's chunks valid.

| scenario (3-scene test reel at 1080×1920) | wall time |
|---|---|
| cold render | 9.4 s |
| identical re-render | 0.1 s |
| edit one caption | 2.5 s (2 of 5 segments re-encoded) |
| move one action's timing | only the frames whose pose changed |

## Determinism

Every random choice is `sha256(seed, stable path)`; nothing uses Python's `hash()` or global random state. The same spec and seed
produce **byte-identical MP4s** regardless of worker count or cache state (verified in `tests/test_render.py` for 1 vs 3 workers).
x264 runs with a fixed thread count per segment, `-bf 0`, fixed GOP and `+bitexact` muxing flags. Muxing is also pinned down: the raw H.264 chunks
carry no timestamps, so the mux stamps frame *n* at *n*/fps explicitly (without that, `-shortest` silently dropped the audio track), uses `-max_interleave_delta 0`
(ffmpeg otherwise lays out the audio and video chunks differently on every run), and pads or trims the audio to exactly the video's frame count.
The MP4 is written to a hidden `.name.partial.mp4` and renamed when it is complete, so Ctrl-C or a crash never leaves a half-written file under the real name (and an older
file is left untouched); Ctrl-C ends the worker pool at once and exits with status 130.

## Tuning

* `reel render --workers N` (default: cores − 2). Spawned workers each hold up to three scene stages (plates are the big memory item).
* `--crf 20` (default; lower = better) with `--maxrate 10M` (see above); `--preview` uses crf 26 + `veryfast` and no cap.
* `--scale 0.5` for a half-size draft that still has the full look (post-FX sizes scale with the frame).
* `--range 0:6` renders only the first six seconds (and trims the audio to match).
* If a scene is slow, `reel debug-backgrounds` shows its static cost (shape count); keep animated shapes under ~60 and use particles for atmosphere.
