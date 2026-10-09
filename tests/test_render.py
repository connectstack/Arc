"""End-to-end rendering: golden frames per style, determinism, the frame/segment cache, lenient mode."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import skia

from reel.core.catalog import CATALOG
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec

GOLDEN = Path(__file__).parent / "golden"
STYLES = ["flat_vector", "paper_cutout", "stickman"]
SCALE = 0.25  # 270x480
FRAMES = (12, 45, 84)  # inside a 3-second render


def golden_spec(style: str) -> ReelSpec:
    """A fixed 3-second scene: two characters, actions, camera moves, captions in all three presets."""
    return ReelSpec.model_validate(
        {
            "meta": {"title": "golden", "style": style, "seed": 42},
            "characters": [
                {"id": "ana", "archetype": "everyman", "props": ["hat", "briefcase"]},
                {"id": "kai", "archetype": "kid"},
            ],
            "scenes": [
                {
                    "id": "g",
                    "duration_sec": 3,
                    "background": {"template": "abstract", "params": {"palette": "violet"}},
                    "camera": {
                        "moves": [
                            {"type": "dolly", "from": 0, "to": 0.3, "t0": 0, "t1": 3},
                            {"type": "pan", "from": [0, 0], "to": [0.06, 0], "t0": 0, "t1": 3},
                        ]
                    },
                    "layers": [
                        {
                            "character": "ana",
                            "position": [0.3, 0.74],
                            "actions": [{"name": "wave", "t0": 0.2, "t1": 2.4}],
                        },
                        {
                            "character": "kai",
                            "position": [0.72, 0.74],
                            "actions": [{"name": "laugh", "t0": 0.3, "t1": 2.8}],
                        },
                    ],
                    "captions": [
                        {"text": "Hello world", "t0": 0.2, "t1": 1.5, "style": "title"},
                        {
                            "text": "Golden frames keep us honest",
                            "t0": 1.0,
                            "t1": 2.9,
                            "style": "subtitle",
                        },
                    ],
                }
            ],
        }
    )


def to_png_array(frame: np.ndarray) -> np.ndarray:
    return frame[..., [2, 1, 0]]  # BGRA -> RGB


def save_png(path: Path, frame: np.ndarray) -> None:
    from PIL import Image

    Image.fromarray(to_png_array(frame)).save(path, optimize=True)


def load_png(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"))


def font_families() -> list[str]:
    """The families the caption stacks resolve to on this machine (captions are part of the golden frames)."""
    from reel.core import fonts

    return [
        fonts.typeface(stack).getFamilyName()
        for stack in (fonts.HEADLINE, fonts.SANS_BOLD, fonts.MARKER)
    ]


@pytest.mark.render
@pytest.mark.parametrize("style", STYLES)
def test_golden_frames(style: str) -> None:
    update = os.environ.get("REEL_UPDATE_GOLDEN") == "1"
    marker = GOLDEN / "fonts.json"
    families = font_families()
    if update or not marker.exists():
        GOLDEN.mkdir(exist_ok=True)
        marker.write_text(json.dumps(families) + "\n")
    recorded = json.loads(marker.read_text())
    if recorded != families:
        pytest.skip(
            f"golden frames were recorded with fonts {recorded}, this machine resolves {families}; "
            "re-record with REEL_UPDATE_GOLDEN=1 pytest -k golden"
        )
    r = Renderer(golden_spec(style), RenderOptions(scale=SCALE, cache=False))
    for f in FRAMES:
        got = r.frame(f)
        assert got.shape == (480, 270, 4)
        path = GOLDEN / f"{style}_{f:03d}.png"
        if update or not path.exists():
            GOLDEN.mkdir(exist_ok=True)
            save_png(path, got)
            if not update:
                pytest.fail(
                    f"created missing golden {path.name}; re-run (set REEL_UPDATE_GOLDEN=1 to refresh deliberately)"
                )
            continue
        want = load_png(path).astype(np.int16)
        diff = np.abs(to_png_array(got).astype(np.int16) - want)
        assert diff.mean() < 1.6, (
            f"{style} frame {f}: mean abs diff {diff.mean():.2f} (golden {path.name})"
        )
        assert np.percentile(diff, 99.9) < 60, f"{style} frame {f}: local differences too large"


@pytest.mark.render
@pytest.mark.parametrize("style", STYLES)
def test_frames_are_deterministic_and_vary_over_time(style: str) -> None:
    a = Renderer(golden_spec(style), RenderOptions(scale=0.15, cache=False))
    b = Renderer(golden_spec(style), RenderOptions(scale=0.15, cache=False))
    f10, f60 = a.frame(10), a.frame(60)
    assert np.array_equal(f10, b.frame(10))
    assert not np.array_equal(f10, f60)  # it actually animates
    other = Renderer(golden_spec(style), RenderOptions(scale=0.15, cache=False, seed=43)).frame(10)
    assert not np.array_equal(f10, other)  # the seed matters (grain, jitter, blinks, ...)


@pytest.mark.render
def test_the_same_spec_renders_identically_in_every_style_without_edits() -> None:
    """Switching style never needs a spec change: every registered style renders the same JSON."""
    base = golden_spec("flat_vector")
    for style in CATALOG.styles.names():
        r = Renderer(base, RenderOptions(style=style, scale=0.15, cache=False))
        f = r.frame(30)
        assert f[..., :3].std() > 2


@pytest.mark.render
def test_frame_cache_roundtrip_is_exact(tmp_path: Path) -> None:
    spec = golden_spec("flat_vector")
    cold = Renderer(spec, RenderOptions(scale=0.15, cache_dir=str(tmp_path)))
    f = cold.frame(20)
    warm = Renderer(spec, RenderOptions(scale=0.15, cache_dir=str(tmp_path)))
    g = warm.frame(20)
    assert np.array_equal(f, g)
    assert warm.cache.hits >= 1 and cold.cache.misses >= 1


@pytest.mark.render
def test_edit_invalidates_only_affected_frames(tmp_path: Path) -> None:
    """Change one character's action late in the scene: early frames still hit the cache."""
    spec = golden_spec("flat_vector")
    r1 = Renderer(spec, RenderOptions(scale=0.15, cache_dir=str(tmp_path)))
    frames = [0, 3, 6, 40, 60, 80]
    keys1 = [r1.pre_key(f) for f in frames]
    data: dict[str, Any] = spec.model_dump(by_alias=True)
    data["scenes"][0]["layers"][1]["actions"] = [
        {"name": "laugh", "t0": 1.9, "t1": 2.8}
    ]  # kai now starts laughing late
    r2 = Renderer(ReelSpec.model_validate(data), RenderOptions(scale=0.15, cache_dir=str(tmp_path)))
    keys2 = [r2.pre_key(f) for f in frames]
    same = [a == b for a, b in zip(keys1, keys2)]
    assert same[:3] == [
        True,
        True,
        True,
    ]  # frames before the edited action matters keep their cache keys
    assert same[3:] == [False, False, False]  # frames where the pose really changed do not


