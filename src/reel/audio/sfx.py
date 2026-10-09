"""Procedurally synthesised sound effects (numpy only, deterministic) + the DSP toolbox.

Every effect is a function ``fn(rng, sr) -> float array`` registered with :func:`register_sfx`
into ``CATALOG.sfx``.  :func:`synth` renders one by name: 48 kHz mono float32 in [-1, 1], 0.05-3 s,
DC-free, with a short fade-in and fade-out so nothing ever clicks.  A file ``<name>.wav`` in
``$REEL_SFX_DIR`` or ``./assets/sfx`` replaces the synthesised sound (and a wav with a new name
registers a new effect), so a project can swap in recorded samples without touching code.

The toolbox at the top (envelopes, oscillators, zero-phase FFT filters, time-varying band noise,
modal resonators, a vocal-tract formant source, a small reverb) is shared with
:mod:`reel.audio.music` and :mod:`reel.audio.tts`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from reel.audio.mix import decode_audio, fast_fft_len, k_weight, read_wav, resample, to_mono
from reel.core.catalog import CATALOG, Catalog
from reel.core.rng import stable_int

SAMPLE_RATE = 48_000
MIN_SFX_SEC = 0.05
MAX_SFX_SEC = 3.0
SFX_DIR_ENV = "REEL_SFX_DIR"

Floats = np.ndarray
SfxFn = Callable[[np.random.Generator, int], np.ndarray]


# =============================================================================== toolbox
def nsamp(seconds: float, sr: int = SAMPLE_RATE) -> int:
    return max(1, round(seconds * sr))


def taxis(n: int, sr: int = SAMPLE_RATE) -> Floats:
    """Time axis in seconds (float64)."""
    return np.arange(n, dtype=np.float64) / sr


def exp_decay(t: Floats, tau: float) -> Floats:
    return np.exp(-t / max(tau, 1e-6))


def raised_attack(t: Floats, attack: float) -> Floats:
    """0 -> 1 over ``attack`` seconds on a raised cosine, then 1."""
    a = max(attack, 1e-6)
    return np.where(t >= a, 1.0, 0.5 - 0.5 * np.cos(np.pi * np.minimum(t, a) / a))


def smooth_bump(u: Floats, peak: float = 0.5, power: float = 1.0) -> Floats:
    """Bell on u in [0,1] rising to 1 at ``peak`` and back to 0 (raised-cosine flanks)."""
    peak = min(max(peak, 1e-3), 1.0 - 1e-3)
    up = 0.5 - 0.5 * np.cos(np.pi * np.clip(u / peak, 0.0, 1.0))
    dn = 0.5 + 0.5 * np.cos(np.pi * np.clip((u - peak) / (1.0 - peak), 0.0, 1.0))
    return np.asarray(np.where(u < peak, up, dn) ** power)


def phase_of(freq: Floats, sr: int = SAMPLE_RATE) -> Floats:
    """Phase in radians of an oscillator whose instantaneous frequency is ``freq`` (Hz)."""
    return 2.0 * np.pi * np.cumsum(freq, dtype=np.float64) / sr


def sine(freq: float | Floats, n: int, sr: int = SAMPLE_RATE, phase0: float = 0.0) -> Floats:
    if np.isscalar(freq):
        return np.sin(2.0 * np.pi * float(freq) * taxis(n, sr) + phase0)  # type: ignore[arg-type]
    return np.sin(phase_of(np.asarray(freq), sr) + phase0)


def white(rng: np.random.Generator, n: int) -> Floats:
    return rng.standard_normal(n)


def spectral_filter(
    x: Floats, sr: int, response: Callable[[Floats], Floats], pad: int = 2048
) -> Floats:
    """Zero-phase filter defined by a magnitude function of frequency (Hz)."""
    n = x.shape[0]
    total = fast_fft_len(n + 2 * pad)
    xp = np.zeros(total)
    xp[pad : pad + n] = x
    spec = np.fft.rfft(xp)
    spec *= response(np.fft.rfftfreq(total, 1.0 / sr))
    return np.asarray(np.fft.irfft(spec, total)[pad : pad + n])


def lowpass(x: Floats, sr: int, fc: float, order: int = 4) -> Floats:
    fc = min(fc, 0.49 * sr)
    return spectral_filter(x, sr, lambda f: 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order)))


def highpass(x: Floats, sr: int, fc: float, order: int = 4) -> Floats:
    fc = max(fc, 1.0)
    return spectral_filter(
        x, sr, lambda f: 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-3)) ** (2 * order))
    )


def bandpass(x: Floats, sr: int, lo: float, hi: float, order: int = 3) -> Floats:
    lo, hi = max(lo, 1.0), min(hi, 0.49 * sr)
    return spectral_filter(
        x,
        sr,
        lambda f: (
            1.0
            / np.sqrt(1.0 + (lo / np.maximum(f, 1e-3)) ** (2 * order))
            / np.sqrt(1.0 + (f / hi) ** (2 * order))
        ),
    )


def peaking(x: Floats, sr: int, fc: float, q: float, gain_db: float) -> Floats:
    """Zero-phase resonant boost/cut around ``fc`` (Hz)."""
    g = 10.0 ** (gain_db / 20.0)
    return spectral_filter(
        x, sr, lambda f: 1.0 + (g - 1.0) / (1.0 + (q * (f / fc - fc / np.maximum(f, 1e-3))) ** 2)
    )


def pink_noise(rng: np.random.Generator, n: int, sr: int = SAMPLE_RATE) -> Floats:
    """1/f noise, unit variance."""
    y = spectral_filter(white(rng, n), sr, lambda f: 1.0 / np.sqrt(np.maximum(f, 20.0)))
    return y / (np.std(y) + 1e-12)


def band_noise(
    rng: np.random.Generator, n: int, sr: int, centre: float | Floats, bw: float | Floats
) -> Floats:
    """Unit-variance noise concentrated around ``centre`` Hz with total bandwidth ``bw`` Hz.

    Both may vary per sample (sweeps, formants): a low-passed noise is shifted to the moving centre
    by multiplication with a cosine carrier; time-varying bandwidth cross-fades between octave
    spaced low-pass versions of the same noise.
    """
    src = white(rng, n + 4096)
    centre_a = np.broadcast_to(np.asarray(centre, dtype=np.float64), (n,))
    bw_a = np.broadcast_to(np.asarray(bw, dtype=np.float64), (n,))
    lo_b, hi_b = float(max(np.min(bw_a), 20.0)), float(max(np.max(bw_a), 20.0))
    levels = [lo_b]
    while levels[-1] * 2.0 < hi_b:
        levels.append(levels[-1] * 2.0)
    if hi_b > levels[-1] * 1.001:
        levels.append(hi_b)
    if len(levels) == 1:
        base = lowpass(src, sr, levels[0] / 2.0)[2048 : 2048 + n]
        base = base / (np.std(base) + 1e-12)
    else:
        banks = []
        for lv in levels:
            b = lowpass(src, sr, lv / 2.0)[2048 : 2048 + n]
            banks.append(b / (np.std(b) + 1e-12))
        pos = np.interp(np.log(np.maximum(bw_a, 20.0)), np.log(levels), np.arange(len(levels)))
        lo_i = np.clip(np.floor(pos).astype(int), 0, len(levels) - 2)
        w = pos - lo_i
        stack = np.stack(banks)
        idx = np.arange(n)
        base = stack[lo_i, idx] * (1.0 - w) + stack[lo_i + 1, idx] * w
    return np.asarray(
        base * np.sqrt(2.0) * np.cos(phase_of(centre_a, sr) + rng.uniform(0, 2 * np.pi))
    )


def modal(
    t: Floats,
    freqs: Sequence[float],
    amps: Sequence[float],
    taus: Sequence[float],
    phases: Sequence[float] | None = None,
) -> Floats:
    """Sum of exponentially damped sinusoids (struck bars, bells, wood, glass ...)."""
    out = np.zeros_like(t)
    n = t.shape[0]
    sr_est = 1.0 / (t[1] - t[0]) if n > 1 else 1.0
    for i, (f, a, tau) in enumerate(zip(freqs, amps, taus)):
        m = min(n, int(14.0 * tau * sr_est) + 1)
        ph = phases[i] if phases is not None else 0.0
        out[:m] += a * np.exp(-t[:m] / tau) * np.sin(2.0 * np.pi * f * t[:m] + ph)
    return out


def add_at(buf: Floats, snippet: Floats, start: int, gain: float = 1.0) -> None:
    """Add ``snippet`` into ``buf`` at sample ``start`` (silently clipped to the buffer)."""
    lo = max(start, 0)
    hi = min(start + snippet.shape[0], buf.shape[0])
    if hi > lo:
        buf[lo:hi] += gain * snippet[lo - start : hi - start]


def fft_convolve(a: Floats, b: Floats) -> Floats:
    n = a.shape[0] + b.shape[0] - 1
    nf = fast_fft_len(n)
    return np.asarray(np.fft.irfft(np.fft.rfft(a, nf) * np.fft.rfft(b, nf), nf)[:n])


def reverb(
    x: Floats,
    sr: int,
    rng: np.random.Generator,
    rt60: float = 0.6,
    wet: float = 0.25,
    predelay: float = 0.012,
    damp_hz: float = 6000.0,
    tail: float | None = None,
) -> Floats:
    """Dry + noise-burst-convolution reverb; the output is ``tail`` seconds longer (default rt60*0.8)."""
    tail_n = nsamp(rt60 * 0.8 if tail is None else tail, sr)
    m = nsamp(rt60 * 1.1 + predelay, sr)
    t = taxis(m, sr)
    ir = white(rng, m) * np.exp(-6.9078 * np.maximum(t - predelay, 0.0) / rt60)
    ir[t < predelay] = 0.0
    ir = lowpass(ir, sr, damp_hz, order=2)
    ir /= np.sqrt(np.sum(ir * ir)) + 1e-12
    padded = np.concatenate([x, np.zeros(tail_n)])
    wet_sig = fft_convolve(padded, ir)[: padded.shape[0]]
    return padded + wet * wet_sig


def soft_clip(x: Floats, drive: float = 1.0) -> Floats:
    return np.tanh(x * drive) / np.tanh(drive)


def fade_out(x: Floats, sr: int, seconds: float) -> Floats:
    m = min(x.shape[0], nsamp(seconds, sr))
    if m > 1:
        x = x.copy()
        x[-m:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, m))
    return x


def fade_in(x: Floats, sr: int, seconds: float) -> Floats:
    m = min(x.shape[0], nsamp(seconds, sr))
    if m > 1:
        x = x.copy()
        x[:m] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, m))
    return x


def normalize(x: Floats, peak: float = 0.9) -> Floats:
    m = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (peak / m) if m > 1e-12 else x


def vocal_tract(
    f0: Floats,
    formants: Floats,
    sr: int,
    bandwidths: Sequence[float] = (90.0, 110.0, 160.0),
    *,
    tilt: float = 1.15,
    max_freq: float = 5200.0,
) -> Floats:
    """Voiced source filtered by a cascade of formant resonances (additive, vectorised).

    ``f0`` is the per-sample fundamental in Hz; ``formants`` is ``(k, n)`` (or ``(k,)``) formant
    centre tracks in Hz.  Each harmonic ``h*f0`` gets the amplitude ``h**-tilt`` times the product
    of the second-order resonance gains, so vowels, glides and pitch contours all come from one
    cheap loop over harmonics.  Output is normalised to unit peak.
    """
    n = f0.shape[0]
    form = np.broadcast_to(
        np.asarray(formants, dtype=np.float64).reshape(len(bandwidths), -1), (len(bandwidths), n)
    )
    ph = phase_of(f0, sr)
    kmax = max(1, int(min(max_freq, 0.45 * sr) / max(float(f0.min()), 40.0)))
    out = np.zeros(n)
    for k in range(1, kmax + 1):
        fk = k * f0
        amp = k ** (-tilt) * (fk < min(max_freq, 0.45 * sr))
        for j, bw in enumerate(bandwidths):
            r = fk / form[j]
            amp = amp / np.sqrt((1.0 - r * r) ** 2 + (r * bw / form[j]) ** 2)
        out += amp * np.sin(k * ph)
    return out / (np.max(np.abs(out)) + 1e-12)


def formant_response(
    freqs_hz: Floats, formants: Sequence[float], bandwidths: Sequence[float]
) -> Floats:
    """Static cascade-resonator magnitude (for shaping aspiration / fricative noise)."""
    out = np.ones_like(freqs_hz)
    for f_c, bw in zip(formants, bandwidths):
        r = freqs_hz / f_c
        out = out / np.sqrt((1.0 - r * r) ** 2 + (r * bw / f_c) ** 2)
    return out


# =============================================================================== registry
LEVEL_DB = -15.0  # nominal 200 ms K-weighted loudness every effect is matched to (before level_db)
PEAK_MAX = 0.9  # ... unless that needs a higher sample peak


@dataclass(frozen=True)
class SfxDef:
    """A registered effect: ``fn(rng, sr)`` returns the raw mono signal.

    ``level_db`` nudges the effect's loudness relative to the nominal level (ambience < impacts).
    """

    name: str
    summary: str
    fn: SfxFn
    level_db: float = 0.0

    def render(self, seed: int = 0, sr: int = SAMPLE_RATE) -> np.ndarray:
        """Synthesise deterministically from ``(name, seed)``, clean it and match its loudness."""
        rng = np.random.default_rng(stable_int("sfx", self.name, seed))
        return match_level(clean_sfx(self.fn(rng, sr), sr), sr, LEVEL_DB + self.level_db)


def register_sfx(
    name: str,
    summary: str = "",
    *,
    level_db: float = 0.0,
    catalog: Catalog | None = None,
    replace: bool = False,
) -> Callable[[SfxFn], SfxFn]:
    """Decorator: register ``fn(rng, sr) -> samples`` as the sound effect ``name``."""

    def wrap(fn: SfxFn) -> SfxFn:
        doc = (fn.__doc__ or "").strip().splitlines()
        text = summary or (doc[0] if doc else "")
        defn = SfxDef(name, text, fn, level_db)
        (catalog or CATALOG).sfx.register(name, defn, replace=replace, summary=text)
        return fn

    return wrap


def window_loudness_db(x: np.ndarray, sr: int, window: float = 0.2) -> float:
    """Loudest ``window``-second stretch, K-weighted mean square in dB (short sounds count as
    if sitting in silence, which is how the ear integrates them)."""
    if x.size == 0:
        return -120.0
    p = k_weight(x, sr) ** 2
    w = max(1, min(int(window * sr), p.shape[0]))
    cs = np.concatenate([[0.0], np.cumsum(p)])
    sums = np.concatenate([cs[w:] - cs[:-w], cs[-1] - cs[-w:]])  # full windows + shrinking tail
    return float(10.0 * math.log10(max(float(sums.max()) / max(int(window * sr), 1), 1e-12)))


def match_level(x: np.ndarray, sr: int, level_db: float, peak_max: float = PEAK_MAX) -> np.ndarray:
    """Scale ``x`` so its window loudness is ``level_db`` (dB), but never beyond ``peak_max``."""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 1e-9:
        return x
    gain = 10.0 ** ((level_db - window_loudness_db(x, sr)) / 20.0)
    gain = min(gain, peak_max / peak)
    return (x * np.float32(gain)).astype(np.float32)


def clean_sfx(x: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Make any recipe output safe: finite, DC-free, 0.05-3 s, click-free edges, |x| <= 0.98."""
    y = np.nan_to_num(np.asarray(x, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    min_n, max_n = nsamp(MIN_SFX_SEC, sr), nsamp(MAX_SFX_SEC, sr)
    if y.shape[0] > max_n:
        y = y[:max_n]
    if y.shape[0] < min_n:
        y = np.concatenate([y, np.zeros(min_n - y.shape[0])])
    if y.shape[0] > 256:
        y = highpass(y, sr, 18.0, order=2)
    y = fade_in(y, sr, 0.0008)
    y = fade_out(y, sr, 0.012 if y.shape[0] / sr < 0.5 else 0.03)
    m = float(np.max(np.abs(y)))
    if m > 0.98:
        y = y * (0.98 / m)
    return y.astype(np.float32)


# ----------------------------------------------------------------------------- sample overrides
def sfx_search_dirs(extra: Sequence[str | Path] = ()) -> list[Path]:
    dirs = [Path(d).expanduser() for d in extra]
    env = os.environ.get(SFX_DIR_ENV, "")
    dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    dirs.append(Path("assets") / "sfx")
    return dirs


def find_sfx_file(name: str, extra_dirs: Sequence[str | Path] = ()) -> Path | None:
    """``<name>.wav`` in the extra dirs, ``$REEL_SFX_DIR`` or ``./assets/sfx`` (first hit wins)."""
    for d in sfx_search_dirs(extra_dirs):
        p = d / f"{name}.wav"
        if p.is_file():
            return p
    return None


def load_sample(path: Path, sr: int = SAMPLE_RATE, max_seconds: float = 10.0) -> np.ndarray:
    """Load a recorded effect as mono float32 at ``sr`` (stdlib WAV first, ffmpeg for the rest)."""
    try:
        data, file_sr = read_wav(path)
        mono = to_mono(data)
        if file_sr != sr:
            mono = resample(mono, file_sr, sr)
    except Exception:
        mono = to_mono(decode_audio(path, sr=sr, channels=1, max_seconds=max_seconds))
    mono = np.nan_to_num(mono[: nsamp(max_seconds, sr)]).astype(np.float32)
    m = float(np.max(np.abs(mono))) if mono.size else 0.0
    if m > 0.98:
        mono = mono * (0.98 / m)
    return fade_out(mono.astype(np.float64), sr, 0.01).astype(np.float32)


def discover_sfx_files(
    extra_dirs: Sequence[str | Path] = (), catalog: Catalog | None = None
) -> list[str]:
    """Register every ``*.wav`` in the sfx dirs whose name is not already an effect.

    This is what makes "drop assets/sfx/{name}.wav" work for brand new names (the linter checks
    names against the registry).  Returns the names that were added.
    """
    cat = catalog or CATALOG
    added: list[str] = []
    for d in sfx_search_dirs(extra_dirs):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.wav")):
            if p.stem in cat.sfx._entries:
                continue

            def fn(rng: np.random.Generator, sr: int, _p: Path = p) -> np.ndarray:
                return load_sample(_p, sr)

            cat.sfx.register(
                p.stem,
                SfxDef(p.stem, f"recorded sample ({p.name})", fn),
                summary=f"recorded sample ({p.name})",
            )
            added.append(p.stem)
    return added


def synth(
    name: str,
    sr: int = SAMPLE_RATE,
    seed: int = 0,
    *,
    search_dirs: Sequence[str | Path] = (),
    catalog: Catalog | None = None,
) -> np.ndarray:
    """Render the effect ``name`` -> mono float32 at ``sr`` (a matching wav file wins over the synth)."""
    cat = catalog or CATALOG
    path = find_sfx_file(name, search_dirs)
    if path is not None:
        return load_sample(path, sr)
    if name not in cat.sfx and search_dirs:
        discover_sfx_files(search_dirs, cat)
    defn: SfxDef = cat.sfx.get(name)
    return defn.render(seed, sr)


# =============================================================================== recipes
def brass(freq: Floats, bright: Floats, sr: int, harmonics: int = 14) -> Floats:
    """Additive brass-like tone: harmonic ``k`` falls off as ``k**-0.9`` and is scaled by
    ``bright**(0.55 (k-1))``, so a ``bright`` curve opening/closing 0..1 gives a "wah"."""
    ph = phase_of(freq, sr)
    sig = np.zeros(freq.shape[0])
    for k in range(1, harmonics + 1):
        sig += k**-0.9 * bright ** (0.55 * (k - 1)) * np.sin(k * ph)
    return sig


# --- UI / cartoon ------------------------------------------------------------------------
@register_sfx("pop", summary="Short bubbly pop - something appears or bursts")
def _pop(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.16, sr)
    t = taxis(n, sr)
    f = 260.0 + 760.0 * exp_decay(t, 0.011) * rng.uniform(0.9, 1.1)
    ph = phase_of(f, sr)
    body = (
        (np.sin(ph) + 0.3 * np.sin(2.0 * ph + 0.4)) * exp_decay(t, 0.03) * raised_attack(t, 0.0006)
    )
    tick = bandpass(white(rng, n), sr, 1800, 7000) * exp_decay(t, 0.0012) * 0.45
    return normalize(body + tick, 0.9)


@register_sfx("boing", summary="Cartoon spring - wobbling pitch that settles")
def _boing(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.95, sr)
    t = taxis(n, sr)
    base = 150.0 + 340.0 * exp_decay(t, 0.2)
    wobble = 1.0 + 0.24 * exp_decay(t, 0.34) * np.sin(2.0 * np.pi * 8.6 * t + 0.6)
    ph = phase_of(base * wobble, sr)
    sig = np.sin(ph) + 0.55 * np.sin(2.0 * ph + 0.3) + 0.22 * np.sin(3.0 * ph + 1.1)
    sig *= raised_attack(t, 0.004) * exp_decay(t, 0.4)
    return normalize(lowpass(sig, sr, 3200), 0.85)


@register_sfx("click", summary="Crisp UI click")
def _click(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.06, sr)
    t = taxis(n, sr)
    tick = bandpass(white(rng, n), sr, 2200, 8000) * exp_decay(t, 0.0013)
    ring = np.sin(2.0 * np.pi * 3100 * t) * exp_decay(t, 0.0035) * 0.7
    thunk = np.sin(2.0 * np.pi * 640 * t) * exp_decay(t, 0.004) * 0.35
    return normalize(tick + ring + thunk, 0.8)


@register_sfx("ding", summary="Clean bright 'ding' - a correct answer, a desk bell", level_db=-2.0)
def _ding(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(1.3, sr)
    t = taxis(n, sr)
    f0 = 1318.5 * rng.uniform(0.99, 1.01)
    sig = modal(
        t,
        [f0 * r for r in (1.0, 2.0, 2.76, 4.07)],
        (1.0, 0.33, 0.17, 0.06),
        (0.5, 0.24, 0.14, 0.06),
    )
    sig += bandpass(white(rng, n), sr, 3000, 9000) * exp_decay(t, 0.003) * 0.1
    sig *= raised_attack(t, 0.0005)
    return normalize(reverb(fade_out(sig, sr, 0.35), sr, rng, rt60=0.45, wet=0.18), 0.8)


def _bell_tone(t: Floats, f0: float, tau: float, brightness: float = 1.0) -> Floats:
    """Struck-metal tube/bell partials."""
    ratios = (1.0, 2.76, 5.40, 8.93)
    amps = (1.0, 0.5 * brightness, 0.22 * brightness, 0.08 * brightness)
    taus = (tau, tau * 0.55, tau * 0.3, tau * 0.14)
    return modal(t, [f0 * r for r in ratios], amps, taus)


@register_sfx(
    "chime", summary="Soft cluster of metal chimes - a door chime or wind chime", level_db=-2.0
)
def _chime(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(2.0, sr)
    buf = np.zeros(n)
    notes = [1318.5, 1568.0, 1760.0, 1975.5, 2349.3]
    order = rng.permutation(len(notes))[:4]
    pos = 0.0
    for k, idx in enumerate(order):
        t = taxis(nsamp(1.5, sr), sr)
        tone = fade_out(
            _bell_tone(t, notes[int(idx)] * 0.5, 0.5) * raised_attack(t, 0.0006), sr, 0.5
        )
        add_at(buf, tone, nsamp(pos, sr), rng.uniform(0.6, 1.0))
        pos += rng.uniform(0.09, 0.17)
        if k == 0:
            pos += 0.03
    return normalize(reverb(buf, sr, rng, rt60=0.9, wet=0.25, tail=0.6)[: nsamp(2.8, sr)], 0.8)


@register_sfx("camera_shutter", summary="Mechanical camera shutter - click-clack")
def _shutter(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.26, sr)
    buf = np.zeros(n)
    for start, amp, f_res in ((0.0, 1.0, 1150.0), (0.072, 0.7, 1500.0), (0.115, 0.22, 2100.0)):
        m = nsamp(0.09, sr)
        t = taxis(m, sr)
        snip = bandpass(white(rng, m), sr, 1500, 8500) * exp_decay(t, 0.005)
        snip += np.sin(2.0 * np.pi * f_res * t) * exp_decay(t, 0.009) * 0.9
        snip += np.sin(2.0 * np.pi * 210 * t) * exp_decay(t, 0.016) * 0.7
        add_at(buf, snip * raised_attack(t, 0.0003), nsamp(start, sr), amp)
    return normalize(buf, 0.8)


@register_sfx("page_flip", summary="Paper page turning - a swish and a flutter")
def _page_flip(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.55, sr)
    t = taxis(n, sr)
    u = np.clip(t / 0.16, 0.0, 1.0)
    centre = 1800.0 + 3600.0 * u
    swish = (
        band_noise(rng, n, sr, centre, 2600.0)
        * np.sin(np.pi * np.minimum(t / 0.2, 1.0)) ** 2
        * (t < 0.2)
    )
    rate = 340.0 * exp_decay(np.maximum(t - 0.05, 0.0), 0.16) * (t > 0.05)
    hits = (rng.random(n) < rate / sr) * rng.uniform(0.3, 1.0, n)
    crackle = highpass(lowpass(hits, sr, 9000), sr, 1800) * 6.0
    flutter = (
        band_noise(rng, n, sr, 5200.0, 5000.0)
        * (0.5 + 0.5 * np.sin(2 * np.pi * 38 * t + 1.0))
        * exp_decay(t, 0.12)
        * 0.22
    )
    return normalize(swish * 0.9 + crackle + flutter * (t > 0.04), 0.8)


@register_sfx("pickup", summary="Two-note blip - picking up an item or coin")
def _pickup(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.36, sr)
    buf = np.zeros(n)
    for start, f in ((0.0, 1318.5), (0.075, 1975.5)):
        m = nsamp(0.28, sr)
        t = taxis(m, sr)
        tone = (
            np.sin(2 * np.pi * f * t)
            + 0.35 * np.sin(2 * np.pi * 2 * f * t)
            + 0.12 * np.sin(2 * np.pi * 3 * f * t)
        )
        tone *= raised_attack(t, 0.001) * exp_decay(t, 0.09 if start == 0.0 else 0.11)
        add_at(buf, fade_out(tone, sr, 0.12), nsamp(start, sr), 0.8 if start == 0.0 else 1.0)
    return normalize(buf, 0.8)


@register_sfx("success", summary="Bright rising jingle - win, level complete")
def _success(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(1.25, sr)
    buf = np.zeros(n)

    def tone(f: float, dur: float, tau: float) -> Floats:
        t = taxis(nsamp(dur, sr), sr)
        s = (
            np.sin(2 * np.pi * f * t)
            + 0.4 * np.sin(2 * np.pi * 2 * f * t)
            + 0.15 * np.sin(2 * np.pi * 3 * f * t + 1.0)
        )
        return fade_out(s * raised_attack(t, 0.001) * exp_decay(t, tau), sr, min(0.2, dur * 0.4))

    for k, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        add_at(buf, tone(f, 0.4, 0.14), nsamp(0.085 * k, sr), 0.8)
    for f in (1046.5, 1318.5, 1568.0, 2093.0):
        add_at(buf, tone(f, 1.0, 0.42), nsamp(0.34, sr), 0.55)
    return normalize(reverb(buf, sr, rng, rt60=0.6, wet=0.18, tail=0.25), 0.85)


@register_sfx("fail", summary="Sad trombone 'wah wah wah wahhh' - a failure")
def _fail(rng: np.random.Generator, sr: int) -> Floats:
    plan = [
        (0.0, 233.1, 0.26, False),
        (0.31, 220.0, 0.26, False),
        (0.62, 207.7, 0.26, False),
        (0.93, 196.0, 0.78, True),
    ]
    buf = np.zeros(nsamp(1.85, sr))
    for start, f, dur, last in plan:
        n = nsamp(dur, sr)
        t = taxis(n, sr)
        u = t / dur
        if last:
            bend = 2.0 ** (-(2.2 / 12.0) * np.clip((u - 0.5) / 0.5, 0.0, 1.0) ** 2)
            vib = 1.0 + 0.012 * np.clip((u - 0.25) / 0.4, 0, 1) * np.sin(2 * np.pi * 5.6 * t)
            freq = f * bend * vib
            bright = 0.35 + 0.65 * smooth_bump(u, 0.3, 1.0)
        else:
            freq = f * (1.0 + 0.02 * np.sin(2 * np.pi * 5.0 * t) * u)
            bright = smooth_bump(u, 0.45, 1.0) * 0.85 + 0.15
        env = raised_attack(t, 0.03) * (
            0.5 + 0.5 * np.cos(np.pi * np.clip((u - 0.75) / 0.25, 0, 1))
        )
        add_at(buf, brass(freq, bright, sr) * env, nsamp(start, sr), 1.0)
    return normalize(lowpass(buf, sr, 5200, order=2), 0.85)


# --- whooshes / air ------------------------------------------------------------------------
@register_sfx("whoosh", summary="Big airy sweep - something rushing past")
def _whoosh(rng: np.random.Generator, sr: int) -> Floats:
    dur = 0.9
    n = nsamp(dur, sr)
    t = taxis(n, sr)
    u = t / dur
    fc = 300.0 * 2.0 ** (3.5 * np.sin(np.pi * u**0.8))
    env = np.sin(np.pi * u**0.7) ** 2
    air = band_noise(rng, n, sr, fc, 0.75 * fc + 200.0) * env
    rumble = lowpass(white(rng, n), sr, 260) * env * 0.5
    return normalize(air + rumble, 0.9)


@register_sfx("swoosh", summary="Quick bright swipe - a hand or sword cutting the air")
def _swoosh(rng: np.random.Generator, sr: int) -> Floats:
    dur = 0.4
    n = nsamp(dur, sr)
    t = taxis(n, sr)
    u = t / dur
    fc = 1300.0 * (7800.0 / 1300.0) ** (u**0.6)
    env = np.sin(np.pi * u**0.55) ** 2
    return normalize(band_noise(rng, n, sr, fc, 0.7 * fc + 400.0) * env, 0.85)


@register_sfx("jump", summary="Rising 'hup' with a puff of air - a character jumps")
def _jump(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.36, sr)
    t = taxis(n, sr)
    f = 240.0 * (640.0 / 240.0) ** np.clip(t / 0.22, 0.0, 1.0)
    ph = phase_of(f, sr)
    tone = np.sin(ph) + 0.3 * np.sin(2 * ph + 0.5) + 0.1 * np.sin(3 * ph)
    tone *= raised_attack(t, 0.006) * np.where(
        t < 0.2, 1.0, exp_decay(np.maximum(t - 0.2, 0.0), 0.06)
    )
    puff = (
        band_noise(rng, n, sr, 900.0 + 2200.0 * np.clip(t / 0.3, 0, 1), 1400.0)
        * np.sin(np.pi * np.minimum(t / 0.32, 1.0)) ** 2
        * 0.28
    )
    return normalize(tone + puff, 0.85)


@register_sfx("gasp", summary="Sharp breathy inhale - surprise")
def _gasp(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.62, sr)
    t = taxis(n, sr)
    u = t / 0.62
    rise = np.where(u < 0.6, (u / 0.6) ** 1.3, exp_decay(np.maximum(u - 0.6, 0.0), 0.05))
    f1, f2 = 640.0 + 220.0 * u, 1150.0 + 380.0 * u
    sig = (
        band_noise(rng, n, sr, f1, 300.0)
        + 0.7 * band_noise(rng, n, sr, f2, 480.0)
        + 0.35 * band_noise(rng, n, sr, 2650.0, 700.0)
    )
    sig += highpass(white(rng, n), sr, 3500) * 0.12
    return normalize(sig * rise * raised_attack(t, 0.03), 0.8)


@register_sfx("wind", summary="Gusting wind with a faint whistle - ambience", level_db=-9.0)
def _wind(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(2.9, sr)
    t = taxis(n, sr)
    gust = (
        0.55
        + 0.25 * np.sin(2 * np.pi * 0.37 * t + rng.uniform(0, 6.28))
        + 0.2 * np.sin(2 * np.pi * 0.83 * t + rng.uniform(0, 6.28))
    )
    drift = 520.0 + 260.0 * np.sin(2 * np.pi * 0.31 * t + rng.uniform(0, 6.28))
    body = band_noise(rng, n, sr, drift, 700.0 + 500.0 * gust) * gust
    whistle = (
        band_noise(rng, n, sr, 880.0 + 220.0 * np.sin(2 * np.pi * 0.23 * t + 1.0), 90.0)
        * (gust - 0.35).clip(0)
        * 0.35
    )
    low = lowpass(white(rng, n), sr, 180) * 0.45 * gust
    sig = body + whistle + low
    return normalize(fade_in(fade_out(sig, sr, 0.8), sr, 0.5), 0.7)


@register_sfx("whistle", summary="Friendly rising-then-falling whistle", level_db=-2.0)
def _whistle(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.72, sr)
    t = taxis(n, sr)
    up = 1500.0 * (2650.0 / 1500.0) ** np.clip(t / 0.2, 0, 1)
    down = 2650.0 * (1550.0 / 2650.0) ** np.clip((t - 0.26) / 0.36, 0, 1)
    f = np.where(t < 0.26, up, down)
    vib = 1.0 + 0.012 * np.sin(2 * np.pi * 6.2 * t) * np.clip((t - 0.15) / 0.2, 0, 1)
    ph = phase_of(f * vib, sr)
    tone = np.sin(ph) + 0.05 * np.sin(2 * ph)
    env = raised_attack(t, 0.02) * (0.5 + 0.5 * np.cos(np.pi * np.clip((t - 0.58) / 0.14, 0, 1)))
    breath = band_noise(rng, n, sr, f, 700.0) * 0.07
    return normalize((tone + breath) * env, 0.8)


# --- impacts / foley -----------------------------------------------------------------------
@register_sfx("thud", summary="Heavy low impact - something drops")
def _thud(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.45, sr)
    t = taxis(n, sr)
    body = (
        soft_clip(np.sin(phase_of(46.0 + 95.0 * exp_decay(t, 0.04), sr)) * 1.3, 2.5)
        * exp_decay(t, 0.11)
        * raised_attack(t, 0.0015)
    )
    thump = np.sin(2 * np.pi * 92 * t) * exp_decay(t, 0.055) * 0.35
    low = lowpass(white(rng, n), sr, 240) * exp_decay(t, 0.035) * 0.9
    tick = bandpass(white(rng, n), sr, 700, 3200) * exp_decay(t, 0.004) * 0.22
    return normalize(body + thump + low + tick, 0.92)


@register_sfx(
    "land", summary="Soft landing - feet hit the ground with a puff of dust", level_db=-2.0
)
def _land(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.3, sr)
    t = taxis(n, sr)
    body = (
        np.sin(phase_of(54.0 + 70.0 * exp_decay(t, 0.025), sr))
        * exp_decay(t, 0.06)
        * raised_attack(t, 0.0012)
    )
    scuff = lowpass(white(rng, n), sr, 1300) * exp_decay(t, 0.04) * 0.7
    dust = (
        bandpass(white(rng, n), sr, 2200, 5200)
        * exp_decay(t, 0.05)
        * 0.16
        * raised_attack(t, 0.004)
    )
    return normalize(body + scuff + dust, 0.85)


@register_sfx("footstep", summary="Single soft shoe step on a floor", level_db=-4.0)
def _footstep(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.2, sr)
    t = taxis(n, sr)
    f_body = rng.uniform(72.0, 100.0)
    body = (
        np.sin(phase_of(f_body * 0.7 + f_body * 0.5 * exp_decay(t, 0.02), sr))
        * exp_decay(t, 0.042)
        * raised_attack(t, 0.0012)
    )
    scuff = (
        lowpass(white(rng, n), sr, rng.uniform(650.0, 1100.0))
        * exp_decay(t, 0.028)
        * rng.uniform(0.6, 0.9)
    )
    tick = bandpass(white(rng, n), sr, 1800, 4800) * exp_decay(t, 0.003) * rng.uniform(0.12, 0.26)
    return normalize(body + scuff + tick, 0.7)


@register_sfx("door_knock", summary="Three knocks on a wooden door")
def _door_knock(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(0.7, sr))
    for pos, vel in ((0.0, 1.0), (0.18, 0.82), (0.33, 0.95)):
        m = nsamp(0.3, sr)
        t = taxis(m, sr)
        f = rng.uniform(0.97, 1.03)
        tok = modal(
            t,
            [210 * f, 470 * f, 910 * f, 1500 * f],
            (1.0, 0.7, 0.4, 0.25),
            (0.05, 0.032, 0.021, 0.013),
        )
        tok += lowpass(white(rng, m), sr, 3200) * exp_decay(t, 0.002) * 0.5
        add_at(
            buf, tok * raised_attack(t, 0.0004), nsamp(pos + rng.uniform(-0.006, 0.006), sr), vel
        )
    return normalize(buf, 0.85)


@register_sfx("door_slam", summary="Heavy door slammed shut")
def _door_slam(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.9, sr)
    t = taxis(n, sr)
    body = soft_clip(np.sin(phase_of(36.0 + 42.0 * exp_decay(t, 0.05), sr)) * 1.3, 2.5) * exp_decay(
        t, 0.16
    )
    wood = modal(t, [148.0, 322.0, 613.0, 1010.0], (0.8, 0.55, 0.3, 0.16), (0.3, 0.2, 0.13, 0.08))
    slam = lowpass(white(rng, n), sr, 4800) * exp_decay(t, 0.045) * 1.0
    sig = (body * 1.1 + wood * 0.6 + slam) * raised_attack(t, 0.0008)
    for pos, amp in ((0.055, 0.35), (0.095, 0.24), (0.15, 0.14)):
        m = nsamp(0.06, sr)
        tm = taxis(m, sr)
        add_at(
            sig, bandpass(white(rng, m), sr, 900, 3600) * exp_decay(tm, 0.006) * amp, nsamp(pos, sr)
        )
    return normalize(reverb(sig, sr, rng, rt60=0.5, wet=0.2, tail=0.2), 0.95)


@register_sfx("punch", summary="Punchy body hit")
def _punch(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.3, sr)
    t = taxis(n, sr)
    thump = np.sin(phase_of(52.0 + 130.0 * exp_decay(t, 0.02), sr)) * exp_decay(t, 0.075)
    flesh = bandpass(white(rng, n), sr, 160, 1900) * exp_decay(t, 0.026) * 0.9
    crack = bandpass(white(rng, n), sr, 1500, 6500) * exp_decay(t, 0.004) * 0.6
    air = lowpass(white(rng, n), sr, 520) * exp_decay(t, 0.09) * 0.25
    return normalize((thump + flesh + crack + air) * raised_attack(t, 0.0006), 0.92)


@register_sfx("slap", summary="Sharp skin slap")
def _slap(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.24, sr)
    t = taxis(n, sr)
    crack = bandpass(white(rng, n), sr, 1100, 7800) * exp_decay(t, 0.012)
    palm = (
        np.sin(2 * np.pi * 1280 * t) * exp_decay(t, 0.018) * 0.55
        + np.sin(2 * np.pi * 640 * t) * exp_decay(t, 0.03) * 0.35
    )
    low = lowpass(white(rng, n), sr, 420) * exp_decay(t, 0.02) * 0.3
    return normalize((crack + palm + low) * raised_attack(t, 0.0003), 0.9)


@register_sfx("splash", summary="Water splash with bubbles")
def _splash(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(1.0, sr)
    t = taxis(n, sr)
    wash = (
        lowpass(white(rng, n), sr, 7000) * exp_decay(t, 0.16)
        + lowpass(white(rng, n), sr, 1500) * exp_decay(t, 0.42) * 0.9
    )
    wash *= raised_attack(t, 0.004)
    bub = np.zeros(n)
    for _ in range(16):
        start = 0.015 + float(rng.exponential(0.16))
        if start > 0.75:
            continue
        m = nsamp(0.09, sr)
        tm = taxis(m, sr)
        f0 = rng.uniform(320.0, 1500.0)
        ph = phase_of(f0 * (1.0 + 9.0 * tm), sr)
        add_at(
            bub,
            np.sin(ph) * exp_decay(tm, rng.uniform(0.014, 0.034)) * raised_attack(tm, 0.001),
            nsamp(start, sr),
            rng.uniform(0.25, 0.7),
        )
    return normalize(wash + bub * 0.9, 0.85)


@register_sfx("record_scratch", summary="DJ record scratch - the music stops")
def _record_scratch(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.78, sr)
    t = taxis(n, sr)
    v = 1.0 + 2.6 * np.sin(2 * np.pi * 5.2 * t + 0.5) * exp_decay(t, 0.38) * (t > 0.05)
    v *= 1.0 - smooth_bump(np.clip((t - 0.5) / 0.28, 0, 1), 1.0 - 1e-3, 1.0) * (t > 0.5)
    pos = np.cumsum(v) / sr
    sig = np.zeros(n)
    for f, a in ((220.0, 1.0), (277.2, 0.7), (329.6, 0.6), (110.0, 0.8)):
        for k in range(1, 9):
            sig += a * np.sin(2 * np.pi * f * k * pos) / k
    hiss = highpass(white(rng, n), sr, 2200) * 0.35
    amp = np.minimum(np.abs(v) * 1.3, 1.0)
    screech = (
        np.sin(phase_of(2300.0 * np.sqrt(np.abs(v) + 0.05), sr))
        * np.clip(np.abs(v) - 0.4, 0, 1)
        * 0.35
    )
    env = raised_attack(t, 0.004) * np.where(
        t < 0.6, 1.0, exp_decay(np.maximum(t - 0.6, 0.0), 0.06)
    )
    return normalize(lowpass(sig * 0.5 + hiss * amp + screech, sr, 9000) * env, 0.85)


# --- percussion / crowd ----------------------------------------------------------------------
@register_sfx("cymbal", summary="Crash cymbal", level_db=-2.0)
def _cymbal(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(2.8, sr)
    t = taxis(n, sr)
    metal = np.zeros(n)
    for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0):
        metal += np.sign(np.sin(2 * np.pi * f * 2.0 * t + rng.uniform(0, 6.28)))
    src = highpass(white(rng, n) + 0.35 * metal, sr, 2400, order=4)
    bright = highpass(src, sr, 6500) * exp_decay(t, 0.42)
    mid = bandpass(src, sr, 2600, 6800) * exp_decay(t, 0.95) * 0.85
    shimmer = 1.0 + 0.15 * np.sin(2 * np.pi * 7.3 * t)
    sig = (bright + mid) * shimmer * raised_attack(t, 0.0012)
    return normalize(fade_out(sig, sr, 0.4), 0.85)


def _clap_grains(rng: np.random.Generator, sr: int, count: int = 40) -> list[Floats]:
    grains = []
    m = nsamp(0.035, sr)
    t = taxis(m, sr)
    for _ in range(count):
        fc = rng.uniform(900.0, 3400.0)
        g = bandpass(white(rng, m), sr, fc * 0.6, fc * 1.5, order=2) * exp_decay(
            t, rng.uniform(0.003, 0.008)
        )
        grains.append(g / (np.max(np.abs(g)) + 1e-9))
    return grains


@register_sfx("applause", summary="A crowd clapping", level_db=-3.0)
def _applause(rng: np.random.Generator, sr: int) -> Floats:
    dur = 2.7
    n = nsamp(dur, sr)
    t = taxis(n, sr)
    env = np.clip(t / 0.45, 0, 1) ** 1.4 * np.where(
        t < 1.8, 1.0, exp_decay(np.maximum(t - 1.8, 0.0), 0.33)
    )
    grains = _clap_grains(rng, sr)
    buf = np.zeros(n)
    for rate, amp_lo, amp_hi in ((125.0, 0.25, 1.0), (260.0, 0.05, 0.3)):
        count = int(rng.poisson(rate * dur * 0.62))
        # sample times with density following env (rejection sampling)
        cand = rng.uniform(0, dur, count * 3)
        keep = cand[rng.random(count * 3) < np.interp(cand, t, env)][:count]
        for ts in keep:
            add_at(
                buf,
                grains[int(rng.integers(len(grains)))],
                nsamp(float(ts), sr),
                rng.uniform(amp_lo, amp_hi),
            )
    bed = lowpass(pink_noise(rng, n, sr), sr, 5200) * env * 0.12
    return normalize(buf + bed, 0.8)


@register_sfx("drumroll", summary="Snare drum roll building to a hit")
def _drumroll(rng: np.random.Generator, sr: int) -> Floats:
    dur = 1.95
    n = nsamp(dur, sr)
    buf = np.zeros(n)
    m = nsamp(0.14, sr)
    tm = taxis(m, sr)
    hit_cache = []
    for _ in range(8):
        h = (
            bandpass(white(rng, m), sr, 1500, 9000) * exp_decay(tm, rng.uniform(0.03, 0.045))
            + np.sin(2 * np.pi * rng.uniform(185, 205) * tm) * exp_decay(tm, 0.03) * 0.5
        )
        hit_cache.append(h)
    pos = 0.0
    while pos < 1.75:
        crescendo = 0.18 + 0.82 * (pos / 1.75) ** 1.2
        add_at(
            buf,
            hit_cache[int(rng.integers(len(hit_cache)))],
            nsamp(pos, sr),
            crescendo * rng.uniform(0.75, 1.0),
        )
        pos += 1.0 / (13.0 + 14.0 * (pos / 1.75)) * rng.uniform(0.85, 1.15)
    final_t = nsamp(1.76, sr)
    add_at(buf, hit_cache[0], final_t, 1.5)
    tc = taxis(nsamp(0.2, sr), sr)
    add_at(buf, np.sin(2 * np.pi * 70 * tc) * exp_decay(tc, 0.08) * 0.9, final_t)
    return normalize(buf, 0.9)


@register_sfx("heartbeat", summary="Two 'lub-dub' heartbeats", level_db=-1.0)
def _heartbeat(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(1.75, sr))
    for beat in (0.0, 0.84):
        for off, f, amp, tau in ((0.0, 56.0, 1.0, 0.07), (0.17, 72.0, 0.72, 0.055)):
            m = nsamp(0.3, sr)
            t = taxis(m, sr)
            thump = np.sin(phase_of(f * (1.0 + 0.5 * exp_decay(t, 0.025)), sr)) * exp_decay(t, tau)
            thump = soft_clip(thump * 1.4, 3.0)  # adds harmonics so small speakers can play it
            thump += lowpass(white(rng, m), sr, 380) * exp_decay(t, 0.03) * 0.35
            add_at(buf, thump * raised_attack(t, 0.006), nsamp(beat + off, sr), amp)
    return normalize(buf, 0.9)


@register_sfx(
    "buzz", summary="Electric buzzer - a wrong answer or a phone vibrating", level_db=-3.0
)
def _buzz(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.55, sr)
    t = taxis(n, sr)
    f = 118.0 * (1.0 + 0.004 * np.sin(2 * np.pi * 6.0 * t))
    ph = phase_of(f, sr)
    sig = np.zeros(n)
    for k in range(1, 28):
        sig += np.sin(k * ph) / k**0.85
    sig += 0.5 * np.sin(1.5 * ph + 0.3) + 0.25 * np.sin(phase_of(f * 1.011, sr) * 2.0)
    sig *= 0.7 + 0.3 * np.sin(2 * np.pi * 26.0 * t)
    env = raised_attack(t, 0.004) * (0.5 + 0.5 * np.cos(np.pi * np.clip((t - 0.48) / 0.07, 0, 1)))
    return normalize(bandpass(sig, sr, 160, 4800, order=2) * env, 0.8)


# --- sparkles / bells -------------------------------------------------------------------------
@register_sfx("bell", summary="Large resonant bell strike")
def _bell(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(2.95, sr)
    t = taxis(n, sr)
    f0 = 523.25 * rng.uniform(0.98, 1.02)
    ratios = (0.5, 1.0, 1.183, 1.506, 2.0, 2.514, 2.662, 3.0, 4.0, 5.33)
    amps = (0.6, 1.0, 0.7, 0.5, 0.6, 0.3, 0.25, 0.2, 0.12, 0.07)
    taus = (2.2, 1.6, 1.2, 0.9, 0.8, 0.5, 0.45, 0.32, 0.2, 0.12)
    sig = np.zeros(n)
    for r, a, tau in zip(ratios, amps, taus):
        for det in (-0.12, 0.12):
            sig += (
                0.5
                * a
                * np.exp(-t / tau)
                * np.sin(2 * np.pi * (f0 * r + det) * t + rng.uniform(0, 6.28))
            )
    strike = bandpass(white(rng, n), sr, 1800, 7000) * exp_decay(t, 0.004) * 0.35
    sig = (sig + strike) * raised_attack(t, 0.0008)
    return normalize(fade_out(reverb(sig, sr, rng, rt60=0.8, wet=0.14, tail=0.0), sr, 0.4), 0.85)


@register_sfx("sparkle", summary="Glittering high sparkles - shimmering dust", level_db=-2.0)
def _sparkle(rng: np.random.Generator, sr: int) -> Floats:
    dur = 1.2
    n = nsamp(dur, sr)
    buf = np.zeros(n)
    scale = [2093.0, 2349.3, 2637.0, 3135.9, 3520.0, 4186.0, 4698.6]
    for _ in range(30):
        start = dur * 0.8 * float(rng.random()) ** 1.7
        f = scale[int(rng.integers(len(scale)))] * rng.choice([1.0, 1.0, 2.0])
        if f > 0.42 * sr:
            f *= 0.5
        m = nsamp(0.3, sr)
        t = taxis(m, sr)
        ping = (
            (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * f * 2.76 * t))
            * exp_decay(t, rng.uniform(0.035, 0.11))
            * raised_attack(t, 0.0006)
        )
        add_at(buf, ping, nsamp(start, sr), rng.uniform(0.25, 1.0) * (1.0 - start / dur) ** 0.8)
    return normalize(highpass(buf, sr, 1400, order=2), 0.75)


@register_sfx("magic", summary="Ascending magical glissando with shimmer", level_db=-2.0)
def _magic(rng: np.random.Generator, sr: int) -> Floats:
    dur = 1.9
    n = nsamp(dur, sr)
    t = taxis(n, sr)
    buf = np.zeros(n)
    penta = [0, 2, 4, 7, 9]
    notes = [523.25 * 2 ** ((12 * (i // 5) + penta[i % 5]) / 12.0) for i in range(11)]
    for i, f in enumerate(notes):
        start = 0.0 + 0.068 * i**0.95
        m = nsamp(1.1, sr)
        tm = taxis(m, sr)
        tone = (
            (
                np.sin(2 * np.pi * f * tm)
                + 0.4 * np.sin(2 * np.pi * 2 * f * tm)
                + 0.12 * np.sin(2 * np.pi * 3.02 * f * tm)
            )
            * exp_decay(tm, 0.42)
            * raised_attack(tm, 0.0008)
        )
        add_at(buf, fade_out(tone, sr, 0.25), nsamp(start, sr), 0.5 + 0.05 * i)
    swell = (
        band_noise(rng, n, sr, 1200.0 + 4800.0 * np.clip(t / 0.8, 0, 1), 2400.0)
        * smooth_bump(np.clip(t / 1.1, 0, 1), 0.55)
        * 0.25
    )
    glitter = highpass(_sparkle(rng, sr), sr, 2500)
    sig = buf + swell
    add_at(sig, glitter, nsamp(0.15, sr), 0.4)
    for f in (1567.98, 2093.0):  # closing shimmer
        tm = taxis(nsamp(1.0, sr), sr)
        add_at(
            sig,
            fade_out(
                np.sin(2 * np.pi * f * tm) * exp_decay(tm, 0.45) * raised_attack(tm, 0.004), sr, 0.4
            ),
            nsamp(0.8, sr),
            0.4,
        )
    return normalize(reverb(sig, sr, rng, rt60=1.0, wet=0.22, tail=0.4)[: nsamp(2.9, sr)], 0.8)


# --- voice-like ------------------------------------------------------------------------------
@register_sfx("laugh", summary="A short 'ha-ha-ha-ha' laugh")
def _laugh(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(0.95, sr))
    base = rng.uniform(300.0, 340.0)
    pitches = [base, base * 0.93, base * 0.86, base * 0.79, base * 0.72]
    pos = 0.0
    for k, f_start in enumerate(pitches):
        dur = 0.12 + 0.012 * k
        m = nsamp(dur, sr)
        t = taxis(m, sr)
        u = t / dur
        f0 = f_start * (1.0 - 0.1 * u) * (1.0 + 0.012 * np.sin(2 * np.pi * 27 * t + k))
        f_track = np.stack([780.0 + 60.0 * (1 - u), 1250.0 + 100.0 * (1 - u), 2750.0 + 0 * u])
        vowel = vocal_tract(f0, f_track, sr, (110.0, 130.0, 190.0))
        breath = spectral_filter(
            white(rng, m),
            sr,
            lambda f: formant_response(f, (800.0, 1300.0, 2800.0), (260.0, 300.0, 400.0)),
        )
        breath /= np.max(np.abs(breath)) + 1e-9
        h_on = exp_decay(t, 0.018) * 0.55
        env = (
            raised_attack(t, 0.012)
            * exp_decay(t, 0.075)
            * (0.5 + 0.5 * np.cos(np.pi * np.clip((u - 0.8) / 0.2, 0, 1)))
        )
        snippet = (vowel * env * 0.9 + breath * (h_on + 0.1 * env)) * raised_attack(t, 0.004)
        add_at(buf, snippet, nsamp(pos, sr), 1.0 - 0.1 * k)
        pos += 0.165 + 0.01 * k
    return normalize(lowpass(buf, sr, 6500, order=2), 0.85)


# --- extras ---------------------------------------------------------------------------------
@register_sfx("tick", summary="Dry clock/wood tick", level_db=-2.0)
def _tick(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.14, sr)
    t = taxis(n, sr)
    f = rng.uniform(0.97, 1.03)
    sig = modal(t, [1850 * f, 3100 * f, 4600 * f], (1.0, 0.5, 0.25), (0.006, 0.004, 0.002))
    sig += np.sin(2 * np.pi * 420 * t) * exp_decay(t, 0.012) * 0.5
    sig += bandpass(white(rng, n), sr, 2000, 7000) * exp_decay(t, 0.0008) * 0.4
    return normalize(sig, 0.8)


@register_sfx("boom", summary="Deep explosion boom with a rumbling tail")
def _boom(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(2.2, sr)
    t = taxis(n, sr)
    sub = soft_clip(np.sin(phase_of(26.0 + 95.0 * exp_decay(t, 0.12), sr)) * 1.4, 2.0) * exp_decay(
        t, 0.55
    )
    rumble = lowpass(white(rng, n), sr, 170) * exp_decay(t, 0.75) * 1.2
    blast = lowpass(white(rng, n), sr, 3200) * exp_decay(t, 0.07)
    debris = (rng.random(n) < 90.0 * exp_decay(t, 0.5) / sr) * rng.uniform(0.3, 1.0, n)
    debris = lowpass(highpass(debris, sr, 300), sr, 4500) * 5.0 * (t > 0.08)
    sig = (sub * 1.1 + rumble + blast + debris * 0.4) * raised_attack(t, 0.004)
    return normalize(reverb(sig, sr, rng, rt60=0.9, wet=0.15, tail=0.5)[: nsamp(2.9, sr)], 0.92)


@register_sfx("zap", summary="Electric zap - a spark or laser", level_db=-1.0)
def _zap(rng: np.random.Generator, sr: int) -> Floats:
    n = nsamp(0.4, sr)
    t = taxis(n, sr)
    f = 220.0 + 3300.0 * exp_decay(t, 0.05)
    ph = phase_of(f, sr)
    sig = sum(np.sin(k * ph) / k for k in range(1, 7))
    sig = sig * (0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 75.0 * t)))
    crackle = highpass(white(rng, n), sr, 2500) * (rng.random(n) < 0.012) * 6.0 * exp_decay(t, 0.12)
    env = raised_attack(t, 0.0008) * exp_decay(t, 0.09)
    return normalize(lowpass(sig * env + crackle, sr, 9000), 0.85)


@register_sfx("bubble", summary="Single rising bubble 'bloop'")
def _bubble(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(0.28, sr))
    for start, f0, amp in (
        (0.0, rng.uniform(420.0, 620.0), 1.0),
        (0.085, rng.uniform(700.0, 950.0), 0.55),
    ):
        m = nsamp(0.18, sr)
        t = taxis(m, sr)
        ph = phase_of(f0 * (1.0 + 5.5 * t), sr)
        add_at(
            buf, np.sin(ph) * exp_decay(t, 0.035) * raised_attack(t, 0.001), nsamp(start, sr), amp
        )
    return normalize(buf, 0.8)


@register_sfx("tada", summary="Brassy 'ta-da!' fanfare", level_db=-1.0)
def _tada(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(1.9, sr))
    for start, f, dur in ((0.0, 392.0, 0.15), (0.17, 523.25, 0.15)):  # the quick pick-up notes
        n = nsamp(dur, sr)
        t = taxis(n, sr)
        bright = np.full(n, 0.8)
        pick = brass(np.full(n, f), bright, sr, 10) * raised_attack(t, 0.01)
        add_at(buf, fade_out(pick, sr, 0.04), nsamp(start, sr), 0.8)
    n = nsamp(1.5, sr)
    t = taxis(n, sr)
    bright = 0.55 + 0.4 * exp_decay(t, 0.5)
    env = raised_attack(t, 0.025) * np.where(
        t < 1.1, 1.0, 0.5 + 0.5 * np.cos(np.pi * np.clip((t - 1.1) / 0.4, 0, 1))
    )
    for f in (523.25, 659.25, 783.99, 1046.5):
        for det in (-0.004, 0.004):
            add_at(
                buf, brass(np.full(n, f * (1.0 + det)), bright, sr, 12) * env, nsamp(0.34, sr), 0.2
            )
    return normalize(reverb(buf, sr, rng, rt60=0.7, wet=0.16, tail=0.2)[: nsamp(2.4, sr)], 0.85)


@register_sfx("typing", summary="Keyboard typing - a burst of key clicks", level_db=-3.0)
def _typing(rng: np.random.Generator, sr: int) -> Floats:
    dur = 1.4
    buf = np.zeros(nsamp(dur, sr))
    t_key = 0.02
    for k in range(15):
        m = nsamp(0.08, sr)
        t = taxis(m, sr)
        f = rng.uniform(1300.0, 2600.0)
        key = np.sin(2 * np.pi * f * t) * exp_decay(t, 0.004) * 0.6
        key += bandpass(white(rng, m), sr, 1800, 7500) * exp_decay(t, 0.0015) * 0.5
        key += np.sin(2 * np.pi * rng.uniform(190.0, 300.0) * t) * exp_decay(t, 0.012) * 0.5
        add_at(buf, key, nsamp(t_key, sr), rng.uniform(0.5, 1.0) * (1.3 if k == 14 else 1.0))
        t_key += float(rng.choice([0.07, 0.09, 0.11, 0.15, 0.26]) if k < 13 else 0.3)
        if t_key > dur - 0.1:
            break
    return normalize(buf, 0.75)


@register_sfx("alarm", summary="Three urgent two-tone beeps", level_db=-3.0)
def _alarm(rng: np.random.Generator, sr: int) -> Floats:
    buf = np.zeros(nsamp(1.35, sr))
    for k in range(3):
        for j, f in enumerate((988.0, 740.0)):
            m = nsamp(0.1, sr)
            t = taxis(m, sr)
            tone = (
                (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 3 * f * t))
                * raised_attack(t, 0.004)
                * (0.5 + 0.5 * np.cos(np.pi * np.clip((t - 0.085) / 0.015, 0, 1)))
            )
            add_at(buf, tone, nsamp(0.45 * k + 0.1 * j, sr), 0.9)
    return normalize(lowpass(buf, sr, 6000, order=2), 0.8)


# register recorded samples found next to the project (assets/sfx or $REEL_SFX_DIR)
discover_sfx_files()
