"""Rich rendering of a LintReport (the human view of `reel lint`)."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from reel.core.lint import LintReport, Severity

_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "cyan"}
_LABEL = {Severity.ERROR: "ERROR", Severity.WARNING: "warn", Severity.INFO: "note"}

_ADD_HOW = {
    "action": "reel new-action {name}",
    "background": "src/reel/templates/{name}.py  (@register_background)",
    "style": "reel new-style {name}",
    "transition": "core/transitions.py  (@register_transition)",
    "easing": "core/easing.py  (@register_easing)",
    "camera_move": "core/camera.py  (@register_camera_move)",
    "sfx": "audio/sfx.py  (@register_sfx)  or  assets/sfx/{name}.wav",
    "archetype": "core/archetypes.py",
    "prop": "core/archetypes.py",
    "caption_style": "core/captions.py  (@register_caption_style)",
}


def print_report(console: Console, report: LintReport) -> None:
    title = report.source or "spec"
    bits = []
    if report.n_scenes is not None:
        bits.append(f"{report.n_scenes} scenes")
    if report.total_sec is not None:
        bits.append(f"{report.total_sec:.2f}s")
    console.print(
        Panel.fit(
            Text(title, style="bold") + Text("   " + " · ".join(bits), style="dim"),
            title="reel lint",
            border_style="red" if report.errors else ("yellow" if report.warnings else "green"),
        )
    )

    for i in report.sorted_issues():
        head = Text()
        head.append(f" {_LABEL[i.severity]:<5}", style=_STYLE[i.severity])
        head.append(f" {i.code:<22}", style="dim")
        head.append(i.path or "(root)", style="cyan")
        console.print(head, soft_wrap=True)
        console.print(Text("       " + i.message), soft_wrap=True)
        if i.hint:
            console.print(Text("       → " + i.hint, style="dim italic"), soft_wrap=True)
    if report.issues:
        console.print()

    missing = report.missing()
    if missing:
        console.print(
            Text("Missing from the registry — add these (or fix the spec):", style="bold red")
        )
        for kind, names in sorted(missing.items()):
            for name, paths in sorted(names.items()):
                line = Text("  ")
                line.append(f"{kind.replace('_', ' '):<14}", style="magenta")
                line.append(f"{name}", style="bold")
                line.append(f"  ×{len(paths)}", style="dim")
                console.print(line, soft_wrap=True)
                console.print(Text(f"      used at {paths[0]}", style="cyan"), soft_wrap=True)
                console.print(
                    Text(
                        "      add it:  " + _ADD_HOW.get(kind, "").format(name=name), style="green"
                    ),
                    soft_wrap=True,
                )
        console.print()

    summary = Text()
    summary.append(f"{len(report.errors)} error(s)", style="bold red" if report.errors else "green")
    summary.append(
        f", {len(report.warnings)} warning(s)", style="yellow" if report.warnings else "dim"
    )
    summary.append(f", {len(report.infos)} note(s)", style="dim")
    summary.append(
        "   " + ("✔ OK" if report.ok else "✘ FAILED"),
        style="bold green" if report.ok else "bold red",
    )
    console.print(summary)
