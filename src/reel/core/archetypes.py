"""Character archetypes and props: *data only*, style-agnostic.

An archetype fixes a body's proportions (`RigDims`), default colours and
feature switches (hair, outfit, ...).  A style pack decides how those features are
*drawn*; a spec only names the archetype.  Adding one = adding a `register`-ed
`Archetype` here (or in a plugin).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from reel.core.catalog import CATALOG


@dataclass(frozen=True)
class RigDims:
    """Body proportions in design pixels at scale 1 (reference frame: 1080 px wide)."""

    head_rx: float = 84.0
    head_ry: float = 90.0
    neck: float = 12.0
    torso_len: float = 165.0
    shoulder_w: float = 96.0  # distance between the shoulder joints
    hip_w: float = 52.0  # distance between the hip joints
    chest_w: float = 120.0  # visual torso widths (for drawing)
    waist_w: float = 104.0
    upper_arm: float = 78.0
    fore_arm: float = 72.0
    hand_r: float = 21.0
    arm_w: float = 30.0
    thigh: float = 100.0
    shin: float = 96.0
    foot_h: float = 22.0
    heel: float = 22.0  # ankle -> back of foot
    toe: float = 54.0  # ankle -> front of foot
    leg_w: float = 40.0

    @property
    def hip_height(self) -> float:
        """Hip joint height above ground when standing straight."""
        return self.thigh + self.shin + self.foot_h

    @property
    def leg_len(self) -> float:
        return self.thigh + self.shin

    @property
    def arm_len(self) -> float:
        return self.upper_arm + self.fore_arm

    @property
    def height(self) -> float:
        return self.hip_height + self.torso_len + self.neck + 2 * self.head_ry


@dataclass(frozen=True)
class Archetype:
    name: str
    summary: str
    dims: RigDims
    palette: dict[str, str]
    features: dict[str, Any] = field(default_factory=dict)
    rest: dict[str, float] = field(default_factory=dict)  # resting pose offsets (e.g. stoop)
    category: str = "humanoid"
    #: a character made from library art (category "sprite"): the asset's name, the words a script uses for it, its height
    asset: str | None = None
    tags: tuple[str, ...] = ()
    height_px: float | None = None

    @property
    def height(self) -> float:
        return self.dims.height if self.height_px is None else self.height_px


_BASE_PALETTE = {
    "skin": "#f0c29a",
    "hair": "#3b2a20",
    "shirt": "#e4572e",
    "shirt2": "#f3a712",
    "pants": "#2d4a6b",
    "shoes": "#2a2a2e",
    "accent": "#f3a712",
    "eye": "#202028",
    "outline": "#1c1c24",
}


def _pal(**kw: str) -> dict[str, str]:
    return {**_BASE_PALETTE, **kw}


def register_archetype(a: Archetype) -> Archetype:
    CATALOG.archetypes.register(a.name, a, summary=a.summary)
    return a


EVERYMAN = register_archetype(
    Archetype(
        "everyman",
        "Friendly all-rounder: medium build, short hair, tee and jeans.",
        RigDims(),
        _pal(),
        {"hair": "short", "outfit": "tee", "nose": "round", "ears": True, "eyes": "round"},
    )
)
KID = register_archetype(
    Archetype(
        "kid",
        "Small with a big head and short limbs; messy hair, hoodie.",
        RigDims(
            head_rx=98,
            head_ry=102,
            neck=8,
            torso_len=112,
            shoulder_w=74,
            hip_w=44,
            chest_w=96,
            waist_w=88,
            upper_arm=56,
            fore_arm=52,
            hand_r=19,
            arm_w=27,
            thigh=70,
            shin=66,
            foot_h=20,
            heel=20,
            toe=48,
            leg_w=35,
        ),
        _pal(
            hair="#e0a526",
            shirt="#2a9d8f",
            shirt2="#e9c46a",
            pants="#5b4b8a",
            shoes="#e63946",
            skin="#f6d1b0",
        ),
        {"hair": "messy", "outfit": "hoodie", "nose": "round", "ears": True, "eyes": "wide"},
    )
)
ELDER = register_archetype(
    Archetype(
        "elder",
        "Slightly stooped with white hair and glasses; cardigan.",
        RigDims(
            head_rx=80,
            head_ry=86,
            torso_len=150,
            shoulder_w=88,
            hip_w=50,
            chest_w=112,
            waist_w=104,
            upper_arm=72,
            fore_arm=66,
            thigh=92,
            shin=88,
            leg_w=36,
            arm_w=27,
        ),
        _pal(
            hair="#e8e8ec",
            shirt="#8d6e63",
            shirt2="#bcaaa4",
            pants="#4e5d6c",
            shoes="#3e2f2b",
            skin="#e8b894",
        ),
        {
            "hair": "balding",
            "outfit": "cardigan",
            "nose": "round",
            "ears": True,
            "eyes": "round",
            "accessory": "glasses",
            "facial": "mustache",
        },
        rest={"torso_lean": 7.0, "head_tilt": 3.0},
    )
)
HERO = register_archetype(
    Archetype(
        "hero",
        "Tall and broad-shouldered with spiky hair and a cape.",
        RigDims(
            head_rx=80,
            head_ry=86,
            torso_len=178,
            shoulder_w=118,
            hip_w=56,
            chest_w=142,
            waist_w=108,
            upper_arm=84,
            fore_arm=78,
            hand_r=23,
            arm_w=34,
            thigh=108,
            shin=104,
            leg_w=44,
        ),
        _pal(
            hair="#1b1b2f",
            shirt="#1d6fd1",
            shirt2="#ffd23f",
            pants="#b5123f",
            shoes="#ffd23f",
            accent="#d7263d",
            skin="#c68e66",
        ),
        {
            "hair": "spiky",
            "outfit": "suit",
            "nose": "pointy",
            "ears": True,
            "eyes": "round",
            "accessory": "cape",
        },
    )
)
ROBOT = register_archetype(
    Archetype(
        "robot",
        "Boxy metal body, visor eyes and an antenna.",
        RigDims(
            head_rx=84,
            head_ry=78,
            neck=10,
            torso_len=160,
            shoulder_w=104,
            hip_w=56,
            chest_w=128,
            waist_w=112,
            upper_arm=76,
            fore_arm=70,
            hand_r=24,
            arm_w=34,
            thigh=96,
            shin=92,
            leg_w=44,
            foot_h=24,
        ),
        _pal(
            skin="#b8c4d0",
            hair="#6c7a89",
            shirt="#90a4ae",
            shirt2="#ff8a3d",
            pants="#78909c",
            shoes="#455a64",
            accent="#00e5ff",
            eye="#00e5ff",
        ),
        {
            "hair": "none",
            "outfit": "robot",
            "nose": "none",
            "ears": False,
            "eyes": "visor",
            "head_shape": "box",
            "accessory": "antenna",
        },
        category="robot",
    )
)
BOSS = register_archetype(
    Archetype(
        "boss",
        "Sharp suit and tie, slicked-back hair, a little wider than average.",
        replace(RigDims(), chest_w=132, waist_w=120, shoulder_w=106, torso_len=170),
        _pal(
            hair="#222222",
            shirt="#37474f",
            shirt2="#eceff1",
            pants="#37474f",
            shoes="#111111",
            accent="#c62828",
            skin="#e2b48f",
        ),
        {
            "hair": "side_part",
            "outfit": "suit",
            "nose": "pointy",
            "ears": True,
            "eyes": "round",
            "accessory": "tie",
        },
    )
)


@dataclass(frozen=True)
class PropDef:
    """A thing a character can wear, hold or pick up.  Drawn by the figure builder."""

    name: str
    home: str  # head | face | neck | back | hand_r | hand_l
    size: float  # nominal size in px at scale 1
    summary: str = ""
    colors: tuple[str, ...] = ()  # default palette roles/hex for the prop
    category: str = "prop"


def register_prop(p: PropDef) -> PropDef:
    CATALOG.props.register(p.name, p, summary=p.summary)
    return p


for _p in (
    PropDef("hat", "head", 120, "Wide-brim hat worn on the head", ("#8d5a3b", "#3b2a20")),
    PropDef("cap", "head", 110, "Baseball cap worn on the head", ("#2a9d8f", "#ffffff")),
    PropDef("glasses", "face", 100, "Round glasses", ("#202028",)),
    PropDef("scarf", "neck", 110, "Scarf around the neck with a trailing end", ("#d7263d",)),
    PropDef("backpack", "back", 150, "Backpack on the back", ("#f3a712", "#8d5a3b")),
    PropDef(
        "briefcase", "hand_r", 110, "Briefcase carried in the front hand", ("#6d4c41", "#3e2f2b")
    ),
    PropDef("umbrella", "hand_r", 330, "Umbrella held in the front hand", ("#d7263d", "#202028")),
    PropDef("phone", "hand_l", 56, "Smartphone held in the back hand", ("#202028", "#4fc3f7")),
    PropDef("book", "hand_l", 84, "Book held in the back hand", ("#1d6fd1", "#fff8e1")),
    PropDef("coffee", "hand_r", 62, "Takeaway coffee cup", ("#f5f5f5", "#6d4c41")),
    PropDef("flower", "hand_r", 110, "A single flower", ("#ff6b9d", "#43a047")),
    PropDef("balloon", "hand_l", 250, "A balloon on a string", ("#e63946",)),
):
    register_prop(_p)

#: Where a picked-up item lies on the floor in front of the feet: PICKUP_REACH * height + PICKUP_FORWARD px.
PICKUP_REACH = 0.22
PICKUP_FORWARD = 30.0

PALETTE_ROLE_DEFAULTS = dict(_BASE_PALETTE)
