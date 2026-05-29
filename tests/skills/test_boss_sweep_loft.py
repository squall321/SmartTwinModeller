"""swept_boss_along_curve / loft_boss_between_sketches unit tests."""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.loft_boss_between_sketches import (
    LoftBossBetweenSketches,
)
from phone_designer.skills.modify_boss.swept_boss_along_curve import (
    SweptBossAlongCurve,
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


def _make_box(length=50.0, width=50.0, height=10.0):
    return Box().apply(None, {
        "length_mm": length, "width_mm": width, "height_mm": height,
    }).body


# ── swept_boss_along_curve ─────────────────────────────────────────────────


def test_swept_boss_along_polyline_basic():
    body = _make_box(50, 50, 10)
    v0 = _volume(body)

    # Box top at z=10. L-shaped polyline: start on top, run +Z then +X.
    # Profile circle D=4 (radius 2). First segment is +Z height 4, second is
    # +X length 5. Approx added volume ≈ π·4 · (4 + 5) ≈ 113 mm³ (loose).
    skill = SweptBossAlongCurve()
    r = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 14.0),
            (5.0, 0.0, 14.0),
        ],
        "path_type": "polyline",
    })
    v1 = _volume(r.body)
    assert v1 > v0, "volume should increase"
    added = v1 - v0
    assert added > 30.0, f"added too little: {added:.2f} mm³"


def test_swept_boss_along_bspline():
    body = _make_box(50, 50, 10)
    v0 = _volume(body)

    skill = SweptBossAlongCurve()
    r = skill.apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 3.0},
        "path_points": [
            (-10.0, -10.0, 10.0),
            (-4.0, -2.0, 12.0),
            (2.0, 2.0, 12.0),
            (10.0, 10.0, 10.0),
        ],
        "path_type": "bspline",
    })
    v1 = _volume(r.body)
    assert v1 > v0


def test_swept_boss_with_top_fillet():
    body = _make_box(50, 50, 10)

    skill = SweptBossAlongCurve()
    args_base = {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (0.0, 0.0, 10.0),
            (0.0, 0.0, 14.0),
            (5.0, 0.0, 14.0),
        ],
        "path_type": "polyline",
    }
    r_nob = skill.apply(body, args_base)
    n_nob = _face_count(r_nob.body)

    args_blend = dict(args_base)
    args_blend["top_blend_r_mm"] = 0.3
    r_blend = skill.apply(body, args_blend)
    n_blend = _face_count(r_blend.body)

    # Top blend should add at least one fillet face; never reduce face count.
    assert n_blend >= n_nob, (
        f"top blend should not reduce face count: nob={n_nob}, blend={n_blend}"
    )


def test_swept_boss_post_condition_kind():
    spec = SweptBossAlongCurve.spec
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── loft_boss_between_sketches ─────────────────────────────────────────────


def test_loft_boss_circle_to_smaller_circle():
    body = _make_box(50, 50, 10)
    v0 = _volume(body)

    # Frustum: R1=5 (D=10) at base, R2=2 (D=4) at top, height 5.
    # V = π·h/3 · (R1² + R2² + R1·R2) = π·5/3·(25 + 4 + 10) = π·5/3·39 ≈ 204.2 mm³
    r = LoftBossBetweenSketches().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lower_sketch": {"kind": "circle", "diameter_mm": 10.0},
        "upper_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "height_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    added = v1 - v0
    expected = math.pi * 5.0 / 3.0 * (25.0 + 4.0 + 10.0)
    # Tolerance ±10% (loft approximation with B-spline interpolation of circles).
    assert abs(added - expected) / expected < 0.10, (
        f"added {added:.2f} mm³, expected ≈ {expected:.2f} mm³"
    )


def test_loft_boss_circle_to_rectangle():
    body = _make_box(50, 50, 10)
    v0 = _volume(body)

    r = LoftBossBetweenSketches().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lower_sketch": {"kind": "circle", "diameter_mm": 10.0},
        "upper_sketch": {
            "kind": "rectangle",
            "length_mm": 6.0, "width_mm": 6.0,
        },
        "height_mm": 5.0,
    })
    v1 = _volume(r.body)
    assert v1 > v0
    added = v1 - v0
    # Rough sanity: somewhere between two extrusions of base & top.
    base_vol = math.pi * 25.0 * 5.0   # ≈ 392
    top_vol  = 36.0 * 5.0             # = 180
    assert top_vol * 0.6 < added < base_vol * 1.2, (
        f"added {added:.2f} mm³ out of expected band [{top_vol*0.6:.1f}, "
        f"{base_vol*1.2:.1f}]"
    )


def test_loft_boss_with_base_fillet():
    body = _make_box(50, 50, 10)

    args_base = {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lower_sketch": {"kind": "circle", "diameter_mm": 10.0},
        "upper_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "height_mm": 5.0,
    }
    r_nob = LoftBossBetweenSketches().apply(body, args_base)
    n_nob = _face_count(r_nob.body)

    args_blend = dict(args_base)
    args_blend["base_blend_r_mm"] = 0.3
    r_blend = LoftBossBetweenSketches().apply(body, args_blend)
    n_blend = _face_count(r_blend.body)

    # Base blend should not reduce face count.
    assert n_blend >= n_nob, (
        f"base blend should not reduce face count: nob={n_nob}, "
        f"blend={n_blend}"
    )


def test_loft_boss_post_condition_kind():
    spec = LoftBossBetweenSketches.spec
    assert any(pc.kind == "volume_increased" for pc in spec.post_conditions)


# ── manifest registration sanity ───────────────────────────────────────────


def test_skills_registered_in_manifest():
    import phone_designer.skills.export_manifest as _em  # noqa: F401
    from phone_designer.skills._registry import registry
    assert _em is not None
    names = {s.name for s in registry.all()}
    assert "swept_boss_along_curve" in names
    assert "loft_boss_between_sketches" in names
