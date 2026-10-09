"""ffmpeg integration: parallel-friendly H.264 segment encoding and the final mux.

Segments are encoded independently to raw Annex-B H.264 (no B-frames, closed GOPs, in-band
headers), so unchanged segments can be cached and the final file is made by concatenating bytes
and remuxing - no re-encode.  Frames reach ffmpeg as raw BGRA on stdin.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np


class FfmpegError(RuntimeError):
    pass


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FfmpegError(
            "ffmpeg was not found on PATH. Install it (macOS: `brew install ffmpeg`, Debian/Ubuntu: `apt install ffmpeg`)."
        )
    return exe


def ffmpeg_version() -> str:
    try:
        out = subprocess.run(
            [ffmpeg_path(), "-version"], capture_output=True, text=True, check=True
        ).stdout
        return out.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, FfmpegError) as exc:
        return f"unavailable ({exc})"


def _x264_args(crf: int, preset: str, keyint: int, maxrate: str | None = None) -> list[str]:
    # `maxrate` caps the bitrate (VBV, buffer = one second of it): grain and paper texture are close to
    # incompressible noise and at a plain CRF they cost 30+ Mbps (a 300 MB reel); the cap bounds them.
    cap = ["-maxrate", maxrate, "-bufsize", maxrate] if maxrate else []
    return [
        *cap,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-tune", "animation",
        "-bf", "0", "-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0",
        "-threads", "2", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-x264-params", "aq-mode=3:deblock=1,1",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
    ]  # fmt: skip


class H264SegmentEncoder:
    """Context manager: write BGRA frames, get a raw H.264 file."""

    def __init__(
        self,
        out: Path,
        size: tuple[int, int],
        fps: int,
        *,
        crf: int = 20,
        preset: str = "medium",
        maxrate: str | None = None,
    ) -> None:
        self.out = out
        self.size = size
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
            *_x264_args(crf, preset, keyint=max(1, fps * 2), maxrate=maxrate),
            "-f", "h264", str(out),
        ]  # fmt: skip
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        assert self.proc.stdin is not None
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            raise ValueError(
                f"frame is {frame.shape[1]}x{frame.shape[0]}, encoder expects {self.size[0]}x{self.size[1]}"
            )
        try:
            self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError as exc:
            raise FfmpegError(self._stderr()) from exc

    def _stderr(self) -> str:
        try:
            return (
                self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
            ).strip() or "ffmpeg exited unexpectedly"
        except OSError:
            return "ffmpeg exited unexpectedly"

    def close(self) -> None:
        assert self.proc.stdin is not None
        with contextlib.suppress(BrokenPipeError):
            self.proc.stdin.close()
        rc = self.proc.wait()
        if rc != 0:
            raise FfmpegError(self._stderr())

    def __enter__(self) -> H264SegmentEncoder:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is not None:
            self.proc.kill()
            self.proc.wait()
            return
        self.close()


def concat_h264(parts: list[Path], dst: Path) -> None:
    """Byte-concatenate Annex-B segments (each starts with SPS/PPS + IDR)."""
    with dst.open("wb") as w:
        for p in parts:
            with p.open("rb") as r:
                shutil.copyfileobj(r, w, 1 << 20)


def mux_mp4(
    h264: Path,
    out: Path,
    fps: int,
    *,
    audio: Path | None = None,
    audio_bitrate: str = "192k",
    title: str | None = None,
    frames: int | None = None,
) -> None:
    """Remux raw H.264 (+ optional audio) into a faststart MP4 without re-encoding the video.

    ``frames`` (the number of video frames) makes the result frame-exact: the audio is padded with silence and cut half a
    frame before the end of the picture, so no video frame is ever dropped.  Without it ``-shortest`` is used, which cuts
    the last frame(s) whenever the audio happens to be a hair shorter than the video.

    The MP4 is written next to ``out`` under a hidden name and moved into place only when ffmpeg succeeded, so an interrupted
    or failed run never leaves a truncated file over a good one."""
    part = out.with_name(f".{out.stem}.partial{out.suffix}")
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "h264",
        "-framerate",
        str(fps),
        "-i",
        str(h264),
    ]
    if audio is not None:
        cmd += ["-i", str(audio)]
    cmd += ["-map", "0:v:0"]
    if audio is not None:
        cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", audio_bitrate, "-flags:a", "+bitexact"]
        if frames:
            cmd += ["-af", "apad", "-t", f"{(frames - 0.5) / fps:.6f}"]
        else:
            cmd += ["-shortest"]
        # ffmpeg reads the two inputs on separate threads and, with its default 10 s interleave window, the audio/video
        # chunk layout of the MP4 depended on thread timing: same streams, different bytes on every run
        cmd += ["-max_interleave_delta", "0"]
    cmd += [
        "-c:v",
        "copy",
        # a raw Annex-B stream carries no timestamps; give every frame an explicit one (without this
        # ffmpeg warns, and `-shortest` thinks the video ends at t=0 and drops the whole audio track)
        "-bsf:v",
        f"setts=pts=N/{fps}/TB:dts=N/{fps}/TB",
        "-movflags",
        "+faststart",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-map_metadata",
        "-1",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd += [str(part)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise FfmpegError(res.stderr.strip() or "ffmpeg mux failed")
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)


def probe_video(path: Path) -> dict[str, object]:
    """Basic facts about a rendered file via ffprobe (size, fps, frames, duration, codec, audio codec/duration)."""
    exe = shutil.which("ffprobe")
    if exe is None:
        raise FfmpegError("ffprobe was not found on PATH")
    res = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=codec_name,width,height,r_frame_rate,nb_read_frames,duration,pix_fmt,profile", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    import json

    s = json.loads(res.stdout)["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    info: dict[str, object] = {
        "codec": s["codec_name"],
        "profile": s.get("profile"),
        "pix_fmt": s["pix_fmt"],
        "width": s["width"],
        "height": s["height"],
        "fps": num / den,
        "frames": int(s.get("nb_read_frames", 0)),
        "duration": float(s.get("duration", 0.0)),
        "audio_codec": None,
        "audio_duration": 0.0,
    }
    aud = subprocess.run(
        [exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,duration,sample_rate", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    streams = json.loads(aud.stdout).get("streams", [])
    if streams:
        info["audio_codec"] = streams[0]["codec_name"]
        info["audio_duration"] = float(streams[0].get("duration", 0.0))
    return info
