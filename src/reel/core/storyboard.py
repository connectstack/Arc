"""`reel storyboard`: a self-contained HTML contact sheet of a spec.

Renders a few key frames per scene (through the normal renderer, so the frame cache makes repeats
instant), then writes one HTML file with a proportional timeline, a card per scene (thumbnails,
captions, actions, camera, transition) and the lint report.  Useful to review an LLM-written spec
in seconds before spending a minute on the full render.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from reel.core.lint import LintOptions, LintReport, lint_data
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec
from reel.core.timeline import compute_timeline

PALETTE = ["#6c8cff", "#ff8f6b", "#4fd1a5", "#ffc857", "#b48cff", "#ff6b9d", "#5ec8e5", "#a3d95f"]


def _jpeg_b64(frame: Any, quality: int = 80) -> str:
    im = Image.fromarray(frame[..., [2, 1, 0]])
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def build_storyboard(
    spec: ReelSpec,
    out: Path,
    *,
    style: str | None = None,
    scale: float = 0.25,
    frames_per_scene: int = 3,
    lenient: bool = True,
    lint: LintReport | None = None,
) -> Path:
    r = Renderer(spec, RenderOptions(style=style, scale=scale, lenient=lenient))
    tl = compute_timeline(spec)
    report = lint or lint_data(
        spec.model_dump(by_alias=True),
        options=LintOptions(check_duration=False, style_override=style),
    )
    cards: list[str] = []
    bars: list[str] = []
    total = max(tl.total_sec, 1e-6)
    for slot in tl.slots:
        sc = spec.scenes[slot.index]
        color = PALETTE[slot.index % len(PALETTE)]
        # key frames: spread over the visible (non-overlapped) part of the scene
        n = max(1, frames_per_scene)
        thumbs = []
        for k in range(n):
            frac = (k + 0.5) / n
            local = int((slot.n_frames - slot.overlap_frames) * frac)
            f = slot.start_frame + local
            thumbs.append((local / spec.meta.fps, _jpeg_b64(r.frame(min(f, tl.total_frames - 1)))))
        left = slot.start_sec / total * 100
        width = max(0.8, (slot.duration_sec - slot.overlap_sec * 0.0) / total * 100)
        bars.append(
            f'<a class="seg" href="#{html.escape(sc.id)}" style="left:{left:.3f}%;width:{width:.3f}%;background:{color}" '
            f'title="{html.escape(sc.id)} · {sc.duration_sec:g}s"><span>{slot.index + 1}</span></a>'
        )
        caps = "".join(
            f"<li><b>{c.t0:g}-{c.t1:g}s</b> <i>{html.escape(c.style)}</i>{' · ' + html.escape(c.speaker) if c.speaker else ''} "
            f"{html.escape(c.text)}</li>"
            for c in sc.captions
        )
        acts = "".join(
            f"<li><b>{html.escape(ly.character)}</b> "
            + ", ".join(
                f"{html.escape(a.name)} <small>{a.t0:g}-{a.t1:g}</small>" for a in ly.actions
            )
            + "</li>"
            for ly in sc.layers
        )
        cam = ", ".join(f"{m.type} {m.t0:g}-{m.t1:g}s" for m in sc.camera.moves) or "static"
        tr = sc.transition_out
        trs = "cut" if tr.type == "cut" or tr.duration <= 0 else f"{tr.type} {tr.duration:g}s"
        imgs = "".join(
            f'<figure><img src="data:image/jpeg;base64,{b}" alt="{html.escape(sc.id)} t={t:.1f}s"><figcaption>{t:.1f}s</figcaption></figure>'
            for t, b in thumbs
        )
        bg = sc.background
        params = ", ".join(f"{k}={v}" for k, v in bg.params.items())
        cards.append(
            f'<section class="card" id="{html.escape(sc.id)}" style="--c:{color}"><header><span class="n">{slot.index + 1}</span>'
            f'<h3>{html.escape(sc.id)}</h3><span class="dur">{sc.duration_sec:g}s @ {slot.start_sec:.1f}s</span></header>'
            f'<div class="thumbs">{imgs}</div><div class="meta"><span class="chip">{html.escape(bg.template)}</span>'
            f'<span class="chip dim">{html.escape(params)}</span><span class="chip">camera: {html.escape(cam)}</span>'
            f'<span class="chip">→ {html.escape(trs)}</span></div><ul class="caps">{caps}</ul><ul class="acts">{acts}</ul></section>'
        )
    issues = (
        "".join(
            f'<li class="{i.severity.value}"><b>{html.escape(i.code)}</b> <code>{html.escape(i.path)}</code> {html.escape(i.message)}'
            + (f"<br><small>→ {html.escape(i.hint)}</small>" if i.hint else "")
            + "</li>"
            for i in report.sorted_issues()
        )
        or '<li class="ok">no issues</li>'
    )
    chars = "".join(
        f'<span class="chip"><b>{html.escape(c.id)}</b> {html.escape(c.archetype)}'
        + (f" · {html.escape(', '.join(c.props))}" if c.props else "")
        + "</span>"
        for c in spec.characters
    )
    page = _PAGE.format(
        title=html.escape(spec.meta.title),
        style=html.escape(r.style_name),
        total=f"{tl.total_sec:.1f}",
        scenes=len(spec.scenes),
        ok="ok" if report.ok else "bad",
        verdict=(
            "lint clean"
            if not report.issues
            else f"{len(report.errors)} errors · {len(report.warnings)} warnings"
        ),
        bars="".join(bars),
        cards="".join(cards),
        issues=issues,
        chars=chars,
        spec_json=html.escape(json.dumps(spec.model_dump(mode="json", by_alias=True), indent=2)),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · storyboard</title>
<style>
:root{{--bg:#fbfbfd;--fg:#16181d;--mut:#6a7180;--card:#fff;--line:#e3e6ee;--ok:#1f9d6b;--bad:#d64545;--warn:#c98a10}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--fg:#e8eaf0;--mut:#8d96a8;--card:#171a21;--line:#272b36;--ok:#4fd1a5;--bad:#ff7a7a;--warn:#ffc857}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:24px 16px 80px}}
h1{{font-size:26px;margin:0 0 4px}}.sub{{color:var(--mut);display:flex;gap:14px;flex-wrap:wrap;align-items:center}}
.pill{{padding:2px 10px;border-radius:99px;border:1px solid var(--line);font-size:13px}}.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.bad{{color:var(--bad);border-color:var(--bad)}}
.timeline{{position:relative;height:40px;margin:22px 0 8px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.seg{{position:absolute;top:0;bottom:0;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:600;font-size:13px;text-decoration:none;border-right:2px solid var(--bg)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-top:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:12px;padding:12px;scroll-margin-top:12px}}
.card header{{display:flex;align-items:baseline;gap:8px;margin-bottom:8px}}.card h3{{margin:0;font-size:16px;flex:1}}
.n{{background:var(--c);color:#fff;border-radius:6px;padding:0 7px;font-weight:700;font-size:13px}}.dur{{color:var(--mut);font-size:13px}}
.thumbs{{display:flex;gap:6px}}.thumbs figure{{margin:0;flex:1;min-width:0}}.thumbs img{{width:100%;border-radius:8px;display:block;cursor:zoom-in}}
figcaption{{font-size:11px;color:var(--mut);text-align:center}}
.meta{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}}.chip{{border:1px solid var(--line);border-radius:6px;padding:1px 8px;font-size:12px}}.chip.dim{{color:var(--mut)}}
ul{{margin:6px 0;padding-left:18px}}.caps li,.acts li{{font-size:13px;margin:2px 0}}small{{color:var(--mut)}}
section.panel{{margin-top:28px}}h2{{font-size:18px}}
.issues li{{margin:6px 0;list-style:none;border-left:3px solid var(--line);padding-left:10px}}.issues li.error{{border-color:var(--bad)}}.issues li.warning{{border-color:var(--warn)}}.issues li.ok{{border-color:var(--ok)}}
details{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px}}pre{{overflow:auto;font-size:12px}}
dialog{{border:0;border-radius:12px;padding:0;max-width:92vw;background:transparent}}dialog img{{max-height:92vh;max-width:92vw;border-radius:12px;display:block}}dialog::backdrop{{background:rgba(0,0,0,.7)}}
</style></head><body><main>
<h1>{title}</h1>
<div class="sub"><span class="pill">style: {style}</span><span class="pill">{total}s · {scenes} scenes</span><span class="pill {ok}">{verdict}</span></div>
<div class="timeline" aria-label="timeline">{bars}</div>
<div class="meta">{chars}</div>
<div class="grid">{cards}</div>
<section class="panel"><h2>Lint</h2><ul class="issues">{issues}</ul></section>
<section class="panel"><details><summary>Spec JSON</summary><pre>{spec_json}</pre></details></section>
</main>
<dialog id="dlg"><img id="big" alt=""></dialog>
<script>
const dlg=document.getElementById('dlg'),big=document.getElementById('big');
document.querySelectorAll('.thumbs img').forEach(i=>i.addEventListener('click',()=>{{big.src=i.src;dlg.showModal()}}));
dlg.addEventListener('click',()=>dlg.close());
</script></body></html>
"""
