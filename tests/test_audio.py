"""Tests for the offline audio package: sfx, music, tts, alignment, mixing and the pipeline."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import reel.audio as audio_pkg
from reel.audio import sfx as sfx_mod
from reel.audio.align import estimate_word_timings, spread_words, voiced_span, word_weight
from reel.audio.mix import (
    MixClip,
    MixPlan,
    duck_envelope,
    integrated_lufs,
    limit,
    mix_tracks,
    read_wav,
    resample,
    write_wav,
)
from reel.audio.music import MUSIC_MOODS, generate_music, parse_music_spec
from reel.audio.pipeline import AUTO_EVENT_SFX, AudioPlan, prepare_audio
from reel.audio.tts import (
    BabbleTTS,
    MacSayTTS,
    PiperTTS,
    TTSClip,
    TTSEngine,
    TTSUnavailableError,
    UnknownTTSEngineError,
    get_tts_engine,
    tts_cache_dir,
)
from reel.core.catalog import CATALOG, Catalog
from reel.core.spec import CharacterSpec, ReelSpec

SR = 48_000
REQUIRED_SFX = [
    "whoosh",
    "swoosh",
    "pop",
    "boing",
    "thud",
    "ding",
    "chime",
    "click",
    "camera_shutter",
    "page_flip",
    "footstep",
    "jump",
    "land",
    "gasp",
    "sparkle",
    "magic",
    "splash",
    "door_knock",
    "door_slam",
    "bell",
    "record_scratch",
    "cymbal",
    "applause",
    "buzz",
    "success",
    "whistle",
    "heartbeat",
    "wind",
    "punch",
    "slap",
    "laugh",
    "pickup",
    "drumroll",
    "fail",
]
ACTION_EVENT_SFX = ("footstep", "jump", "land", "thud", "gasp", "laugh", "pickup")

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
needs_say = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("say") is None, reason="macOS `say` is not available"
)


@pytest.fixture
def tts_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point reel's cache at a temp dir so tests never touch (or depend on) ~/.cache/reel."""
    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


def tone(freq: float, seconds: float, sr: int = SR, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def single_bin(x: np.ndarray, freq: float, sr: int = SR) -> float:
    """Amplitude of the ``freq`` component of ``x`` (one-bin DFT)."""
    t = np.arange(x.shape[0]) / sr
    return float(2 * abs(np.mean(x * np.exp(-2j * np.pi * freq * t))))


# =============================================================================== sfx
def test_required_sfx_names_are_registered() -> None:
    names = set(CATALOG.sfx.names())
    missing = [n for n in (*REQUIRED_SFX, *ACTION_EVENT_SFX) if n not in names]
    assert not missing
    assert len(REQUIRED_SFX) == 34
    assert all(CATALOG.sfx.entry(n).meta.get("summary") for n in REQUIRED_SFX)
    assert all(CATALOG.sfx.get(n).name == n for n in REQUIRED_SFX)


@pytest.mark.parametrize("name", REQUIRED_SFX)
def test_sfx_is_finite_bounded_audible_and_deterministic(name: str) -> None:
    x = sfx_mod.synth(name)
    assert x.dtype == np.float32 and x.ndim == 1
    assert np.all(np.isfinite(x))
    assert 0.05 <= x.shape[0] / SR <= 3.0
    assert float(np.max(np.abs(x))) <= 1.0
    assert float(np.max(np.abs(x))) > 0.05, "effect is (nearly) silent"
    assert float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) > 0.003
    assert np.array_equal(x, sfx_mod.synth(name)), "same (name, seed) must give the same samples"
    # click-free: starts and ends from silence
    assert abs(float(x[0])) < 0.02 and abs(float(x[-1])) < 0.02
    assert abs(float(np.mean(x))) < 0.02, "no DC offset"


def test_sfx_seed_changes_the_noise_but_not_the_character() -> None:
    a, b = sfx_mod.synth("footstep", seed=1), sfx_mod.synth("footstep", seed=2)
    assert a.shape[0] > 0 and not np.array_equal(a, b)
    assert abs(a.shape[0] - b.shape[0]) <= 1
    assert sfx_mod.synth("whoosh", sr=24_000).shape[0] == pytest.approx(
        sfx_mod.synth("whoosh").shape[0] / 2, abs=2
    )


def test_sfx_effects_are_distinct() -> None:
    sounds = {n: sfx_mod.synth(n) for n in REQUIRED_SFX}
    keys = {(s.shape[0], round(float(np.sum(np.abs(s))), 1)) for s in sounds.values()}
    assert len(keys) == len(REQUIRED_SFX)


