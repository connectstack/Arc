"""The final mix: resampling, panning, ducking, BS.1770 loudness, limiting and WAV output.

Everything here is plain numpy (no scipy).  The pieces are usable on their own:

* :func:`resample` - windowed-sinc rational resampler (polyphase table, chunked gather).
* :func:`decode_audio` / :func:`read_wav` / :func:`write_wav` - file I/O (ffmpeg pipe, stdlib ``wave``).
* :func:`integrated_lufs` - ITU-R BS.1770-4 / EBU R128 integrated loudness (K-weighting applied in
  the frequency domain, 400 ms blocks, absolute and relative gates).
* :func:`duck_envelope` - sidechain-style gain curve that dips under speech (look-ahead, hold,
  raised-cosine ramps - no per-sample recursion).
* :func:`limit` - look-ahead soft-knee peak limiter that never hard clips.
* :func:`mix_tracks` - ``MixPlan`` (clips on a timeline) -> 16-bit stereo 48 kHz WAV + ``MixReport``.

Levels: clips are summed in "nominal" level (their ``gain_db`` is the only trim), music is ducked
under voice, the sum is normalised to about -16 LUFS and finally limited to -1 dBFS.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from reel.core.rng import stable_int

SAMPLE_RATE = 48_000
TARGET_LUFS = -16.0
CEILING_DB = -1.0
SILENCE_LUFS = -100.0  # reported for silent audio instead of -inf

ClipKind = Literal["voice", "sfx", "music"]
CLIP_KINDS: tuple[str, ...] = ("voice", "sfx", "music")


class AudioDecodeError(RuntimeError):
    """An audio file could not be decoded."""


# ----------------------------------------------------------------------------- small helpers
def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def gain_to_db(gain: float) -> float:
    return float(20.0 * math.log10(max(gain, 1e-12)))


def as_frames(x: np.ndarray) -> np.ndarray:
    """Coerce to float32 ``(frames, channels)``.  1-D is mono; ``(ch, frames)`` is transposed."""
    a = np.asarray(x)
    if a.dtype.kind in "iu":
        scale = float(max(abs(np.iinfo(a.dtype).min), np.iinfo(a.dtype).max))
        a = a.astype(np.float32) / scale
    a = a.astype(np.float32, copy=False)
    if a.ndim == 1:
        return a[:, None]
    if a.ndim != 2:
        raise ValueError(f"audio must be 1-D or 2-D, got shape {a.shape}")
    if a.shape[0] in (1, 2) and a.shape[1] > 8:  # (channels, frames) layout
        a = a.T
    return np.ascontiguousarray(a)


def to_stereo(x: np.ndarray) -> np.ndarray:
    """``(frames, 2)`` float32 from mono / stereo / multichannel input."""
    a = as_frames(x)
    if a.shape[1] == 1:
        return np.repeat(a, 2, axis=1)
    return np.ascontiguousarray(a[:, :2])


def to_mono(x: np.ndarray) -> np.ndarray:
    a = as_frames(x)
    return a[:, 0].copy() if a.shape[1] == 1 else a.mean(axis=1, dtype=np.float32)


def pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan law normalised so that the centre is unity gain on both sides."""
    p = min(1.0, max(-1.0, float(pan)))
    ang = (p + 1.0) * math.pi / 4.0
    return math.sqrt(2.0) * math.cos(ang), math.sqrt(2.0) * math.sin(ang)


def fast_fft_len(n: int) -> int:
    """Smallest 2^a 3^b 5^c >= n (numpy's FFT is slow for sizes with large prime factors)."""
    if n <= 16:
        return max(1, n)
    best = 1 << (n - 1).bit_length()
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            q = p35
            while q < n:
                q *= 2
            best = min(best, q)
            p35 *= 3
        p5 *= 5
    return best


def _smoothstep(u: np.ndarray) -> np.ndarray:
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