@pytest.mark.render
@pytest.mark.ffmpeg
def test_video_render_is_valid_h264_and_deterministic(tmp_path: Path) -> None:
    from reel.core.ffmpeg import probe_video

    spec = golden_spec("flat_vector")
    opts = {"scale": 0.25, "crf": 26, "preset": "veryfast", "cache": False, "frame_range": (0, 45)}
    a = Renderer(spec, RenderOptions(workers=1, **opts)).render_video(tmp_path / "a.mp4")
    b = Renderer(spec, RenderOptions(workers=3, **opts)).render_video(tmp_path / "b.mp4")
    assert (tmp_path / "a.mp4").read_bytes() == (
        tmp_path / "b.mp4"
    ).read_bytes()  # same bytes whatever the worker count
    info = probe_video(tmp_path / "a.mp4")
    assert (info["width"], info["height"]) == (270, 480)
    assert (
        info["codec"] == "h264"
        and info["pix_fmt"] == "yuv420p"
        and info["frames"] == 45
        and round(float(info["fps"])) == 30
    )
    assert a.frames == 45 and b.frames == 45


def write_tone(path: Path, seconds: float, rate: int = 48000) -> None:
    import wave

    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


@pytest.mark.render
@pytest.mark.ffmpeg
def test_audio_is_muxed_and_lines_up_with_the_video(tmp_path: Path) -> None:
    """Regression: raw H.264 has no timestamps, and `-shortest` used to silently drop the whole track."""
    from reel.core.ffmpeg import probe_video

    wav = tmp_path / "tone.wav"
    write_tone(wav, 1.5)
    opts = RenderOptions(
        scale=0.1, crf=30, preset="veryfast", cache=False, frame_range=(0, 45), workers=1
    )
    Renderer(golden_spec("flat_vector"), opts).render_video(tmp_path / "av.mp4", audio=wav)
    info = probe_video(tmp_path / "av.mp4")
    assert info["audio_codec"] == "aac"
    assert info["frames"] == 45 and abs(float(info["duration"]) - 1.5) < 0.02
    assert abs(float(info["audio_duration"]) - 1.5) < 0.06  # AAC packets are 21 ms long
    # a longer soundtrack is cut at the end of the picture, a video-only render has no audio track
    long_wav = tmp_path / "long.wav"
    write_tone(long_wav, 4.0)
    Renderer(golden_spec("flat_vector"), opts).render_video(tmp_path / "cut.mp4", audio=long_wav)
    assert abs(float(probe_video(tmp_path / "cut.mp4")["audio_duration"]) - 1.5) < 0.06
    Renderer(golden_spec("flat_vector"), opts).render_video(tmp_path / "mute.mp4")
    assert probe_video(tmp_path / "mute.mp4")["audio_codec"] is None


