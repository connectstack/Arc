from __future__ import annotations

import math

import numpy as np
import pytest

from reel.core.catalog import CATALOG
from reel.core.rig import CHANNELS, Rig, arm_ik, default_pose, solve, squash_scale

ARCHETYPES = ["everyman", "kid", "elder", "hero", "robot", "boss"]


@pytest.fixture(params=ARCHETYPES)
def rig(request: pytest.FixtureRequest) -> Rig:
    return Rig(CATALOG.archetypes.get(request.param))


def test_standing_pose_touches_ground_and_has_expected_height(rig: Rig) -> None:
    fig = solve(rig.dims, default_pose())
    sole_y = max(fig.pts[k][1] for k in ("heel_r", "toe_r", "heel_l", "toe_l"))
    assert sole_y == pytest.approx(0.0, abs=1e-6)
    top = fig.pts["head_top"][1]
    assert -top == pytest.approx(rig.dims.height, rel=1e-6)


def test_ground_lock_lowers_hips_when_knees_bend(rig: Rig) -> None:
    straight = solve(rig.dims, default_pose())
    pose = default_pose() | {
        "leg_r_kn": 70.0,
        "leg_l_kn": 70.0,
        "leg_r_hip": 35.0,
        "leg_l_hip": 35.0,
    }
    bent = solve(rig.dims, pose)
    assert bent.pts["hip_c"][1] > straight.pts["hip_c"][1]  # y is down: hips are lower
    sole_y = max(bent.pts[k][1] for k in ("heel_r", "toe_r", "heel_l", "toe_l"))
    assert sole_y == pytest.approx(0.0, abs=1e-6)  # still standing on the floor


def test_ground_lock_off_and_lift(rig: Rig) -> None:
    pose = default_pose() | {"lift": 100.0}
    fig = solve(rig.dims, pose)
    sole_y = max(fig.pts[k][1] for k in ("heel_r", "toe_r", "heel_l", "toe_l"))
    assert sole_y == pytest.approx(-100.0, abs=1e-6)
    free = solve(rig.dims, default_pose() | {"ground_lock": 0.0, "leg_r_kn": 80.0})
    assert max(free.pts[k][1] for k in ("heel_r", "toe_r")) < 0  # foot floats when unlocked


def test_limb_lengths_are_preserved(rig: Rig) -> None:
    pose = default_pose() | {
        "arm_r_sh": 77.0,
        "arm_r_el": 55.0,
        "leg_l_hip": -30.0,
        "leg_l_kn": 50.0,
        "torso_lean": 20.0,
    }
    f = solve(rig.dims, pose)
    d = rig.dims

    def dist(a: str, b: str) -> float:
        return math.dist(f.pts[a], f.pts[b])

    assert dist("shoulder_r", "elbow_r") == pytest.approx(d.upper_arm)
    assert dist("elbow_r", "wrist_r") == pytest.approx(d.fore_arm)
    assert dist("hip_l", "knee_l") == pytest.approx(d.thigh)
    assert dist("knee_l", "ankle_l") == pytest.approx(d.shin)


def test_angle_convention_forward_is_plus_x() -> None:
    rig = Rig(CATALOG.archetypes.get("everyman"))
    f = solve(rig.dims, default_pose() | {"arm_r_sh": 90.0, "arm_r_el": 0.0, "torso_lean": 0.0})
    sx, sy = f.pts["shoulder_r"]
    ex, ey = f.pts["elbow_r"]
    assert ex - sx == pytest.approx(rig.dims.upper_arm, abs=1e-6)  # 90 deg = horizontal, forward
    assert ey == pytest.approx(sy, abs=1e-6)
    lean = solve(rig.dims, default_pose() | {"torso_lean": 30.0})
    assert lean.pts["shoulder_c"][0] > lean.pts["hip_c"][0]  # leaning forward moves shoulders +x


def test_rot_hip_rotates_rigidly_about_the_hips(rig: Rig) -> None:
    a = solve(rig.dims, default_pose() | {"ground_lock": 0.0})
    b = solve(rig.dims, default_pose() | {"ground_lock": 0.0, "rot_hip": -90.0})
    hc = a.pts["hip_c"]
    assert b.pts["hip_c"] == pytest.approx(hc)
    ra = math.dist(a.pts["head_c"], hc)
    rb = math.dist(b.pts["head_c"], hc)
    assert ra == pytest.approx(rb)
    assert b.pts["head_c"][0] < hc[0]  # -90 deg = falling backwards (head ends up behind the hips)


@pytest.mark.parametrize("angle", [10.0, 60.0, 100.0, 150.0, -40.0])
@pytest.mark.parametrize("reach", [0.55, 0.8, 0.95])
def test_arm_ik_reaches_reachable_targets(rig: Rig, angle: float, reach: float) -> None:
    pose = rig.rest_pose()
    sh = rig.solve(pose).pts["shoulder_r"]
    r = reach * rig.dims.arm_len
    target = (sh[0] + r * math.sin(math.radians(angle)), sh[1] + r * math.cos(math.radians(angle)))
    ch = rig.reach(pose, "r", target)
    wrist = rig.solve(pose | ch).pts["wrist_r"]
    assert math.dist(wrist, target) < 0.5


def test_arm_ik_clamps_unreachable_target(rig: Rig) -> None:
    sh, el = arm_ik(rig.dims, (0.0, 0.0), (1000.0, 0.0), 0.0)
    assert math.isfinite(sh) and math.isfinite(el)
    assert el < 5  # fully extended


def test_landmarks_exist(rig: Rig) -> None:
    for name in ("chin", "mouth", "eye_c", "head_top", "belly", "hip_c"):
        x, y = rig.landmark(rig.rest_pose(), name)
        assert math.isfinite(x) and math.isfinite(y)
    with pytest.raises(KeyError):
        rig.landmark(rig.rest_pose(), "elbow_of_doom")


def test_squash_preserves_area_and_sign() -> None:
    sx, sy = squash_scale(0.2)
    assert sy < 1 < sx  # positive squash compresses vertically
    assert sx * sy == pytest.approx(1.0)
    sx, sy = squash_scale(-0.2)
    assert sy > 1 > sx


def test_channel_defaults_are_inside_limits() -> None:
    for c in CHANNELS.values():
        assert c.lo <= c.default <= c.hi, c.name


def test_archetypes_have_sane_proportions() -> None:
    heights = {n: CATALOG.archetypes.get(n).dims.height for n in ARCHETYPES}
    assert heights["kid"] < heights["everyman"] < heights["hero"]
    assert all(450 < h < 700 for h in heights.values())
    assert np.isfinite(list(heights.values())).all()
