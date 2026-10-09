"""The ElevenLabs engine, against a scripted fake of the API (no network, no real key, no credits)."""

from __future__ import annotations

import base64
import io
import json
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from typer.testing import CliRunner

from reel.audio.elevenlabs import (
    DEFAULT_MODEL,
    OUTPUT_FORMAT,
    SAMPLE_RATE,
    ElevenLabsTTS,
    word_timings,
)
from reel.audio.pipeline import prepare_audio
from reel.audio.tts import TTSUnavailableError, get_tts_engine
from reel.cli.main import app
from reel.core.spec import ReelSpec
from reel.llm.client import ENV_KEYS, load_env_file

KEY = "xi-FAKE-0123456789abcdef"  # obviously not a real key
RACHEL = "21m00Tcm4TlvDq8ikWAM"
ADAM = "pNInz6obpgDQGcFmaJgB"
ANTONI = "ErXwobaYiN019PkySvjV"
ELLI = "MF3mGyEYCl7XYWbV9V6O"
ARNOLD = "VR6AewLTigWG4xSOukaG"
CLONE = "cccccccccccccccccccc"

VOICES = [
    {
        "voice_id": RACHEL,
        "name": "Rachel",
        "category": "premade",
        "labels": {
            "gender": "female",
            "age": "young",
            "accent": "american",
            "use_case": "narration",
        },
    },
    {
        "voice_id": ADAM,
        "name": "Adam",
        "category": "premade",
        "labels": {"gender": "male", "age": "middle_aged", "use_case": "narration"},
    },
    {
        "voice_id": ANTONI,
        "name": "Antoni",
        "category": "premade",
        "labels": {"gender": "male", "age": "young", "use_case": "characters"},
    },
    {
        "voice_id": ELLI,
        "name": "Elli",
        "category": "premade",
        "labels": {"gender": "female", "age": "young", "use_case": "social media"},
    },
    {
        "voice_id": ARNOLD,
        "name": "Arnold",
        "category": "premade",
        "labels": {"gender": "male", "age": "old", "use_case": "characters"},
    },
    {"voice_id": CLONE, "name": "My own clone", "category": "cloned", "labels": {}},
]


def pcm(seconds: float) -> bytes:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    fade = np.minimum(1.0, np.minimum(t, seconds - t) * 20)
    return (0.4 * np.sin(2 * np.pi * 200 * t) * fade * 32767).astype("<i2").tobytes()


def alignment(text: str, per_char: float = 0.05) -> dict[str, Any]:
    chars = list(text)
    return {
        "characters": chars,
        "character_start_times_seconds": [round(i * per_char, 4) for i in range(len(chars))],
        "character_end_times_seconds": [round((i + 1) * per_char, 4) for i in range(len(chars))],
    }


class Server:
    """A tiny ElevenLabs: records every request and answers from a script, or with sensible defaults."""

    def __init__(
        self,
        voices: list[dict[str, Any]] | None = None,
        script: list[httpx.Response | Exception] | None = None,
        always: Callable[[], httpx.Response] | None = None,
    ) -> None:
        self.voices = VOICES if voices is None else voices
        self.script = list(script or [])
        self.always = always
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self)

    @property
    def speech(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/with-timestamps")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/with-timestamps"):
            if self.script:
                reply = self.script.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            if self.always is not None:
                return self.always()
            text = json.loads(request.content)["text"]
            audio = base64.b64encode(pcm(0.05 * len(text) + 0.2)).decode()
            return httpx.Response(
                200,
                json={
                    "audio_base64": audio,
                    "alignment": alignment(text),
                    "normalized_alignment": alignment(text),
                },
            )
        if path == "/v2/voices":
            return httpx.Response(200, json={"voices": self.voices, "has_more": False})
        if path == "/v1/user/subscription":
            return httpx.Response(
                200, json={"tier": "creator", "character_count": 1200, "character_limit": 100000}
            )
        if path == "/v1/models":
            return httpx.Response(
                200,
                json=[
                    {"model_id": DEFAULT_MODEL, "can_do_text_to_speech": True},
                    {"model_id": "eleven_flash_v2_5", "can_do_text_to_speech": True},
                ],
            )
        return httpx.Response(404, json={"detail": "no such route"})


def error(status: int, code: str, message: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"detail": {"status": code, "message": message}}, headers=headers
    )


