"""The shared "spec file -> MP4" flow behind `reel render` and `reel build`.

lint -> (audio plan: TTS, SFX, music, caption retiming) -> render -> mux.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from reel.cli.report import print_report
from reel.core.lint import LintOptions, lint_file
from reel.core.render import Renderer, RenderOptions, RenderResult
from reel.core.spec import ReelSpec


@dataclass
class RenderRequest:
    spec_path: Path
    style: str | None = None
    out: Path | None = None
    preview: bool = False
    scale: float | None = None
    time_range: tuple[float, float] | None = None  # seconds
    frames_dir: Path | None = None
    lenient: bool | None = None
    no_cache: bool = False
    cache_dir: Path | None = None
    workers: int | None = None
    seed: int | None = None
    crf: int | None = None
    maxrate: str | None = None  # "10M"; "none"/"0" = uncapped; None = the default for full renders
    no_duration_check: bool = False
    no_audio: bool = False
    tts: str | None = None
    plugins: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()  # the user's asset folders (read again in every render worker)
    extra_warnings: list[str] = field(default_factory=list)


def wants_audio(spec: ReelSpec) -> bool:
    a = spec.audio
    return bool(a.music) or a.voiceover != "none" or a.auto_sfx or any(s.sfx for s in spec.scenes)


def _audio_plan(
    req: RenderRequest, model: ReelSpec, opts: RenderOptions, console: Console, workdir: Path
) -> tuple[ReelSpec, Path | None]:
    """Synthesise voice/SFX/music for ``model``. Returns (spec with retimed captions, wav or None)."""
    try:
        from reel.audio.pipeline import prepare_audio
        from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError
    except ImportError as exc:  # the audio package is optional at runtime
        console.print(f"[yellow]audio unavailable ({escape(str(exc))}); rendering silent video[/]")
        return model, None
    events: list[tuple[float, str]] = []
    if model.audio.auto_sfx:
        events = Renderer(model, opts).action_events()
    rng = None
    if req.time_range is not None:
        rng = (req.time_range[0], req.time_range[1])
    try:
        plan = prepare_audio(
            model,
            base_dir=req.spec_path.parent,
            work_dir=workdir,
            engine=req.tts,
            action_events=events,
            frame_range_sec=rng,
        )
    except (TTSUnavailableError, UnknownTTSEngineError):
        raise  # asked for by name and not usable: say so now, instead of rendering a silent video nobody wanted
    except Exception as exc:  # never lose the picture because the sound failed
        console.print(f"[yellow]audio failed ({escape(str(exc))}); rendering silent video[/]")
        return model, None
    for w in plan.warnings:
        console.print(f"[yellow]audio:[/] {escape(w)}")
    for note in plan.notes:
        console.print(f"[dim]audio:[/] {escape(note)}")
    opts.word_timings = plan.word_timings
    return plan.spec, plan.wav


def run_render(req: RenderRequest, console: Console, err: Console) -> int:
    """Run the whole flow; returns a process exit code (errors become messages, not tracebacks)."""
    from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError
    from reel.core.ffmpeg import FfmpegError
    from reel.core.planner import PlanError
    from reel.core.registry import UnknownEntryError

    try:
        return _run_render(req, console, err)
    except KeyboardInterrupt:
        err.print(
            "\n[yellow]interrupted[/]: nothing was written; chunks finished so far are cached, so a re-run resumes"
        )
        return 130
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        err.print(
            f"[red]speech engine problem:[/] {escape(str(exc))}  (use --tts auto for the offline voices)"
        )
    except FfmpegError as exc:
        err.print(f"[red]ffmpeg problem:[/] {escape(str(exc))}")
    except UnknownEntryError as exc:
        err.print(
            f"[red]{escape(str(exc))}[/]  (run `reel lint` for the full list of what is missing)"
        )
    except PlanError as exc:
        err.print(f"[red]{escape(str(exc))}[/]  (use --lenient to fall back instead of stopping)")
    return 1


def _run_render(req: RenderRequest, console: Console, err: Console) -> int:
    is_lenient = req.lenient if req.lenient is not None else req.preview
    partial = req.time_range is not None or req.no_duration_check
    report = lint_file(
        req.spec_path, options=LintOptions(check_duration=not partial, style_override=req.style)
    )
    if not report.ok and not is_lenient:
        print_report(console, report)
        err.print(
            "\n[red]refusing to render a spec with errors.[/] Fix them, or use [bold]--lenient[/] to fall back "
            "(unknown action -> idle, unknown background -> abstract, ...)."
        )
        return 1
    if report.warnings or (report.errors and is_lenient):
        console.print(
            f"[yellow]lint:[/] {len(report.errors)} error(s), {len(report.warnings)} warning(s)"
            + (" (lenient: falling back)" if report.errors else "")
        )
    try:
        model = ReelSpec.from_file(req.spec_path)
    except Exception:
        print_report(console, report)
        return 1
    fps = model.meta.fps
    sc = req.scale if req.scale is not None else (1 / 3 if req.preview else 1.0)
    frame_range = (
        (round(req.time_range[0] * fps), round(req.time_range[1] * fps)) if req.time_range else None
    )
    if (
        req.cache_dir
    ):  # the speech cache and the workers follow --cache-dir too, not only the frame/segment caches
        os.environ["REEL_CACHE_DIR"] = str(req.cache_dir)
    opts = RenderOptions(
        style=req.style, scale=sc, lenient=is_lenient, workers=req.workers, cache=not req.no_cache,
        cache_dir=str(req.cache_dir) if req.cache_dir else None, crf=req.crf if req.crf is not None else (26 if req.preview else 20),
        preset="veryfast" if req.preview else "medium", frame_range=frame_range, plugins=req.plugins, assets=req.assets, seed=req.seed,
        audio=not req.no_audio, maxrate=_maxrate(req),
    )  # fmt: skip
    if req.frames_dir is not None:
        paths = Renderer(model, opts).dump_frames(req.frames_dir)
        console.print(f"wrote {len(paths)} frame(s) to {req.frames_dir}")
        return 0

    target = (
        req.out
        or Path("out")
        / f"{req.spec_path.stem}{'_' + req.style if req.style else ''}{'_preview' if req.preview else ''}.mp4"
    )
    with tempfile.TemporaryDirectory(prefix="reel-audio-") as td:
        wav: Path | None = None
        if not req.no_audio and wants_audio(model):
            model, wav = _audio_plan(req, model, opts, console, Path(td))
        r = Renderer(model, opts)
        with Progress(
            TextColumn("[bold]render[/]"), BarColumn(), TextColumn("{task.completed}/{task.total} frames"),
            TimeElapsedColumn(), TimeRemainingColumn(), console=console,
        ) as bar:  # fmt: skip
            segments = r.plan_segments()
            task = bar.add_task("r", total=max(1, sum(len(s.frames) for s in segments)))
            res: RenderResult = r.render_video(
                target,
                progress=lambda done, total: bar.update(task, completed=done, total=total),
                segments=segments,
                audio=wav,
            )
    for w in res.warnings:
        console.print(f"[yellow]warning:[/] {escape(w)}")
    console.print(
        f"[green]wrote[/] {res.path}  {res.size[0]}x{res.size[1]}  {res.duration_sec:.1f}s  {res.frames} frames  in {res.seconds:.1f}s  "
        f"(segments: {res.segments_encoded} rendered, {res.segments_reused} from cache)"
        + ("  + audio" if wav else "")
    )
    return 0


_RATE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*(?:bps|bit/s)?\s*$")


def parse_rate(value: str) -> str | None:
    """A bitrate as ffmpeg wants it: ``'10M'``, ``'10m'``, ``'8000k'`` and ``'12000000'`` are read, ``none`` / ``0`` / empty give ``None``.

    The case of the suffix matters to ffmpeg (a lowercase ``m`` means *milli*, which silently turned the cap off), so it is normalised."""
    if value.strip().lower() in ("", "0", "none", "off"):
        return None
    m = _RATE.match(value)
    if not m or float(m.group(1)) == 0:
        raise ValueError(
            f"cannot read the bitrate {value!r}; use something like 10M, 8000k or none"
        )
    number, suffix = m.groups()
    return f"{number}{ {'': '', 'k': 'k', 'm': 'M', 'g': 'G'}[suffix.lower()] }"


def _maxrate(req: RenderRequest) -> str | None:
    """The x264 bitrate cap: the request's, else 10M for full renders and none for previews."""
    if req.maxrate is None:
        return None if req.preview else "10M"
    return parse_rate(req.maxrate)


def parse_range(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    a, b = value.split(":")
    return float(a or 0), float(b)