def test_register_sfx_into_a_private_catalog() -> None:
    cat = Catalog()

    @sfx_mod.register_sfx("beep", summary="a test beep", catalog=cat)
    def _beep(rng: np.random.Generator, sr: int) -> np.ndarray:
        return 0.5 * np.sin(2 * np.pi * 880 * np.arange(sr // 4) / sr)

    assert "beep" in cat.sfx and cat.sfx.entry("beep").meta["summary"] == "a test beep"
    x = sfx_mod.synth("beep", catalog=cat)
    assert x.dtype == np.float32 and 0.2 < x.shape[0] / SR < 0.3
    assert (
        abs(float(x[0])) < 0.02 and abs(float(x[-1])) < 0.02
    )  # faded even though the recipe was not
    with pytest.raises(KeyError):
        sfx_mod.synth("not_an_effect", catalog=cat)


def test_wav_file_overrides_the_synth_and_registers_new_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "sfx"
    write_wav(folder / "pop.wav", tone(500, 0.4), SR)  # overrides a built-in
    write_wav(folder / "my_quack.wav", tone(300, 0.2, 22_050), 22_050)  # brand new name, other rate
    monkeypatch.setenv("REEL_SFX_DIR", str(folder))
    popped = sfx_mod.synth("pop")
    assert popped.shape[0] == pytest.approx(0.4 * SR, abs=5)
    assert single_bin(popped[2000:20000], 500) > 0.2
    cat = Catalog()
    assert sfx_mod.discover_sfx_files(catalog=cat) == ["my_quack", "pop"]
    quack = sfx_mod.synth("my_quack", catalog=cat)
    assert quack.shape[0] == pytest.approx(0.2 * SR, abs=5), "resampled to 48 kHz"
    assert sfx_mod.find_sfx_file("whoosh") is None
    # explicit search dirs work too (the pipeline passes <spec dir>/assets/sfx)
    monkeypatch.delenv("REEL_SFX_DIR")
    assert sfx_mod.synth("pop", search_dirs=[folder]).shape[0] == pytest.approx(0.4 * SR, abs=5)


# =============================================================================== music
def test_music_moods_cover_the_required_set() -> None:
    required = {"upbeat", "calm", "playful", "tense", "epic", "mysterious", "sad", "corporate"}
    assert required <= set(MUSIC_MOODS)


@pytest.mark.parametrize("mood", MUSIC_MOODS)
def test_music_is_stereo_right_length_bounded_and_deterministic(mood: str) -> None:
    x = generate_music(mood, 6.0, seed=3)
    assert x.dtype == np.float32 and x.shape == (6 * SR, 2)
    assert np.all(np.isfinite(x))
    assert 0.3 < float(np.max(np.abs(x))) <= 0.9 + 1e-6
    assert float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) > 0.03
    assert np.array_equal(x, generate_music(mood, 6.0, seed=3))
    # fades: starts and ends near silence
    assert float(np.max(np.abs(x[:200]))) < 0.02 and float(np.max(np.abs(x[-200:]))) < 0.02
    # the two channels are genuinely different (stereo, not dual mono)
    assert not np.array_equal(x[:, 0], x[:, 1])
    assert np.corrcoef(x[:, 0], x[:, 1])[0, 1] < 0.999


def test_music_differs_by_mood_and_by_seed() -> None:
    beds = {m: generate_music(m, 5.0, seed=1) for m in MUSIC_MOODS}
    for i, a in enumerate(MUSIC_MOODS):
        for b in MUSIC_MOODS[i + 1 :]:
            assert not np.array_equal(beds[a], beds[b])
            assert abs(float(np.corrcoef(beds[a][:, 0], beds[b][:, 0])[0, 1])) < 0.5
    for m in ("upbeat", "mysterious", "epic"):
        assert not np.array_equal(generate_music(m, 5.0, seed=1), generate_music(m, 5.0, seed=2))


def test_music_length_edge_cases() -> None:
    assert generate_music("calm", 0.0).shape == (0, 2)
    short = generate_music("upbeat", 0.8)
    assert short.shape == (int(0.8 * SR), 2) and np.all(np.isfinite(short))
    assert generate_music("calm", 1.0, sr=22_050).shape == (22_050, 2)
    with pytest.raises(ValueError, match="unknown music mood"):
        generate_music("polka", 5.0)


def test_music_opening_does_not_depend_on_the_duration() -> None:
    a, b = generate_music("upbeat", 30.0, seed=4), generate_music("upbeat", 40.0, seed=4)
    seg = slice(SR, 5 * SR)
    corr = float(np.corrcoef(a[seg, 0], b[seg, 0])[0, 1])
    assert corr > 0.95


def test_music_speed_is_practical() -> None:
    t0 = time.perf_counter()
    generate_music("upbeat", 30.0, seed=0)
    assert time.perf_counter() - t0 < 5.0


def test_parse_music_spec() -> None:
    assert parse_music_spec("procedural:upbeat") == "upbeat"
    assert parse_music_spec("procedural:Calm") == "calm"
    assert parse_music_spec("procedural") == "upbeat"
    assert parse_music_spec("procedural", default="sad") == "sad"
    assert parse_music_spec("music/theme.mp3") is None
    assert parse_music_spec(None) is None and parse_music_spec("") is None
    with pytest.raises(ValueError, match="polka"):
        parse_music_spec("procedural:polka")


# =============================================================================== tts
def test_babble_clip_length_scales_with_syllables(tts_cache: Path) -> None:
    eng = BabbleTTS()
    dur = {k: eng.synthesize(" ".join(["banana"] * k)).duration for k in (1, 2, 4, 8)}
    # ~4.3 syllables a second: each 3-syllable word adds ~0.70 s, linearly
    per_word = [(dur[4] - dur[2]) / 2, (dur[8] - dur[4]) / 4]
    assert per_word[0] == pytest.approx(3 / 4.3, rel=0.04) and per_word[1] == pytest.approx(
        per_word[0], rel=0.02
    )
    assert dur[8] > 3 * dur[2] > 0


def test_babble_pauses_at_punctuation_and_speed(tts_cache: Path) -> None:
    eng = BabbleTTS()
    plain, comma, period = (eng.synthesize(t).duration for t in ("one two", "one, two", "one. two"))
    assert comma - plain == pytest.approx(0.14, abs=0.01) and period - plain == pytest.approx(
        0.30, abs=0.01
    )
    base = eng.synthesize("this is a test of the speed control").duration
    fast = eng.synthesize("this is a test of the speed control", speed=2.0).duration
    assert fast < 0.62 * base


def test_babble_voices_and_waveform(tts_cache: Path) -> None:
    eng = BabbleTTS()
    a = eng.synthesize("Hello there my friend")
    b = eng.synthesize("Hello there my friend", voice="low")
    c = eng.synthesize("Hello there my friend", voice=eng.speaker_voice("ana"))
    assert a.samples.dtype == np.float32 and a.samples.ndim == 1 and a.sample_rate == 22_050
    assert float(np.max(np.abs(a.samples))) <= 0.9 and np.all(np.isfinite(a.samples))
    assert not np.array_equal(a.samples, b.samples) and not np.array_equal(a.samples, c.samples)
    assert a.duration == pytest.approx(b.duration, abs=1e-6), "pitch changes, timing does not"
    assert set(eng.list_voices()) >= {"babble", "low", "high", "child"}
    assert eng.available()


def test_babble_has_exact_monotonic_word_timings(tts_cache: Path) -> None:
    clip = BabbleTTS().synthesize("Wait, what? You did WHAT?!")
    assert clip.words is not None and [w for _, _, w in clip.words] == [
        "Wait,",
        "what?",
        "You",
        "did",
        "WHAT?!",
    ]
    prev = 0.0
    for a, b, _ in clip.words:
        assert prev <= a < b <= clip.duration
        prev = b


def test_tts_disk_cache_roundtrip(tts_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    eng = BabbleTTS()
    first = eng.synthesize("cache me if you can", voice="high", speed=1.1)
    files = sorted(p.suffix for p in tts_cache_dir().rglob("*") if p.is_file())
    assert files == [".json", ".npy"] and tts_cache_dir() == tts_cache / "tts"

    def boom(*_: Any, **__: Any) -> TTSClip:
        raise AssertionError("re-synthesised although the clip was cached")

    monkeypatch.setattr(BabbleTTS, "_render", boom)
    again = BabbleTTS().synthesize("cache me if you can", voice="high", speed=1.1)
    assert np.array_equal(first.samples, again.samples) and again.sample_rate == first.sample_rate
    assert again.words == first.words
    with pytest.raises(AssertionError):  # a different voice / speed / text is a different key
        BabbleTTS().synthesize("cache me if you can", voice="low", speed=1.1)
    with pytest.raises(AssertionError):
        BabbleTTS().synthesize("cache me if you can!", voice="high", speed=1.1)


def test_tts_cache_survives_corrupt_files(tts_cache: Path) -> None:
    eng = BabbleTTS()
    first = eng.synthesize("hello hello")
    for p in tts_cache_dir().rglob("*.npy"):
        p.write_bytes(b"not numpy")
    again = eng.synthesize("hello hello")
    assert np.array_equal(first.samples, again.samples)


@needs_say
def test_say_engine_speaks_and_caches(tts_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    eng = MacSayTTS()
    assert eng.available() and eng.list_voices()
    assert eng.resolve_voice(None) and eng.resolve_voice(None) in eng.list_voices()
    clip = eng.synthesize("Hello there, this is a test.")
    assert clip.samples.dtype == np.float32 and clip.samples.ndim == 1
    assert 0.8 < clip.duration < 6.0 and clip.sample_rate >= 16_000
    assert 0.05 < float(np.max(np.abs(clip.samples))) <= 1.0
    # faster -r means a shorter clip
    assert eng.synthesize("Hello there, this is a test.", speed=1.6).duration < clip.duration
    assert eng.voice_warning("definitely-not-a-voice")
    monkeypatch.setattr(
        MacSayTTS, "_render", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache miss"))
    )
    assert np.array_equal(
        MacSayTTS().synthesize("Hello there, this is a test.").samples, clip.samples
    )


def test_say_command_construction(tmp_path: Path) -> None:
    eng = MacSayTTS(say_binary="/usr/bin/say")
    cmd = eng.build_command("Samantha", 1.0, tmp_path / "o.aiff", tmp_path / "t.txt")
    assert cmd == [
        "/usr/bin/say",
        "-v",
        "Samantha",
        "-o",
        str(tmp_path / "o.aiff"),
        "-f",
        str(tmp_path / "t.txt"),
    ]
    fast = eng.build_command("Daniel", 1.5, tmp_path / "o.aiff", tmp_path / "t.txt")
    assert fast[fast.index("-r") + 1] == str(round(175 * 1.5))
    assert "--file-format=WAVE" in eng.build_command(
        None, 1.0, tmp_path / "o.wav", tmp_path / "t.txt", wav=True
    )


def fake_say(*voices: str) -> MacSayTTS:
    listing = "".join(f"{v}  en_US    # Hello\n" for v in voices)

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    return MacSayTTS(say_binary="/usr/bin/say", runner=runner)


def character(cid: str, archetype: str, name: str | None = None) -> Any:
    from reel.core.spec import CharacterSpec

    return CharacterSpec(id=cid, archetype=archetype, name=name)


def test_say_gives_each_character_a_voice_that_suits_them() -> None:
    eng = fake_say(
        "Samantha", "Junior", "Flo (English (US))", "Grandpa (English (US))", "Grandma (English (US))",
        "Fred", "Ralph", "Daniel", "Karen", "Moira",
    )  # fmt: skip
    pick = eng.speaker_voice
    assert pick("mia", character("mia", "kid", "Mia")) == "Flo (English (US))"  # a girl's name
    assert pick("tom", character("tom", "kid", "Tom")) == "Junior"
    assert pick("pip", character("pip", "elder", "Mr. Pip")) == "Grandpa (English (US))"
    assert pick("nan", character("nan", "elder", "Grandma Rose")) == "Grandma (English (US))"
    assert pick("bolt", character("bolt", "robot", "Bolt")) == "Fred"
    assert pick("boss", character("boss", "boss")) == "Ralph"
    assert pick("ana", character("ana", "everyman")) in {"Samantha", "Daniel", "Karen", "Moira"}
    # stable: the same character always gets the same voice
    assert pick("ana", character("ana", "everyman")) == pick("ana", character("ana", "everyman"))
    assert pick(None) is None


def test_a_base_voice_name_prefers_the_english_variant() -> None:
    listing = "Flo (German (Germany))  de_DE    # Hallo\nFlo (English (UK))  en_GB    # Hello\nFlo (English (US))  en_US    # Hello\n"

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    eng = MacSayTTS(say_binary="/usr/bin/say", runner=runner)
    assert eng.resolve_voice("Flo") == "Flo (English (US))"
    assert (
        eng.resolve_voice("Flo (German (Germany))") == "Flo (German (Germany))"
    )  # exact names still win


def test_say_voices_degrade_gracefully_when_the_nice_ones_are_not_installed() -> None:
    eng = fake_say("Samantha", "Daniel")
    for arch in ("kid", "elder", "robot", "boss", "hero", "everyman"):
        assert eng.speaker_voice("x", character("x", arch)) in {"Samantha", "Daniel"}
    assert (
        fake_say().speaker_voice("x", character("x", "kid")) is None
    )  # no voices at all: engine default


def test_the_pipeline_asks_the_engine_for_the_speakers_voice() -> None:
    from reel.audio.tts import reads_female

    assert reads_female("Mrs. Okafor") and reads_female("Priya") and not reads_female("Mr. Pip")
    assert not reads_female(None) and not reads_female("")


def test_say_voice_resolution_with_a_fake_say() -> None:
    listing = "Albert              en_US    # Hello\nEddy (English (US)) en_US    # Hi\nDaniel              en_GB    # Hello\n"

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    eng = MacSayTTS(say_binary="/usr/bin/say", runner=runner)
    assert eng.list_voices() == ["Albert", "Eddy (English (US))", "Daniel"]
    assert eng.default_voice() == "Eddy (English (US))", (
        "no Samantha: first non-novelty en_US voice"
    )
    assert (
        eng.resolve_voice("daniel") == "Daniel"
        and eng.resolve_voice("Eddy") == "Eddy (English (US))"
    )
    assert eng.resolve_voice("nope") == "Eddy (English (US))" and "nope" in (
        eng.voice_warning("nope") or ""
    )


def _fake_piper(calls: list[dict[str, Any]], sr: int = 22_050) -> Any:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"cmd": cmd, **kwargs})
        out = Path(cmd[cmd.index("--output_file") + 1])
        write_wav(out, tone(220, 0.5, sr, 0.4), sr)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return run


def test_piper_command_construction_with_a_fake_subprocess(tts_cache: Path, tmp_path: Path) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    model = voices / "en_US-test-medium.onnx"
    model.write_bytes(b"\0")
    calls: list[dict[str, Any]] = []
    eng = PiperTTS(
        binary="/opt/bin/piper", voice_dirs=[voices], runner=_fake_piper(calls), use_python=False
    )
    assert eng.available() and eng.list_voices() == ["en_US-test-medium"]

    clip = eng.synthesize("Hello\nthere world", voice=None, speed=1.0)
    cmd = calls[0]["cmd"]
    assert cmd[0] == "/opt/bin/piper"
    assert cmd[cmd.index("--model") + 1] == str(model)
    assert "--output_file" in cmd and "--length_scale" not in cmd
    assert calls[0]["input"] == b"Hello there world\n", "text goes on stdin, newlines flattened"
    assert clip.sample_rate == 22_050 and clip.duration == pytest.approx(0.5, abs=0.01)
    assert (
        clip.samples.dtype == np.float32 and single_bin(clip.samples[2000:9000], 220, 22_050) > 0.3
    )

    eng.synthesize("slower please", voice="en_US-test-medium", speed=0.5)
    assert calls[1]["cmd"][calls[1]["cmd"].index("--length_scale") + 1] == "2.0000"
    # a path works as a voice, ``name:3`` selects a speaker, and clips are cached on disk
    eng.synthesize("another one", voice=f"{model}:3")
    assert calls[2]["cmd"][calls[2]["cmd"].index("--speaker") + 1] == "3"
    eng.synthesize("another one", voice=f"{model}:3")
    assert len(calls) == 3
    assert eng.voice_warning("no-such-voice") and eng.voice_warning("en_US-test-medium") is None


def test_piper_failure_raises_with_stderr(tmp_path: Path) -> None:
    (tmp_path / "v.onnx").write_bytes(b"\0")

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 3, stdout=b"", stderr=b"onnx exploded")

    eng = PiperTTS(binary="piper", voice_dirs=[tmp_path], runner=run, use_python=False)
    with pytest.raises(RuntimeError, match="onnx exploded"):
        eng._render("hello", None, 1.0)