# ----------------------------------------------------------------------------- resampling
def resample(
    x: np.ndarray, sr_in: int, sr_out: int, *, zeros: int = 16, beta: float = 8.0
) -> np.ndarray:
    """Windowed-sinc (Kaiser) resampler for ``(n,)`` or ``(n, channels)`` float audio.

    Exact for rational ratios up to 4096 polyphase rows, anti-aliased when downsampling, DC gain
    exactly 1.  Output length is ``round(n * sr_out / sr_in)``.
    """
    a = np.asarray(x, dtype=np.float32)
    if sr_in == sr_out or a.shape[0] == 0:
        return a
    one_d = a.ndim == 1
    xx = a[:, None] if one_d else a
    n_in, n_ch = xx.shape
    n_out = max(1, round(n_in * sr_out / sr_in))
    g = math.gcd(int(sr_in), int(sr_out))
    up, down = int(sr_out) // g, int(sr_in) // g
    scale = min(1.0, sr_out / sr_in)
    cutoff = 0.97 * scale
    half = math.ceil(zeros / scale)
    nph = up if up <= 4096 else 4096
    frac = np.arange(nph + 1, dtype=np.float64) / nph
    taps = np.arange(-half + 1, half + 1, dtype=np.int64)
    u = taps[None, :] - frac[:, None]
    win = np.i0(beta * np.sqrt(np.clip(1.0 - (u / half) ** 2, 0.0, None))) / np.i0(beta)
    table = cutoff * np.sinc(cutoff * u) * win
    table /= table.sum(axis=1, keepdims=True)
    table = table.astype(np.float32)

    j = np.arange(n_out, dtype=np.int64)
    num = j * down
    base = num // up
    rem = num % up
    ph = rem if nph == up else np.rint(rem * nph / up).astype(np.int64)
    xp = np.pad(xx, ((half, half + 1), (0, 0)))
    out = np.empty((n_out, n_ch), dtype=np.float32)
    chunk = 16384
    for s in range(0, n_out, chunk):
        sl = slice(s, min(n_out, s + chunk))
        idx = (base[sl] + half)[:, None] + taps[None, :]
        w = table[ph[sl]]
        for ch in range(n_ch):
            out[sl, ch] = np.einsum("mt,mt->m", xp[idx, ch], w)
    return out[:, 0].copy() if one_d else out


# ----------------------------------------------------------------------------- file I/O
def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV (8/16/24/32-bit) -> ``(frames, channels)`` float32 and its sample rate."""
    with wave.open(str(path), "rb") as w:
        n_ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        data = v.astype(np.float32) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioDecodeError(f"unsupported WAV sample width {width} in {path}")
    return data.reshape(-1, n_ch), int(sr)


def ffmpeg_exe() -> str | None:
    """Path of the ffmpeg binary, or None when it is not installed."""
    return shutil.which("ffmpeg")


def decode_audio(
    path: str | Path,
    *,
    sr: int = SAMPLE_RATE,
    channels: int = 2,
    max_seconds: float | None = None,
) -> np.ndarray:
    """Decode any audio file to float32 ``(frames, channels)`` at ``sr`` through an ffmpeg pipe.

    Without ffmpeg only plain PCM WAV files can be read (via the stdlib).
    """
    p = Path(path)
    if not p.exists():
        raise AudioDecodeError(f"audio file not found: {p}")
    exe = ffmpeg_exe()
    if exe is None:
        if p.suffix.lower() == ".wav":
            try:
                data, file_sr = read_wav(p)
            except (wave.Error, EOFError) as exc:
                raise AudioDecodeError(f"cannot read {p} without ffmpeg: {exc}") from exc
            data = resample(data, file_sr, sr)
            if max_seconds is not None:
                data = data[: int(max_seconds * sr)]
            return _force_channels(data, channels)
        raise AudioDecodeError(f"ffmpeg is required to decode {p.suffix or 'this'} audio")
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(p), "-vn"]
    if max_seconds is not None:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels), "-ar", str(sr), "pipe:1"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        msg = res.stderr.decode(errors="replace").strip() or "ffmpeg failed"
        raise AudioDecodeError(f"cannot decode {p}: {msg}")
    data = np.frombuffer(res.stdout, dtype="<f4")
    usable = (data.size // channels) * channels
    return data[:usable].reshape(-1, channels).copy()


def _force_channels(data: np.ndarray, channels: int) -> np.ndarray:
    if data.shape[1] == channels:
        return data
    if channels == 1:
        return data.mean(axis=1, keepdims=True, dtype=np.float32)
    if data.shape[1] == 1:
        return np.repeat(data, channels, axis=1)
    return np.ascontiguousarray(data[:, :channels])


def write_wav(path: str | Path, data: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Write float audio as a 16-bit PCM WAV (mono or stereo).  Returns the linear sample peak."""
    a = as_frames(data)
    pcm = np.clip(np.rint(np.clip(a, -1.0, 1.0) * 32767.0), -32768, 32767).astype("<i2")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return float(np.abs(pcm).max()) / 32768.0 if pcm.size else 0.0