@pytest.mark.render
@pytest.mark.ffmpeg
def test_segment_cache_reuses_unchanged_chunks(tmp_path: Path) -> None:
    spec = golden_spec("flat_vector")
    kw = {
        "scale": 0.2,
        "crf": 28,
        "preset": "veryfast",
        "cache_dir": str(tmp_path / "c"),
        "workers": 1,
    }
    first = Renderer(spec, RenderOptions(**kw)).render_video(tmp_path / "1.mp4")
    assert first.segments_encoded == first.segments_total and first.segments_reused == 0
    again = Renderer(spec, RenderOptions(**kw)).render_video(tmp_path / "2.mp4")
    assert again.segments_encoded == 0 and again.segments_reused == again.segments_total
    assert (tmp_path / "1.mp4").read_bytes() == (tmp_path / "2.mp4").read_bytes()
    # editing a caption shown only in the first 2 s re-encodes only that chunk
    data: dict[str, Any] = spec.model_dump(by_alias=True)
    data["scenes"][0]["captions"][0]["text"] = "A different title"  # only shown in the first chunk
    edited = Renderer(ReelSpec.model_validate(data), RenderOptions(**kw)).render_video(
        tmp_path / "3.mp4"
    )
    assert 0 < edited.segments_encoded < edited.segments_total


@pytest.mark.render
def test_lenient_mode_renders_a_spec_with_missing_pieces() -> None:
    data = golden_spec("flat_vector").model_dump(by_alias=True)
    data["scenes"][0]["layers"][0]["actions"] = [{"name": "moonwalk", "t0": 0.2, "t1": 2.0}]
    data["scenes"][0]["background"]["template"] = "spaceship"
    spec = ReelSpec.model_validate(data)
    with pytest.raises(Exception, match=r"moonwalk|spaceship"):
        Renderer(spec, RenderOptions(scale=0.15, cache=False)).frame(10)
    r = Renderer(spec, RenderOptions(scale=0.15, cache=False, lenient=True))
    assert r.frame(10)[..., :3].std() > 2
    assert any("moonwalk" in w for w in r.report.warnings) and any(
        "spaceship" in w for w in r.report.warnings
    )


_ = skia


def test_aborting_the_pool_kills_workers_and_drops_queued_work() -> None:
    """Regression: Ctrl-C (or a failed segment) used to leave the pool draining its whole queue in the background."""
    import multiprocessing
    import time
    from concurrent.futures import ProcessPoolExecutor

    from reel.core.render import _abort_pool

    ex = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
    futs = [ex.submit(time.sleep, 120) for _ in range(6)]
    # let the workers start on their first tasks (spawning a process takes longer on a busy machine, so wait for it)
    deadline = time.monotonic() + 20
    while len(ex._processes) < 2 and time.monotonic() < deadline:  # type: ignore[attr-defined]
        time.sleep(0.1)
    time.sleep(0.5)
    procs = list(ex._processes.values())  # type: ignore[attr-defined]
    assert procs and all(p.is_alive() for p in procs)
    t0 = time.perf_counter()
    _abort_pool(ex)
    assert time.perf_counter() - t0 < 10
    assert all(not p.is_alive() for p in procs)
    # the executor pre-fetches max_workers + 1 calls; everything beyond that was only queued and is dropped
    assert sum(f.cancelled() for f in futs) >= len(futs) - 3


