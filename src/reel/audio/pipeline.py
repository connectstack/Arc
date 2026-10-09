"""``prepare_audio``: a :class:`ReelSpec` in, a finished soundtrack WAV out.

What it does, in order:

1. **Voice-over.**  ``voiceover == "tts"``: every spoken caption (``speak`` if set, else style
   ``subtitle``) is synthesised, trimmed to its speech, levelled and placed at the caption's global
   start; the caption is *retimed* so it stays on screen while it is spoken and per-word timings are
   produced for the highlight.  ``"file"``: one recording is placed at t=0 and the word timings
   are found inside each caption's window.
2. **Music.**  ``audio.music`` is a file (decoded through ffmpeg) or ``procedural[:mood]``.
3. **SFX.**  Scene ``sfx`` events, plus (``auto_sfx``) the footsteps/landings the actions emitted.
4. **Mix.**  Gains, ducking, loudness normalisation, limiting -> ``work_dir/audio.wav``.

It never mutates the spec it is given: the (possibly retimed) copy is in ``AudioPlan.spec`` and
that is the spec the renderer should draw.  Problems that should not stop a render (a missing music
file, an unknown sfx, speech that overflows its scene) become ``AudioPlan.warnings``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from reel.audio.align import estimate_word_timings, spread_words, voiced_span
from reel.audio.mix import (
    SAMPLE_RATE,
    SILENCE_LUFS,
    AudioDecodeError,
    MixClip,
    MixPlan,
    MixReport,
    active_level_db,
    decode_audio,
    mix_tracks,
)
from reel.audio.music import DEFAULT_MOOD, generate_music, parse_music_spec
from reel.audio.sfx import synth
from reel.audio.tts import BabbleTTS, TTSClip, TTSEngine, get_tts_engine, text_language
from reel.core.catalog import CATALOG
from reel.core.rng import stable_int
from reel.core.spec import CaptionSpec, ReelSpec
from reel.core.timeline import Timeline, compute_timeline

#: active-speech RMS (dBFS) every voice clip is levelled to before ``voice_gain_db``
VOICE_REF_DB = -20.0
#: RMS (dBFS) the music bed is levelled to before ``music_gain_db``
MUSIC_REF_DB = -14.0
#: seconds a retimed caption stays up after its speech ends
CAPTION_TAIL_SEC = 0.25
#: pre/post-roll kept around the speech when a TTS clip is trimmed
CLIP_LEAD_SEC = 0.02
CLIP_TAIL_SEC = 0.06
#: action event -> (sfx name, extra gain in dB); everything else the actions emit is ignored
AUTO_EVENT_SFX: dict[str, tuple[str, float]] = {
    "footstep": ("footstep", -14.0),
    "jump": ("jump", -6.0),
    "land": ("land", -6.0),
    "thud": ("thud", -6.0),
    "gasp": ("gasp", -6.0),
    "laugh": ("laugh", -6.0),
    "pickup": ("pickup", -6.0),
}
#: style pack -> procedural music mood when ``audio.music`` is just ``"procedural"``
STYLE_MOODS = {"paper_cutout": "playful", "stickman": "upbeat", "flat_vector": "upbeat"}
_SFX_VARIANTS = 3


@dataclass
class AudioPlan:
    """Result of :func:`prepare_audio`."""

    wav: Path | None  # the mixed soundtrack, or None when the spec has no audio at all
    spec: ReelSpec  # a copy of the input spec with caption windows retimed to the speech
    word_timings: dict[int, dict[int, list[tuple[float, float, str]]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    report: MixReport | None = None
    notes: list[str] = field(
        default_factory=list
    )  # information, not problems (an online voice: what was billed)
    clips: list[MixClip] = field(
        default_factory=list
    )  # everything that was mixed (voice, music, sfx): what a UI draws waveforms from


# ----------------------------------------------------------------------------- helpers
def _speakable(cap: CaptionSpec) -> bool:
    spoken = cap.speak if cap.speak is not None else cap.style == "subtitle"
    return bool(spoken) and bool(cap.text.strip())


def _resolve(base: Path, name: str) -> Path:
    p = Path(name).expanduser()
    return p if p.is_absolute() else base / p


def _gain_to_level(x: np.ndarray, sr: int, target_db: float, max_peak: float = 0.95) -> float:
    """dB to add so the active RMS of ``x`` is ``target_db``, without pushing the peak past ``max_peak``."""
    level = active_level_db(x, sr)
    if level <= SILENCE_LUFS + 1.0:
        return 0.0
    gain = target_db - level
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.0:
        gain = min(gain, 20.0 * math.log10(max_peak / peak))
    return gain


def _is_silent(clip: TTSClip) -> bool:
    """True when the clip holds no sound at all (digital silence or less than -54 dBFS), whatever the speech detector says."""
    x = np.asarray(clip.samples, dtype=np.float32).reshape(-1)
    return x.shape[0] < int(0.03 * clip.sample_rate) or float(np.max(np.abs(x))) < 0.002


def _has_words(text: str) -> bool:
    """False for "..." or "-": nothing to say, so silence is the right answer."""
    return any(c.isalnum() for c in text)


def _default_mood(spec: ReelSpec) -> str:
    return STYLE_MOODS.get(spec.meta.style, DEFAULT_MOOD)


# ----------------------------------------------------------------------------- voice: TTS
def _voice_for(spec: ReelSpec, engine: TTSEngine, cap: CaptionSpec) -> str | None:
    """The voice a spoken caption is read with: its character's, else ``audio.tts_voice``, else the engine's pick."""
    char = spec.character(cap.speaker) if cap.speaker else None
    return (
        (char.voice if char and char.voice else None)
        or spec.audio.tts_voice
        or engine.speaker_voice(cap.speaker, char)
    )


@dataclass
class SpeechEstimate:
    """What speaking a spec would need from an engine, worked out without synthesising anything."""

    engine: str
    online: bool
    lines: int  # distinct (text, voice) pairs that would be spoken
    cached_lines: int  # of those, already in the speech cache (free, and no network needed)
    new_lines: int
    billable_characters: int  # characters an online engine would bill (0 for offline engines)


def estimate_speech(spec: ReelSpec, engine: TTSEngine) -> SpeechEstimate:
    """How many spoken lines are new (would be synthesised, and billed by an online engine) and how many are cached."""
    from reel.audio.tts import _cache_paths, tts_cache_key

    seen: set[str] = set()
    cached = 0
    chars = 0
    for sc in spec.scenes:
        for cap in sc.captions:
            if not _speakable(cap) or cap.t0 >= sc.duration_sec:
                continue
            voice = engine.voice_for_text(cap.text, _voice_for(spec, engine, cap))
            text = " ".join(cap.text.strip().split())
            key = tts_cache_key(
                engine.name, getattr(engine, "version", "1"), engine.resolve_voice(voice), 1.0, text
            )
            if key in seen:
                continue
            seen.add(key)
            npy, meta = _cache_paths(key)
            if npy.exists() and meta.exists():
                cached += 1
            else:
                chars += len(text)
    online = engine.name == "elevenlabs"
    return SpeechEstimate(
        engine=engine.name,
        online=online,
        lines=len(seen),
        cached_lines=cached,
        new_lines=len(seen) - cached,
        billable_characters=chars if online else 0,
    )


def _tts_voice(
    spec: ReelSpec,
    tl: Timeline,
    engine_name: str | None,
    warnings: list[str],
    timings: dict[int, dict[int, list[tuple[float, float, str]]]],
    notes: list[str] | None = None,
) -> list[MixClip]:
    jobs = [
        (si, ci, cap)
        for si, sc in enumerate(spec.scenes)
        for ci, cap in enumerate(sc.captions)
        if _speakable(cap)
    ]
    if not jobs:
        return []
    engine: TTSEngine = get_tts_engine(
        engine_name
    )  # an explicitly requested engine that cannot run raises
    fallback = BabbleTTS()
    warned_voices: set[str] = set()
    warned_langs: set[tuple[str, str | None]] = set()
    failures: dict[
        str, list[str]
    ] = {}  # engine error -> the captions that fell back to babble because of it
    memo: dict[tuple[str, str | None], TTSClip] = {}
    clips: list[MixClip] = []
    spans: list[
        tuple[float, float, str]
    ] = []  # placed speech (global start, end, label) for overlap checks

    for si, ci, cap in jobs:
        scene = spec.scenes[si]
        label = f"scenes[{si}].captions[{ci}]"
        if cap.t0 >= scene.duration_sec:
            warnings.append(
                f"{label}: starts at {cap.t0:g}s, after scene {scene.id!r} ends ({scene.duration_sec:g}s); not spoken"
            )
            continue
        voice = _voice_for(spec, engine, cap)
        if voice and voice not in warned_voices:
            warned_voices.add(voice)
            msg = engine.voice_warning(voice)
            if msg:
                warnings.append(msg)
        voice = engine.voice_for_text(
            cap.text, voice
        )  # an English voice cannot read Hindi: use one that can
        lang = text_language(cap.text)
        if (
            lang
            and engine.voice_language(voice) not in (None, lang)
            and (lang, voice) not in warned_langs
        ):
            warned_langs.add((lang, voice))
            warnings.append(
                f"{label}: the text is {lang!r} but there is no {lang!r} voice (using {voice or 'the default'}), so it will be "
                "mispronounced; install a voice for it or set characters[].voice"
            )
        key = (cap.text.strip(), voice)
        clip = memo.get(key)
        if clip is None:
            try:
                clip = engine.synthesize(cap.text.strip(), voice)
            except Exception as exc:  # a flaky engine must not lose the whole render
                failures.setdefault(str(exc), []).append(
                    label
                )  # reported once per distinct problem, below
                clip = fallback.synthesize(cap.text.strip(), None)
            else:
                if _is_silent(clip) and _has_words(cap.text):
                    # e.g. a voice that has no phonemes for the script: silence would also leave the captions untimed
                    said = f" {lang!r} text" if lang else " this text"
                    warnings.append(
                        f"{label}: the {engine.name!r} voice {voice or 'default'!r} produced no audible speech for{said}; "
                        "the babble voice is used for this line (install a voice for the language or set characters[].voice)"
                    )
                    clip = fallback.synthesize(cap.text.strip(), None)
            memo[key] = clip
            warnings.extend(engine.take_warnings())

        samples = np.asarray(clip.samples, dtype=np.float32).reshape(-1)
        sr = int(clip.sample_rate)
        span = voiced_span(samples, sr)
        if _is_silent(
            clip
        ):  # nothing audible (a caption of "..." or "-"): nothing to place, retime or highlight
            continue
        a = max(0.0, span[0] - CLIP_LEAD_SEC) if span else 0.0
        b = min(samples.shape[0] / sr, span[1] + CLIP_TAIL_SEC) if span else samples.shape[0] / sr
        trimmed = samples[int(a * sr) : max(int(a * sr) + 1, round(b * sr))]
        if clip.words:
            words = [(max(0.0, w0 - a), max(0.0, w1 - a), w) for w0, w1, w in clip.words]
        else:
            words = estimate_word_timings(cap.text, trimmed, sr)
        dur = trimmed.shape[0] / sr

        start = tl.global_time(si, cap.t0)
        timings.setdefault(si, {})[ci] = [(cap.t0 + w0, cap.t0 + w1, w) for w0, w1, w in words]

        # keep the caption up while it is spoken (never past the end of the scene)
        needed = min(scene.duration_sec, cap.t0 + dur + CAPTION_TAIL_SEC)
        new_t1 = cap.t1 if needed <= cap.t1 <= scene.duration_sec else needed
        if cap.t0 + dur > scene.duration_sec + 1e-6:
            warnings.append(
                f"{label}: speech ({dur:.1f}s from {cap.t0:g}s) runs {cap.t0 + dur - scene.duration_sec:.1f}s past the end of scene {scene.id!r}"
            )
        if new_t1 > cap.t1 + 1e-9:
            for cj, other in enumerate(scene.captions):
                if cj != ci and other.style == cap.style and cap.t1 <= other.t0 < new_t1 - 1e-6:
                    warnings.append(
                        f"{label}: retimed to end at {new_t1:.2f}s, which overlaps captions[{cj}] (starts {other.t0:g}s)"
                    )
        cap.t1 = new_t1

        for s0, s1, other_label in spans:
            overlap = min(s1, start + dur) - max(s0, start)
            if overlap > 0.05:
                warnings.append(f"{label}: voice overlaps {other_label} by {overlap:.2f}s")
        spans.append((start, start + dur, label))

        gain = _gain_to_level(trimmed, sr, VOICE_REF_DB) + spec.audio.voice_gain_db
        clips.append(MixClip(start, trimmed, sr, gain_db=gain, kind="voice", name=label))
    for problem, labels in failures.items():
        where = labels[0] if len(labels) == 1 else f"{labels[0]} and {len(labels) - 1} more line(s)"
        warnings.append(
            f"{where}: TTS engine {engine.name!r} failed ({problem}); used the babble voice for "
            f"{'this line' if len(labels) == 1 else 'these lines'}"
        )
    usage = engine.usage()
    if usage and notes is not None:
        notes.append(usage)
    return clips


# ----------------------------------------------------------------------------- voice: recording
def _file_voice(
    spec: ReelSpec,
    tl: Timeline,
    base: Path,
    warnings: list[str],
    timings: dict[int, dict[int, list[tuple[float, float, str]]]],
) -> list[MixClip]:
    audio = spec.audio
    if not audio.voiceover_file:
        warnings.append(
            "audio.voiceover is 'file' but audio.voiceover_file is not set; no voice-over"
        )
        return []
    path = _resolve(base, audio.voiceover_file)
    try:
        data = decode_audio(path, sr=SAMPLE_RATE, channels=2)
    except AudioDecodeError as exc:
        warnings.append(f"voice-over skipped: {exc}")
        return []
    mono = data.mean(axis=1)
    for si, sc in enumerate(spec.scenes):
        for ci, cap in enumerate(sc.captions):
            if not _speakable(cap) or cap.t1 <= cap.t0:
                continue
            g0 = tl.global_time(si, cap.t0)
            g1 = tl.global_time(si, min(cap.t1, sc.duration_sec))
            window = mono[max(0, int(g0 * SAMPLE_RATE)) : max(0, int(g1 * SAMPLE_RATE))]
            words = estimate_word_timings(cap.text, window, SAMPLE_RATE) if window.size else []
            if not words:
                words = spread_words(cap.text.split(), 0.0, cap.t1 - cap.t0)
            timings.setdefault(si, {})[ci] = [(cap.t0 + a, cap.t0 + b, w) for a, b, w in words]
    gain = _gain_to_level(mono, SAMPLE_RATE, VOICE_REF_DB) + audio.voice_gain_db
    return [MixClip(0.0, data, SAMPLE_RATE, gain_db=gain, kind="voice", name=path.name)]


# ----------------------------------------------------------------------------- music
def _music(spec: ReelSpec, total: float, base: Path, warnings: list[str]) -> list[MixClip]:
    audio = spec.audio
    if not audio.music:
        return []
    default = _default_mood(spec)
    try:
        mood = parse_music_spec(audio.music, default=default)
    except ValueError as exc:
        warnings.append(f"{exc}; using {default!r}")
        mood = default
    if mood is not None:
        bed = generate_music(mood, total, seed=spec.meta.seed, sr=SAMPLE_RATE)
        name = f"procedural:{mood}"
    else:
        path = _resolve(base, audio.music)
        try:
            bed = decode_audio(path, sr=SAMPLE_RATE, channels=2, max_seconds=total + 2.0)
        except AudioDecodeError as exc:
            warnings.append(f"music skipped: {exc}")
            return []
        name = path.name
    if bed.shape[0] == 0:
        return []
    gain = _gain_to_level(bed, SAMPLE_RATE, MUSIC_REF_DB) + audio.music_gain_db
    return [MixClip(0.0, bed, SAMPLE_RATE, gain_db=gain, kind="music", name=name)]


# ----------------------------------------------------------------------------- sfx
class _SfxBank:
    """Renders effects once per (name, variant); repeated cues cycle variants so they do not machine-gun."""

    def __init__(self, seed: int, search_dirs: Sequence[Path], warnings: list[str]) -> None:
        self.seed = seed
        self.dirs = list(search_dirs)
        self.warnings = warnings
        self._cache: dict[tuple[str, int], np.ndarray | None] = {}
        self._count: dict[str, int] = {}
        self._warned: set[str] = set()

    def next(self, name: str, where: str) -> np.ndarray | None:
        k = self._count.get(name, 0)
        self._count[name] = k + 1
        variant = k % _SFX_VARIANTS
        key = (name, variant)
        if key not in self._cache:
            try:
                self._cache[key] = synth(
                    name,
                    SAMPLE_RATE,
                    stable_int(self.seed, name, variant) % (2**31),
                    search_dirs=self.dirs,
                )
            except KeyError:
                if name not in self._warned:
                    self._warned.add(name)
                    near = CATALOG.sfx.suggest(name)
                    hint = f" (did you mean {', '.join(repr(n) for n in near)}?)" if near else ""
                    self.warnings.append(f"{where}: unknown sfx {name!r}{hint}; skipped")
                self._cache[key] = None
        return self._cache[key]


def _sfx(
    spec: ReelSpec,
    tl: Timeline,
    base: Path,
    action_events: Sequence[tuple[float, str]],
    warnings: list[str],
) -> list[MixClip]:
    audio = spec.audio
    bank = _SfxBank(spec.meta.seed, [base / "assets" / "sfx"], warnings)
    clips: list[MixClip] = []
    for si, sc in enumerate(spec.scenes):
        for xi, ev in enumerate(sc.sfx):
            where = f"scenes[{si}].sfx[{xi}]"
            if ev.t > sc.duration_sec + 1e-6:
                warnings.append(
                    f"{where}: {ev.name!r} at {ev.t:g}s is after scene {sc.id!r} ends ({sc.duration_sec:g}s); skipped"
                )
                continue
            if ev.volume <= 0:
                continue
            snd = bank.next(ev.name, where)
            if snd is None:
                continue
            gain = audio.sfx_gain_db + 20.0 * math.log10(ev.volume)
            clips.append(
                MixClip(
                    tl.global_time(si, ev.t),
                    snd,
                    SAMPLE_RATE,
                    gain_db=gain,
                    kind="sfx",
                    name=f"{sc.id}:{ev.name}:{xi}",
                )
            )
    if audio.auto_sfx:
        last: dict[str, float] = {}
        for t, event in sorted(action_events):
            mapped = AUTO_EVENT_SFX.get(event)
            if mapped is None or t < 0:
                continue
            name, extra = mapped
            if t - last.get(name, -1.0) < 0.04:  # two characters on the same beat -> one sound
                continue
            last[name] = t
            snd = bank.next(name, f"action event {event!r} at {t:.2f}s")
            if snd is not None:
                clips.append(
                    MixClip(
                        float(t),
                        snd,
                        SAMPLE_RATE,
                        gain_db=audio.sfx_gain_db + extra,
                        kind="sfx",
                        name=f"auto:{event}:{t:.3f}",
                    )
                )
    return clips


# ----------------------------------------------------------------------------- entry point
def prepare_audio(
    spec: ReelSpec,
    *,
    base_dir: Path | None = None,
    work_dir: Path,
    engine: str | None = None,
    action_events: Sequence[tuple[float, str]] = (),
    frame_range_sec: tuple[float, float] | None = None,
) -> AudioPlan:
    """Build the soundtrack for ``spec`` and return it with the retimed spec and word timings.

    ``base_dir`` resolves relative music/voice-over/sfx paths (default: the current directory),
    ``engine`` is a TTS engine name (``auto`` | ``piper`` | ``say`` | ``babble`` | ``elevenlabs``; an engine that was
    asked for by name but cannot run raises; ``None`` = ``REEL_TTS`` or ``auto``), ``action_events`` is ``[(global_seconds, name)]`` from
    the baked actions (used when ``audio.auto_sfx``), and ``frame_range_sec`` trims the mix to a
    window for partial previews.  ``AudioPlan.wav`` is ``None`` (and nothing is written) when the
    spec has no music, no sfx, no voice-over and no events.
    """
    out_spec = spec.model_copy(deep=True)
    audio = out_spec.audio
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    warnings: list[str] = []
    notes: list[str] = []
    timings: dict[int, dict[int, list[tuple[float, float, str]]]] = {}
    tl = compute_timeline(out_spec)
    total = tl.total_frames / tl.fps

    has_events = audio.auto_sfx and any(name in AUTO_EVENT_SFX for _, name in action_events)
    if not (
        audio.music
        or any(sc.sfx for sc in out_spec.scenes)
        or audio.voiceover != "none"
        or has_events
    ):
        spoken_but_off = any(c.speak for sc in out_spec.scenes for c in sc.captions)
        if spoken_but_off:
            warnings.append(
                "captions have speak=true but audio.voiceover is 'none'; nothing will be spoken"
            )
        return AudioPlan(None, out_spec, timings, warnings, None)
    if audio.voiceover == "none" and any(c.speak for sc in out_spec.scenes for c in sc.captions):
        warnings.append(
            "captions have speak=true but audio.voiceover is 'none'; nothing will be spoken"
        )

    clips: list[MixClip] = []
    if audio.voiceover == "tts":
        clips += _tts_voice(out_spec, tl, engine, warnings, timings, notes)
    elif audio.voiceover == "file":
        clips += _file_voice(out_spec, tl, base, warnings, timings)
    clips += _music(out_spec, total, base, warnings)
    clips += _sfx(out_spec, tl, base, action_events, warnings)

    if not clips:
        return AudioPlan(None, out_spec, timings, warnings, None, notes)
    mixed = list(clips)

    window: tuple[float, float] | None = None
    if frame_range_sec is not None:
        lo, hi = max(0.0, frame_range_sec[0]), min(total, frame_range_sec[1])
        if hi > lo:
            window = (lo, hi)
        else:
            warnings.append(f"frame_range_sec {frame_range_sec} is empty; mixing the whole reel")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / "audio.wav"
    plan = MixPlan(
        total_duration=total,
        clips=clips,
        ducking=audio.ducking,
        seed=out_spec.meta.seed,
        window=window,
    )
    report = mix_tracks(plan, wav, SAMPLE_RATE)
    return AudioPlan(wav, out_spec, timings, warnings, report, notes, mixed)