# ----------------------------------------------------------------------------- loudness
def _k_weighting_response(freqs: np.ndarray, sr: int) -> np.ndarray:
    """Magnitude of the two-stage BS.1770 K-weighting filter at ``freqs`` (Hz).

    Coefficients follow the standard exactly at 48 kHz and are re-derived (bilinear transform of
    the same analogue prototypes, as libebur128 does) for other rates.
    """
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b_shelf = np.array(
        [(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    )
    a_shelf = np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0])
    f1, q1 = 38.13547087602444, 0.5003270373238773
    k1 = math.tan(math.pi * f1 / sr)
    d = 1.0 + k1 / q1 + k1 * k1
    b_hp = np.array([1.0, -2.0, 1.0])
    a_hp = np.array([1.0, 2.0 * (k1 * k1 - 1.0) / d, (1.0 - k1 / q1 + k1 * k1) / d])
    z1 = np.exp(-1j * 2.0 * math.pi * freqs / sr)
    z2 = z1 * z1

    def mag(b: np.ndarray, a: np.ndarray) -> np.ndarray:
        return np.abs((b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2))

    return np.asarray(mag(b_shelf, a_shelf) * mag(b_hp, a_hp))


def k_weight(x: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """K-weight a 1-D signal (zero-phase magnitude response in the frequency domain)."""
    a = np.asarray(x, dtype=np.float64)
    nfft = fast_fft_len(a.shape[0] + int(sr * 0.05))
    resp = _k_weighting_response(np.fft.rfftfreq(nfft, 1.0 / sr), sr)
    return np.asarray(np.fft.irfft(np.fft.rfft(a, nfft) * resp, nfft)[: a.shape[0]])


def integrated_lufs(x: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Integrated loudness (LUFS) per ITU-R BS.1770-4 with the EBU R128 gating scheme.

    The K-weighting is applied as a zero-phase magnitude response in the frequency domain (only
    power matters, so the phase is irrelevant).  Returns ``SILENCE_LUFS`` for silent input.
    """
    a = as_frames(x).astype(np.float64)
    n = a.shape[0]
    if n == 0:
        return SILENCE_LUFS
    nfft = fast_fft_len(n + int(sr * 0.05))
    resp = _k_weighting_response(np.fft.rfftfreq(nfft, 1.0 / sr), sr)
    power = np.zeros(n, dtype=np.float64)
    for ch in range(min(a.shape[1], 2)):
        y = np.fft.irfft(np.fft.rfft(a[:, ch], nfft) * resp, nfft)[:n]
        power += y * y
    blk = round(0.4 * sr)
    hop = round(0.1 * sr)
    cs = np.concatenate([[0.0], np.cumsum(power)])
    if n < blk:
        z = np.array([cs[-1] / n])
    else:
        starts = np.arange(0, n - blk + 1, hop)
        z = (cs[starts + blk] - cs[starts]) / blk
    with np.errstate(divide="ignore"):
        lk = -0.691 + 10.0 * np.log10(np.maximum(z, 1e-30))
    keep = lk > -70.0
    if not keep.any():
        return SILENCE_LUFS
    rel = -0.691 + 10.0 * math.log10(float(z[keep].mean())) - 10.0
    keep &= lk > rel
    if not keep.any():
        return SILENCE_LUFS
    return float(-0.691 + 10.0 * math.log10(float(z[keep].mean())))


def active_level_db(x: np.ndarray, sr: int, *, gate_db: float = -40.0) -> float:
    """RMS level (dBFS) of the active parts of ``x``: 20 ms frames within ``gate_db`` of the loudest."""
    m = to_mono(x).astype(np.float64)
    hop = max(1, int(sr * 0.02))
    nfr = m.size // hop
    if nfr == 0:
        return SILENCE_LUFS if m.size == 0 else 10.0 * math.log10(float(np.mean(m * m)) + 1e-20)
    p = np.mean(m[: nfr * hop].reshape(nfr, hop) ** 2, axis=1)
    loudest = float(np.max(p))
    if loudest <= 1e-14:
        return SILENCE_LUFS
    sel = p > loudest * 10.0 ** (gate_db / 10.0)
    return float(10.0 * math.log10(float(p[sel].mean())))


# ----------------------------------------------------------------------------- ducking
def _frame_activity(x: np.ndarray, sr: int, hop: int, threshold_db: float | None) -> np.ndarray:
    """Per-frame boolean voice activity from a mask (bool / 0-1 floats) or from audio."""
    a = np.asarray(x)
    if a.ndim == 2:
        a = np.max(np.abs(a), axis=1)
    n = a.shape[0]
    nfr = -(-n // hop)
    pad = nfr * hop - n
    if a.dtype == bool or (a.dtype.kind == "f" and bool(np.all((a == 0) | (a == 1)))):
        return np.pad(a.astype(bool), (0, pad)).reshape(nfr, hop).any(axis=1)
    f = np.pad(a.astype(np.float32), (0, pad)).reshape(nfr, hop)
    rms = np.sqrt(np.mean(f * f, axis=1, dtype=np.float64))
    db = 20.0 * np.log10(rms + 1e-9)
    thr = threshold_db if threshold_db is not None else max(-60.0, float(np.max(db)) - 30.0)
    return np.asarray(db > max(thr, -70.0))


def duck_envelope(
    voice_mask_or_audio: np.ndarray,
    sr: int,
    depth_db: float = -12.0,
    attack: float = 0.08,
    release: float = 0.45,
    *,
    hold: float = 0.3,
    lookahead: float | None = None,
    threshold_db: float | None = None,
) -> np.ndarray:
    """Gain curve (one value per sample, ``10**(depth_db/20)`` .. 1) that dips under speech.

    ``voice_mask_or_audio`` is a boolean mask (or a float array containing only 0/1) marking when
    somebody speaks, or the voice audio itself (activity is then found from a 10 ms RMS envelope).
    Speech bursts closer than ``hold`` seconds are bridged so the bed does not pump between words.
    The gain starts falling ``lookahead`` seconds before the voice (default: ``attack``, so it is
    fully ducked when the voice starts), falls over ``attack`` seconds and recovers over ``release``
    seconds after the voice, both on a raised-cosine curve in dB.
    """
    a = np.asarray(voice_mask_or_audio)
    n = int(a.shape[0]) if a.ndim else 0
    env = np.ones(n, dtype=np.float32)
    if n == 0 or depth_db >= 0.0:
        return env
    hop = max(1, round(sr * 0.01))
    active = _frame_activity(a, sr, hop, threshold_db)
    if not active.any():
        return env
    edges = np.diff(np.concatenate([[0], active.astype(np.int8), [0]]))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    hold_f = hold / (hop / sr)
    spans: list[list[int]] = []
    for s, e in zip(starts.tolist(), ends.tolist()):
        if spans and s - spans[-1][1] < hold_f:
            spans[-1][1] = e
        else:
            spans.append([s, e])
    look = attack if lookahead is None else lookahead
    att = max(1, round(attack * sr))
    rel = max(1, round(release * sr))
    depth = np.zeros(n, dtype=np.float32)
    for s, e in spans:
        s0 = s * hop - round(look * sr)  # the dip starts falling here
        e0 = min(n, e * hop)
        lo = max(0, s0)
        hi = min(n, e0 + rel)
        if hi <= lo:
            continue
        t = np.arange(lo, hi, dtype=np.float64)
        down = _smoothstep((t - s0) / att)
        up = _smoothstep((e0 + rel - t) / rel)
        np.maximum(depth[lo:hi], np.minimum(down, up).astype(np.float32), out=depth[lo:hi])
    return np.power(10.0, depth * (depth_db / 20.0)).astype(np.float32)


# ----------------------------------------------------------------------------- limiter
def _sliding_min_ahead(g: np.ndarray, w: int) -> np.ndarray:
    """``m[i] = min(g[i : i + w])`` (van Herk / Gil-Werman, fully vectorised; tail padded with 1)."""
    if w <= 1:
        return g
    n = g.shape[0]
    total = n + w - 1
    total += (-total) % w
    gp = np.ones(total, dtype=g.dtype)
    gp[:n] = g
    blocks = gp.reshape(-1, w)
    pre = np.minimum.accumulate(blocks, axis=1).reshape(-1)
    suf = np.minimum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].reshape(-1)
    return np.minimum(suf[:n], pre[w - 1 : w - 1 + n])


def limit(
    x: np.ndarray,
    sr: int = SAMPLE_RATE,
    *,
    ceiling_db: float = CEILING_DB,
    knee_db: float = 6.0,
    lookahead_s: float = 0.005,
    release_s: float = 0.08,
) -> tuple[np.ndarray, float]:
    """Look-ahead soft-knee peak limiter.  Returns ``(limited, deepest_gain_reduction_db)``.

    The static curve bends smoothly over ``knee_db`` and is flat at ``ceiling_db`` above it.  The
    resulting gain is smoothed so it falls *before* a peak (look-ahead min + moving average) and
    recovers linearly over ``release_s``, and is guaranteed never to be higher than the
    instantaneous requirement - so the output cannot exceed the ceiling and is never hard clipped.
    """
    a = as_frames(x)
    if a.shape[0] == 0:
        return a, 0.0
    peak = np.max(np.abs(a), axis=1).astype(np.float64)
    knee_start = ceiling_db - knee_db / 2.0
    if float(np.max(peak)) <= 10.0 ** (knee_start / 20.0):
        return a, 0.0
    p_db = 20.0 * np.log10(np.maximum(peak, 1e-9))
    over = p_db - knee_start
    red_db = np.where(
        over <= 0.0, 0.0, np.where(over < knee_db, over * over / (2.0 * knee_db), p_db - ceiling_db)
    )
    g = np.power(10.0, -red_db / 20.0)
    w = max(1, round(lookahead_s * sr))
    m = _sliding_min_ahead(g, w)
    if w > 1:
        cs = np.concatenate([[0.0], np.cumsum(np.concatenate([np.ones(w - 1), m]))])
        sm = (cs[w : w + m.size] - cs[: m.size]) / w
    else:
        sm = m
    delta = 1.0 / max(1.0, release_s * sr)
    ramp = np.arange(sm.size, dtype=np.float64) * delta
    s = np.minimum(np.minimum.accumulate(sm - ramp) + ramp, 1.0)
    out = (a * s[:, None].astype(np.float32)).astype(np.float32)
    return out, float(-20.0 * math.log10(max(float(s.min()), 1e-6)))


# ----------------------------------------------------------------------------- the mix
@dataclass
class MixClip:
    """One piece of audio on the timeline.  ``samples`` is mono ``(n,)`` or ``(n, channels)``."""

    start_sec: float
    samples: np.ndarray
    sr: int
    gain_db: float = 0.0
    kind: ClipKind = "sfx"
    pan: float | None = None  # -1 left .. +1 right; None = centre (sfx: a small seeded offset)
    name: str = ""


@dataclass
class MixPlan:
    total_duration: float
    clips: list[MixClip] = field(default_factory=list)
    ducking: bool = True
    duck_depth_db: float = -12.0
    sfx_duck_db: float = -3.0
    seed: int = 0
    target_lufs: float | None = TARGET_LUFS  # None skips loudness normalisation
    ceiling_db: float = CEILING_DB
    music_fade_in: float = 0.5
    music_fade_out: float = 1.5
    window: tuple[float, float] | None = None  # crop the finished mix to [start, end) seconds


@dataclass
class MixReport:
    duration: float
    peak: float
    approx_lufs: float
    ducked_seconds: float
    n_clips: int
    gain_db: float = 0.0  # loudness-normalisation gain that was applied
    limiter_db: float = 0.0  # deepest gain reduction applied by the limiter
    sr: int = SAMPLE_RATE


def _add_at(buf: np.ndarray, clip: np.ndarray, start: int) -> bool:
    """Add ``clip`` into ``buf`` at sample ``start`` (clipped to the buffer).  False if nothing fit."""
    lo = max(start, 0)
    hi = min(start + clip.shape[0], buf.shape[0])
    if hi <= lo:
        return False
    buf[lo:hi] += clip[lo - start : hi - start]
    return True


def _loop_to_length(m: np.ndarray, length: int, xfade: int) -> np.ndarray:
    """Repeat ``m`` with equal-power crossfades until it is ``length`` frames long (or trim it)."""
    k = m.shape[0]
    if k >= length:
        return m[:length].copy()
    xf = max(0, min(xfade, k // 4))
    out = np.zeros((length, m.shape[1]), dtype=np.float32)
    out[:k] = m
    if k - xf <= 0:
        return out
    ramp = np.linspace(0.0, 1.0, xf, dtype=np.float32) if xf else np.zeros(0, np.float32)
    fade_in = np.sin(ramp * (math.pi / 2.0))[:, None]
    fade_out = np.cos(ramp * (math.pi / 2.0))[:, None]
    pos = k - xf
    while pos < length:
        end = min(length, pos + k)
        seg = m[: end - pos]
        ov = min(xf, seg.shape[0])
        if ov:
            out[pos : pos + ov] = out[pos : pos + ov] * fade_out[:ov] + seg[:ov] * fade_in[:ov]
        out[pos + ov : end] = seg[ov:]
        pos += k - xf
    return out


def _prepare(clip: MixClip, sr: int, pan: float) -> np.ndarray | None:
    """Clip -> gained, panned float32 stereo at ``sr`` (None for empty clips)."""
    x = as_frames(clip.samples)
    if x.shape[0] == 0:
        return None
    x = np.nan_to_num(x, copy=False)
    if clip.sr != sr:
        x = resample(x, int(clip.sr), sr)
    gain = db_to_gain(clip.gain_db)
    if x.shape[1] == 1:
        gl, gr = pan_gains(pan)
        return np.stack([x[:, 0] * (gain * gl), x[:, 0] * (gain * gr)], axis=1).astype(np.float32)
    st = np.ascontiguousarray(x[:, :2]) * gain
    if pan:
        gl, gr = pan_gains(pan)
        st = st * np.array([gl, gr], dtype=np.float32)
    return st.astype(np.float32)


def mix_tracks(plan: MixPlan, out_wav: str | Path, sr: int = SAMPLE_RATE) -> MixReport:
    """Mix ``plan`` into a 16-bit stereo WAV at ``sr`` and return a :class:`MixReport`.

    Voice and sfx are placed at their start times; music is looped (crossfaded) or trimmed to
    the timeline and faded; music is ducked under the voice (and sfx gently, ``sfx_duck_db``);
    the sum is normalised to ``target_lufs`` (never needing more than ~6 dB of limiting) and
    limited to ``ceiling_db``.
    """
    n = max(1, round(plan.total_duration * sr))
    buses = {k: np.zeros((n, 2), dtype=np.float32) for k in CLIP_KINDS}
    used = 0
    for idx, clip in enumerate(plan.clips):
        if clip.kind not in buses:
            raise ValueError(f"unknown clip kind {clip.kind!r}; expected one of {CLIP_KINDS}")
        if clip.pan is not None:
            pan = clip.pan
        elif clip.kind == "sfx":
            pan = ((stable_int("pan", plan.seed, idx, clip.name) % 2001) / 1000.0 - 1.0) * 0.3
        else:
            pan = 0.0
        st = _prepare(clip, sr, pan)
        if st is None:
            continue
        start = round(clip.start_sec * sr)
        if clip.kind == "music":
            length = n - max(0, start)
            if length <= 0:
                continue
            bed = _loop_to_length(st[max(0, -start) :], length, xfade=int(1.0 * sr))
            fi = min(int(plan.music_fade_in * sr), length // 2)
            fo = min(int(plan.music_fade_out * sr), length // 2)
            if fi > 1:
                bed[:fi] *= (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fi, dtype=np.float32)))[
                    :, None
                ]
            if fo > 1:
                bed[-fo:] *= (0.5 + 0.5 * np.cos(np.linspace(0.0, math.pi, fo, dtype=np.float32)))[
                    :, None
                ]
            used += int(_add_at(buses["music"], bed, max(0, start)))
        else:
            used += int(_add_at(buses[clip.kind], st, start))

    ducked_seconds = 0.0
    voice_amp = np.max(np.abs(buses["voice"]), axis=1)
    if plan.ducking and float(voice_amp.max()) > 1e-5:
        music_env = duck_envelope(
            voice_amp, sr, depth_db=plan.duck_depth_db, attack=0.08, release=0.45
        )
        buses["music"] *= music_env[:, None]
        half_depth = 10.0 ** (plan.duck_depth_db / 40.0)
        ducked_seconds = float(np.count_nonzero(music_env <= half_depth)) / sr
        if plan.sfx_duck_db < 0.0 and float(np.abs(buses["sfx"]).max()) > 0.0:
            sfx_env = duck_envelope(
                voice_amp, sr, depth_db=plan.sfx_duck_db, attack=0.03, release=0.25
            )
            buses["sfx"] *= sfx_env[:, None]

    mix = buses["voice"] + buses["sfx"] + buses["music"]
    gain_db = 0.0
    peak_in = float(np.abs(mix).max())
    if plan.target_lufs is not None and peak_in > 1e-6:
        measured = integrated_lufs(mix, sr)
        if measured > SILENCE_LUFS + 1.0:
            gain_db = float(np.clip(plan.target_lufs - measured, -30.0, 30.0))
            # never ask the limiter for more than ~6 dB of reduction
            peak_db = gain_to_db(peak_in) + gain_db
            excess = peak_db - (plan.ceiling_db + 6.0)
            if excess > 0.0:
                gain_db -= excess
    if gain_db:
        mix = (mix * np.float32(db_to_gain(gain_db))).astype(np.float32)
    mix, limiter_db = limit(mix, sr, ceiling_db=plan.ceiling_db)

    if plan.window is not None:
        a = max(0, round(plan.window[0] * sr))
        b = min(n, round(plan.window[1] * sr))
        if b > a:
            mix = mix[a:b].copy()
            edge = min(int(0.004 * sr), mix.shape[0] // 2)
            if edge > 1:
                ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)[:, None]
                mix[:edge] *= ramp
                mix[-edge:] *= ramp[::-1]

    peak = write_wav(out_wav, mix, sr)
    return MixReport(
        duration=mix.shape[0] / sr,
        peak=peak,
        approx_lufs=integrated_lufs(mix, sr),
        ducked_seconds=ducked_seconds,
        n_clips=used,
        gain_db=gain_db,
        limiter_db=limiter_db,
        sr=sr,
    )
