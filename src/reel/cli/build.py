"""`reel build`: script text -> scene spec (LLM or offline planner) -> lint -> render.

Stage 1 (script -> JSON) is optional and swappable; stage 2 (JSON -> video) never needs it.  This
module only chooses a :class:`~reel.llm.base.SpecGenerator`, writes the spec it returns next to the
video (so it can be edited by hand and re-rendered with ``reel render``) and hands over to the same
render flow as ``reel render``.  Every failure ends in a message that says what to do next.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from reel.cli.pipeline import RenderRequest, run_render
from reel.cli.report import print_report

if TYPE_CHECKING:
    from reel.llm.base import SpecGenerationError, SpecGenerator

#: where each provider's requests normally go; anything else is shown in the build output
_DEFAULT_HOSTS = {"api.openai.com", "api.anthropic.com", "localhost:11434"}

#: the environment variable that selects a model when ``--llm`` is not given
LLM_ENV = "REEL_LLM"


@dataclass
class BuildRequest:
    script: str  # a file path, or "-" for stdin
    style: str = "paper_cutout"
    out: Path | None = None
    llm: str | None = None
    no_llm: bool = False
    spec_out: Path | None = None
    spec_only: bool = False
    duration: float = 50.0
    repairs: int = 3
    compact: bool | None = None  # the short prompt for small-context models; None = automatic
    enrich: bool = (
        True  # fill in what a weak model left empty (gestures, camera, sound, caption timing)
    )
    verbatim: bool = (
        True  # the captions are the script's own words, in order (a model's rewording is put right)
    )
    seed: int = 7
    render: RenderRequest = field(default_factory=lambda: RenderRequest(spec_path=Path()))


_NO_MODEL_HELP = """\
Options:
  • start the server (for Ollama: [bold]ollama serve[/] and [bold]ollama pull <model>[/]) or point --llm at one that is running
  • build without a model:  [bold]reel build {script} --no-llm[/]   (the offline rule-based planner)
  • write the JSON yourself: copy examples/story_50s.json, edit it, then [bold]reel render spec.json[/]"""


def _read_script(path: str, err: Console) -> str | None:
    try:
        if path == "-":
            text = sys.stdin.read()
        else:
            text = Path(path).read_text(
                encoding="utf-8-sig"
            )  # tolerate an editor's byte-order mark
    except UnicodeDecodeError:
        err.print(
            "[red]the script is not valid UTF-8 text.[/] Save it as UTF-8 (Hindi, accents and emoji are all fine in UTF-8) and try again."
        )
        return None
    except OSError as exc:
        err.print(
            f"[red]cannot read the script {escape(path)}:[/] {escape(str(exc.strerror or exc))}"
        )
        return None
    if not text.strip():
        err.print("[red]the script is empty;[/] write at least a few sentences")
        return None
    return text


def ollama_models(timeout: float = 0.6) -> list[str] | None:
    """The models a local Ollama server has pulled (hosted ``-cloud`` ones excluded), or ``None`` if none is running."""
    import httpx

    from reel.llm.client import OLLAMA_URL

    try:
        data = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=timeout).json()
        return [m["name"] for m in data["models"] if not m["name"].endswith("-cloud")]
    except Exception:  # not running, or not Ollama: both are fine
        return None


def ready_models(env: dict[str, str]) -> list[str]:
    """``--llm`` values that would work right now: a key is set for the hosted ones, a server answers for local ones."""
    ready = []
    if env.get("OPENAI_API_KEY"):
        ready.append("openai (key found)")
    if env.get("ANTHROPIC_API_KEY"):
        ready.append("anthropic (key found)")
    local = ollama_models()
    if local:
        ready.append(f"ollama:{local[0]}")
    return ready


def _make_generator(req: BuildRequest, console: Console) -> tuple[SpecGenerator, str]:
    """The generator to use and a short label for it (raises ``LLMConfigError`` for a bad --llm)."""
    from reel.llm.client import get_client, llm_env
    from reel.llm.generator import LLMSpecGenerator
    from reel.llm.heuristic import HeuristicSpecGenerator

    env = {} if req.no_llm else llm_env()  # the shell environment over a git-ignored .env file
    wanted = None if req.no_llm else (req.llm or env.get(LLM_ENV) or None)
    if wanted is None:
        if not req.no_llm:
            ready = ready_models(env)
            console.print(
                "[dim]no model selected: using the offline planner. For a model-written spec add --llm "
                + (
                    "[/][bold]" + "[/], [bold]".join(f"--llm {r}" for r in ready) + "[/][dim]"
                    if ready
                    else f"ollama:llama3.1 (or openai, anthropic), or set {LLM_ENV}"
                )
                + ".[/]"
            )
        return HeuristicSpecGenerator(), "the offline planner"
    client = get_client(wanted, env=env)
    generator = LLMSpecGenerator(
        client,
        max_repairs=req.repairs,
        compact=req.compact,
        enrich=req.enrich,
        verbatim=req.verbatim,
    )
    dest = getattr(client, "destination", "")
    where = (
        f" -> {escape(dest)}" if dest and dest not in _DEFAULT_HOSTS else ""
    )  # never silent about a custom URL
    return generator, f"[bold]{escape(client.name)}[/]{where}"


def _keep_failed_attempt(exc: SpecGenerationError, spec_path: Path, err: Console) -> None:
    """Save what a failed generation produced so it can be fixed by hand and rendered."""
    kept: list[Path] = []
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    if exc.spec is not None:
        failed = spec_path.with_name(f"{spec_path.stem}.failed.json")
        failed.write_text(
            json.dumps(exc.spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        kept.append(failed)
    if exc.raw:
        raw = spec_path.with_name(f"{spec_path.stem}.raw.txt")
        raw.write_text(exc.raw, encoding="utf-8")
        kept.append(raw)
    if kept:
        err.print(
            "kept what the generator produced, for fixing by hand: "
            + ", ".join(str(k) for k in kept)
            + "\nthen run [bold]reel lint <spec.json>[/] and [bold]reel render <spec.json>[/]"
        )


def run_build(req: BuildRequest, console: Console, err: Console) -> int:
    """Run the whole flow; returns a process exit code (errors become messages, not tracebacks)."""
    from reel.llm.base import SpecGenerationError
    from reel.llm.client import LLMConfigError, LLMError, LLMUnavailable

    text = _read_script(req.script, err)
    if text is None:
        return 2
    stem = "script" if req.script == "-" else Path(req.script).stem
    out = req.out or Path("out") / f"{stem}.mp4"
    spec_path = req.spec_out or out.with_name(f"{out.stem}.spec.json")

    try:
        generator, label = _make_generator(req, console)
    except LLMConfigError as exc:
        err.print(f"[red]{escape(str(exc))}[/]")
        return 2

    console.print(f"[bold]script → spec[/] with {label} …")
    try:
        result = generator.generate(
            text, style=req.style, seed=req.seed, target_duration=req.duration
        )
    except LLMUnavailable as exc:
        err.print(f"[red]the language model is not available:[/] {escape(str(exc))}\n")
        err.print(_NO_MODEL_HELP.format(script=req.script))
        return 2
    except LLMError as exc:
        err.print(f"[red]the language model failed:[/] {escape(str(exc))}")
        return 2
    except SpecGenerationError as exc:
        err.print(f"[red]{escape(exc.message)}[/]")
        if exc.lint is not None:
            print_report(console, exc.lint)
        _keep_failed_attempt(exc, spec_path, err)
        return 1

    for note in result.notes:
        console.print(f"[dim]  · {escape(note)}[/]")
    spec_path = result.write(spec_path)
    summary = f"[green]wrote[/] {spec_path}  {len(result.spec.get('scenes', []))} scenes"
    if result.lint.total_sec is not None:
        summary += f" · {result.lint.total_sec:.1f}s"
    calls = f", {result.attempts} model calls" if result.attempts > 1 else ""
    console.print(f"{summary}  ({result.generator}{calls})")
    for issue in result.lint.warnings:
        console.print(f"[yellow]lint warning:[/] {escape(issue.path)}: {escape(issue.message)}")
    if req.spec_only:
        return 0

    req.render.spec_path = spec_path
    req.render.out = out
    return run_render(req.render, console, err)
