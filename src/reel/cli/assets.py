"""`reel assets ...`: see, add, check and preview the asset library (characters, objects and places you can use in scenes)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from reel.assets.library import (
    BUILTIN_DIR,
    asset_dirs_from_env,
    asset_from_files,
    discover,
)
from reel.assets.model import ART_EXT, KIND_HEIGHT, KINDS, AssetKind, slug
from reel.core.catalog import CATALOG

assets_app = typer.Typer(
    name="assets",
    help="The asset library: characters, objects and places a script can use. List them, add your own, check and preview them.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err = Console(stderr=True)

AssetsOpt = Annotated[
    list[str] | None,
    typer.Option("--assets", help="An extra folder of assets (repeatable; also REEL_ASSETS=a:b)"),
]
FOLDER = {"character": "characters", "object": "objects", "place": "places"}


def effective_dirs(extra: list[str] | None, *, near: Path | None = None) -> list[str]:
    """The user's asset folders for a command: ``REEL_ASSETS``, an ``assets/`` folder next to the spec (or, for a Studio
    project, in its workspace), then ``--assets``."""
    dirs: list[str] = list(asset_dirs_from_env())
    if near is not None:
        here = near.resolve().parent
        # a project of Reel Studio lives in <workspace>/projects/: its library is <workspace>/assets
        for side in (here / "assets", here.parent / "assets" if here.name == "projects" else None):
            if side is not None and side.is_dir():
                dirs.append(str(side))
    dirs += list(extra or [])
    out: list[str] = []
    for d in dirs:
        r = str(Path(d).expanduser().resolve())
        if r not in out:
            out.append(r)
    return out


def load_assets(extra: list[str] | None, *, near: Path | None = None) -> list[str]:
    """Load the asset folders of a command into the catalog (a problem is printed, never raised); returns the folders."""
    dirs = effective_dirs(extra, near=near)
    for problem in CATALOG.load_assets(dirs):
        err.print(f"[yellow]assets:[/] {problem}")
    return dirs


def _rows(kind: str | None, only_user: bool) -> list[dict[str, Any]]:
    CATALOG.assets.names()  # make sure the catalog is loaded
    rows = []
    for e in CATALOG.assets.entries():
        a = e.obj
        if kind and a.kind != kind:
            continue
        if only_user and a.origin != "user":
            continue
        rows.append(a.to_dict())
    return rows


@assets_app.command("list")
def list_(
    kind: Annotated[str | None, typer.Option("--kind", help="character | object | place")] = None,
    mine: Annotated[bool, typer.Option("--mine", help="only the assets you added")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="machine-readable")] = False,
    assets: AssetsOpt = None,
) -> None:
    """List the library: the built-in assets and the ones in your folders."""
    load_assets(assets)
    if kind and kind not in KINDS:
        err.print(f"[red]unknown kind {kind!r}[/]: choose from {', '.join(KINDS)}")
        raise typer.Exit(2)
    rows = _rows(kind, mine)
    if as_json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return
    table = Table(box=box.SIMPLE_HEAD, title=f"asset library ({len(rows)})", title_justify="left")
    table.add_column("name", style="bold")
    table.add_column("kind", style="magenta")
    table.add_column("from")
    table.add_column("height", justify="right")
    table.add_column("a script can say it as", overflow="fold")
    for r in rows:
        tags = [t for t in r["tags"] if t != str(r["name"]).replace("_", " ")][:6]
        table.add_row(
            str(r["name"]),
            str(r["kind"]),
            "you" if r["origin"] == "user" else "built in",
            f"{r['height']:g}",
            ", ".join(tags),
        )
    console.print(table)
    if not mine and not any(r["origin"] == "user" for r in rows):
        console.print(
            "[dim]No assets of your own yet: `reel assets add FILE` adds a picture or drawing (docs/assets.md).[/]"
        )


@assets_app.command("add")
def add(
    file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="an SVG, PNG, JPG or WebP")
    ],
    name: Annotated[
        str | None, typer.Option("--name", help="id used in specs (default: the file name)")
    ] = None,
    kind: Annotated[str, typer.Option("--kind", help="character | object | place")] = "object",
    tags: Annotated[
        str,
        typer.Option(
            "--tags", help="words a script uses for it, comma separated (other languages welcome)"
        ),
    ] = "",
    summary: Annotated[str, typer.Option("--summary", help="one line: what it is")] = "",
    height: Annotated[
        float | None,
        typer.Option(
            "--height", help="how tall it is drawn, design px at scale 1 (a person is 575)"
        ),
    ] = None,
    anchor: Annotated[
        str,
        typer.Option(
            "--anchor", help="where it stands, as X,Y fractions of its box (0.5,1 = bottom middle)"
        ),
    ] = "0.5,1",
    facing: Annotated[
        str, typer.Option("--facing", help="which way the art looks: right | left | none")
    ] = "right",
    cutout: Annotated[
        bool | None,
        typer.Option(
            "--cutout/--no-cutout",
            help="pictures: remove a plain background (default: when there is no transparency)",
        ),
    ] = None,
    credit: Annotated[str, typer.Option("--credit", help="who made it, and the licence")] = "",
    to: Annotated[
        Path | None,
        typer.Option("--to", help="the library folder to add it to (default: ./assets)"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="replace an asset of the same name")
    ] = False,
) -> None:
    """Add a picture or drawing to your library (copies it, with a small sidecar file, into ./assets)."""
    if kind not in KINDS:
        err.print(f"[red]unknown kind {kind!r}[/]: choose from {', '.join(KINDS)}")
        raise typer.Exit(2)
    ext = file.suffix.lower()
    if ext not in ART_EXT:
        err.print(
            f"[red]{file.name} is not a picture or drawing[/]: use one of {', '.join(ART_EXT)}"
        )
        raise typer.Exit(2)
    try:
        ax, ay = (float(v) for v in anchor.split(","))
    except ValueError:
        err.print("[red]--anchor must look like 0.5,1[/]")
        raise typer.Exit(2) from None
    nm = name or slug(file.stem)
    root = (to or Path("assets")).expanduser()
    dest_dir = root / FOLDER[kind]
    dest = dest_dir / f"{nm}{ext}"
    sidecar = dest.with_suffix(".json")
    manifest: dict[str, object] = {
        "name": nm,
        "kind": kind,
        "summary": summary or nm.replace("_", " "),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "anchor": [ax, ay],
        "facing": facing,
    }
    if height:
        manifest["height"] = height
    if cutout is not None:
        manifest["cutout"] = cutout
    if credit:
        manifest["credit"] = credit
    if dest.exists() and not force:
        err.print(
            f"[red]{dest} already exists[/]: pass --force to replace it, or choose another --name"
        )
        raise typer.Exit(1)
    # try it in a scratch place first: nothing lands in the library unless it can be read
    import tempfile

    from reel.assets.art import ArtError, forget, load_art

    with tempfile.TemporaryDirectory(prefix="reel-asset-") as td:
        tmp = Path(td) / f"{nm}{ext}"
        shutil.copyfile(file, tmp)
        (Path(td) / f"{nm}.json").write_text(json.dumps(manifest), encoding="utf-8")
        trial = None
        try:
            trial = asset_from_files(
                tmp,
                root=Path(td),
                origin="user",
                manifest_path=Path(td) / f"{nm}.json",
                kind_hint=kind,
            )
            art = load_art(trial)
        except (ValueError, ArtError) as exc:
            err.print(f"[red]cannot add {file.name}:[/] {exc}")
            raise typer.Exit(1) from None
        finally:
            if trial is not None:
                forget(trial)
        notes = [*art.notes, *art.warnings]
    taken = [
        r
        for r in ("object", "archetype", "background")
        if nm in CATALOG.registry(r) and nm not in CATALOG.assets
    ]
    if taken:
        err.print(
            f"[red]the name {nm!r} is taken by the engine's own {taken[0]}[/]: choose another with --name"
        )
        raise typer.Exit(1)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file, dest)
    sidecar.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    console.print(f"[green]added[/] [bold]{nm}[/] ({kind}) → {dest}")
    for n in notes:
        console.print(f"  [yellow]•[/] {n}")
    h = height or KIND_HEIGHT[kind]
    console.print(
        f"  drawn {h:g} design px tall (a person is 575): change it with --height, or in {sidecar.name}"
    )
    console.print(
        f"  use it in a spec: [cyan]{_how(kind, nm)}[/]   — the folder {root.resolve()} is read when it is `assets/` next to the spec, or via --assets / REEL_ASSETS"
    )


def _how(kind: str, name: str) -> str:
    if kind == "character":
        return f'"characters": [{{"id": "pal", "archetype": "{name}"}}]'
    if kind == "place":
        return f'"background": {{"template": "{name}"}}'
    return f'"objects": [{{"asset": "{name}", "position": [0.5, 0.8]}}]'


@assets_app.command("remove")
def remove(
    name: Annotated[str, typer.Argument(help="the asset's name")],
    from_: Annotated[
        Path | None, typer.Option("--from", help="the library folder (default: ./assets)")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="do not ask")] = False,
) -> None:
    """Remove one of your assets (the built-in ones cannot be removed)."""
    root = (from_ or Path("assets")).expanduser()
    found = [a for a in discover(root, origin="user").assets if a.name == name]
    if not found:
        in_builtin = any(a.name == name for a in discover(BUILTIN_DIR, origin="builtin").assets)
        err.print(
            f"[red]{name!r} is not in {root}[/]"
            + (" (it is a built-in asset, which cannot be removed)" if in_builtin else "")
        )
        raise typer.Exit(1)
    a = found[0]
    if not yes and not typer.confirm(f"Delete {a.path.name} from {root}?"):
        raise typer.Exit(1)
    for f in a.files():
        f.unlink(missing_ok=True)
    console.print(f"[green]removed[/] {name}")


@assets_app.command("coverage")
def coverage(
    script: Annotated[str, typer.Argument(help="script text file, or - for stdin")],
    as_json: Annotated[bool, typer.Option("--json", help="machine-readable")] = False,
    assets: AssetsOpt = None,
) -> None:
    """Read a script and say which characters, places and objects the library can draw and which it lacks."""
    from reel.assets.coverage import analyze_script

    near = Path(script) if script != "-" and Path(script).is_file() else None
    load_assets(assets, near=near)
    if script == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(script).read_text(encoding="utf-8")
        except OSError as exc:
            err.print(f"[red]cannot read {script}:[/] {exc.strerror or exc}")
            raise typer.Exit(2) from None
    cov = analyze_script(text)
    if as_json:
        sys.stdout.write(json.dumps(cov.to_dict(), indent=2, ensure_ascii=False) + "\n")
        return
    if cov.covered:
        table = Table(box=box.SIMPLE_HEAD, title="the library can draw", title_justify="left")
        table.add_column("asset", style="bold")
        table.add_column("kind", style="magenta")
        table.add_column("from")
        table.add_column("the script says", overflow="fold")
        for c in cov.covered:
            table.add_row(
                c.asset,
                c.kind,
                {"user": "you", "builtin": "built in", "engine": "the engine"}.get(
                    c.source, c.source
                ),
                ", ".join(c.words) + (f"  ×{c.count}" if c.count > 1 else ""),
            )
        console.print(table)
    if not cov.missing:
        console.print("[green]Nothing the script mentions is missing from the library.[/]")
        return
    console.print(f"[bold yellow]Not in the library ({len(cov.missing)}):[/]")
    for m in cov.missing:
        console.print(
            f"  [magenta]{m.kind:<10}[/] [bold]{m.name}[/]  [dim]the script says {', '.join(m.words)}"
            + (f" ×{m.count}" if m.count > 1 else "")
            + "[/]"
        )
        if m.snippet:
            console.print(f"             [dim italic]{m.snippet}[/]")
        console.print(
            f"             add it: [green]reel assets add FILE --kind {m.kind} --name {slug(m.name)}[/]"
        )


@assets_app.command("fill")
def fill(
    spec: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="scene spec JSON")],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="write the updated spec here")
    ] = None,
    write: Annotated[bool, typer.Option("--write", help="update the spec file itself")] = False,
    assets: AssetsOpt = None,
) -> None:
    """Make a spec use library assets it was missing when it was written (its `meta.library_gaps`).

    A planner that could not find a dragon, a village or a rickshaw used a stand-in and noted the gap. Once you have
    added the asset, this swaps it in: the stand-in character becomes the new one, the scenes get the new place, the
    missing object is placed. Without --out or --write it only shows what would change.
    """
    from reel.assets.gaps import apply_library_gap, resolve_gap

    load_assets(assets, near=spec)
    try:
        data = json.loads(spec.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        err.print(f"[red]{spec.name} is not valid JSON:[/] {exc}")
        raise typer.Exit(1) from None
    gaps = [g for g in ((data.get("meta") or {}).get("library_gaps") or []) if isinstance(g, dict)]
    if not gaps:
        console.print("This spec has no library gaps: nothing to fill.")
        return
    filled, waiting = [], []
    for g in gaps:
        have = resolve_gap(g, CATALOG)
        if have is not None and apply_library_gap(data, g, have, CATALOG):
            filled.append((g, have))
        else:
            waiting.append(g)
    for g, have in filled:
        console.print(f"[green]filled[/] {g['kind']} [bold]{g['name']}[/] -> {have.name}")
    for g in waiting:
        console.print(
            f"[yellow]still missing[/] {g['kind']} [bold]{g['name']}[/]: "
            f"`reel assets add FILE --kind {g['kind']} --name {slug(str(g['name']))}`"
        )
    target = out or (spec if write else None)
    if target and filled:
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        console.print(f"wrote {target}")
    elif filled:
        console.print("[dim]dry run: pass --write (or --out FILE) to save the changes[/]")


@assets_app.command("check")
def check(
    dirs: Annotated[
        list[Path] | None, typer.Argument(help="library folders (default: your asset folders)")
    ] = None,
    assets: AssetsOpt = None,
) -> None:
    """Read every asset of a library and report what is wrong or will not be drawn."""
    from reel.assets.art import ArtError, load_art

    targets = [str(d) for d in (dirs or [])] or effective_dirs(assets, near=Path.cwd() / "x")
    if not targets:
        console.print(
            "[dim]No asset folders: add one with --assets, REEL_ASSETS, or an ./assets folder.[/]"
        )
        return
    bad = 0
    n = 0
    for d in targets:
        found = discover(Path(d), origin="user")
        for p in found.problems:
            err.print(f"[red]✘[/] {p}")
            bad += 1
        for a in found.assets:
            n += 1
            try:
                art = load_art(a)
            except ArtError as exc:
                err.print(f"[red]✘[/] {a.name}: {exc}")
                bad += 1
                continue
            for w in [*art.warnings, *art.notes]:
                console.print(f"[yellow]•[/] {a.name}: {w}")
    console.print(f"{n} asset(s) read, {bad} problem(s)")
    raise typer.Exit(1 if bad else 0)


@assets_app.command("preview")
def preview(
    names: Annotated[
        list[str] | None, typer.Argument(help="asset names (default: all of them)")
    ] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="the PNG to write")] = Path(
        "assets_preview.png"
    ),
    styles: Annotated[
        str, typer.Option("--styles", help="comma separated")
    ] = "paper_cutout,flat_vector,stickman",
    kind: Annotated[str | None, typer.Option("--kind", help="character | object | place")] = None,
    true_scale: Annotated[
        bool,
        typer.Option(
            "--true-scale",
            help="show objects and characters at the size a scene gives them, next to a person",
        ),
    ] = False,
    tile: Annotated[
        float, typer.Option("--tile", help="size of one picture (0.2 = 216x384)")
    ] = 0.2,
    assets: AssetsOpt = None,
) -> None:
    """Draw a contact sheet of assets in the three styles (what a scene would show)."""
    from PIL import Image

    from reel.assets.preview import render_preview

    load_assets(assets)
    pool = [
        e.obj
        for e in CATALOG.assets.entries()
        if (not names or e.name in names) and (not kind or e.obj.kind == kind)
    ]
    if names:
        missing = [n for n in names if n not in CATALOG.assets]
        if missing:
            err.print(f"[red]not in the library:[/] {', '.join(missing)}")
            raise typer.Exit(1)
    if not pool:
        err.print("[red]nothing to preview[/]")
        raise typer.Exit(1)
    sty = [s.strip() for s in styles.split(",") if s.strip()]
    tiles: list[list[Image.Image]] = []
    for a in pool:
        row = []
        for s in sty:
            arr = render_preview(a, s, scale=tile, true_scale=true_scale)
            row.append(Image.fromarray(arr[..., [2, 1, 0]]))
        tiles.append(row)
    w, h = tiles[0][0].size
    cols = max(1, min(len(pool), 4))
    rows_n = -(-len(pool) // cols)
    cell_w, cell_h = (w + 4) * len(sty) + 10, h + 28
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows_n), (28, 30, 38))
    from PIL import ImageDraw

    dr = ImageDraw.Draw(sheet)
    for i, (a, row) in enumerate(zip(pool, tiles, strict=True)):
        cx, cy = (i % cols) * cell_w, (i // cols) * cell_h
        dr.text((cx + 6, cy + 6), f"{a.name} · {a.kind}", fill=(230, 232, 240))
        for j, im in enumerate(row):
            sheet.paste(im, (cx + 4 + j * (w + 4), cy + 24))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    console.print(f"wrote {out}  ({len(pool)} assets × {len(sty)} styles)")


__all__ = ["AssetKind", "assets_app", "effective_dirs", "load_assets"]
