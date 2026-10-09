"""ElevenLabs text-to-speech: optional, online, and never chosen by ``--tts auto``.

Everything else in reel runs offline.  This engine sends each *spoken line* (the caption text, nothing else) to
``api.elevenlabs.io`` and bills your ElevenLabs credits for it, so it is built to be predictable:

* **opt-in**: ``--tts elevenlabs`` or ``REEL_TTS=elevenlabs``; ``auto`` never picks it;
* **cached**: every synthesised line is stored on disk (``tts_cache_key``: engine, model, settings, voice, text), so
  re-rendering, editing the picture or the music, or changing *other* lines never pays for a line twice, and a cached
  line needs no network at all;
* **exact word timings**: the ``/with-timestamps`` endpoint returns per-character times, which drive the caption
  highlight, so no alignment is guessed from the audio;
* **key hygiene**: the key is read from ``ELEVENLABS_API_KEY`` only (shell, or a git-ignored ``.env``), sent in the
  ``xi-api-key`` header and scrubbed from every message and ``repr``.  Never put it in a spec, a script or a chat.

Voices: ``characters[].voice`` / ``audio.tts_voice`` may be a voice id or a voice *name* from your account
(``reel voices --tts elevenlabs`` lists them); without one, each speaker is given a fitting default voice (gender
and age from the character, distinct speakers get distinct voices).  ``ELEVENLABS_VOICE`` makes one voice the default
for everyone.  Multilingual models read Hindi, Arabic, Japanese ... with any voice, so no per-language voice is needed.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import tempfile
import time
import wave
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote, urlparse

import httpx
import numpy as np

from reel.audio.tts import CachedEngine, TTSClip, TTSUnavailableError, reads_female
from reel.core.rng import stable_int

ELEVENLABS_URL = "https://api.elevenlabs.io"
#: the API's own default model: available on every plan; ``ELEVENLABS_MODEL`` picks another (eleven_v4, eleven_flash_v2_5 ...)
DEFAULT_MODEL = "eleven_multilingual_v2"
#: 16-bit little-endian mono PCM at 24 kHz: needs no decoder and is allowed on every plan (44.1 kHz needs Pro)
OUTPUT_FORMAT = "pcm_24000"
SAMPLE_RATE = 24_000
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_VOICE_ID = re.compile(r"[A-Za-z0-9]{20}")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: ElevenLabs's own age labels for the ages reel asks for
_AGE_LABELS = {"young": "young", "mid": "middle_aged", "old": "old"}
_ARCHETYPE_AGE = {"kid": "young", "hero": "young", "elder": "old"}  # everyone else: middle-aged


def _labels(voice: dict[str, Any]) -> dict[str, Any]:
    """The ``labels`` of a voice (gender, age, accent, use_case ...), ``{}`` when it has none."""
    labels = voice.get("labels")
    return labels if isinstance(labels, dict) else {}


def _error_parts(resp: httpx.Response) -> tuple[str, str]:
    """``(status, message)`` of an ElevenLabs error body: ``{"detail": {"status", "message"}}``, ``{"detail": "..."}``
    or a validation list."""
    try:
        data = resp.json()
    except ValueError:
        return "", resp.text.strip()[:300]
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("status") or ""), str(detail.get("message") or "")[:300]
    if isinstance(detail, str):
        return "", detail[:300]
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        first = detail[0]
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return "", f"{first.get('msg', 'invalid request')}{f' ({where})' if where else ''}"[:300]
    return "", ""


def _is_container(raw: bytes) -> bool:
    """MP3 (with or without an ID3 tag), Ogg or FLAC: the reply is a file, not the bare PCM that was asked for."""
    return (
        raw[:3] == b"ID3"
        or raw[:4] in (b"OggS", b"fLaC")
        or (len(raw) > 2 and raw[0] == 0xFF and raw[1] & 0xE0 == 0xE0 and raw[1] & 0x18 != 0x08)
    )


def _decode_audio(raw: bytes) -> tuple[np.ndarray, int]:
    """Mono float32 samples and their rate from the reply's audio: raw S16LE PCM as requested, or (defensively) a WAV
    or a compressed file, which ffmpeg reads."""
    if raw[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(raw)) as w:
                if w.getsampwidth() != 2:
                    raise ValueError("not 16-bit audio")
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                if w.getnchannels() > 1:
                    pcm = pcm.reshape(-1, w.getnchannels()).mean(axis=1)
                return (pcm.astype(np.float32) / 32768.0), int(w.getframerate())
        except (wave.Error, ValueError, EOFError) as exc:
            raise TTSUnavailableError(
                f"ElevenLabs sent audio that could not be read ({exc})"
            ) from exc
    if _is_container(raw):
        from reel.audio.mix import AudioDecodeError, decode_audio

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reply.audio"
            path.write_bytes(raw)
            try:
                return decode_audio(path, sr=SAMPLE_RATE, channels=1)[:, 0], SAMPLE_RATE
            except AudioDecodeError as exc:
                raise TTSUnavailableError(
                    f"ElevenLabs sent audio that could not be read ({exc})"
                ) from exc
    raw = raw[: len(raw) // 2 * 2]
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, SAMPLE_RATE


def word_timings(text: str, alignment: object) -> list[tuple[float, float, str]] | None:
    """``(t0, t1, word)`` per whitespace word of ``text`` from ElevenLabs's per-character alignment.

    ``None`` when the alignment is missing, malformed, or does not spell exactly the words that were sent (the
    pipeline then estimates timings from the audio, as for any other engine).
    """
    if not isinstance(alignment, dict):
        return None
    chars = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not (
        isinstance(chars, list)
        and isinstance(starts, list)
        and isinstance(ends, list)
        and len(chars) == len(starts) == len(ends)
    ):
        return None
    words: list[tuple[float, float, str]] = []
    cur, t0, t1 = "", 0.0, 0.0
    try:
        for ch, a, b in zip(chars, starts, ends, strict=True):
            piece = str(ch)
            if not piece.strip():  # whitespace closes a word
                if cur:
                    words.append((t0, t1, cur))
                    cur = ""
                continue
            if not cur:
                t0 = float(a)
            cur += piece
            t1 = float(b)
    except (TypeError, ValueError):
        return None
    if cur:
        words.append((t0, t1, cur))
    if [w for _, _, w in words] != text.split():
        return None
    ok = all(0.0 <= a <= b for a, b, _ in words) and all(
        words[i][0] >= words[i - 1][0] for i in range(1, len(words))
    )
    return words if ok else None


class ElevenLabsTTS(CachedEngine):
    """Speech from the ElevenLabs API (see the module docstring).

    ``transport`` and ``sleep`` are injectable so tests run against ``httpx.MockTransport`` without a network or a key.
    """

    name = "elevenlabs"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        voice: str | None = None,
        env: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 90.0,
        max_retries: int = 3,
        stability: float = 0.5,
        similarity: float = 0.75,
    ) -> None:
        if env is None:
            from reel.llm.client import llm_env  # the same .env rules as every other key

            env = llm_env()
        self._key = self._clean_key(
            api_key if api_key is not None else env.get("ELEVENLABS_API_KEY")
        )
        self.model = model or env.get("ELEVENLABS_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or env.get("ELEVENLABS_BASE_URL") or ELEVENLABS_URL).rstrip("/")
        parts = urlparse(self.base_url)
        if parts.scheme != "https" and (
            parts.scheme != "http" or parts.hostname not in _LOCAL_HOSTS
        ):
            raise TTSUnavailableError(
                "ELEVENLABS_BASE_URL must be an https:// address: the API key travels in a header of every request"
            )
        self.default_voice = voice or env.get("ELEVENLABS_VOICE") or None
        self.stability, self.similarity = float(stability), float(similarity)
        # everything that changes the audio is part of the cache key, so a new model or setting never reuses old takes
        self.version = f"1|{self.model}|{OUTPUT_FORMAT}|{self.stability:g}|{self.similarity:g}"
        self.timeout = float(timeout)
        self._transport = transport
        self._sleep = sleep
        self._max_retries = max_retries
        self._voice_list: list[dict[str, Any]] | None = None
        self._assigned: dict[
            str, str
        ] = {}  # automatic voice choices made so far (speaker token -> voice id)
        self._warnings: list[str] = []
        self._fatal: str | None = (
            None  # set by a rejected key or an empty account: later lines fail without a request
        )
        self.characters_sent = 0
        self.lines_sent = 0

    def __repr__(self) -> str:  # never includes the key
        return f"ElevenLabsTTS(model={self.model!r}, host={self.destination!r})"

    @staticmethod
    def _clean_key(raw: str | None) -> str | None:
        if raw is None:
            return None
        key = raw.strip()
        if not key:
            return None
        if not key.isprintable() or any(c.isspace() for c in key):
            raise TTSUnavailableError(
                "ELEVENLABS_API_KEY contains a space or a control character (a stray newline from copy/paste?); "
                "remove it and try again"
            )
        return key

    @property
    def destination(self) -> str:
        """``host[:port]`` the text is sent to, so an unusual ``ELEVENLABS_BASE_URL`` is visible and never silent."""
        parts = urlparse(self.base_url)
        host = parts.hostname or "?"
        return f"{host}:{parts.port}" if parts.port else host

    # -- engine interface ---------------------------------------------------------------------
    def available(self) -> bool:
        return self._key is not None

    def require_key(self) -> None:
        if self._key is None:
            raise TTSUnavailableError(
                "TTS engine 'elevenlabs' needs an API key: set ELEVENLABS_API_KEY in your shell or in a git-ignored .env "
                "file (see .env.example). reel never asks for it in a spec or a chat, and never prints it."
            )

    def list_voices(self) -> list[str]:
        try:
            return sorted({str(v.get("name", "")) for v in self.fetch_voices() if v.get("name")})
        except TTSUnavailableError:
            return []

    def resolve_voice(self, voice: str | None) -> str | None:
        return voice or self.default_voice or "auto"

    def speaker_voice(self, speaker: str | None, character: Any = None) -> str | None:
        """The default voice if one is set, else a token that picks a fitting voice (and a distinct one per speaker)
        from the account's voices the first time that speaker is synthesised."""
        if self.default_voice:
            return self.default_voice
        label = (
            getattr(character, "name", None) or getattr(character, "id", None) or speaker or ""
        ).strip()
        gender = (
            "f" if reads_female(label) else "n"
        )  # a name does not tell the rest: leave it to the balancing below
        age = _ARCHETYPE_AGE.get(getattr(character, "archetype", "") or "", "mid")
        return f"auto:{gender}:{age}:{speaker or label or 'narrator'}"

    def take_warnings(self) -> list[str]:
        out, self._warnings = self._warnings, []
        return out

    def usage(self) -> str | None:
        if not self.lines_sent:
            return None
        return (
            f"elevenlabs ({self.model}): {self.characters_sent} characters in {self.lines_sent} line(s) were sent to "
            f"{self.destination} and billed; lines already in the cache cost nothing"
        )

    # -- HTTP ---------------------------------------------------------------------------------
    def _scrub(self, text: str) -> str:
        if self._key and len(self._key) >= 4:
            for form in (self._key, repr(self._key)[1:-1]):
                text = text.replace(form, "***")
        return text

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        try:
            return float(min(30.0, max(0.0, float(resp.headers.get("retry-after", "")))))
        except ValueError:
            return float(min(30.0, 1.5 * (2**attempt)))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        self.require_key()
        if self._fatal:
            raise TTSUnavailableError(self._fatal)
        url = f"{self.base_url}{path}"
        headers = {"xi-api-key": self._key or "", "Accept": "application/json"}
        attempt = 0
        while True:
            try:
                with httpx.Client(
                    transport=self._transport, timeout=httpx.Timeout(self.timeout, connect=10.0)
                ) as http:
                    resp = http.request(method, url, params=params, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise TTSUnavailableError(
                    f"ElevenLabs did not answer within {self.timeout:g}s; check your connection and try again"
                ) from exc
            except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
                reason = self._scrub(str(exc)) or exc.__class__.__name__
                raise TTSUnavailableError(
                    f"cannot reach ElevenLabs at {self.destination} ({exc.__class__.__name__}: {reason}); "
                    "check your network connection"
                ) from exc
            if resp.status_code in RETRY_STATUS and attempt < self._max_retries:
                self._sleep(self._retry_delay(resp, attempt))
                attempt += 1
                continue
            return resp

    def _fail(self, resp: httpx.Response, what: str, *, account_wide: bool = True) -> NoReturn:
        """Raise the right error for a failed call; a rejected key or an empty account stops further requests."""
        code = resp.status_code
        status, message = _error_parts(resp)
        detail = f": {self._scrub(message)}" if message else ""
        if code == 401 and ("quota" in status or "credit" in message.lower()):
            text = (
                f"ElevenLabs has no credits left for {what} (HTTP 401 {status or 'quota_exceeded'}){detail}. "
                "Top up, wait for the monthly reset, or render with another engine (--tts auto)"
            )
        elif code in (401, 403):
            text = (
                f"ElevenLabs rejected the API key for {what} (HTTP {code}){detail}. Check ELEVENLABS_API_KEY, and that "
                "the key's permissions allow text-to-speech"
            )
        elif code == 402:
            text = f"ElevenLabs needs a paid plan or payment for {what} (HTTP 402){detail}"
            account_wide = False
        elif code == 404:
            text = f"ElevenLabs does not know {what} (HTTP 404){detail}"
            account_wide = False
        elif code == 429:
            text = (
                f"ElevenLabs is rate limiting this account (HTTP 429){detail}; wait a moment and render again "
                "(finished lines are cached, only the rest is requested)"
            )
            account_wide = False
        elif code >= 500:
            text = f"ElevenLabs had a server error for {what} (HTTP {code}){detail}; try again in a moment"
            account_wide = False
        else:
            text = f"ElevenLabs rejected {what} (HTTP {code}){detail}"
            account_wide = False
        if account_wide and code in (401, 403):
            self._fatal = text
        raise TTSUnavailableError(text)

    def _json(self, resp: httpx.Response, what: str) -> dict[str, Any]:
        if resp.status_code >= 400:
            self._fail(resp, what)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TTSUnavailableError(
                f"ElevenLabs answered {what} with something that is not JSON"
            ) from exc
        if not isinstance(data, dict):
            raise TTSUnavailableError(f"ElevenLabs answered {what} with an unexpected JSON shape")
        return data

    # -- voices -------------------------------------------------------------------------------
    def fetch_voices(self) -> list[dict[str, Any]]:
        """The voices of the account (name, id, labels ...), fetched once per run."""
        if self._voice_list is not None:
            return self._voice_list
        voices: list[dict[str, Any]] = []
        page: str | None = None
        for _ in range(5):  # up to 500 voices
            params: dict[str, Any] = {"page_size": 100}
            if page:
                params["next_page_token"] = page
            resp = self._request("GET", "/v2/voices", params=params)
            if resp.status_code == 404 and not voices:  # an older deployment: one unpaged list
                data = self._json(self._request("GET", "/v1/voices"), "the voice list")
                voices += [
                    v for v in data.get("voices", []) if isinstance(v, dict) and v.get("voice_id")
                ]
                break
            data = self._json(resp, "the voice list")
            voices += [
                v for v in data.get("voices", []) if isinstance(v, dict) and v.get("voice_id")
            ]
            page = data.get("next_page_token") if data.get("has_more") else None
            if not page:
                break
        self._voice_list = voices
        return voices

    def _voice_id(self, voice: str) -> str:
        """The id behind ``voice``: an id as given, a name of the account, or an automatic pick (``auto:...``)."""
        if voice.startswith("auto"):
            return self._auto_voice(voice)
        if _VOICE_ID.fullmatch(voice):
            return voice
        wanted = voice.strip().lower()
        voices = self.fetch_voices()
        for pick in (
            [v for v in voices if str(v.get("name", "")).strip().lower() == wanted],
            [v for v in voices if str(v.get("name", "")).strip().lower().startswith(wanted)],
        ):
            if pick:
                return str(pick[0]["voice_id"])
        self._warnings.append(
            f"ElevenLabs has no voice named {voice!r} (it may be a voice for another engine); a fitting voice was "
            "picked instead. `reel voices --tts elevenlabs` lists yours"
        )
        return self._auto_voice(f"auto:n:mid:{voice}")

    def _auto_voice(self, token: str) -> str:
        """A voice for a speaker token ``auto:<gender>:<age>:<who>``: right gender and age if the account has one,
        preferring voices no other speaker got, the same pick every run (it is cached with the token)."""
        if token in self._assigned:
            return self._assigned[token]
        _, gender, age, who = ([*token.split(":", 3), "", "", "", ""])[:4]
        voices = self.fetch_voices()
        pool = [v for v in voices if v.get("category") == "premade"] or [
            v for v in voices if v.get("category") not in ("cloned",)
        ]
        if not pool:
            raise TTSUnavailableError(
                "this ElevenLabs key lists no voices to choose from: set ELEVENLABS_VOICE (or characters[].voice) to a voice id"
            )
        used = set(self._assigned.values())
        by_id = {str(v["voice_id"]): v for v in voices}

        def gender_of(v: dict[str, Any]) -> str:
            return str(_labels(v).get("gender", "")).lower()

        taken = [gender_of(by_id[i]) for i in used if i in by_id]
        scarcer = (
            "female" if taken.count("male") > taken.count("female") else "male"
        )  # keep a cast of 2+ varied

        def score(v: dict[str, Any]) -> float:
            labels = _labels(v)
            s = 0.0
            if gender == "f":
                s += 3 if gender_of(v) == "female" else 0
            elif gender == "m":
                s += 3 if gender_of(v) == "male" else 0
            elif gender_of(v) == scarcer:
                s += 0.5
            if str(labels.get("age", "")).lower() == _AGE_LABELS.get(age, ""):
                s += 2
            use = f"{labels.get('use_case', '')} {labels.get('descriptive', '')}".lower()
            if who == "narrator":
                s += 1 if any(k in use for k in ("narrat", "audiobook", "news")) else 0
            elif any(k in use for k in ("character", "animation", "conversation", "social")):
                s += 1
            return s

        best = min(
            pool,
            key=lambda v: (
                -score(v),
                str(v["voice_id"]) in used,
                stable_int("el-voice", token, str(v["voice_id"])),
            ),
        )
        self._assigned[token] = str(best["voice_id"])
        return self._assigned[token]

    # -- synthesis ----------------------------------------------------------------------------
    def _render(self, text: str, voice: str | None, speed: float) -> TTSClip:
        voice_id = self._voice_id(voice or self.default_voice or "auto")
        settings: dict[str, float] = {
            "stability": self.stability,
            "similarity_boost": self.similarity,
        }
        if abs(speed - 1.0) > 1e-6:
            settings["speed"] = float(np.clip(speed, 0.7, 1.2))  # the range the API accepts
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": settings,
            "seed": stable_int("elevenlabs", voice_id, text)
            % 2**32,  # the same take for the same line, as far as it can
        }
        resp = self._request(
            "POST",
            f"/v1/text-to-speech/{quote(voice_id, safe='')}/with-timestamps",
            params={"output_format": OUTPUT_FORMAT},
            body=body,
        )
        if resp.status_code >= 400:
            self._fail(resp, f"the voice {voice_id}" if resp.status_code == 404 else "this line")
        data = self._json(resp, "this line")
        try:
            raw = base64.b64decode(str(data.get("audio_base64") or ""), validate=False)
        except (binascii.Error, ValueError) as exc:
            raise TTSUnavailableError("ElevenLabs sent audio that is not valid base64") from exc
        if not raw:
            raise TTSUnavailableError("ElevenLabs returned no audio for this line")
        samples, sr = _decode_audio(raw)
        self.characters_sent += len(text)
        self.lines_sent += 1
        return TTSClip(samples, sr, word_timings(text, data.get("alignment")))

    # -- account check (spends no credits) ----------------------------------------------------
    def account(self) -> dict[str, Any] | None:
        """Plan and credits of the account, ``{}`` when the reply has no numbers, ``None`` when this key may not read the
        subscription (fine for synthesis).  A rejected key raises.  Spends no credits."""
        resp = self._request("GET", "/v1/user/subscription")
        if resp.status_code < 400:
            sub = resp.json()
            used, limit = sub.get("character_count"), sub.get("character_limit")
            if isinstance(used, int) and isinstance(limit, int):
                return {
                    "tier": str(sub.get("tier", "?")),
                    "characters_used": used,
                    "characters_limit": limit,
                    "characters_left": max(limit - used, 0),
                }
            return {}
        if resp.status_code in (401, 403) and "permission" not in resp.text.lower():
            self._fail(resp, "the account")
        return None

    def check(self) -> list[str]:
        """What ``reel doctor --tts elevenlabs`` prints: plan, credits left, the model, the voices.  No credits are spent."""
        self.require_key()
        lines = [f"elevenlabs: key accepted by {self.destination}; model {self.model}"]
        acct = self.account()
        if acct:
            lines.append(
                f"            plan {acct['tier']}: {acct['characters_left']} of {acct['characters_limit']} characters left this period"
            )
        elif acct is None:
            lines.append(
                "            (this key may not read the subscription; that is fine for synthesis)"
            )
        resp = self._request("GET", "/v1/models")
        if resp.status_code < 400:
            models = {m.get("model_id"): m for m in resp.json() if isinstance(m, dict)}
            known = models.get(self.model)
            if known is None:
                lines.append(
                    f"            model {self.model!r} is not in your model list: {', '.join(sorted(map(str, models))[:6])}"
                )
            elif not known.get("can_do_text_to_speech", True):
                lines.append(f"            model {self.model!r} cannot do text-to-speech")
        try:
            voices = self.fetch_voices()
            lines.append(
                f"            {len(voices)} voice(s) available; `reel voices --tts elevenlabs` lists them"
            )
        except TTSUnavailableError as exc:
            lines.append(f"            voices: {exc}")
        return lines


__all__ = ["DEFAULT_MODEL", "ELEVENLABS_URL", "OUTPUT_FORMAT", "ElevenLabsTTS", "word_timings"]
