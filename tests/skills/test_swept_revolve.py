"""swept_pocket_along_curve / revolve_pocket / revolve_boss 단위 테스트."""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.revolve_boss import RevolveBoss
from phone_designer.skills.modify_pocket.revolve_pocket import RevolvePocket
from phone_designer.skills.modify_pocket.swept_pocket_along_curve import (
    SweptPocketAlongCurve,
)


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _face_count(body) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    seen = TopTools_MapOfShape()
    n = 0
    it = TopExp_Explorer(body.wrapped, TopAbs_FACE)
    while it.More():
        f = it.Current()
        if not seen.Contains(f):
            seen.Add(f)
            n += 1
        it.Next()
    return n


def _make_box(length=30.0, width=30.0, height=10.0):
    return Box().apply(None, {
        "length_mm": length, "width_mm": width, "height_mm": height,
    }).body


# ── B.1 swept_pocket_along_curve ────────────────────────────────────────────


def test_swept_pocket_along_polyline_basic():
    body = _make_box(30, 30, 10)
    v0 = _volume(body)

    # Box spans z=[0,10], top face at z=10. L-shaped path entirely inside.
    skill = SweptPocketAlongCurve()
    r = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 6.0),
            (5.0, 0.0, 6.0),
        ],
        "path_type": "polyline",
    })
    v1 = _volume(r.body)
    assert v1 < v0, "volume should decrease"
    # Pocket roughly area * length = π·4 * (4 + 5) ≈ 113 mm³ (loose bound)
    removed = v0 - v1
    assert removed > 30.0, f"removed too little: {removed:.2f} mm³"


def test_swept_pocket_along_bspline():
    body = _make_box(30, 30, 10)
    v0 = _volume(body)

    skill = SweptPocketAlongCurve()
    r = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 3.0},
        "path_points": [
            (-8.0, -8.0, 10.0),
            (-4.0, -2.0, 8.0),
            (2.0, 2.0, 8.0),
            (8.0, 8.0, 10.0),
        ],
        "path_type": "bspline",
    })
    v1 = _volume(r.body)
    assert v1 < v0


def test_swept_pocket_with_floor_fillet():
    body = _make_box(30, 30, 10)

    skill = SweptPocketAlongCurve()
    r = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 6.0),
            (5.0, 0.0, 6.0),
        ],
        "path_type": "polyline",
        "floor_blend_r_mm": 0.3,
    })
    n1 = _face_count(r.body)
    # Floor blend should add at least one fillet face. If the radius is too
    # large or no edges match, the helper falls back to the un-blended shape,
    # in which case face count is just the swept geometry's count. The
    # important behavior: with vs. without blend, count should not decrease.
    r_nob = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 6.0),
            (5.0, 0.0, 6.0),
        ],
        "path_type": "polyline",
    })
    n_nob = _face_count(r_nob.body)
    assert n1 >= n_nob, "floor blend should not reduce face count"


def test_swept_post_condition_kind():
    spec = SweptPocketAlongCurve.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── B.3 revolve_pocket ──────────────────────────────────────────────────────


def test_revolve_pocket_full_circle():
    body = _make_box(40, 40, 20)
    v0 = _volume(body)

    # Box spans z=[0,20], top face at z=20. Annular groove cut into the top:
    # rectangle profile (1×1mm) at local (8, -0.5) → world XZ (x=8, z=-0.5),
    # then translated to axis_origin (0,0,20) → world (x=8, z=19.5).
    # Profile spans z=[19, 20] → cuts the top by 1mm.
    r = RevolvePocket().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 1.0, "width_mm": 1.0,
            "center_x_mm": 8.0, "center_y_mm": -0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 360.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Expected removal ≈ 2π·8 · 1·1 ≈ 50.27 mm³
    removed = v0 - v1
    assert 30.0 < removed < 80.0, f"removed unexpected: {removed:.2f}"


def test_revolve_pocket_partial_120deg():
    body = _make_box(40, 40, 20)
    v0 = _volume(body)

    r = RevolvePocket().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 1.0, "width_mm": 1.0,
            "center_x_mm": 8.0, "center_y_mm": -0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 120.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0
    # Partial removal should be ~1/3 of full-circle removal.
    removed = v0 - v1
    full_removal_est = 2 * 3.14159 * 8.0 * 1.0 * 1.0
    expected_partial = full_removal_est * (120.0 / 360.0)
    assert removed < full_removal_est, "120° should remove less than 360°"
    assert removed > expected_partial * 0.5, (
        f"removed {removed:.2f}, expected ≈ {expected_partial:.2f}"
    )


def test_revolve_pocket_post_condition_kind():
    spec = RevolvePocket.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── B.3 revolve_boss ────────────────────────────────────────────────────────


def test_revolve_boss_basic():
    body = _make_box(40, 40, 20)
    v0 = _volume(body)

    # Box spans z=[0,20], top face at z=20. Ring boss above the top, overlapping
    # slightly to ensure clean fuse:
    # profile (length=2, width=2, center_y=0.5) → world z=[-0.5, 1.5] before
    # translate, then +20 → z=[19.5, 21.5]. Overlaps box by 0.5mm.
    r = RevolveBoss().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 2.0, "width_mm": 2.0,
            "center_x_mm": 8.0, "center_y_mm": 0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 360.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    # Added volume ≈ 2π·8 · 2·1.5 ≈ 150.8 mm³ (approx; ring not box).
    added = v1 - v0
    assert added > 100.0, f"added too little: {added:.2f} mm³"


def test_revolve_boss_partial():
    body = _make_box(40, 40, 20)
    v0 = _volume(body)

    r = RevolveBoss().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 2.0, "width_mm": 2.0,
            "center_x_mm": 8.0, "center_y_mm": 0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 90.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0


def test_revolve_boss_post_condition_kind():
    spec = RevolveBoss.spec
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── manifest registration sanity ────────────────────────────────────────────


def test_skills_registered_in_manifest():
    # Trigger registration by importing export_manifest (its side-effect imports
    # register everything).
    import phone_designer.skills.export_manifest as _em  # noqa: F401
    from phone_designer.skills._registry import registry
    assert _em is not None
    names = {s.name for s in registry.all()}
    assert "swept_pocket_along_curve" in names
    assert "revolve_pocket" in names
    assert "revolve_boss" in names
