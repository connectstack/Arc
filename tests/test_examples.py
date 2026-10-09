"""The shipped examples must stay valid: they are the first thing a new user runs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from reel.core.catalog import CATALOG
from reel.core.lint import lint_data, lint_file
from reel.core.render import Renderer, RenderOptions
from reel.core.spec import ReelSpec
from reel.llm.heuristic import HeuristicSpecGenerator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SPECS = sorted(EXAMPLES.glob("*.json"))
SCRIPTS = sorted((EXAMPLES / "scripts").glob("*.txt"))
DURATION = {"story_50s": (49.0, 51.0), "explainer_45s": (44.5, 46.5)}


def test_both_examples_are_there() -> None:
    assert {p.stem for p in SPECS} == set(DURATION)
    assert len(SCRIPTS) >= 2


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.stem)
def test_example_lints_clean_without_warnings(path: Path) -> None:
    report = lint_file(path)
    assert report.ok and not report.warnings, report.format_text()
    lo, hi = DURATION[path.stem]
    assert report.total_sec is not None and lo <= report.total_sec <= hi


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.stem)
def test_example_uses_a_good_part_of_the_catalog(path: Path) -> None:
    """An example that only waves is a poor example: it should show off range."""
    spec = ReelSpec.from_file(path)
    actions = {a.name for s in spec.scenes for layer in s.layers for a in layer.actions}
    templates = {s.background.template for s in spec.scenes}
    transitions = {s.transition_out.type for s in spec.scenes if s.transition_out}
    caption_styles = {c.style for s in spec.scenes for c in s.captions}
    moves = {m.type for s in spec.scenes for m in s.camera.moves}
    assert len(actions) >= 8 and len(templates) >= 3
    assert {"subtitle", "title"} <= caption_styles
    assert len(transitions) >= 2 and len(moves) >= 3
    assert all(a in CATALOG.actions for a in actions)


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.stem)
def test_example_scripts_build_offline_into_lint_clean_specs(path: Path) -> None:
    result = HeuristicSpecGenerator().generate(path.read_text(), style="paper_cutout", seed=7)
    report = lint_data(result.spec)
    assert report.ok, report.format_text()
    assert report.total_sec is not None and 45 <= report.total_sec <= 60


@pytest.mark.render
@pytest.mark.parametrize("style", sorted(CATALOG.styles.names()))
@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.stem)
def test_every_scene_of_every_example_renders_in_every_style(path: Path, style: str) -> None:
    """Strict (non-lenient) render of a mid-scene frame of each scene: nothing is missing, nothing is blank."""
    spec = ReelSpec.from_file(path)
    r = Renderer(spec, RenderOptions(style=style, scale=0.1, cache=False, lenient=False))
    for slot in r.timeline.slots:
        mid = slot.start_frame + (slot.n_frames - slot.overlap_frames) // 2
        frame = r.frame(mid)
        assert frame[..., :3].std() > 3, f"{path.stem}/{slot.scene_id} is blank in {style}"
        assert np.isfinite(frame).all()


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.stem)
def test_example_soundtrack_fits_its_scenes(path: Path, tmp_path: Path) -> None:
    """With the deterministic babble voice, no line overruns its scene and nothing talks over anything."""
    from reel.audio import prepare_audio

    plan = prepare_audio(
        ReelSpec.from_file(path), base_dir=path.parent, work_dir=tmp_path, engine="babble"
    )
    assert plan.warnings == [] and plan.wav is not None and plan.report is not None
    assert abs(plan.report.approx_lufs - (-16.0)) < 1.0 and plan.report.peak < 0.98