@pytest.mark.ffmpeg
def test_mux_with_audio_is_byte_deterministic(tmp_path: Path) -> None:
    """Regression: with ffmpeg's default interleave window the chunk layout varied from run to run (needs a long clip)."""
    import subprocess

    from reel.core.ffmpeg import ffmpeg_path, mux_mp4

    h264 = tmp_path / "v.h264"
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=96x160:rate=30:duration=40",
         "-c:v", "libx264", "-bf", "0", "-g", "60", "-f", "h264", str(h264)],
        check=True,
    )  # fmt: skip
    wav = tmp_path / "a.wav"
    write_tone(wav, 40.0)
    outs = []
    for i in range(4):
        out = tmp_path / f"m{i}.mp4"
        mux_mp4(h264, out, 30, audio=wav, title="t")
        outs.append(out.read_bytes())
    assert len({hash(o) for o in outs}) == 1 and all(o == outs[0] for o in outs)


@pytest.mark.ffmpeg
@pytest.mark.parametrize("fps", [23, 29, 30, 60])
def test_mux_keeps_every_video_frame_even_when_the_audio_is_a_hair_short(
    tmp_path: Path, fps: int
) -> None:
    """Regression: `-shortest` dropped the last frame at 37 of 49 legal frame rates (audio rounds to whole samples)."""
    import subprocess

    from reel.core.ffmpeg import ffmpeg_path, mux_mp4, probe_video

    frames = 31
    h264 = tmp_path / "v.h264"
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=64x96:rate={fps}",
         "-frames:v", str(frames), "-c:v", "libx264", "-bf", "0", "-g", "60", "-f", "h264", str(h264)],
        check=True,
    )  # fmt: skip
    wav = tmp_path / "a.wav"
    write_tone(
        wav, frames / fps - 0.0003
    )  # a few hundredths of a millisecond shorter than the picture
    out = tmp_path / "o.mp4"
    mux_mp4(h264, out, fps, audio=wav, frames=frames)
    info = probe_video(out)
    assert info["frames"] == frames, f"{info['frames']} of {frames} frames kept at {fps} fps"
    assert abs(float(info["duration"]) - frames / fps) < 0.001
    assert (
        abs(float(info["audio_duration"]) - frames / fps) < 2 / fps
    )  # within a frame of the picture
    assert not list(tmp_path.glob(".*partial*"))  # the temporary file is gone


@pytest.mark.ffmpeg
def test_a_failed_mux_leaves_neither_a_partial_file_nor_clobbers_the_old_output(
    tmp_path: Path,
) -> None:
    from reel.core.ffmpeg import FfmpegError, mux_mp4

    out = tmp_path / "reel.mp4"
    out.write_bytes(b"the previous good render")
    broken = tmp_path / "broken.h264"
    broken.write_bytes(b"this is not h264")
    with pytest.raises(FfmpegError):
        mux_mp4(broken, out, 30)
    assert out.read_bytes() == b"the previous good render"
    assert not list(tmp_path.glob(".*partial*"))


@pytest.mark.render
def test_installing_a_font_invalidates_cached_frames_and_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: cache keys ignored fonts, so a font installed later never replaced the fallback drawn earlier."""
    from reel.core import fonts

    spec = golden_spec("flat_vector")
    opts = RenderOptions(scale=0.15, cache_dir=str(tmp_path))
    before = Renderer(spec, opts)
    keys = [before.pre_key(f) for f in (0, 30)]
    segs = [s.key for s in before.plan_segments()]
    same = Renderer(spec, opts)
    assert [same.pre_key(f) for f in (0, 30)] == keys  # nothing changed: nothing invalidated

    monkeypatch.setattr(fonts, "inventory_fingerprint", lambda: "a-new-font-was-installed")
    after = Renderer(spec, opts)
    assert [after.pre_key(f) for f in (0, 30)] != keys
    assert not {s.key for s in after.plan_segments()} & set(segs)


def test_the_font_inventory_notices_font_files_in_the_search_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reel.core import fonts

    monkeypatch.setenv("REEL_FONT_DIR", str(tmp_path))
    fonts.inventory_fingerprint.cache_clear()
    try:
        empty = fonts.inventory_fingerprint()
        font = tmp_path / "Bangers-Regular.ttf"
        font.write_bytes(b"not really a font")
        fonts.inventory_fingerprint.cache_clear()
        added = fonts.inventory_fingerprint()
        font.write_bytes(
            b"a different, longer, replacement"
        )  # replaced in place under the same name
        fonts.inventory_fingerprint.cache_clear()
        replaced = fonts.inventory_fingerprint()
        assert len({empty, added, replaced}) == 3
        fonts.inventory_fingerprint.cache_clear()
        assert fonts.inventory_fingerprint() == replaced  # stable while nothing changes
    finally:
        fonts.inventory_fingerprint.cache_clear()
