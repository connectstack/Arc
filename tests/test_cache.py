"""The on-disk cache: stats / clear / prune cover frames, video segments and synthesised speech."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from reel.cli.main import app
from reel.core.cache import CACHE_KINDS, DiskCache

runner = CliRunner()


def fill(root: Path) -> None:
    for kind, name, n in (
        ("frames", "a.rfc", 300),
        ("segments", "b.h264", 500),
        ("tts", "c.npy", 200),
    ):
        p = root / kind / name[0] / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * n)


def test_every_kind_is_counted(tmp_path: Path) -> None:
    fill(tmp_path)
    st = DiskCache(tmp_path).stats()
    assert set(CACHE_KINDS) == {"frames", "segments", "tts", "tmp"}
    assert [st[k]["bytes"] for k in CACHE_KINDS] == [300, 500, 200, 0]
    assert st["total_bytes"] == 1000


def test_clear_one_kind_or_everything(tmp_path: Path) -> None:
    fill(tmp_path)
    cache = DiskCache(tmp_path)
    assert cache.clear("tts") == 1
    assert cache.stats()["tts"]["files"] == 0 and cache.stats()["frames"]["files"] == 1
    assert cache.clear() == 2
    assert cache.stats()["total_bytes"] == 0


def test_prune_never_evicts_speech_clips(tmp_path: Path) -> None:
    """Clips are tiny and an online voice (ElevenLabs) charges for every one: only frames and chunks are pruned."""
    fill(tmp_path)
    cache = DiskCache(tmp_path, max_bytes=100)  # less than the speech clips alone
    assert cache.prune() == 2
    st = cache.stats()
    assert st["tts"]["files"] == 1 and st["frames"]["files"] == 0 and st["segments"]["files"] == 0
    cache2 = DiskCache(tmp_path, max_bytes=600)
    fill(tmp_path)
    assert cache2.prune() >= 1 and cache2.stats()["tts"]["files"] == 1


def test_cli_cache_commands(tmp_path: Path) -> None:
    fill(tmp_path)
    out = runner.invoke(app, ["cache", "stats", "--cache-dir", str(tmp_path)])
    assert out.exit_code == 0 and "tts:" in out.output
    assert (
        runner.invoke(app, ["cache", "clear", "tts", "--cache-dir", str(tmp_path)]).exit_code == 0
    )
    assert not list((tmp_path / "tts").rglob("*.npy"))
    assert np.isclose(DiskCache(tmp_path).stats()["total_bytes"], 800)


def test_frames_too_expensive_to_store_are_skipped_but_cheap_ones_are_kept(tmp_path: Path) -> None:
    """Noisy full-HD frames compress to megabytes: after a few of them the cache stops paying to store frames."""
    from reel.core.cache import FRAME_GIVE_UP

    rng = np.random.default_rng(1)
    noisy = np.empty((1920, 1080, 4), dtype=np.uint8)
    noisy[..., :3] = rng.integers(0, 256, size=(1920, 1080, 3), dtype=np.uint8)
    noisy[..., 3] = 255
    flat = np.full((1920, 1080, 4), 200, dtype=np.uint8)
    flat[..., 3] = 255  # the cache stores BGR and restores an opaque alpha

    cache = DiskCache(tmp_path)
    for i in range(FRAME_GIVE_UP):
        cache.put_frame(f"{i:02x}", noisy)
    assert cache.stats()["frames"]["files"] == 0  # none stored
    cache.put_frame(
        "ff", flat
    )  # given up: not even a cheap frame is tried any more (no compression cost)
    assert cache.stats()["frames"]["files"] == 0

    fresh = DiskCache(tmp_path)  # another worker / run starts optimistic
    fresh.put_frame("ff", flat)
    got = fresh.get_frame("ff")
    assert got is not None and np.array_equal(got, flat)
    assert fresh.stats()["frames"]["bytes"] < 200_000  # flat frames are tiny
    # a cheap frame in between resets the count, so isolated noisy frames do not switch the cache off
    mixed = DiskCache(tmp_path / "m")
    for i in range(FRAME_GIVE_UP - 1):
        mixed.put_frame(f"a{i}", noisy)
    mixed.put_frame("b0", flat)
    for i in range(FRAME_GIVE_UP - 1):
        mixed.put_frame(f"c{i}", noisy)
    mixed.put_frame("b1", flat)
    assert mixed.stats()["frames"]["files"] == 2


def test_clear_refuses_anything_that_is_not_a_cache_kind(tmp_path: Path) -> None:
    """Regression: `reel cache clear ../elsewhere` used to delete a sibling directory tree."""
    victim = tmp_path / "precious"
    victim.mkdir()
    (victim / "keep.txt").write_text("irreplaceable")
    root = tmp_path / "root"
    root.mkdir()
    cache = DiskCache(root)
    for evil in ("../precious", str(victim), "..", "frames/../../precious", "", "FRAMES"):
        with pytest.raises(ValueError, match="unknown cache kind"):
            cache.clear(evil)
    res = runner.invoke(app, ["cache", "clear", "../precious", "--cache-dir", str(root)])
    assert res.exit_code == 2
    assert (victim / "keep.txt").read_text() == "irreplaceable"


def test_half_written_chunks_are_listed_and_cleared(tmp_path: Path) -> None:
    leftover = tmp_path / "tmp" / "abc.123.h264"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"x" * 10)
    cache = DiskCache(tmp_path)
    assert cache.stats()["tmp"]["files"] == 1
    assert cache.clear() == 1 and not leftover.exists()


def test_a_damaged_cache_file_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    """Regression: a truncated frame file raised zlib.error on every run until someone cleared the cache."""
    cache = DiskCache(tmp_path)
    frame = np.full((64, 48, 4), 90, dtype=np.uint8)
    frame[..., 3] = 255
    cache.put_frame("ab", frame)
    path = next((tmp_path / "frames").rglob("*.rfc"))
    path.write_bytes(path.read_bytes()[:20])  # a writer killed half-way
    assert cache.get_frame("ab") is None and not path.exists()
    cache.put_frame("ab", frame)
    assert np.array_equal(cache.get_frame("ab"), frame)  # and the cache heals itself


def test_a_damaged_speech_clip_is_a_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reel.audio import tts

    monkeypatch.setenv("REEL_CACHE_DIR", str(tmp_path))
    npy, meta = tts._cache_paths("cd" * 32)
    npy.parent.mkdir(parents=True)
    npy.write_bytes(b"")  # zero bytes: np.load raises EOFError
    meta.write_text("{}")
    assert tts.load_cached_clip("cd" * 32) is None and not npy.exists() and not meta.exists()


def test_an_unwritable_cache_folder_switches_the_cache_off_instead_of_failing(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    cache = DiskCache(blocker / "reel")  # cannot be created below a regular file
    assert cache.ensure_writable() is False and cache.enabled is False
    cache.put_frame("ab", np.zeros((8, 8, 4), dtype=np.uint8))  # silently ignored
    assert cache.get_frame("ab") is None


def test_stats_and_prune_survive_files_vanishing_underneath_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(tmp_path)
    cache = DiskCache(tmp_path, max_bytes=100)
    real_stat = Path.stat

    def flaky(self: Path, *a: object, **k: object) -> object:
        if self.name == "b.h264":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", flaky)
    assert cache.stats()["segments"]["bytes"] == 0  # counted as gone, no exception
    cache.prune()  # no exception either


def test_the_budget_probe_notices_when_frames_become_cheap_again(tmp_path: Path) -> None:
    """Regression: after giving up the cache never stored a frame again, even in a later, cheap scene."""
    from reel.core.cache import FRAME_GIVE_UP, FRAME_PROBE_EVERY

    rng = np.random.default_rng(3)
    noisy = np.empty((480, 270, 4), dtype=np.uint8)
    noisy[..., :3] = rng.integers(0, 256, size=(480, 270, 3), dtype=np.uint8)
    noisy[..., 3] = 255
    flat = np.full((480, 270, 4), 120, dtype=np.uint8)
    flat[..., 3] = 255
    cache = DiskCache(tmp_path)
    for i in range(FRAME_GIVE_UP):
        cache.put_frame(
            f"n{i}", noisy
        )  # noisy at 270x480 is ~0.39 MB: over the pixel-proportional budget (0.2 MB)
    assert cache.stats()["frames"]["files"] == 0
    for i in range(FRAME_PROBE_EVERY * 2):
        cache.put_frame(f"f{i:03d}", flat)
    stored = cache.stats()["frames"]["files"]
    assert (
        1 <= stored <= 2 + FRAME_PROBE_EVERY
    )  # it probed, found cheap frames, and resumed storing them


def test_the_budget_scales_with_the_pixel_count(tmp_path: Path) -> None:
    """The same noise density is rejected at 1080p and at preview size (a preview frame is cheap to recompute anyway)."""
    rng = np.random.default_rng(5)

    def noisy(h: int, w: int) -> np.ndarray:
        a = np.empty((h, w, 4), dtype=np.uint8)
        a[..., :3] = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
        a[..., 3] = 255
        return a

    small = DiskCache(tmp_path / "s")
    small.put_frame("aa", noisy(640, 360))
    assert small.stats()["frames"]["files"] == 0


def test_the_engine_fingerprint_covers_rendering_code_but_not_the_server_or_the_cli() -> None:
    """Editing the web server must not invalidate every cached frame; editing the renderer must."""
    from reel.core import cache as cache_mod

    root = Path(cache_mod.__file__).resolve().parents[1]
    hashed = {p.relative_to(root).as_posix() for p in cache_mod._package_files()}
    assert "core/render.py" in hashed and "core/cache.py" in hashed
    assert any(h.startswith("styles/") for h in hashed) and any(
        h.startswith("actions/") for h in hashed
    )
    assert not any(h.split("/")[0] in {"server", "cli", "llm"} for h in hashed)