def test_piper_is_unavailable_without_a_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PIPER_VOICES", raising=False)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert not PiperTTS().available()
    assert not PiperTTS(
        voice_dirs=[tmp_path / "nowhere"], binary="piper", runner=lambda *a, **k: None
    ).available()
    with pytest.raises(TTSUnavailableError, match="piper"):
        get_tts_engine("piper")
    monkeypatch.setenv("PIPER_VOICES", str(tmp_path / "pv"))
    (tmp_path / "pv").mkdir()
    (tmp_path / "pv" / "en_GB-x.onnx").write_bytes(b"\0")
    assert PiperTTS().list_voices() == ["en_GB-x"], "$PIPER_VOICES is searched"


def test_get_tts_engine_selection(
    tts_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # no piper voices anywhere
    monkeypatch.chdir(tmp_path)
    assert isinstance(get_tts_engine("babble"), BabbleTTS)
    auto = get_tts_engine()
    assert auto.available() and auto.name == ("say" if MacSayTTS().available() else "babble")
    assert get_tts_engine("auto").name == auto.name
    with pytest.raises(UnknownTTSEngineError, match="auto, piper, say, babble"):
        get_tts_engine("festival")
    if not MacSayTTS().available():
        with pytest.raises(TTSUnavailableError):
            get_tts_engine("say")


def test_tts_engine_interface_is_small() -> None:
    class Minimal(TTSEngine):
        name = "minimal"

        def available(self) -> bool:
            return True

        def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> TTSClip:
            return TTSClip(np.zeros(100, np.float32), 8000)

        def list_voices(self) -> list[str]:
            return []

    m = Minimal()
    assert (
        m.resolve_voice("x") == "x"
        and m.voice_warning("x") is None
        and m.speaker_voice("ana") is None
    )
    with pytest.raises(TypeError):
        TTSEngine()  # type: ignore[abstract]


# =============================================================================== alignment
def _burst(seconds: float, sr: int, rng: np.random.Generator, f: float = 180.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    x = (
        np.sin(2 * np.pi * f * t)
        + 0.4 * np.sin(2 * np.pi * 2 * f * t)
        + 0.15 * rng.standard_normal(t.size)
    )
    env = np.clip(np.minimum(t / 0.02, (seconds - t) / 0.03), 0, 1)
    return (0.4 * x * env).astype(np.float32)


def test_alignment_is_monotonic_and_covers_the_voiced_span() -> None:
    sr = 22_050
    rng = np.random.default_rng(0)

    def sil(seconds: float) -> np.ndarray:
        return np.zeros(int(seconds * sr), np.float32)

    clip = np.concatenate(
        [
            sil(0.3),
            _burst(0.4, sr, rng),
            sil(0.15),
            _burst(0.5, sr, rng),
            sil(0.1),
            _burst(0.3, sr, rng),
            sil(0.4),
        ]
    )
    words = estimate_word_timings("one two three", clip, sr)
    assert [w for _, _, w in words] == ["one", "two", "three"]
    span = voiced_span(clip, sr)
    assert (
        span is not None
        and span[0] == pytest.approx(0.3, abs=0.03)
        and span[1] == pytest.approx(1.75, abs=0.03)
    )
    assert words[0][0] == pytest.approx(span[0], abs=1e-6) and words[-1][1] == pytest.approx(
        span[1], abs=1e-6
    )
    prev = 0.0
    for a, b, _ in words:
        assert prev - 1e-9 <= a < b
        prev = b
    # the pauses are in the right places: boundaries snap to the silent gaps
    assert words[0][1] == pytest.approx(0.70, abs=0.03) and words[1][0] == pytest.approx(
        0.85, abs=0.03
    )
    assert words[1][1] == pytest.approx(1.35, abs=0.03) and words[2][0] == pytest.approx(
        1.45, abs=0.03
    )


def test_alignment_uses_syllables_and_punctuation_without_dips() -> None:
    sr = 22_050
    clip = np.concatenate(
        [
            np.zeros(int(0.2 * sr), np.float32),
            _burst(2.0, sr, np.random.default_rng(1)),
            np.zeros(int(0.2 * sr), np.float32),
        ]
    )
    words = estimate_word_timings("a wonderful, quick brown fox", clip, sr)
    assert len(words) == 5
    lengths = [b - a for a, b, _ in words]
    assert lengths[1] > 1.6 * lengths[0], "'wonderful' (3 syllables) gets more time than 'a'"
    gap = words[2][0] - words[1][1]
    assert gap > 0.03, "a pause follows the comma"
    assert words[0][0] == pytest.approx(0.2, abs=0.03) and words[-1][1] == pytest.approx(
        2.2, abs=0.03
    )
    prev = 0.0
    for a, b, _ in words:
        assert a >= prev - 1e-9 and b > a
        prev = b


def test_alignment_edge_cases() -> None:
    sr = 16_000
    assert estimate_word_timings("", np.ones(sr, np.float32), sr) == []
    silent = estimate_word_timings("hi there", np.zeros(sr, np.float32), sr)
    assert (
        silent[0][0] == 0.0 and silent[-1][1] == pytest.approx(1.0) and silent[0][1] <= silent[1][0]
    )
    solo = estimate_word_timings(
        "solo",
        np.concatenate(
            [np.zeros(sr // 2), _burst(1.0, sr, np.random.default_rng(2)), np.zeros(sr // 2)]
        ),
        sr,
    )
    assert (
        len(solo) == 1
        and solo[0][0] == pytest.approx(0.5, abs=0.03)
        and solo[0][1] == pytest.approx(1.5, abs=0.03)
    )
    assert estimate_word_timings("tiny clip", np.zeros(10, np.float32), sr)[-1][1] <= 10 / sr + 1e-9
    stereo = np.stack([_burst(1.0, sr, np.random.default_rng(3))] * 2, axis=1)
    assert len(estimate_word_timings("two words", stereo, sr)) == 2
    spread = spread_words(["a", "b", "c"], 1.0, 4.0)
    assert (
        spread[0][0] == 1.0
        and spread[-1][1] == pytest.approx(4.0)
        and all(s[0] < s[1] for s in spread)
    )
    assert [word_weight(w) for w in ("hello", "a", "2024", "—", "café")] == [
        2.0,
        1.0,
        4.0,
        0.0,
        1.0,
    ]


def test_alignment_agrees_with_the_babble_ground_truth(tts_cache: Path) -> None:
    text = "The quick brown fox jumps over the lazy dog, and keeps running through the forest."
    clip = BabbleTTS().synthesize(text)
    assert clip.words is not None
    est = estimate_word_timings(text, clip.samples, clip.sample_rate)
    assert len(est) == len(clip.words)
    err = [abs(a - ea) for (a, _, _), (ea, _, _) in zip(clip.words, est)] + [
        abs(b - eb) for (_, b, _), (_, eb, _) in zip(clip.words, est)
    ]
    assert float(np.mean(err)) < 0.04 and max(err) < 0.1


# =============================================================================== mix
def test_duck_envelope_dips_under_voice_and_recovers() -> None:
    voice = np.zeros(10 * SR, np.float32)
    voice[2 * SR : 4 * SR] = tone(200, 2.0, amp=0.3)
    env = duck_envelope(voice, SR, depth_db=-12, attack=0.08, release=0.45)
    floor = 10 ** (-12 / 20)
    assert env.shape == voice.shape and env.dtype == np.float32
    assert float(env.max()) == pytest.approx(1.0) and float(env.min()) == pytest.approx(
        floor, rel=1e-3
    )
    assert env[int(0.5 * SR)] == pytest.approx(1.0)
    assert env[int(2.0 * SR)] == pytest.approx(floor, rel=0.02), (
        "already fully ducked when the voice starts"
    )
    assert env[3 * SR] == pytest.approx(floor, rel=1e-3)
    assert floor < env[int(4.2 * SR)] < 1.0, "recovering during the release"
    assert env[int(4.6 * SR)] == pytest.approx(1.0, abs=1e-3), "recovered after attack+release"
    assert env[8 * SR] == 1.0


def test_duck_envelope_bridges_short_gaps_and_takes_masks() -> None:
    voice = np.zeros(6 * SR, np.float32)
    voice[1 * SR : 2 * SR] = tone(200, 1.0, amp=0.3)
    voice[int(2.2 * SR) : 3 * SR] = tone(200, 0.8, amp=0.3)
    env = duck_envelope(voice, SR)
    assert env[int(2.1 * SR)] == pytest.approx(10 ** (-12 / 20), rel=1e-3), (
        "no pumping across a 0.2 s gap"
    )
    mask = np.zeros(6 * SR, bool)
    mask[SR : 3 * SR] = True
    assert duck_envelope(mask, SR)[2 * SR] == pytest.approx(10 ** (-12 / 20), rel=1e-3)
    assert duck_envelope(mask.astype(np.float32), SR)[2 * SR] == pytest.approx(
        10 ** (-12 / 20), rel=1e-3
    )
    assert float(duck_envelope(np.zeros(SR, np.float32), SR).min()) == 1.0
    stereo = np.stack([voice, voice], axis=1)
    assert duck_envelope(stereo, SR).shape == (6 * SR,)
    assert duck_envelope(np.zeros(0, np.float32), SR).shape == (0,)
    assert float(duck_envelope(mask, SR, depth_db=-24).min()) == pytest.approx(
        10 ** (-24 / 20), rel=1e-3
    )


def test_resample_is_accurate_and_keeps_duration() -> None:
    sr_in = 22_050
    t = np.arange(2 * sr_in) / sr_in
    x = (0.5 * np.sin(2 * np.pi * 1000 * t) + 0.2 * np.sin(2 * np.pi * 5000 * t)).astype(np.float32)
    y = resample(x, sr_in, SR)
    assert y.shape[0] == 2 * SR
    ty = np.arange(y.shape[0]) / SR
    ref = 0.5 * np.sin(2 * np.pi * 1000 * ty) + 0.2 * np.sin(2 * np.pi * 5000 * ty)
    assert float(np.max(np.abs(y[2000:-2000] - ref[2000:-2000]))) < 5e-4
    # downsampling removes what the new rate cannot hold
    hi = (0.5 * np.sin(2 * np.pi * 30_000 * np.arange(2 * 96_000) / 96_000)).astype(np.float32)
    assert float(np.max(np.abs(resample(hi, 96_000, SR)[2000:-2000]))) < 1e-3
    stereo = np.stack([x, 0.5 * x], axis=1)
    assert resample(stereo, sr_in, SR).shape == (2 * SR, 2)
    assert resample(x, SR, SR) is not None and resample(
        np.zeros(0, np.float32), 8000, SR
    ).shape == (0,)


@needs_ffmpeg
def test_integrated_lufs_agrees_with_ffmpeg_ebur128(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    t = np.arange(8 * SR) / SR
    sig = np.stack(
        [
            0.2 * np.sin(2 * np.pi * 440 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t))
            + 0.05 * rng.standard_normal(t.size),
            0.15 * np.sin(2 * np.pi * 660 * t) + 0.03 * rng.standard_normal(t.size),
        ],
        axis=1,
    ).astype(np.float32)
    sig[3 * SR : 5 * SR] *= 0.05
    path = tmp_path / "l.wav"
    write_wav(path, sig, SR)
    out = subprocess.run(
        [
            "ffmpeg",
            "-nostats",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "ebur128",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    ref = float(re.findall(r"I:\s+(-?\d+\.\d+) LUFS", out)[-1])
    assert integrated_lufs(sig, SR) == pytest.approx(ref, abs=0.3)
    assert integrated_lufs(np.zeros((SR, 2), np.float32), SR) < -90


def test_limiter_never_exceeds_the_ceiling_and_is_transparent_below() -> None:
    t = np.arange(5 * SR) / SR
    x = np.stack(
        [0.9 * np.sin(2 * np.pi * 220 * t), 0.8 * np.sin(2 * np.pi * 330 * t)], axis=1
    ).astype(np.float32)
    x[2 * SR : 2 * SR + 100] *= 3.0
    y, reduction = limit(x, SR, ceiling_db=-1.0)
    assert float(np.max(np.abs(y))) <= 10 ** (-1 / 20) + 1e-6 and reduction > 3
    assert np.all(np.isfinite(y)) and y.dtype == np.float32
    quiet, r0 = limit(x * 0.1, SR)
    assert r0 == 0.0 and np.array_equal(quiet, x * 0.1)
    # the gain is smooth (no zipper noise): it never jumps between neighbouring samples
    loud = np.abs(x[:, 0]) > 0.05
    gain = np.where(loud, y[:, 0] / np.where(x[:, 0] == 0, 1.0, x[:, 0]), np.nan)
    assert float(np.nanmax(np.abs(np.diff(gain)))) < 0.01


def _wav_info(path: Path) -> tuple[int, int, int, float]:
    with wave.open(str(path)) as w:
        return (
            w.getnchannels(),
            w.getsampwidth(),
            w.getframerate(),
            w.getnframes() / w.getframerate(),
        )


def test_mix_writes_a_valid_stereo_wav_with_the_right_duration(tmp_path: Path) -> None:
    voice = tone(220, 3.0, 22_050, 0.3) * np.hanning(3 * 22_050).astype(np.float32)
    music = np.stack([tone(110, 5.0, amp=0.5), tone(110, 5.0, amp=0.5)], axis=1)
    click = (
        np.exp(-np.arange(2400) / 200) * np.sin(2 * np.pi * 1500 * np.arange(2400) / SR)
    ).astype(np.float32)
    plan = MixPlan(
        total_duration=12.0,
        clips=[
            MixClip(0.0, music, SR, -16, "music"),
            MixClip(2.0, voice, 22_050, 0.0, "voice"),
            MixClip(1.0, click, SR, -8, "sfx", name="click"),
        ],
        seed=5,
    )
    rep = mix_tracks(plan, tmp_path / "out" / "mix.wav")
    assert _wav_info(tmp_path / "out" / "mix.wav") == (2, 2, 48_000, 12.0)
    assert rep.duration == pytest.approx(12.0) and rep.n_clips == 3 and rep.sr == SR
    assert 0.2 < rep.peak <= 0.95
    assert rep.approx_lufs == pytest.approx(-16.0, abs=0.5)
    assert rep.ducked_seconds > 1.0
    data, sr = read_wav(tmp_path / "out" / "mix.wav")
    assert (
        sr == SR
        and data.shape == (12 * SR, 2)
        and float(np.max(np.abs(data))) == pytest.approx(rep.peak, abs=1e-4)
    )
    assert integrated_lufs(data, sr) == pytest.approx(rep.approx_lufs, abs=0.05)


def test_music_is_ducked_under_the_voice(tmp_path: Path) -> None:
    music = np.stack([tone(200, 10.0, amp=0.4)] * 2, axis=1)
    voice = tone(1000, 3.0, amp=0.4)
    clips = [MixClip(0.0, music, SR, 0.0, "music"), MixClip(3.0, voice, SR, 0.0, "voice")]
    ducked = mix_tracks(MixPlan(10.0, list(clips), ducking=True), tmp_path / "d.wav")
    plain = mix_tracks(MixPlan(10.0, list(clips), ducking=False), tmp_path / "p.wav")
    assert ducked.ducked_seconds > 3.0 and plain.ducked_seconds == 0.0
    for name, expect_drop in (("d", True), ("p", False)):
        data, _ = read_wav(tmp_path / f"{name}.wav")
        mono = data.mean(axis=1)
        during = single_bin(
            mono[int(3.8 * SR) : int(5.8 * SR)], 200
        )  # music while the voice speaks
        outside = single_bin(mono[int(7.5 * SR) : int(9.0 * SR)], 200)  # music alone
        if expect_drop:
            assert during < 0.35 * outside, "music must drop ~12 dB under the voice"
        else:
            assert during == pytest.approx(outside, rel=0.1)


def test_mix_loops_trims_and_fades_music(tmp_path: Path) -> None:
    bed = np.stack([tone(200, 2.0, amp=0.5)] * 2, axis=1)
    mix_tracks(
        MixPlan(7.0, [MixClip(0.0, bed, SR, 0.0, "music")], ducking=False), tmp_path / "loop.wav"
    )
    data, _ = read_wav(tmp_path / "loop.wav")
    assert data.shape[0] == 7 * SR
    mono = data.mean(axis=1)
    assert float(np.max(np.abs(mono[: SR // 100]))) < 0.02, "faded in"
    assert float(np.max(np.abs(mono[-SR // 100 :]))) < 0.02, "faded out"
    steady = single_bin(mono[int(3.6 * SR) : int(4.4 * SR)], 200)  # between two loop crossfades
    assert steady > 0.1 and single_bin(mono[int(5.2 * SR) : int(5.9 * SR)], 200) == pytest.approx(
        steady, rel=0.1
    )
    slope = np.abs(
        np.diff(mono[int(1.6 * SR) : -int(1.6 * SR)])
    )  # a click at a loop point would show up here
    assert float(slope.max()) < 0.02, "loops are crossfaded, not cut"
    long_bed = np.stack([tone(200, 20.0, amp=0.5)] * 2, axis=1)
    rep = mix_tracks(
        MixPlan(5.0, [MixClip(0.0, long_bed, SR, 0.0, "music")]), tmp_path / "trim.wav"
    )
    assert rep.duration == 5.0 and _wav_info(tmp_path / "trim.wav")[3] == 5.0


def test_mix_places_resamples_and_pans_sfx(tmp_path: Path) -> None:
    click = np.zeros(2205, np.float32)
    click[0] = 0.8
    clips = [
        MixClip(1.0, click, 22_050, 0.0, "sfx", name="a"),
        MixClip(2.0, click, 22_050, 0.0, "sfx", name="b", pan=-1.0),
    ]
    mix_tracks(MixPlan(3.0, clips, target_lufs=None, ducking=False, seed=1), tmp_path / "c.wav")
    data, _ = read_wav(tmp_path / "c.wav")
    first = int(np.argmax(np.abs(data[:, 0]) + np.abs(data[:, 1])))
    assert abs(first - SR) < 120, "placed at 1.0 s even though the clip is 22.05 kHz"
    seg = data[SR - 200 : SR + 200]
    left, right = float(np.abs(seg[:, 0]).max()), float(np.abs(seg[:, 1]).max())
    assert left != right and 0.5 < left / right < 2.0, "auto pan is slight"
    hard = data[2 * SR - 200 : 2 * SR + 200]
    assert float(np.abs(hard[:, 1]).max()) < 0.02 * float(np.abs(hard[:, 0]).max())


def test_mix_window_crop_empty_plan_and_bad_kind(tmp_path: Path) -> None:
    music = np.stack([tone(200, 10.0, amp=0.5)] * 2, axis=1)
    rep = mix_tracks(
        MixPlan(10.0, [MixClip(0.0, music, SR, 0.0, "music")], window=(2.0, 5.0)),
        tmp_path / "w.wav",
    )
    assert rep.duration == pytest.approx(3.0) and _wav_info(tmp_path / "w.wav")[3] == pytest.approx(
        3.0
    )
    empty = mix_tracks(MixPlan(2.0, []), tmp_path / "e.wav")
    assert empty.peak == 0.0 and empty.approx_lufs < -90 and empty.n_clips == 0
    banjo: Any = "banjo"
    with pytest.raises(ValueError, match="clip kind"):
        mix_tracks(MixPlan(1.0, [MixClip(0.0, tone(100, 0.5), SR, 0.0, banjo)]), tmp_path / "x.wav")


def test_mix_limits_a_hot_transient_instead_of_clipping(tmp_path: Path) -> None:
    n = np.arange(4800)
    click = (np.exp(-n / 600) * np.sin(2 * np.pi * 1000 * n / SR)).astype(np.float32)
    plan = MixPlan(3.0, [MixClip(1.0, click, SR, 0.0, "sfx", pan=0.0)], ducking=False)
    rep = mix_tracks(plan, tmp_path / "hot.wav")
    assert rep.limiter_db > 1.0, "normalising a lone click to -16 LUFS needs the limiter"
    assert rep.peak <= 10 ** (-1 / 20) + 1e-3, "and the result stays under the -1 dBFS ceiling"
    assert rep.peak > 0.5, "without being squashed flat"


def test_mix_of_sixty_seconds_is_fast(tmp_path: Path) -> None:
    bed = np.stack([tone(110, 60.0, amp=0.4)] * 2, axis=1)
    voice = tone(220, 3.0, 22_050, 0.3)
    click = tone(1500, 0.1, amp=0.5)
    clips = (
        [MixClip(0.0, bed, SR, -16, "music")]
        + [MixClip(i * 2.0, voice, 22_050, 0.0, "voice") for i in range(20)]
        + [MixClip(i * 0.9, click, SR, -8, "sfx", name=str(i)) for i in range(60)]
    )
    t0 = time.perf_counter()
    rep = mix_tracks(MixPlan(60.0, clips), tmp_path / "big.wav")
    assert time.perf_counter() - t0 < 5.0 and rep.n_clips == 81


# =============================================================================== pipeline
def make_spec(
    audio: dict[str, Any] | None = None, scenes: list[dict[str, Any]] | None = None, **meta: Any
) -> ReelSpec:
    default_scenes = []
    for i in range(3):
        default_scenes.append(
            {
                "id": f"s{i}",
                "duration_sec": 5.0,
                "background": {"template": "room"},
                "captions": [
                    {
                        "text": "Hello there, my friend.",
                        "t0": 0.3,
                        "t1": 1.2,
                        "style": "subtitle",
                        "speaker": "ana",
                    },
                    {
                        "text": "Nice to meet you!",
                        "t0": 3.0,
                        "t1": 3.6,
                        "style": "subtitle",
                        "speaker": "ben",
                    },
                    {"text": "CHAPTER ONE", "t0": 0.0, "t1": 1.0, "style": "title"},
                ],
                "sfx": [{"name": "pop", "t": 0.2}, {"name": "whoosh", "t": 4.5, "volume": 0.7}],
                "transition_out": {"type": "crossfade", "duration": 0.5}
                if i < 2
                else {"type": "cut", "duration": 0},
            }
        )
    return ReelSpec.model_validate(
        {
            "meta": {"title": "audio test", "style": "paper_cutout", "seed": 3, **meta},
            "characters": [
                {"id": "ana", "archetype": "kid", "name": "Ana", "voice": "high"},
                {"id": "ben", "archetype": "everyman"},
            ],
            "scenes": scenes if scenes is not None else default_scenes,
            "audio": {"music": "procedural:calm", "voiceover": "tts", **(audio or {})},
        }
    )


def test_prepare_audio_builds_wav_retimes_captions_and_returns_word_timings(
    tts_cache: Path, tmp_path: Path
) -> None:
    spec = make_spec()
    snapshot = spec.model_dump_json()
    plan = prepare_audio(spec, work_dir=tmp_path / "work", engine="babble")
    assert isinstance(plan, AudioPlan)
    assert spec.model_dump_json() == snapshot, "the input spec is never mutated"
    assert plan.spec is not spec

    # a valid 48 kHz stereo wav as long as the reel (3 x 5 s - 2 x 0.5 s of crossfade)
    assert plan.wav == tmp_path / "work" / "audio.wav"
    assert _wav_info(plan.wav) == (2, 2, 48_000, pytest.approx(14.0, abs=0.04))
    assert (
        plan.report is not None
        and plan.report.approx_lufs == pytest.approx(-16.0, abs=0.5)
        and plan.report.peak <= 0.95
    )
    assert plan.report.n_clips == 3 * 2 + 1 + 3 * 2, "6 voice clips, 1 music bed, 6 sfx"

    # word timings: scene index -> caption index -> [(t0, t1, word)] in scene-local seconds
    assert set(plan.word_timings) == {0, 1, 2}
    for si in range(3):
        assert set(plan.word_timings[si]) == {0, 1}, (
            "only the spoken captions (subtitles), not the title"
        )
        cap0 = spec.scenes[si].captions[0]
        words = plan.word_timings[si][0]
        assert [w for _, _, w in words] == ["Hello", "there,", "my", "friend."]
        assert words[0][0] >= cap0.t0 and words[0][0] < cap0.t0 + 0.2
        assert all(a < b for a, b, _ in words) and all(
            words[i][1] <= words[i + 1][0] for i in range(3)
        )

    # captions are retimed so the text stays up for the speech (+0.25 s), inside the scene
    for si in range(3):
        old, new = spec.scenes[si].captions[0], plan.spec.scenes[si].captions[0]
        last_word_end = plan.word_timings[si][0][-1][1]
        assert new.t0 == old.t0 and new.t1 > old.t1 and new.t1 <= 5.0
        assert last_word_end < new.t1 <= last_word_end + 0.5
        title_old, title_new = spec.scenes[si].captions[2], plan.spec.scenes[si].captions[2]
        assert title_new.t1 == title_old.t1, "unspoken captions are untouched"


def test_caption_retiming_keeps_a_longer_original_window_and_caps_at_the_scene(
    tts_cache: Path, tmp_path: Path
) -> None:
    scene = {
        "id": "a",
        "duration_sec": 4.0,
        "background": {"template": "room"},
        "captions": [
            {"text": "Hi.", "t0": 0.2, "t1": 3.5, "style": "subtitle"},  # roomy: keep 3.5
            {
                "text": "This sentence is much too long for the little time it was given.",
                "t0": 3.0,
                "t1": 3.4,
                "style": "subtitle",
            },  # capped at 4.0
            {"text": "Never spoken", "t0": 0.0, "t1": 1.0, "style": "subtitle", "speak": False},
            {"text": "Spoken title", "t0": 1.0, "t1": 1.2, "style": "title", "speak": True},
        ],
    }
    plan = prepare_audio(
        make_spec(scenes=[scene], audio={"music": None}), work_dir=tmp_path / "w", engine="babble"
    )
    caps = plan.spec.scenes[0].captions
    assert caps[0].t1 == 3.5
    assert caps[1].t1 == 4.0
    assert caps[2].t1 == 1.0, "speak=false leaves the caption alone"
    assert set(plan.word_timings[0]) == {0, 1, 3}, (
        "speak=false is skipped, speak=true on a title is spoken"
    )
    assert caps[3].t1 > 1.2
    assert any("runs" in w and "past the end" in w for w in plan.warnings), plan.warnings


def test_prepare_audio_warns_about_overlapping_voices(tts_cache: Path, tmp_path: Path) -> None:
    scene = {
        "id": "a",
        "duration_sec": 6.0,
        "background": {"template": "room"},
        "captions": [
            {
                "text": "One two three four five six seven eight.",
                "t0": 0.0,
                "t1": 2.0,
                "style": "subtitle",
            },
            {"text": "Right on top of the first line.", "t0": 1.0, "t1": 3.0, "style": "subtitle"},
        ],
    }
    plan = prepare_audio(
        make_spec(scenes=[scene], audio={"music": None}), work_dir=tmp_path / "w", engine="babble"
    )
    assert any("voice overlaps" in w and "captions[0]" in w for w in plan.warnings), plan.warnings
    assert plan.wav is not None, "overlaps are warnings, not failures"


def test_prepare_audio_returns_no_wav_when_there_is_no_audio(tmp_path: Path) -> None:
    scene = {
        "id": "a",
        "duration_sec": 3.0,
        "background": {"template": "room"},
        "captions": [{"text": "Silent film", "t0": 0.0, "t1": 2.0}],
    }
    spec = make_spec(scenes=[scene], audio={"music": None, "voiceover": "none"})
    work = tmp_path / "never_created"
    plan = prepare_audio(spec, work_dir=work)
    assert plan.wav is None and plan.report is None and plan.word_timings == {}
    assert not work.exists(), "no work is done (and nothing written) for a silent reel"
    assert plan.spec is not spec and plan.spec == spec
    # events are ignored unless auto_sfx is on
    assert prepare_audio(spec, work_dir=work, action_events=[(1.0, "footstep")]).wav is None
    # tts requested but nothing is spoken and nothing else to play -> still nothing
    quiet = make_spec(
        scenes=[scene | {"captions": [{"text": "x", "t0": 0, "t1": 1, "style": "title"}]}],
        audio={"music": None},
    )
    assert prepare_audio(quiet, work_dir=work, engine="babble").wav is None


def test_prepare_audio_only_sfx_and_only_music(tts_cache: Path, tmp_path: Path) -> None:
    only_sfx = make_spec(audio={"music": None, "voiceover": "none"})
    plan = prepare_audio(only_sfx, work_dir=tmp_path / "sfx")
    assert (
        plan.wav is not None
        and plan.report is not None
        and plan.report.n_clips == 6
        and plan.word_timings == {}
    )
    only_music = make_spec(
        audio={"voiceover": "none"},
        scenes=[{"id": "a", "duration_sec": 4.0, "background": {"template": "room"}}],
    )
    plan = prepare_audio(only_music, work_dir=tmp_path / "music")
    assert (
        plan.report is not None
        and plan.report.n_clips == 1
        and plan.report.duration == pytest.approx(4.0, abs=0.04)
    )


def test_sfx_volume_scales_the_effect(tts_cache: Path, tmp_path: Path) -> None:
    scenes = [
        {
            "id": "a",
            "duration_sec": 6.0,
            "background": {"template": "room"},
            "sfx": [
                {"name": "ding", "t": 0.5, "volume": 1.0},
                {"name": "ding", "t": 3.5, "volume": 0.25},
            ],
        }
    ]
    spec = make_spec(scenes=scenes, audio={"music": None, "voiceover": "none"})
    plan = prepare_audio(spec, work_dir=tmp_path / "w")
    assert plan.wav is not None
    data, _ = read_wav(plan.wav)
    loud = float(np.abs(data[int(0.5 * SR) : int(1.5 * SR)]).max())
    soft = float(np.abs(data[int(3.5 * SR) : int(4.5 * SR)]).max())
    # volume 0.25 is -12 dB; the slight random pan of each cue moves it by about +/-2 dB
    assert soft == pytest.approx(0.25 * loud, rel=0.4)


def test_prepare_audio_auto_sfx_events(tts_cache: Path, tmp_path: Path) -> None:
    scenes = [{"id": "a", "duration_sec": 6.0, "background": {"template": "room"}}]
    events = [
        (0.5, "footstep"),
        (0.9, "footstep"),
        (0.9, "footstep"),
        (2.0, "jump"),
        (2.5, "land"),
        (3.0, "wiggle"),
        (3.5, "pickup"),
    ]
    off = prepare_audio(
        make_spec(scenes=scenes, audio={"music": None, "voiceover": "none", "auto_sfx": False}),
        work_dir=tmp_path / "off",
        action_events=events,
    )
    assert off.wav is None
    on = prepare_audio(
        make_spec(scenes=scenes, audio={"music": None, "voiceover": "none", "auto_sfx": True}),
        work_dir=tmp_path / "on",
        action_events=events,
    )
    assert on.report is not None and on.report.n_clips == 5, (
        "unknown events and same-beat duplicates are dropped"
    )
    assert set(AUTO_EVENT_SFX) == set(ACTION_EVENT_SFX) and all(
        name in CATALOG.sfx for name, _ in AUTO_EVENT_SFX.values()
    )


def test_prepare_audio_warns_instead_of_failing(tts_cache: Path, tmp_path: Path) -> None:
    scenes = [
        {
            "id": "a",
            "duration_sec": 3.0,
            "background": {"template": "room"},
            "sfx": [
                {"name": "whoosh_typo", "t": 0.5},
                {"name": "pop", "t": 9.0},
                {"name": "pop", "t": 1.0},
            ],
            "captions": [{"text": "Hi there", "t0": 0.2, "t1": 1.0}],
        }
    ]
    plan = prepare_audio(
        make_spec(scenes=scenes, audio={"music": "does/not/exist.mp3", "voiceover": "file"}),
        work_dir=tmp_path / "w",
        base_dir=tmp_path,
    )
    text = "\n".join(plan.warnings)
    assert "unknown sfx 'whoosh_typo'" in text and "whoosh" in text, "with a did-you-mean hint"
    assert "after scene" in text and "music skipped" in text and "voiceover_file is not set" in text
    assert plan.wav is not None and plan.report is not None and plan.report.n_clips == 1, (
        "the one good sfx still plays"
    )
    bad_mood = prepare_audio(
        make_spec(scenes=scenes, audio={"music": "procedural:polka", "voiceover": "none"}),
        work_dir=tmp_path / "m",
    )
    assert (
        any("polka" in w for w in bad_mood.warnings)
        and bad_mood.report is not None
        and bad_mood.report.n_clips == 2
    )
    off = prepare_audio(
        make_spec(scenes=scenes, audio={"music": None, "voiceover": "none"}),
        work_dir=tmp_path / "x",
    )
    assert not any("speak=true" in w for w in off.warnings)


def test_prepare_audio_unknown_engine_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(UnknownTTSEngineError, match="babble"):
        prepare_audio(make_spec(), work_dir=tmp_path / "w", engine="festival")
    # ... but an engine is only needed when something is actually spoken
    silent = make_spec(
        scenes=[{"id": "a", "duration_sec": 3.0, "background": {"template": "room"}}]
    )
    assert prepare_audio(silent, work_dir=tmp_path / "w2", engine="festival").wav is not None


class _Recorder(TTSEngine):
    """A third-party engine implementing only the four abstract members."""

    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = fail

    def available(self) -> bool:
        return True

    def list_voices(self) -> list[str]:
        return ["v1"]

    def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> TTSClip:
        self.calls.append((text, voice))
        if self.fail:
            raise RuntimeError("engine on fire")
        sr = 16_000
        n = int(0.15 * sr * len(text.split()))
        t = np.arange(n) / sr
        return TTSClip(
            (
                0.3
                * np.sin(2 * np.pi * 200 * t)
                * (np.sin(np.pi * 3 * len(text.split()) * t / (n / sr)) ** 2)
            ).astype(np.float32),
            sr,
        )


def test_voices_come_from_character_then_spec_default_then_engine(
    tts_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder()
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: rec)
    spec = make_spec(audio={"music": None, "tts_voice": "narrator"})
    plan = prepare_audio(spec, work_dir=tmp_path / "w")
    assert plan.wav is not None
    voices = dict(rec.calls)
    assert voices["Hello there, my friend."] == "high", "character(speaker).voice wins"
    assert voices["Nice to meet you!"] == "narrator", "falls back to audio.tts_voice"
    rec2 = _Recorder()
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: rec2)
    prepare_audio(make_spec(audio={"music": None}), work_dir=tmp_path / "w2")
    assert {voice for _, voice in rec2.calls} == {"high", None}, (
        "engine default when nothing is configured"
    )
    # no word timings from the engine -> they are estimated from the audio
    plan2 = prepare_audio(make_spec(audio={"music": None}), work_dir=tmp_path / "w3")
    words = plan2.word_timings[0][0]
    assert len(words) == 4 and words[0][0] >= 0.3 and words[-1][1] > words[0][1]


def test_a_failing_engine_falls_back_to_babble_with_a_warning(
    tts_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder(fail=True)
    monkeypatch.setattr("reel.audio.pipeline.get_tts_engine", lambda name=None: rec)
    plan = prepare_audio(make_spec(audio={"music": None}), work_dir=tmp_path / "w")
    assert plan.wav is not None and any(
        "engine on fire" in w and "babble" in w for w in plan.warnings
    )
    assert plan.word_timings[0][0], "timings still produced"


def test_voiceover_file_mode_places_the_recording_and_finds_word_timings(
    tts_cache: Path, tmp_path: Path
) -> None:
    babble = BabbleTTS()
    line1, line2 = (
        babble.synthesize("Hello there, my friend."),
        babble.synthesize("Nice to meet you!"),
    )
    sr = line1.sample_rate
    track = np.zeros(int(8 * sr), np.float32)
    track[int(0.5 * sr) : int(0.5 * sr) + line1.samples.shape[0]] += line1.samples
    track[int(4.0 * sr) : int(4.0 * sr) + line2.samples.shape[0]] += line2.samples
    write_wav(tmp_path / "vo.wav", track, sr)
    scene = {
        "id": "a",
        "duration_sec": 8.0,
        "background": {"template": "room"},
        "captions": [
            {"text": "Hello there, my friend.", "t0": 0.4, "t1": 3.0, "style": "subtitle"},
            {"text": "Nice to meet you!", "t0": 3.9, "t1": 6.0, "style": "subtitle"},
            {"text": "No speech in this window", "t0": 6.5, "t1": 7.5, "style": "subtitle"},
        ],
    }
    spec = make_spec(
        scenes=[scene], audio={"music": None, "voiceover": "file", "voiceover_file": "vo.wav"}
    )
    plan = prepare_audio(spec, work_dir=tmp_path / "w", base_dir=tmp_path)
    assert plan.wav is not None and plan.report is not None and plan.report.n_clips == 1
    assert plan.report.duration == pytest.approx(8.0, abs=0.04)
    assert plan.spec.scenes[0].captions[0].t1 == 3.0, (
        "a recorded voice-over defines the timing; captions are not retimed"
    )
    w0, w1, w2 = (plan.word_timings[0][i] for i in range(3))
    assert [w for _, _, w in w0] == ["Hello", "there,", "my", "friend."]
    assert w0[0][0] == pytest.approx(0.5 + line1.words[0][0], abs=0.08), (
        "found inside the recording, not just spread"
    )  # type: ignore[index]
    assert w1[0][0] == pytest.approx(4.0 + line2.words[0][0], abs=0.08)  # type: ignore[index]
    assert w2[0][0] >= 6.5 and w2[-1][1] <= 7.5 + 1e-6, (
        "silent window: spread evenly over the caption"
    )


def test_music_file_is_decoded_levelled_and_looped(tts_cache: Path, tmp_path: Path) -> None:
    write_wav(tmp_path / "bed.wav", np.stack([tone(220, 2.0, amp=0.2)] * 2, axis=1), SR)
    spec = make_spec(
        audio={"music": "bed.wav", "voiceover": "none"},
        scenes=[{"id": "a", "duration_sec": 5.0, "background": {"template": "room"}}],
    )
    plan = prepare_audio(spec, work_dir=tmp_path / "w", base_dir=tmp_path)
    assert plan.wav is not None and plan.report is not None and not plan.warnings
    data, _ = read_wav(plan.wav)
    assert data.shape[0] == 5 * SR and single_bin(data.mean(axis=1)[3 * SR : 4 * SR], 220) > 0.05, (
        "2 s file looped to 5 s"
    )


def test_frame_range_trims_the_mix(tts_cache: Path, tmp_path: Path) -> None:
    spec = make_spec()
    full = prepare_audio(spec, work_dir=tmp_path / "full", engine="babble")
    part = prepare_audio(
        spec, work_dir=tmp_path / "part", engine="babble", frame_range_sec=(2.0, 6.5)
    )
    assert part.report is not None and full.report is not None
    assert part.report.duration == pytest.approx(4.5, abs=0.001) and _wav_info(part.wav)[
        3
    ] == pytest.approx(4.5, abs=0.001)  # type: ignore[arg-type]
    full_data, _ = read_wav(full.wav)  # type: ignore[arg-type]
    part_data, _ = read_wav(part.wav)  # type: ignore[arg-type]
    mid = slice(int(0.5 * SR), int(4.0 * SR))
    ref = full_data[int(2.0 * SR) : int(6.5 * SR)][mid]
    assert float(np.max(np.abs(part_data[mid] - ref))) < 2e-3, (
        "same samples as the full mix, just cut out"
    )
    assert any(
        "empty" in w
        for w in prepare_audio(
            spec, work_dir=tmp_path / "bad", engine="babble", frame_range_sec=(5.0, 5.0)
        ).warnings
    )


def test_prepare_audio_is_deterministic(tts_cache: Path, tmp_path: Path) -> None:
    a = prepare_audio(make_spec(), work_dir=tmp_path / "a", engine="babble")
    b = prepare_audio(make_spec(), work_dir=tmp_path / "b", engine="babble")
    assert a.wav is not None and b.wav is not None and a.wav.read_bytes() == b.wav.read_bytes()
    assert a.word_timings == b.word_timings
    other = prepare_audio(make_spec(seed=99), work_dir=tmp_path / "c", engine="babble")
    assert other.wav is not None and other.wav.read_bytes() != a.wav.read_bytes(), (
        "meta.seed changes the music"
    )


def test_default_music_mood_follows_the_style(tts_cache: Path, tmp_path: Path) -> None:
    scenes = [{"id": "a", "duration_sec": 6.0, "background": {"template": "room"}}]
    base = {"voiceover": "none", "music": "procedural"}
    playful = prepare_audio(
        make_spec(scenes=scenes, audio=base), work_dir=tmp_path / "p"
    )  # paper_cutout -> playful
    explicit = prepare_audio(
        make_spec(scenes=scenes, audio=base | {"music": "procedural:playful"}),
        work_dir=tmp_path / "e",
    )
    other = prepare_audio(
        make_spec(scenes=scenes, audio=base | {"music": "procedural:epic"}), work_dir=tmp_path / "o"
    )
    assert playful.wav and explicit.wav and other.wav
    assert playful.wav.read_bytes() == explicit.wav.read_bytes() != other.wav.read_bytes()


# =============================================================================== package
def test_public_api_is_exported_lazily() -> None:
    expected = {
        "prepare_audio",
        "AudioPlan",
        "mix_tracks",
        "MixPlan",
        "MixClip",
        "MixReport",
        "duck_envelope",
        "estimate_word_timings",
        "get_tts_engine",
        "TTSEngine",
        "TTSClip",
        "PiperTTS",
        "MacSayTTS",
        "BabbleTTS",
        "generate_music",
        "MUSIC_MOODS",
        "parse_music_spec",
        "register_sfx",
        "SfxDef",
        "synth",
    }
    assert expected <= set(audio_pkg.__all__) and expected <= set(dir(audio_pkg))
    assert audio_pkg.prepare_audio is prepare_audio and audio_pkg.synth is sfx_mod.synth
    assert audio_pkg.MUSIC_MOODS == MUSIC_MOODS
    with pytest.raises(AttributeError):
        _ = audio_pkg.not_a_thing


# ------------------------------------------------------------------ the voice follows the script of the text
def test_text_language_comes_from_the_dominant_script() -> None:
    from reel.audio.tts import text_language

    assert text_language("नमस्ते दुनिया") == "hi" and text_language("Hello नमस्ते दुनिया सब") == "hi"
    assert (
        text_language("今日は雨です") == "ja"
    )  # kana settles it, although kanji alone would be Chinese
    assert text_language("今天下雨") == "zh" and text_language("안녕하세요") == "ko"
    assert text_language("مرحبا") == "ar" and text_language("Привет") == "ru"
    assert text_language("Hello, señor!") is None and text_language("50% off") is None


def test_say_switches_to_a_voice_for_the_texts_language_when_there_is_one() -> None:
    listing = (
        "Samantha  en_US    # Hello\nLekha (Hindi (India))  hi_IN    # Namaste\n"
        "Kyoko  ja_JP    # Konnichiwa\n"
    )

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    eng = MacSayTTS(say_binary="/usr/bin/say", runner=runner)
    assert eng.voice_language("Samantha") == "en" and eng.voice_language("Lekha") == "hi"
    # a character's own voice cannot read it: the Hindi voice does, at a pitch that stays theirs
    assert eng.voice_for_text("नमस्ते", "Samantha") == "Lekha (Hindi (India))|pbas=50"
    assert eng.voice_for_text("今日は", "Samantha") == "Kyoko|pbas=50"
    assert eng.voice_for_text("नमस्ते", None) == "Lekha (Hindi (India))"  # narration stays neutral
    assert eng.voice_for_text("Hello", "Samantha") == "Samantha"  # Latin text keeps its voice
    assert eng.voice_for_text("नमस्ते", "Lekha") == "Lekha"  # already a Hindi voice
    assert (
        eng.voice_for_text("مرحبا", "Samantha") == "Samantha"
    )  # no Arabic voice installed: unchanged


def test_characters_sharing_the_only_voice_of_a_language_keep_a_pitch_of_their_own(
    tmp_path: Path,
) -> None:
    """A Hindi story with one Hindi voice installed: Mia, Pip and Bolt must not all sound alike."""
    listing = (
        "Samantha  en_US    # Hello\nJunior  en_US    # Hi\nGrandpa (English (US))  en_US    # Hi\n"
        "Fred  en_US    # Hi\nLekha (Hindi (India))  hi_IN    # Namaste\n"
    )
    spoken: list[tuple[list[str], str]] = []

    def runner(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if "-f" in cmd and cmd[1:3] != ["-v", "?"]:
            spoken.append((cmd, Path(cmd[cmd.index("-f") + 1]).read_text(encoding="utf-8")))
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"")
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="stop here"
            )  # no audio needed
        return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

    eng = MacSayTTS(say_binary="/usr/bin/say", runner=runner)
    cast = [
        CharacterSpec.model_validate({"id": "mia", "archetype": "kid"}),
        CharacterSpec.model_validate({"id": "pip", "archetype": "elder"}),
        CharacterSpec.model_validate({"id": "bolt", "archetype": "robot"}),
    ]
    kid, elder, robot = (eng.voice_for_text("मेरा छाता", eng.speaker_voice(c.id, c)) for c in cast)
    assert kid and elder and robot
    # three speakers, one Hindi voice, three different pitches (the voice each would have had decides)
    pitches = {eng.split_pitch(v)[1] for v in (kid, elder, robot)}
    assert all(eng.split_pitch(v)[0] == "Lekha (Hindi (India))" for v in (kid, elder, robot))
    assert len(pitches) == 3 and all(p is not None and 0 <= p <= 127 for p in pitches)
    assert (
        eng.split_pitch(kid)[1] > eng.split_pitch(elder)[1] > eng.split_pitch(robot)[1]
    )  # child high, robot low

    # the same character always gets the same pitch, and the pitch is part of the speech-cache identity
    again = eng.voice_for_text("कुछ और", eng.speaker_voice("mia", cast[0]))
    assert eng.split_pitch(again)[1] == eng.split_pitch(kid)[1]
    assert eng.resolve_voice("Junior|pbas=64") == "Junior|pbas=64"
    assert eng.resolve_voice("Nobody|pbas=40") == "Samantha|pbas=40"
    assert eng.voice_language("Lekha (Hindi (India))|pbas=40") == "hi"
    assert eng.voice_warning("Lekha (Hindi (India))|pbas=40") is None
    assert eng.voice_warning("Nobody|pbas=40") and "Nobody" in str(
        eng.voice_warning("Nobody|pbas=40")
    )

    # and say is told: the pitch is an embedded command in front of the line, the voice name is plain
    with pytest.raises(RuntimeError):
        eng._render("मेरा छाता", "Lekha (Hindi (India))|pbas=64", 1.0)
    cmd, text = spoken[-1]
    assert cmd[cmd.index("-v") + 1] == "Lekha (Hindi (India))"
    assert text == "[[pbas 64]] मेरा छाता"
    with pytest.raises(RuntimeError):
        eng._render("मेरा छाता", "Lekha (Hindi (India))", 1.0)
    assert spoken[-1][1] == "मेरा छाता"  # no pitch asked for: the line is untouched


def test_the_pipeline_warns_when_no_voice_can_read_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class EnglishOnly(MacSayTTS):
        def __init__(self) -> None:
            listing = "Samantha  en_US    # Hello\n"
            super().__init__(
                say_binary="/usr/bin/say",
                runner=lambda cmd, **_: subprocess.CompletedProcess(
                    cmd, 0, stdout=listing, stderr=""
                ),
            )

        def _render(
            self, text: str, voice: str | None, speed: float
        ) -> Any:  # no real speech needed
            return BabbleTTS()._render(text, None, speed)

    eng = EnglishOnly()
    assert eng.voice_for_text("नमस्ते", "Samantha") == "Samantha"
    scene = {"id": "a", "duration_sec": 5.0, "background": {"template": "abstract"}}
    scene["captions"] = [{"text": "नमस्ते दुनिया", "t0": 0.3, "t1": 4.5, "style": "subtitle"}]  # type: ignore[assignment]
    spec = make_spec(scenes=[scene], audio={"music": None, "voiceover": "tts"})
    from reel.audio import pipeline

    monkeypatch.setattr(pipeline, "get_tts_engine", lambda name=None: eng)
    plan = prepare_audio(spec, work_dir=tmp_path / "w", engine="say")
    assert any("'hi'" in w and "mispronounced" in w for w in plan.warnings), plan.warnings


def test_text_language_needs_the_script_to_be_at_least_half_of_the_letters() -> None:
    """Regression: any Chinese word made a whole English caption a 'zh' text, which then went to a Chinese voice."""
    from reel.audio.tts import text_language

    assert text_language("Buy 3 coffees ☕ 咖啡") is None
    assert text_language("Check out the new नमस्ते feature today") is None
    assert text_language("你好 hi") == "zh"  # half and half: the script wins
    assert text_language("ਸਤ ਸ੍ਰੀ ਅਕਾਲ") == "pa" and text_language("ខ្ញុំស្រលាញ់") == "km"
    assert text_language("สวัสดี") == "th" and text_language("ສະບາຍດີ") == "lo"
    assert text_language("မင်္ဂလာပါ") == "my" and text_language("Γειά σου") == "el"
    assert (
        text_language("שלום") == "he" and text_language("ﷲ أكبر") == "ar"
    )  # presentation forms count too
    assert text_language("123 ... !!!") is None  # no letters at all


def test_accent_folding_keeps_the_virama_that_decides_a_syllable_count() -> None:
    from reel.audio.align import fold_accents, word_weight

    assert fold_accents("café Zoë naïve") == "cafe Zoe naive"
    assert fold_accents("क्षमा") == "क्षमा"  # nothing stripped from a Devanagari word
    assert word_weight("क्षमा") == 2 and word_weight("कषमा") == 3  # with and without the virama
    assert fold_accents("ก็") == "ก็" and fold_accents("ผู้") == "ผู้"  # Thai marks stay


def test_babble_speaks_non_latin_words_and_digits(tts_cache: Path) -> None:
    """Regression: only a-z words made sound, so a Hindi or Chinese line (or "2024") was silent."""
    from reel.audio.align import voiced_span

    eng = BabbleTTS()
    for text in ("नमस्ते दुनिया", "你好 世界", "مرحبا بالعالم", "In 2024"):
        clip = eng.synthesize(text)
        assert voiced_span(clip.samples, clip.sample_rate) is not None, text
        assert len(clip.words) == len(text.split())
    a, b = eng.synthesize("你好"), eng.synthesize("世界")
    assert a.samples.shape != b.samples.shape or not np.allclose(a.samples, b.samples)
    again = eng.synthesize("你好")
    assert np.array_equal(a.samples, again.samples)  # same word, same sound


class _Mute(TTSEngine):
    """An engine whose voice has no phonemes for the text: it 'succeeds' with silence."""

    name = "mute"

    def available(self) -> bool:
        return True

    def list_voices(self) -> list[str]:
        return ["quiet"]

    def synthesize(self, text: str, voice: str | None = None, speed: float = 1.0) -> TTSClip:
        return TTSClip(np.zeros(16_000, dtype=np.float32), 16_000)


def test_a_voice_that_says_nothing_is_replaced_by_babble_and_reported(
    tts_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reel.audio import pipeline

    monkeypatch.setattr(pipeline, "get_tts_engine", lambda name=None: _Mute())
    scene = {"id": "a", "duration_sec": 5.0, "background": {"template": "abstract"}}
    scene["captions"] = [  # type: ignore[assignment]
        {"text": "नमस्ते दुनिया", "t0": 0.3, "t1": 4.5, "style": "subtitle"},
        {"text": "...", "t0": 4.6, "t1": 4.9, "style": "subtitle"},
    ]
    spec = make_spec(scenes=[scene], audio={"music": None, "voiceover": "tts"})
    plan = prepare_audio(spec, work_dir=tmp_path / "w")
    assert any("no audible speech" in w and "'hi'" in w and "babble" in w for w in plan.warnings), (
        plan.warnings
    )
    assert (
        sum("no audible speech" in w for w in plan.warnings) == 1
    )  # "..." has nothing to say: no warning
    assert plan.word_timings[0][0], "the captions are still timed"
    assert plan.report is not None and plan.report.approx_lufs > -60.0  # and something is audible
