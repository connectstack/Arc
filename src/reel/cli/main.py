"""`reel` command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from reel import __version__
from reel.core.catalog import CATALOG, KINDS, PLURALS

app = typer.Typer(
    pretty_exceptions_show_locals=False,  # a crash must never print local variables (they can hold API keys)
    name="reel",
    help="Offline, template-driven script-to-animation reels.  JSON scene spec in, 1080x1920 MP4 out.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err = Console(stderr=True)

PluginOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--plugin",
        help="Extra module / .py file / folder that registers actions, styles, ... (repeatable)",
    ),
]


def _plugins(plugins: list[str] | None) -> None:
    if plugins:
        CATALOG.load_plugins(plugins)


def _version(value: bool) -> None:
    if value:
        console.print(f"reel {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version")
    ] = False,
) -> None:
    """reel — script-to-animation reel renderer."""


# ------------------------------------------------------------------------------- lint
@app.command()
def lint(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="scene spec JSON")],
    as_json: Annotated[bool, typer.Option("--json", help="machine-readable report")] = False,
    style: Annotated[
        str | None, typer.Option("--style", help="lint as if rendering with this style")
    ] = None,
    strict: Annotated[bool, typer.Option("--strict", help="warnings fail too")] = False,
    no_duration_check: Annotated[
        bool, typer.Option("--no-duration-check", help="skip the 45-60s budget")
    ] = False,
    plugin: PluginOpt = None,
) -> None:
    """Validate a spec: schema, registry references, params, timing and the 45-60s budget."""
    from reel.cli.report import print_report
    from reel.core.lint import LintOptions, lint_file

    _plugins(plugin)
    report = lint_file(
        spec, options=LintOptions(check_duration=not no_duration_check, style_override=style)
    )
    if as_json:
        sys.stdout.write(report.to_json() + "\n")
    else:
        print_report(console, report)
    raise typer.Exit(0 if report.ok and not (strict and report.warnings) else 1)


# ------------------------------------------------------------------------------- list
@app.command("list")
def list_(
    what: Annotated[
        str,
        typer.Argument(
            help="actions | styles | backgrounds | transitions | camera | captions | archetypes | props | sfx | easings"
        ),
    ],
    as_json: Annotated[bool, typer.Option("--json", help="machine-readable")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="show params")] = False,
    plugin: PluginOpt = None,
) -> None:
    """List what is registered."""
    from reel.core.manifest import describe

    _plugins(plugin)
    kind = PLURALS.get(what, what)
    if kind not in KINDS:
        err.print(
            f"[red]unknown thing to list:[/] {what!r}. choose from: {', '.join(sorted(PLURALS))}"
        )
        raise typer.Exit(2)
    rows = describe(kind)
    if as_json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return
    table = Table(
        box=box.SIMPLE_HEAD, title=f"{kind.replace('_', ' ')} ({len(rows)})", title_justify="left"
    )
    table.add_column("name", style="bold")
    has_cat = any("category" in r for r in rows)
    if has_cat:
        table.add_column("category", style="magenta")
    table.add_column("summary", overflow="fold")
    if verbose:
        table.add_column("params", style="cyan", overflow="fold")
    for r in rows:
        cells = [r["name"]]
        if has_cat:
            cells.append(str(r.get("category", "")))
        cells.append(r.get("summary", ""))
        if verbose:
            cells.append(
                ", ".join(
                    f"{p['name']}={p.get('default')!r}" if "default" in p else p["name"]
                    for p in r.get("params", [])
                )
            )
        table.add_row(*cells)
    console.print(table)


# ------------------------------------------------------------------------------- schema / manifest
@app.command()
def schema(
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="write here instead of stdout")
    ] = None,
    enums: Annotated[
        bool, typer.Option("--enums", help="pin registry-bound names to what is registered")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="verify committed schema/manifest files are fresh")
    ] = False,
    plugin: PluginOpt = None,
) -> None:
    """Export the JSON Schema of the scene spec (and, with --check, verify the generated files)."""
    from reel.core.manifest import build_manifest, manifest_path
    from reel.core.schema import default_schema_path, schema_json

    _plugins(plugin)
    if check:
        ok = True
        want = schema_json(enums=False)
        p = default_schema_path()
        if not p.exists() or p.read_text() != want:
            err.print(f"[red]stale:[/] {p}  (run: reel schema -o {p.relative_to(p.parents[1])})")
            ok = False
        mp = manifest_path()
        mwant = json.dumps(build_manifest(), indent=2) + "\n"
        if not mp.exists() or mp.read_text() != mwant:
            err.print(
                f"[red]stale:[/] {mp}  (run: reel manifest -o src/reel/templates/manifest.json)"
            )
            ok = False
        from reel.llm.prompt import render_prompt_preview

        prompts_dir = p.parents[1] / "docs" / "prompts"
        for name, compact in (("full.txt", False), ("compact.txt", True)):
            target = prompts_dir / name
            want_prompt = render_prompt_preview(
                "paper_cutout", 50.0, include_schema=True, compact=compact
            )
            if not target.exists() or target.read_text(encoding="utf-8") != want_prompt:
                err.print(f"[red]stale:[/] {target}  (run: make generated)")
                ok = False
        if ok:
            console.print(
                "[green]schema/, templates/manifest.json and docs/prompts/ are up to date[/]"
            )
        raise typer.Exit(0 if ok else 1)
    text = schema_json(enums=enums)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        console.print(f"wrote {out}")
    else:
        sys.stdout.write(text)


@app.command()
def manifest(
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    plugin: PluginOpt = None,
) -> None:
    """Print the template catalog (everything a spec may reference) as JSON — what the LLM reads."""
    from reel.core.manifest import build_manifest

    _plugins(plugin)
    text = json.dumps(build_manifest(), indent=2) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        console.print(f"wrote {out}")
    else:
        sys.stdout.write(text)


# ------------------------------------------------------------------------------- render
def _check_rate(value: str | None) -> str | None:
    from reel.cli.pipeline import parse_rate

    if value is not None:
        try:
            parse_rate(value)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    return value


def _range_option(value: str | None) -> tuple[float, float] | None:
    from reel.cli.pipeline import parse_range

    try:
        return parse_range(value)
    except ValueError as exc:
        raise typer.BadParameter("use --range START:END in seconds, e.g. 0:3") from exc


@app.command()
def render(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="scene spec JSON")],
    style: Annotated[
        str | None,
        typer.Option("--style", "-s", help="render with this style instead of meta.style"),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="output .mp4 (default: out/<name>.mp4)")
    ] = None,
    preview: Annotated[
        bool, typer.Option("--preview", help="low-res fast preview (360x640, lenient)")
    ] = False,
    scale: Annotated[
        float | None, typer.Option("--scale", help="output size relative to the spec resolution")
    ] = None,
    time_range: Annotated[
        str | None, typer.Option("--range", help="render only START:END seconds, e.g. 0:3")
    ] = None,
    frames_dir: Annotated[
        Path | None, typer.Option("--frames-dir", help="write PNG frames here instead of a video")
    ] = None,
    lenient: Annotated[
        bool | None,
        typer.Option(
            "--lenient/--strict", help="fall back (unknown action -> idle, ...) instead of failing"
        ),
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="ignore and do not write the frame/segment cache")
    ] = False,
    cache_dir: Annotated[
        Path | None, typer.Option("--cache-dir", help="cache location (default ~/.cache/reel)")
    ] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", "-j", help="render processes (default: cores-2)")
    ] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="override meta.seed")] = None,
    crf: Annotated[int | None, typer.Option("--crf", help="x264 quality (lower = better)")] = None,
    maxrate: Annotated[
        str | None,
        typer.Option(
            "--maxrate",
            help="x264 bitrate cap, e.g. 10M (default for full renders); 'none' lifts it",
            callback=_check_rate,
        ),
    ] = None,
    no_duration_check: Annotated[
        bool, typer.Option("--no-duration-check", help="allow reels outside 45-60 s")
    ] = False,
    no_audio: Annotated[bool, typer.Option("--no-audio", help="video only")] = False,
    tts: Annotated[
        str | None,
        typer.Option(
            "--tts", help="speech engine: auto | piper | say | babble | elevenlabs (online, opt-in)"
        ),
    ] = None,
    plugin: PluginOpt = None,
) -> None:
    """Render a spec to MP4 (deterministic; unchanged parts come from the cache)."""
    from reel.cli.pipeline import RenderRequest, run_render

    _plugins(plugin)
    req = RenderRequest(
        spec_path=spec, style=style, out=out, preview=preview, scale=scale, time_range=_range_option(time_range),
        frames_dir=frames_dir, lenient=lenient, no_cache=no_cache, cache_dir=cache_dir, workers=workers, seed=seed,
        crf=crf, maxrate=maxrate, no_duration_check=no_duration_check, no_audio=no_audio, tts=tts, plugins=tuple(plugin or ()),
    )  # fmt: skip
    code = run_render(req, console, err)
    if code:
        raise typer.Exit(code)


@app.command()
def build(
    script: Annotated[str, typer.Argument(help="plain-text script file ('-' reads stdin)")],
    style: Annotated[
        str, typer.Option("--style", "-s", help="style pack (see `reel list styles`)")
    ] = "paper_cutout",
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="output .mp4 (default: out/<script>.mp4)")
    ] = None,
    llm: Annotated[
        str | None,
        typer.Option(
            "--llm",
            help="model that writes the spec: ollama:llama3.1 | openai:gpt-4o-mini[@url] | "
            "anthropic:claude-sonnet-5-5  (env REEL_LLM; default: the offline planner)",
        ),
    ] = None,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="force the offline rule-based planner (ignores REEL_LLM)"),
    ] = False,
    spec_out: Annotated[
        Path | None,
        typer.Option("--spec-out", help="where to write the spec (default: next to the video)"),
    ] = None,
    spec_only: Annotated[
        bool, typer.Option("--spec-only", help="stop after writing and linting the spec")
    ] = False,
    duration: Annotated[
        float, typer.Option("--duration", min=45.0, max=60.0, help="target length in seconds")
    ] = 50.0,
    repairs: Annotated[
        int,
        typer.Option(
            "--repairs", min=0, max=8, help="times lint errors are sent back to the model"
        ),
    ] = 3,
    compact: Annotated[
        bool | None,
        typer.Option(
            "--compact/--full-prompt",
            help="short prompt for small local models (default: automatic from the model's context window)",
        ),
    ] = None,
    enrich: Annotated[
        bool,
        typer.Option(
            "--enrich/--no-enrich",
            help="fill in what a model left empty (gestures, camera, sound, caption timing); the lint result never gets worse",
        ),
    ] = True,
    verbatim: Annotated[
        bool,
        typer.Option(
            "--verbatim/--condense",
            help="a model's captions are the script's own words, in order: anything it reworded, dropped or invented is put right (--condense lets it shorten)",
        ),
    ] = True,
    seed: Annotated[int, typer.Option("--seed", help="seed for the spec and the render")] = 7,
    preview: Annotated[
        bool, typer.Option("--preview", help="low-res fast preview (360x640, lenient)")
    ] = False,
    scale: Annotated[
        float | None, typer.Option("--scale", help="output size relative to 1080x1920")
    ] = None,
    time_range: Annotated[
        str | None, typer.Option("--range", help="render only START:END seconds, e.g. 0:3")
    ] = None,
    lenient: Annotated[
        bool | None, typer.Option("--lenient/--strict", help="fall back instead of failing")
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="skip the frame/segment cache")
    ] = False,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
    workers: Annotated[int | None, typer.Option("--workers", "-j")] = None,
    crf: Annotated[int | None, typer.Option("--crf", help="x264 quality (lower = better)")] = None,
    maxrate: Annotated[
        str | None,
        typer.Option(
            "--maxrate",
            help="x264 bitrate cap, e.g. 10M (default for full renders); 'none' lifts it",
            callback=_check_rate,
        ),
    ] = None,
    no_audio: Annotated[bool, typer.Option("--no-audio", help="video only")] = False,
    tts: Annotated[
        str | None,
        typer.Option(
            "--tts", help="speech engine: auto | piper | say | babble | elevenlabs (online, opt-in)"
        ),
    ] = None,
    plugin: PluginOpt = None,
) -> None:
    """Plain-text script -> spec -> video, in one go (LLM or offline planner).

    The spec comes from an LLM (--llm / REEL_LLM) or the offline planner, is linted, saved next to
    the video, and rendered exactly like `reel render`.  Edit the saved JSON and re-render it any time.
    """
    from reel.cli.build import BuildRequest, run_build
    from reel.cli.pipeline import RenderRequest

    _plugins(plugin)
    render_req = RenderRequest(
        spec_path=Path(), style=None, preview=preview, scale=scale, time_range=_range_option(time_range),
        lenient=lenient, no_cache=no_cache, cache_dir=cache_dir, workers=workers, crf=crf,
        maxrate=maxrate, no_audio=no_audio, tts=tts, plugins=tuple(plugin or ()),
    )  # fmt: skip
    req = BuildRequest(
        script=script, style=style, out=out, llm=llm, no_llm=no_llm, spec_out=spec_out,
        spec_only=spec_only, duration=duration, repairs=repairs, compact=compact, enrich=enrich, verbatim=verbatim, seed=seed, render=render_req,
    )  # fmt: skip
    code = run_build(req, console, err)
    if code:
        raise typer.Exit(code)


@app.command()
def prompt(
    style: Annotated[
        str, typer.Option("--style", "-s", help="style the prompt is written for")
    ] = "paper_cutout",
    duration: Annotated[float, typer.Option("--duration", min=45.0, max=60.0)] = 50.0,
    compact: Annotated[
        bool,
        typer.Option("--compact", help="the short variant used for small-context local models"),
    ] = False,
    schema: Annotated[
        bool, typer.Option("--schema", help="also print the JSON schema sent as structured output")
    ] = False,
    script: Annotated[
        Path | None,
        typer.Option(
            "--script",
            exists=True,
            dir_okay=False,
            help="use this script in the sample user message",
        ),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="write here instead of stdout")
    ] = None,
    plugin: PluginOpt = None,
) -> None:
    """Print the exact script -> JSON prompt (system + user message) for the live catalog.

    It lists only what is registered right now, including anything a `--plugin` adds.
    """
    from reel.llm.prompt import render_prompt_preview

    _plugins(plugin)
    text = render_prompt_preview(
        style, duration, script=script.read_text(encoding="utf-8") if script else None,
        include_schema=schema, compact=compact,
    )  # fmt: skip
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"wrote {out}  ({len(text):,} characters)")
    else:
        sys.stdout.write(text)


@app.command("match-cut")
def match_cut_cmd(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="scene spec JSON")],
    from_scene: Annotated[str, typer.Argument(help="outgoing scene id or index")],
    to_scene: Annotated[
        str, typer.Argument(help="incoming scene id or index (must directly follow)")
    ],
    character: Annotated[str, typer.Argument(help="character id that the cut matches on")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="write the edited spec here (default: print JSON)"),
    ] = None,
    no_camera: Annotated[
        bool, typer.Option("--no-camera", help="do not carry the camera framing across")
    ] = False,
    plugin: PluginOpt = None,
) -> None:
    """Rewrite the incoming scene so the character keeps position, size and framing across a hard cut."""
    from reel.core.matchcut import match_cut
    from reel.core.spec import ReelSpec

    _plugins(plugin)

    def ref(v: str) -> str | int:
        return int(v) if v.isdigit() else v

    model = ReelSpec.from_file(spec)
    try:
        res = match_cut(model, ref(from_scene), ref(to_scene), character, keep_camera=not no_camera)
    except (KeyError, IndexError, ValueError) as exc:
        err.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    for n in res.notes:
        console.print(f"[yellow]note:[/] {escape(str(n))}")
    console.print(
        f"[green]{character}[/] now opens {to_scene!r} at {res.position[0]:.3f}, {res.position[1]:.3f} (scale {res.scale:.3f})"
    )
    text = res.spec.to_json()
    if out:
        out.write_text(text)
        console.print(f"wrote {out}")
    else:
        sys.stdout.write(text + "\n")


@app.command()
def reference(
    out: Annotated[
        Path, typer.Option("--out", "-o", help="folder for the generated markdown")
    ] = Path("docs/reference"),
    plugin: PluginOpt = None,
) -> None:
    """Write the reference docs (actions, backgrounds, styles, channels, ...) from the live registries."""
    from reel.core.docgen import write_reference

    _plugins(plugin)
    paths = write_reference(out)
    console.print(f"wrote {len(paths)} reference pages to {out}")


@app.command()
def storyboard(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="scene spec JSON")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="HTML file (default: out/<name>_storyboard.html)"),
    ] = None,
    style: Annotated[str | None, typer.Option("--style", "-s")] = None,
    scale: Annotated[float, typer.Option(help="thumbnail scale (0.25 = 270x480)")] = 0.25,
    frames: Annotated[int, typer.Option("--frames", help="key frames per scene")] = 3,
    plugin: PluginOpt = None,
) -> None:
    """Write a self-contained HTML storyboard (timeline, key frames per scene, lint) to review a spec fast."""
    from reel.core.spec import ReelSpec
    from reel.core.storyboard import build_storyboard

    _plugins(plugin)
    path = build_storyboard(
        ReelSpec.from_file(spec),
        out or Path("out") / f"{spec.stem}_storyboard.html",
        style=style,
        scale=scale,
        frames_per_scene=frames,
    )
    console.print(f"wrote {path}")


# ------------------------------------------------------------------------------- cache / doctor
cache_app = typer.Typer(
    help="Inspect or clear the frame / segment / speech cache.", no_args_is_help=True
)
app.add_typer(cache_app, name="cache")


@cache_app.command("stats")
def cache_stats(cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None) -> None:
    """Show cache location and size."""
    from reel.core.cache import DiskCache

    st = DiskCache(cache_dir).stats()
    mb = lambda b: f"{b / 1e6:,.1f} MB"  # noqa: E731
    console.print(f"cache: {st['root']}")
    console.print(f"  frames:   {st['frames']['files']:>6} files  {mb(st['frames']['bytes'])}")
    console.print(f"  segments: {st['segments']['files']:>6} files  {mb(st['segments']['bytes'])}")
    console.print(f"  tts:      {st['tts']['files']:>6} files  {mb(st['tts']['bytes'])}")
    console.print(f"  total:    {mb(st['total_bytes'])}")


@cache_app.command("clear")
def cache_clear(
    kind: Annotated[
        str | None, typer.Argument(help="frames | segments | tts | tmp (default: all)")
    ] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    """Delete cached frames, video segments and/or synthesised speech."""
    from reel.core.cache import CACHE_KINDS, DiskCache

    if kind is not None and kind not in CACHE_KINDS:
        raise typer.BadParameter(
            f"choose one of {', '.join(CACHE_KINDS)} (or nothing for all), not {kind!r}"
        )
    n = DiskCache(cache_dir).clear(kind)
    console.print(f"removed {n} file(s)")


@app.command()
def doctor(
    llm: Annotated[
        str | None,
        typer.Option(
            "--llm",
            help="also test this model with ONE tiny request (e.g. openai, anthropic:claude-sonnet-5-5, "
            "ollama:gemma2:2b); a hosted model costs a fraction of a cent",
        ),
    ] = None,
    tts: Annotated[
        str | None,
        typer.Option(
            "--tts",
            help="also check this online speech engine against your account (elevenlabs): key, plan, "
            "credits left, model and voices; no speech is synthesised, so no credits are spent",
        ),
    ] = None,
) -> None:
    """Check the environment: ffmpeg, skia, fonts, speech engines and local LLMs."""
    import shutil

    from reel.core.ffmpeg import ffmpeg_version

    ok = True
    console.print(f"reel {__version__}  python {sys.version.split()[0]}")
    v = ffmpeg_version()
    console.print(f"ffmpeg:  {v}")
    ok &= not v.startswith("unavailable")
    try:
        import skia

        console.print(f"skia-python: {skia.__version__}")
    except ImportError:
        console.print("[red]skia-python missing[/]")
        ok = False
    from reel.core import fonts

    for role, stack in (
        ("headline", fonts.HEADLINE),
        ("sans", fonts.SANS_BOLD),
        ("marker", fonts.MARKER),
    ):
        tf = fonts.typeface(stack)
        console.print(f"font {role:<9} -> {tf.getFamilyName()}")
    for line in _tts_status(shutil.which("piper")):
        console.print(line)
    for line in _llm_status():
        console.print(line)
    if llm:
        ok &= _llm_ping(llm)
    if tts:
        ok &= _tts_ping(tts)
    console.print(
        f"actions {len(CATALOG.actions)}  styles {len(CATALOG.styles)}  backgrounds {len(CATALOG.backgrounds)}  transitions {len(CATALOG.transitions)}"
    )
    raise typer.Exit(0 if ok else 1)


def _tts_status(piper_exe: str | None) -> list[str]:
    """Which speech engines `--tts auto` can use here, in the order it tries them."""
    from reel.audio.tts import MacSayTTS, PiperTTS

    piper = PiperTTS()
    voices = piper.list_voices() if piper_exe else []
    if piper.available():
        piper_line = (
            f"piper:   ready ({piper_exe}); voices: {', '.join(voices[:4]) or 'none listed'}"
        )
    elif piper_exe:
        piper_line = (
            f"piper:   found at {piper_exe} but no voice model: put a .onnx voice in ~/.local/share/piper "
            "or $PIPER_VOICES (reel never downloads one for you)"
        )
    else:
        piper_line = "piper:   not on PATH (optional: `pip install piper-tts`, then add a voice)"
    say = "ready" if MacSayTTS().available() else "not available (macOS only)"
    engine = "piper" if piper.available() else ("say" if MacSayTTS().available() else "babble")
    return [
        piper_line,
        f"say:     {say}",
        f"babble:  always available (a placeholder voice for tests and lip-sync)  ->  --tts auto uses: {engine}",
        _elevenlabs_status(),
    ]


def _elevenlabs_status() -> str:
    """The online voice, from the environment only (no request is made); only the *name* of the key is ever shown."""
    import os

    from reel.audio.elevenlabs import DEFAULT_MODEL
    from reel.llm.client import llm_env

    env = llm_env()
    if not env.get("ELEVENLABS_API_KEY"):
        return (
            "elevenlabs: no key (optional, online, billed by ElevenLabs): set ELEVENLABS_API_KEY in your shell or a "
            "git-ignored .env, then `--tts elevenlabs`"
        )
    where = "shell" if os.environ.get("ELEVENLABS_API_KEY") else ".env"
    default = (
        "  (REEL_TTS makes it the default)"
        if (env.get("REEL_TTS") or "").lower() in ("elevenlabs", "eleven", "11labs")
        else "  (never used by --tts auto)"
    )
    return f"elevenlabs: key found in {where}; model {env.get('ELEVENLABS_MODEL') or DEFAULT_MODEL}  ->  --tts elevenlabs{default}"


def _tts_ping(name: str) -> bool:
    """Check an online speech engine against the account without synthesising anything. True when it is usable."""
    from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError, get_tts_engine

    try:
        engine = get_tts_engine(name)
        check = getattr(engine, "check", None)
        if check is None:
            console.print(f"tts {engine.name}: runs on this machine, nothing to check")
            return True
        for line in check():
            console.print(escape(line))
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        err.print(f"[red]speech check failed[/] ({escape(name)}): {escape(str(exc))}")
        return False
    return True


@app.command()
def voices(
    tts: Annotated[
        str | None,
        typer.Option(
            "--tts", help="speech engine to list (default: the one --tts auto / REEL_TTS picks)"
        ),
    ] = None,
) -> None:
    """List the voices of a speech engine: use a name (or, for elevenlabs, an id) as characters[].voice or audio.tts_voice."""
    from reel.audio.tts import TTSUnavailableError, UnknownTTSEngineError, get_tts_engine

    try:
        engine = get_tts_engine(tts)
        fetch = getattr(engine, "fetch_voices", None)
        if fetch is None:
            names = engine.list_voices()
            console.print(f"{engine.name}: {len(names)} voice(s)")
            for n in names:
                console.print(f"  {escape(n)}")
            return
        found = fetch()
    except (TTSUnavailableError, UnknownTTSEngineError) as exc:
        err.print(f"[red]cannot list voices:[/] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    console.print(
        f"{engine.name}: {len(found)} voice(s); give a name or an id to characters[].voice"
    )
    for v in sorted(found, key=lambda v: str(v.get("name", "")).lower()):
        labels = v.get("labels") if isinstance(v.get("labels"), dict) else {}
        traits = ", ".join(
            str(labels[k]) for k in ("gender", "age", "accent", "use_case") if labels.get(k)
        )
        console.print(
            f"  {escape(str(v.get('name', '?'))):<22} {escape(str(v.get('voice_id'))):<22} "
            f"{escape(str(v.get('category', '')))}{'  ' + escape(traits) if traits else ''}"
        )


@app.command()
def serve(
    port: Annotated[
        int, typer.Option(help="port to listen on (the next free one is used if it is taken)")
    ] = 8765,
    workspace: Annotated[
        Path | None,
        typer.Option(
            "--workspace", "-w", help="folder for projects and renders (default: ./studio)"
        ),
    ] = None,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="open the web app in your browser")
    ] = False,
    host: Annotated[
        str,
        typer.Option(help="address to listen on; anything but this machine needs --allow-remote"),
    ] = "127.0.0.1",
    allow_remote: Annotated[
        bool,
        typer.Option(
            "--allow-remote",
            help="accept a --host other than this machine (the access token is still required)",
        ),
    ] = False,
    no_token: Annotated[
        bool,
        typer.Option(
            "--no-token",
            help="development only: switch the access token off (never on a shared machine)",
        ),
    ] = False,
    cache_dir: Annotated[
        Path | None, typer.Option("--cache-dir", help="frame / chunk / speech cache folder")
    ] = None,
    plugin: PluginOpt = None,
) -> None:
    """Start Reel Studio: the web app for writing, editing, voicing and exporting reels (local only)."""
    import os
    import secrets
    import socket
    import threading
    import webbrowser

    try:
        import uvicorn

        from reel.server.app import create_app
        from reel.server.config import LOOPBACK_NAMES, ServerConfig
    except ImportError as exc:
        err.print(
            f"[red]Reel Studio needs the web packages:[/] {escape(str(exc))}\n"
            'install them with  [bold]pip install -e ".[ui]"[/]  (or  uv pip install fastapi uvicorn)'
        )
        raise typer.Exit(1) from exc
    if host not in (*LOOPBACK_NAMES, "::1") and not allow_remote:
        err.print(
            f"[red]refusing to listen on {escape(host)}:[/] Reel Studio is for this machine only. "
            "Use --allow-remote if you really mean to share it (the access token is still required)."
        )
        raise typer.Exit(2)
    _plugins(plugin)
    if cache_dir is not None:
        os.environ["REEL_CACHE_DIR"] = str(cache_dir)
    chosen = port
    for candidate in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if probe.connect_ex((host if host != "localhost" else "127.0.0.1", candidate)) != 0:
                chosen = candidate
                break
    else:
        err.print(f"[red]ports {port}-{port + 19} are all in use[/]; choose another with --port")
        raise typer.Exit(1)
    token = None if no_token else secrets.token_urlsafe(24)
    config = ServerConfig(
        workspace=(workspace or Path("studio")).expanduser(),
        host=host,
        port=chosen,
        token=token,
        allow_remote=allow_remote,
    )
    app_ = create_app(config, plugins=tuple(plugin or ()))
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{chosen}/" + (
        f"?token={token}" if token else ""
    )
    console.print(f"[bold]Reel Studio[/]  {escape(url)}")
    console.print(f"  workspace  {escape(str(config.workspace.resolve()))}")
    if config.static_dir is None:
        console.print(
            "  [yellow]the web app is not built yet:[/] run `make ui-build` (the API is available already)"
        )
    if token:
        console.print(
            "  [dim]the link carries an access token: keep it private. Ctrl-C stops the server.[/]"
        )
    else:
        console.print("  [yellow]--no-token: anyone who can reach this port can use the server[/]")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    uvicorn.run(app_, host=host, port=chosen, log_level="warning", access_log=False)


def _llm_status() -> list[str]:
    """What `reel build --llm ...` could use right now (nothing here is required: the planner is offline).

    Only the *names* of keys are shown, never a value."""
    import os

    from reel.cli.build import ollama_models
    from reel.llm.client import OLLAMA_URL, display_host, llm_env

    lines = []
    local = ollama_models(timeout=0.7)
    if local is None:
        lines.append(
            "ollama:  not running (optional: `reel build` also works with the offline planner)"
        )
    else:
        shown = ", ".join(local[:4]) + (" ..." if len(local) > 4 else "")
        lines.append(
            f"ollama:  running at {OLLAMA_URL}  local models: {shown or 'none pulled yet'}"
        )
        try:  # models whose name ends in -cloud run on Ollama's servers: the script would leave this machine
            import httpx

            allm = [
                m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=0.7).json()["models"]
            ]
            hosted = [m for m in allm if m.endswith("-cloud")]
            if hosted:
                lines.append(f"         hosted (-cloud, not local): {', '.join(hosted[:3])}")
        except Exception:
            pass
    env = llm_env()
    found = [
        f"{k} ({'shell' if os.environ.get(k) else '.env'})"
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
        if env.get(k)
    ]
    lines.append(
        f"api keys: {', '.join(found)}  ->  --llm openai / --llm anthropic"
        if found
        else "api keys: none (set OPENAI_API_KEY / ANTHROPIC_API_KEY, or put them in a git-ignored .env; only hosted models need one)"
    )
    if env.get("OPENAI_BASE_URL"):
        lines.append(
            f"OPENAI_BASE_URL: {escape(display_host(env['OPENAI_BASE_URL']))}  (host only)"
        )
    if env.get("REEL_LLM"):
        llm = env["REEL_LLM"].partition("@")[
            0
        ]  # a URL after @ may carry credentials: show the model, not the URL
        at = "@..." if "@" in env["REEL_LLM"] else ""
        lines.append(f"REEL_LLM: {escape(llm)}{at}  (default model for `reel build`)")
    return lines


def _llm_ping(spec: str) -> bool:
    """One tiny real request (~100 tokens) to check key, model name and connectivity. True when it worked."""
    import time

    from reel.llm.client import LLMError, Message, get_client, llm_env

    try:
        client = get_client(spec, env=llm_env())
        t0 = time.perf_counter()
        reply = client.complete(
            "You are a connectivity test for a video tool. Answer with a single word.",
            [Message("user", "Reply with exactly: OK")],
            temperature=0.0,
            max_tokens=256,
        )
    except LLMError as exc:
        err.print(f"[red]model test failed[/] ({escape(spec)}): {escape(str(exc))}")
        return False
    dest = getattr(client, "destination", "")
    console.print(
        f"[green]model test ok[/]  {escape(client.name)}{' at ' + escape(dest) if dest else ''} answered "
        f"{escape(repr(reply.strip()[:30]))} in {time.perf_counter() - t0:.1f}s"
    )
    return True


# ------------------------------------------------------------------------------- scaffolding / debug
@app.command("new-action")
def new_action(
    name: Annotated[
        str, typer.Argument(help="action name, e.g. moonwalk (lowercase, underscores)")
    ],
    category: Annotated[
        str, typer.Option(help="locomotion | gesture | expression | posture")
    ] = "gesture",
    directory: Annotated[
        Path | None,
        typer.Option(
            "--dir", help="where to write it (default: src/reel/actions, or ./reel_plugins)"
        ),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing file")] = False,
    plugin: PluginOpt = None,
) -> None:
    """Scaffold a new action file with a params model and a worked keyframe example."""
    from reel.cli.scaffold import scaffold_action, source_checkout_root

    _plugins(plugin)
    if name in CATALOG.actions:
        err.print(
            f"[red]action {name!r} already exists.[/] Registered actions: {', '.join(CATALOG.actions.names())}"
        )
        raise typer.Exit(1)
    try:
        path = scaffold_action(name, category=category, directory=directory, force=force)
    except (ValueError, FileExistsError) as exc:
        err.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]created[/] {path}")
    in_tree = source_checkout_root() is not None and directory is None
    console.print("\nnext:")
    console.print(f"  1. edit {path.name}: the keyframes in the body are a worked example")
    console.print(
        f"  2. preview it:   reel debug-actions {name} --out {name}_sheet.png"
        + ("" if in_tree else f" --plugin {path.parent}")
    )
    console.print(f'  3. use it:       "actions": [{{"name": "{name}", "t0": 1.0, "t1": 2.5}}]')
    if not in_tree:
        console.print(
            f"  (outside the source tree, load it with --plugin {path.parent} or REEL_PLUGINS={path.parent})"
        )


@app.command("new-style")
def new_style(
    name: Annotated[str, typer.Argument(help="style name, e.g. watercolor")],
    directory: Annotated[
        Path | None, typer.Option("--dir", help="parent folder for the style package")
    ] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    plugin: PluginOpt = None,
) -> None:
    """Scaffold a new style pack folder (subclass StylePack, register it, done)."""
    from reel.cli.scaffold import scaffold_style

    _plugins(plugin)
    if name in CATALOG.styles:
        err.print(f"[red]style {escape(repr(name))} already exists.[/]")
        raise typer.Exit(1)
    try:
        path = scaffold_style(name, directory=directory, force=force)
    except (ValueError, FileExistsError) as exc:
        err.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]created[/] {path}")


@app.command("debug-backgrounds")
def debug_backgrounds(
    names: Annotated[
        list[str] | None, typer.Argument(help="templates to draw (default: all)")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("backgrounds_sheet.png"),
    style: Annotated[str, typer.Option("--style", "-s")] = "flat_vector",
    params: Annotated[
        str | None, typer.Option("--params", help="extra template params as JSON")
    ] = None,
    scale: Annotated[float, typer.Option(help="cell scale (1.0 = 1080x1920)")] = 0.3,
    at: Annotated[float, typer.Option("--at", help="scene time in seconds")] = 1.0,
    plugin: PluginOpt = None,
) -> None:
    """Contact sheet of background templates at day/dusk/night/gloomy with two characters on the set."""
    from reel.core.debug import render_background_sheet

    _plugins(plugin)
    chosen = names or CATALOG.backgrounds.names()
    unknown = [n for n in chosen if n not in CATALOG.backgrounds]
    if unknown:
        err.print(
            f"[red]unknown background(s): {', '.join(unknown)}[/]  registered: {', '.join(CATALOG.backgrounds.names())}"
        )
        raise typer.Exit(1)
    path = render_background_sheet(
        chosen, out, style=style, params=json.loads(params) if params else None, scale=scale, t=at
    )
    console.print(f"wrote {path}  ({len(chosen)} templates)")


@app.command("debug-actions")
def debug_actions(
    names: Annotated[
        list[str] | None, typer.Argument(help="actions to draw (default: all)")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="PNG contact sheet")] = Path(
        "actions_sheet.png"
    ),
    archetype: Annotated[str, typer.Option(help="body to use")] = "everyman",
    plugin: PluginOpt = None,
) -> None:
    """Draw a skeleton contact sheet of actions (style-free) to check motion before styling."""
    from reel.core.debug import render_action_sheet

    _plugins(plugin)
    chosen = names or CATALOG.actions.names()
    unknown = [n for n in chosen if n not in CATALOG.actions]
    if unknown:
        err.print(
            f"[red]unknown action(s): {', '.join(unknown)}[/]  registered: {', '.join(CATALOG.actions.names())}"
        )
        raise typer.Exit(1)
    path = render_action_sheet(chosen, out, archetype=archetype)
    console.print(f"wrote {path}  ({len(chosen)} actions)")


if __name__ == "__main__":  # pragma: no cover
    app()
