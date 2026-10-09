"""`reel new-action` / `reel new-style` scaffolding."""

from __future__ import annotations

import keyword
import re
from pathlib import Path

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

ACTION_TEMPLATE = '''"""{name}: TODO one line about what this action looks like.

Scaffolded by `reel new-action {name}`.  Preview the motion without any style:

    reel debug-actions {name} --out {name}_sheet.png

Channels you can animate are listed in reel/core/rig.py (arm_r_sh, torso_lean, mouth_open ...).
Angles are degrees (0 = limb hangs down, +90 = pointing forward); times are seconds from the
start of the action.  Use ctx.duration so the action stretches to whatever window a spec gives it.
"""

from __future__ import annotations

from pydantic import Field

from reel.actions.base import ParamsBase, register_action
from reel.core.animation import ActionContext, Clip


class {cls}Params(ParamsBase):
    """What a spec may put in `params`.  Strict: unknown keys are lint errors."""

    intensity: float = Field(1.0, ge=0.2, le=2.0, description="how big the motion is")


@register_action(
    "{name}",
    params_schema={cls}Params,
    category="{category}",
    summary="TODO: one line shown in `reel list actions` and given to the LLM",
    min_duration=0.5,
    default_duration=1.5,
)
def {fn}(ctx: ActionContext, p: {cls}Params) -> Clip:
    D = ctx.duration
    k = p.intensity
    up = min(0.3, 0.3 * D)  # time to reach the pose; the same again to come back
    c = ctx.clip()

    # 1. big motion: keyframes are (time, value[, easing]).  "overshoot" gives follow-through.
    c.key("arm_r_sh", [(0, 5), (up, 110 * k, "overshoot"), (D - up, 110 * k), (D, 5)])
    c.key("arm_r_el", [(0, 10), (up, 40, "overshoot"), (D - up, 40), (D, 10)])

    # 2. secondary motion: sinusoids ride on top of the pose (mode="add" is the default)
    c.osc("hand_r_wr", amp=18 * k, freq=2.0, t0=up, t1=D - up)

    # 3. face and body language sell the action
    c.key("mouth_smile", [(0, 0.3), (up, 0.9), (D - up, 0.9), (D, 0.3)])
    c.key("head_tilt", [(0, 0), (up, 6), (D - up, 6), (D, 0)])
    return c
'''

STYLE_TEMPLATE = '''"""{name} style pack.  Scaffolded by `reel new-style {name}`."""

from __future__ import annotations

from reel.styles.base import StylePack, register_style


@register_style("{name}")
class {cls}Style(StylePack):
    """TODO: one line describing the look."""

    # override the hooks you care about: draw_background, draw_character, draw_caption,
    # post_process, transition.  Everything you do not override falls back to the shared defaults.
'''


def _valid(name: str) -> str:
    if not _NAME.match(name) or keyword.iskeyword(name):
        raise ValueError(
            f"{name!r} is not a valid name: use lowercase letters, digits and underscores, "
            "starting with a letter (it becomes a Python module and the spec's action name)"
        )
    return name


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def source_checkout_root() -> Path | None:
    """Repo root when running from a source checkout (editable install), else None."""
    import reel

    pkg = Path(reel.__file__).resolve().parent  # .../src/reel
    root = pkg.parent.parent
    return root if (root / "pyproject.toml").exists() and (root / "src" / "reel").exists() else None


def default_action_dir() -> Path:
    root = source_checkout_root()
    return root / "src" / "reel" / "actions" if root else Path.cwd() / "reel_plugins"


def scaffold_action(
    name: str, *, category: str = "gesture", directory: Path | None = None, force: bool = False
) -> Path:
    _valid(name)
    target = (directory or default_action_dir()) / f"{name}.py"
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    fn = name if name != "idle" else "idle_"
    target.write_text(ACTION_TEMPLATE.format(name=name, cls=_camel(name), fn=fn, category=category))
    return target


def scaffold_style(name: str, *, directory: Path | None = None, force: bool = False) -> Path:
    _valid(name)
    root = source_checkout_root()
    base = directory or (root / "src" / "reel" / "styles" if root else Path.cwd() / "reel_plugins")
    target = base / name / "__init__.py"
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STYLE_TEMPLATE.format(name=name, cls=_camel(name)))
    return target