def engine(server: Server, **kw: Any) -> ElevenLabsTTS:
    env = {"ELEVENLABS_API_KEY": KEY, **kw.pop("env", {})}
    kw.setdefault("sleep", lambda _s: None)
    return ElevenLabsTTS(env=env, transport=server.transport, **kw)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private cache, and no keys or .env files from the machine running the tests."""
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REEL_ENV_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "cache"


# ------------------------------------------------------------------ the request and the clip
def test_a_line_is_one_request_in_the_documented_shape() -> None:
    server = Server()
    clip = engine(server).synthesize("Hello brave world", RACHEL)
    (req,) = server.requests  # an id needs no voice lookup
    assert req.method == "POST"
    assert req.url.path == f"/v1/text-to-speech/{RACHEL}/with-timestamps"
    assert dict(req.url.params) == {"output_format": OUTPUT_FORMAT}
    assert req.headers["xi-api-key"] == KEY
    body = json.loads(req.content)
    assert body["text"] == "Hello brave world" and body["model_id"] == DEFAULT_MODEL
    assert body["voice_settings"] == {
        "stability": 0.5,
        "similarity_boost": 0.75,
    }  # no speed unless asked
    assert isinstance(body["seed"], int) and 0 <= body["seed"] < 2**32
    assert clip.sample_rate == SAMPLE_RATE and clip.samples.dtype == np.float32
    assert abs(float(clip.samples.max())) <= 1.0 and clip.duration == pytest.approx(0.05 * 17 + 0.2)


def test_word_timings_come_from_the_character_alignment() -> None:
    clip = engine(Server()).synthesize("Hello brave world", RACHEL)
    assert clip.words is not None
    assert [w for _, _, w in clip.words] == ["Hello", "brave", "world"]
    assert clip.words[0][:2] == pytest.approx((0.0, 0.25))  # five characters at 50 ms
    assert clip.words[1][0] == pytest.approx(0.30)  # after the space
    assert all(a < b for a, b, _ in clip.words)


def test_a_speed_other_than_one_is_sent_and_kept_in_the_accepted_range() -> None:
    server = Server()
    engine(server).synthesize("fast one", RACHEL, speed=1.1)
    engine(server).synthesize("silly one", RACHEL, speed=3.0)
    assert [json.loads(r.content)["voice_settings"]["speed"] for r in server.speech] == [1.1, 1.2]


def test_audio_wrapped_in_a_wav_file_is_read_at_its_own_rate() -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes((np.sin(np.arange(8000) / 9) * 9000).astype("<i2").tobytes())
    reply = httpx.Response(
        200, json={"audio_base64": base64.b64encode(buf.getvalue()).decode(), "alignment": None}
    )
    clip = engine(Server(script=[reply])).synthesize("wrapped", RACHEL)
    assert clip.sample_rate == 16_000 and clip.samples.shape == (8000,) and clip.words is None


@pytest.mark.ffmpeg
def test_a_compressed_reply_is_decoded_instead_of_played_as_noise() -> None:
    """If the API ever ignored output_format and sent an MP3, reading it as PCM would be a burst of static."""
    import shutil
    import subprocess

    exe = shutil.which("ffmpeg")
    if exe is None:
        pytest.skip("ffmpeg is not installed")
    mp3 = subprocess.run(
        [
            exe,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=0.5",
            "-f",
            "mp3",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF
    reply = httpx.Response(
        200, json={"audio_base64": base64.b64encode(mp3).decode(), "alignment": None}
    )
    clip = engine(Server(script=[reply])).synthesize("compressed", RACHEL)
    assert clip.sample_rate == SAMPLE_RATE and 0.4 < clip.duration < 0.7
    spectrum = np.abs(np.fft.rfft(clip.samples[2000:8000]))
    assert abs(float(np.argmax(spectrum)) * SAMPLE_RATE / 6000 - 300) < 30  # still the 300 Hz tone


def test_alignment_that_does_not_spell_the_text_is_ignored() -> None:
    good = alignment("one two")
    assert word_timings("one two", good) is not None
    assert (
        word_timings("one two three", good) is None
    )  # the words differ: estimate from the audio instead
    assert word_timings("one two", None) is None and word_timings("one two", {}) is None
    broken = {**good, "character_end_times_seconds": good["character_end_times_seconds"][:-1]}
    assert word_timings("one two", broken) is None
    backwards = {
        **good,
        "character_start_times_seconds": list(reversed(good["character_start_times_seconds"])),
    }
    assert word_timings("one two", backwards) is None
    hindi = alignment("नमस्ते दुनिया")
    assert [w for _, _, w in word_timings("नमस्ते दुनिया", hindi) or []] == ["नमस्ते", "दुनिया"]


# ------------------------------------------------------------------ what a line costs
def test_a_line_is_paid_once_and_cached_lines_need_no_network() -> None:
    server = Server()
    first = engine(server)
    a = first.synthesize("Count me once", RACHEL)
    b = first.synthesize("Count me once", RACHEL)
    again = engine(server)  # a new process, in effect: only the disk cache is shared
    c = again.synthesize("Count me once", RACHEL)
    assert len(server.speech) == 1
    assert np.array_equal(a.samples, b.samples) and np.array_equal(a.samples, c.samples)
    assert c.words == a.words  # the exact timings are cached with the audio
    assert first.usage() is not None and "13 characters" in first.usage()  # type: ignore[operator]
    assert again.usage() is None  # nothing was billed for the cached line


def test_a_different_model_voice_or_text_is_a_different_take() -> None:
    server = Server()
    engine(server).synthesize("Same words", RACHEL)
    engine(server, model="eleven_flash_v2_5").synthesize("Same words", RACHEL)
    engine(server).synthesize("Same words", ADAM)
    engine(server).synthesize("Other words", RACHEL)
    assert len(server.speech) == 4
    assert json.loads(server.speech[1].content)["model_id"] == "eleven_flash_v2_5"


def test_the_cache_works_without_a_key_for_lines_that_were_already_paid_for() -> None:
    engine(Server()).synthesize("Already paid", RACHEL)
    no_key = ElevenLabsTTS(env={}, transport=Server().transport)
    assert no_key.available() is False
    assert no_key.synthesize("Already paid", RACHEL).duration > 0  # served from disk
    with pytest.raises(TTSUnavailableError, match="ELEVENLABS_API_KEY"):
        no_key.synthesize("A new line", RACHEL)


# ------------------------------------------------------------------ voices
def test_a_voice_can_be_an_id_or_a_name_of_the_account() -> None:
    server = Server()
    eng = engine(server)
    eng.synthesize("by id", RACHEL)
    assert not [r for r in server.requests if r.url.path == "/v2/voices"]  # an id needs no lookup
    eng.synthesize("by name", "adam")  # case does not matter
    eng.synthesize("by prefix", "Anton")
    assert [r.url.path.split("/")[3] for r in server.speech] == [RACHEL, ADAM, ANTONI]
    assert (
        len([r for r in server.requests if r.url.path == "/v2/voices"]) == 1
    )  # listed once per run
    assert eng.list_voices() == sorted(v["name"] for v in VOICES)


def test_an_unknown_voice_name_gets_a_fitting_voice_and_a_warning() -> None:
    """The example specs name voices of other engines ('high', 'narrator'): they must not break an ElevenLabs render."""
    server = Server()
    eng = engine(server)
    eng.synthesize("hello", "high")
    assert server.speech and eng.take_warnings() == [
        "ElevenLabs has no voice named 'high' (it may be a voice for another engine); a fitting voice was picked "
        "instead. `reel voices --tts elevenlabs` lists yours"
    ]
    assert eng.take_warnings() == []  # reported once


def test_each_speaker_gets_a_fitting_and_a_distinct_voice() -> None:
    eng = engine(Server())
    mia = SimpleNamespace(id="mia", name="Mia", archetype="kid")
    ben = SimpleNamespace(id="ben", name="Ben", archetype="everyman")
    joe = SimpleNamespace(id="joe", name="Grandpa Joe", archetype="elder")
    tokens = [eng.speaker_voice(c.id, c) for c in (mia, ben, joe)]
    assert tokens[0].startswith("auto:f:young:")  # type: ignore[union-attr]
    assert tokens[1].startswith("auto:n:mid:") and tokens[2].startswith("auto:n:old:")  # type: ignore[union-attr]
    ids = [eng._voice_id(t) for t in tokens]  # type: ignore[arg-type]
    by_id = {v["voice_id"]: v for v in VOICES}
    assert len(set(ids)) == 3
    assert by_id[ids[0]]["labels"]["gender"] == "female"
    assert by_id[ids[2]]["labels"]["age"] == "old"
    assert CLONE not in ids  # a clone of someone's voice is never handed out automatically
    assert eng._voice_id(tokens[0]) == ids[0]  # type: ignore[arg-type]


def test_a_default_voice_from_the_environment_is_used_for_everyone() -> None:
    server = Server()
    eng = engine(server, env={"ELEVENLABS_VOICE": "Rachel"})
    ben = SimpleNamespace(id="ben", name="Ben", archetype="everyman")
    assert eng.speaker_voice("ben", ben) == "Rachel"
    eng.synthesize("hello", eng.speaker_voice("ben", ben))
    assert server.speech[0].url.path.split("/")[3] == RACHEL


# ------------------------------------------------------------------ errors
def test_a_rejected_key_stops_everything_without_leaking_the_key() -> None:
    server = Server(
        script=[error(401, "invalid_api_key", f"Invalid API key {KEY}")],
    )
    eng = engine(server)
    with pytest.raises(TTSUnavailableError) as err:
        eng.synthesize("first", RACHEL)
    message = str(err.value)
    assert KEY not in message and "HTTP 401" in message and "ELEVENLABS_API_KEY" in message
    before = len(server.requests)
    with pytest.raises(TTSUnavailableError):  # later lines fail at once, without another request
        eng.synthesize("second", RACHEL)
    assert len(server.requests) == before


def test_running_out_of_credits_is_reported_as_such() -> None:
    server = Server(
        script=[error(401, "quota_exceeded", "This request exceeds your quota of 10000.")]
    )
    with pytest.raises(TTSUnavailableError, match="no credits left"):
        engine(server).synthesize("a line", RACHEL)


def test_a_missing_voice_is_reported_but_does_not_stop_other_lines() -> None:
    server = Server(script=[error(404, "voice_not_found", "A voice with that id does not exist.")])
    eng = engine(server)
    with pytest.raises(TTSUnavailableError, match="does not know the voice"):
        eng.synthesize("one", "0" * 20)
    assert eng.synthesize("two", RACHEL).duration > 0  # not an account-wide problem


def test_validation_errors_show_the_servers_reason() -> None:
    reply = httpx.Response(
        422, json={"detail": [{"loc": ["body", "text"], "msg": "text too long", "type": "x"}]}
    )
    with pytest.raises(TTSUnavailableError, match=r"text too long \(text\)"):
        engine(Server(script=[reply])).synthesize("x", RACHEL)


def test_rate_limits_and_server_errors_are_retried_with_a_pause() -> None:
    sleeps: list[float] = []
    server = Server(
        script=[
            error(429, "too_many_concurrent_requests", "slow down", **{"retry-after": "2"}),
            httpx.Response(503, text="busy"),
        ]
    )
    clip = engine(server, sleep=sleeps.append).synthesize("persistent", RACHEL)
    assert clip.duration > 0 and len(server.speech) == 3
    assert sleeps[0] == 2.0 and len(sleeps) == 2  # Retry-After is honoured, then a backoff

    always = Server(always=lambda: error(500, "oops", "internal"))
    with pytest.raises(TTSUnavailableError, match="server error"):
        engine(always, max_retries=2).synthesize("hopeless", RACHEL)
    assert len(always.speech) == 3  # the first try and two retries


def test_network_trouble_is_a_clean_error_without_the_key() -> None:
    server = Server(script=[httpx.ConnectError(f"all connection attempts failed for {KEY}")])
    with pytest.raises(TTSUnavailableError) as err:
        engine(server).synthesize("offline", RACHEL)
    assert KEY not in str(err.value) and "cannot reach ElevenLabs" in str(err.value)
    timeout = Server(script=[httpx.ReadTimeout("slow")])
    with pytest.raises(TTSUnavailableError, match="did not answer"):
        engine(timeout).synthesize("slow", RACHEL)


def test_an_empty_or_broken_reply_is_an_error_not_silence() -> None:
    for reply in (
        httpx.Response(200, json={"audio_base64": "", "alignment": None}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["unexpected"]),
    ):
        with pytest.raises(TTSUnavailableError):
            engine(Server(script=[reply])).synthesize("x", RACHEL)


# ------------------------------------------------------------------ keys and where they go
def test_the_engine_needs_a_key_and_says_where_to_put_it() -> None:
    with pytest.raises(TTSUnavailableError) as err:
        get_tts_engine("elevenlabs")
    text = str(err.value)
    assert "ELEVENLABS_API_KEY" in text and ".env" in text and "never asks" in text


def test_a_malformed_key_is_refused_without_quoting_it() -> None:
    with pytest.raises(TTSUnavailableError) as err:
        ElevenLabsTTS(env={"ELEVENLABS_API_KEY": f"{KEY} extra"})
    assert KEY not in str(err.value) and "control character" in str(err.value)
    assert (
        ElevenLabsTTS(env={"ELEVENLABS_API_KEY": f"  {KEY}\r\n"})._key == KEY
    )  # a pasted newline is fine


def test_reprs_never_contain_the_key() -> None:
    eng = engine(Server())
    assert KEY not in repr(eng) and "api.elevenlabs.io" in repr(eng)


def test_the_key_must_travel_over_https_unless_the_server_is_local() -> None:
    env = {"ELEVENLABS_API_KEY": KEY}
    with pytest.raises(TTSUnavailableError, match="https"):
        ElevenLabsTTS(env={**env, "ELEVENLABS_BASE_URL": "http://api.example.com"})
    local = ElevenLabsTTS(env={**env, "ELEVENLABS_BASE_URL": "http://localhost:8765"})
    assert local.destination == "localhost:8765"
    eu = ElevenLabsTTS(env={**env, "ELEVENLABS_BASE_URL": "https://api.eu.residency.elevenlabs.io"})
    assert eu.destination == "api.eu.residency.elevenlabs.io"


def test_a_cloned_repositorys_env_file_cannot_redirect_the_key(tmp_path: Path) -> None:
    f = tmp_path / "cloned.env"
    f.write_text(
        f"ELEVENLABS_API_KEY={KEY}\nELEVENLABS_BASE_URL=https://attacker.example\n"
        "ELEVENLABS_MODEL=eleven_flash_v2_5\nREEL_TTS=elevenlabs\n"
    )
    untrusted = load_env_file(f, trusted=False)
    assert "ELEVENLABS_BASE_URL" not in untrusted
    assert (
        untrusted["ELEVENLABS_MODEL"] == "eleven_flash_v2_5"
        and untrusted["ELEVENLABS_API_KEY"] == KEY
    )
    eng = ElevenLabsTTS(env=untrusted)
    assert eng.destination == "api.elevenlabs.io" and eng.model == "eleven_flash_v2_5"


def test_reel_tts_chooses_the_engine_only_when_none_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert not isinstance(get_tts_engine(), ElevenLabsTTS)  # auto never picks it
    monkeypatch.setenv("REEL_TTS", "elevenlabs")
    assert isinstance(get_tts_engine(), ElevenLabsTTS)
    assert not isinstance(
        get_tts_engine("auto"), ElevenLabsTTS
    )  # an explicit choice beats the default
    assert isinstance(get_tts_engine("11labs"), ElevenLabsTTS)


# ------------------------------------------------------------------ inside the audio pipeline
def story() -> ReelSpec:
    return ReelSpec.model_validate(
        {
            "meta": {"title": "voices", "style": "paper_cutout", "seed": 3},
            "characters": [
                {"id": "mia", "archetype": "kid", "name": "Mia"},
                {"id": "ben", "archetype": "everyman", "name": "Ben"},
            ],
            "scenes": [
                {
                    "id": "s0",
                    "duration_sec": 6.0,
                    "background": {"template": "room"},
                    "captions": [
                        {
                            "text": "Hello brave world",
                            "t0": 0.3,
                            "t1": 1.5,
                            "style": "subtitle",
                            "speaker": "mia",
                        },
                        {
                            "text": "Nice to meet you",
                            "t0": 3.0,
                            "t1": 4.0,
                            "style": "subtitle",
                            "speaker": "ben",
                        },
                    ],
                }
            ],
            "audio": {"music": None, "voiceover": "tts"},
        }
    )


def test_the_pipeline_uses_the_exact_timings_and_reports_what_was_billed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    eng = engine(server)
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: eng)
    plan = prepare_audio(story(), work_dir=tmp_path / "w", engine="elevenlabs")
    assert plan.wav is not None and plan.wav.exists() and plan.warnings == []
    words = plan.word_timings[0][0]
    assert [w for _, _, w in words] == ["Hello", "brave", "world"]
    assert words[0][1] - words[0][0] == pytest.approx(
        0.25, abs=1e-6
    )  # the API's own times, not estimates
    assert (
        len(plan.notes) == 1
        and "33 characters" in plan.notes[0]
        and "api.elevenlabs.io" in plan.notes[0]
    )
    assert KEY not in "".join(plan.notes + plan.warnings)
    voices = [r.url.path.split("/")[3] for r in server.speech]
    assert len(voices) == 2 and voices[0] != voices[1]  # two speakers, two voices

    fresh = engine(server)  # the next run: a new engine object, the same disk cache
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: fresh)
    again = prepare_audio(story(), work_dir=tmp_path / "w2", engine="elevenlabs")
    assert (
        len(server.speech) == 2 and again.notes == []
    )  # every line came from the cache: nothing billed


def test_a_failing_line_falls_back_to_the_babble_voice_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server(always=lambda: error(500, "oops", "internal"))
    eng = engine(server, max_retries=0)
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: eng)
    plan = prepare_audio(story(), work_dir=tmp_path / "w", engine="elevenlabs")
    assert plan.wav is not None
    failed = [w for w in plan.warnings if "'elevenlabs' failed" in w]
    assert (
        len(failed) == 1 and "and 1 more line(s)" in failed[0] and "babble" in failed[0]
    )  # said once, not per line
    assert plan.word_timings[0][0], "the captions are still timed"


# ------------------------------------------------------------------ account check and the CLI
def test_the_account_check_reports_plan_credits_model_and_voices() -> None:
    lines = engine(Server()).check()
    text = "\n".join(lines)
    assert "key accepted" in text and "plan creator" in text and "98800 of 100000" in text
    assert "5 voice(s)" not in text and "6 voice(s)" in text and KEY not in text
    unknown = "\n".join(engine(Server(), model="eleven_nope").check())
    assert "'eleven_nope' is not in your model list" in unknown


def test_the_account_check_survives_a_key_without_every_permission() -> None:
    server = Server()
    original = server.__call__

    def restricted(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/subscription":
            return error(401, "missing_permissions", "The key is missing the permission user_read")
        return original(request)

    eng = ElevenLabsTTS(env={"ELEVENLABS_API_KEY": KEY}, transport=httpx.MockTransport(restricted))
    assert "may not read the subscription" in "\n".join(eng.check())


def test_doctor_names_the_key_but_never_shows_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    result = CliRunner().invoke(app, ["doctor"])
    assert (
        "elevenlabs: key found in shell" in result.output
        and "never used by --tts auto" in result.output
    )
    assert KEY not in result.output


def test_doctor_without_a_key_explains_how_to_set_one() -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert "elevenlabs: no key" in result.output and "ELEVENLABS_API_KEY" in result.output


def test_voices_and_the_account_check_fail_cleanly_without_a_key() -> None:
    runner = CliRunner()
    listing = runner.invoke(app, ["voices", "--tts", "elevenlabs"])
    assert listing.exit_code == 1 and "ELEVENLABS_API_KEY" in listing.output
    check = runner.invoke(app, ["doctor", "--tts", "elevenlabs"])
    assert check.exit_code == 1 and "speech check failed" in check.output


def test_voices_lists_names_ids_and_traits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    server = Server()
    original = ElevenLabsTTS.__init__

    def with_server(self: ElevenLabsTTS, **kw: Any) -> None:
        original(self, transport=server.transport, **kw)

    monkeypatch.setattr(ElevenLabsTTS, "__init__", with_server)
    result = CliRunner().invoke(app, ["voices", "--tts", "elevenlabs"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())  # the terminal may wrap long lines
    assert "6 voice(s)" in flat and RACHEL in flat and "female, young, american" in flat
    assert KEY not in result.output


def test_a_render_asking_for_elevenlabs_without_a_key_stops_before_rendering(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "story.json"
    spec.write_text(story().to_json())
    result = CliRunner().invoke(app, ["render", str(spec), "--preview", "--tts", "elevenlabs"])
    assert result.exit_code == 1
    assert "speech engine problem" in result.output and "ELEVENLABS_API_KEY" in result.output
    assert not list(tmp_path.glob("*.mp4"))  # no silent video was produced instead
