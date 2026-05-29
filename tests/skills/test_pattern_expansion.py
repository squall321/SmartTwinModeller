"""Advanced pattern skills — variable / nested / mirror-about-two-planes."""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pattern.mirror_about_two_planes import (
    MirrorAboutTwoPlanes,
)
from phone_designer.skills.modify_pattern.nested_pattern import NestedPattern
from phone_designer.skills.modify_pattern.variable_pattern import VariablePattern


# ── helpers ──────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _top_face_selector():
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _flat_top_pockets(body, box_top_z: float = 10.0) -> list:
    """Pocket-floor faces strictly inside the body (normal ≈ +Z, cz < top)."""
    from phone_designer.skills._resolvers import (
        _all_faces,
        _face_center,
        _face_normal_at_center,
    )
    pockets = []
    for f in _all_faces(body.wrapped):
        nx, ny, nz = _face_normal_at_center(f)
        cx, cy, cz = _face_center(f)
        if nz > 0.9 and cz < box_top_z - 0.1:
            pockets.append((cx, cy, cz))
    return pockets


def _make_box(L: float = 60.0, W: float = 60.0, H: float = 10.0):
    return Box().apply(
        None, {"length_mm": L, "width_mm": W, "height_mm": H}
    ).body


# ── variable_pattern ─────────────────────────────────────────────────────────


def test_variable_pattern_tapered_row_volume_changed():
    """5 holes in a row scaling 1.0 → 2.0 in diameter; volume changes by sum."""
    box = _make_box()
    v_before = _volume(box)

    count = 5
    base_d = 2.0
    depth = 4.0
    scale_start = 1.0
    scale_end = 2.0
    spacing = 5.0

    r = VariablePattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": base_d,
        "operation": "pocket",
        "feature_depth_mm": depth,
        "count": count,
        "spacing_mm": spacing,
        "direction": "X",
        "start_offset_x_mm": -10.0,
        "start_offset_y_mm": 0.0,
        "scale_start": scale_start,
        "scale_end": scale_end,
        "rotation_start_deg": 0.0,
        "rotation_end_deg": 0.0,
    })

    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == count, (
        f"variable_pattern expected {count} pockets, got {len(pocks)}"
    )

    # Total volume removed should equal sum over k of π r_k² · depth.
    v_after = _volume(r.body)
    actual_loss = v_before - v_after
    expected_loss = 0.0
    denom = count - 1
    for k in range(count):
        t = k / denom
        s = (1.0 - t) * scale_start + t * scale_end
        d_k = base_d * s
        expected_loss += math.pi * (d_k / 2.0) ** 2 * depth
    rel = abs(actual_loss - expected_loss) / expected_loss
    assert rel < 0.02, (
        f"variable_pattern volume loss: expected {expected_loss:.4f}, "
        f"got {actual_loss:.4f} (rel err {rel:.4f})"
    )


def test_variable_pattern_rejects_count_one():
    box = _make_box()
    with pytest.raises(Exception):
        VariablePattern().apply(box, {
            "face_selector": _top_face_selector().model_dump(),
            "profile_diameter_mm": 2.0,
            "operation": "pocket",
            "feature_depth_mm": 3.0,
            "count": 1,
            "spacing_mm": 5.0,
            "direction": "X",
        })


# ── nested_pattern ───────────────────────────────────────────────────────────


def test_nested_pattern_linear_of_linear_produces_grid():
    """3 outer × 2 inner linear = 6 distinct pockets on a flat grid."""
    box = _make_box()
    v_before = _volume(box)

    diameter = 2.0
    depth = 3.0
    outer_count = 3
    inner_count = 2
    expected_total = outer_count * inner_count

    r = NestedPattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": diameter,
        "operation": "pocket",
        "feature_depth_mm": depth,
        "outer_pattern_kind": "linear",
        "outer_params": {
            "count": outer_count,
            "spacing_mm": 10.0,
            "direction": "X",
            "start_offset_x_mm": -10.0,
            "start_offset_y_mm": -5.0,
        },
        "inner_pattern_kind": "linear",
        "inner_params": {
            "count": inner_count,
            "spacing_mm": 5.0,
            "direction": "Y",
            "start_offset_x_mm": 0.0,
            "start_offset_y_mm": 0.0,
        },
    })

    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == expected_total, (
        f"nested_pattern expected {expected_total} pockets, got {len(pocks)}"
    )

    # Volume reduction = expected_total × (π r² · depth) within 2%.
    v_after = _volume(r.body)
    actual_loss = v_before - v_after
    expected_loss = expected_total * math.pi * (diameter / 2.0) ** 2 * depth
    rel = abs(actual_loss - expected_loss) / expected_loss
    assert rel < 0.02, (
        f"nested_pattern volume loss expected {expected_loss:.4f}, "
        f"got {actual_loss:.4f} (rel {rel:.4f})"
    )


def test_nested_pattern_rejects_bad_kind():
    box = _make_box()
    with pytest.raises(Exception):
        NestedPattern().apply(box, {
            "face_selector": _top_face_selector().model_dump(),
            "profile_diameter_mm": 2.0,
            "operation": "pocket",
            "feature_depth_mm": 3.0,
            "outer_pattern_kind": "bogus",     # invalid Literal
            "outer_params": {"count": 2, "spacing_mm": 5.0, "direction": "X"},
            "inner_pattern_kind": "linear",
            "inner_params": {"count": 2, "spacing_mm": 5.0, "direction": "Y"},
        })


# ── mirror_about_two_planes ──────────────────────────────────────────────────


def test_mirror_about_two_planes_four_quadrants():
    """Original at (8, 6); planes YZ + XZ → 4 pockets at (±8, ±6)."""
    box = _make_box()
    r = MirrorAboutTwoPlanes().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 3.0,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "mirror_plane_1": "YZ",
        "mirror_plane_2": "XZ",
        "mirror_origin": (0.0, 0.0, 0.0),
        "original_x_mm": 8.0,
        "original_y_mm": 6.0,
    })

    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == 4, (
        f"mirror_about_two_planes expected 4 pockets, got {len(pocks)}: "
        f"{pocks}"
    )

    # Verify quadrant positions: x in {±8}, y in {±6}.
    centers_xy = sorted((round(p[0], 2), round(p[1], 2)) for p in pocks)
    expected = sorted([
        (-8.0, -6.0),
        (-8.0,  6.0),
        ( 8.0, -6.0),
        ( 8.0,  6.0),
    ])
    for got, exp in zip(centers_xy, expected):
        assert got[0] == pytest.approx(exp[0], abs=0.05), (
            f"x mismatch: got {got}, expected {exp}"
        )
        assert got[1] == pytest.approx(exp[1], abs=0.05), (
            f"y mismatch: got {got}, expected {exp}"
        )


def test_mirror_about_two_planes_rejects_same_plane():
    """Two identical planes → ValueError."""
    box = _make_box()
    with pytest.raises(ValueError, match="must differ"):
        MirrorAboutTwoPlanes().apply(box, {
            "face_selector": _top_face_selector().model_dump(),
            "profile_diameter_mm": 3.0,
            "operation": "pocket",
            "feature_depth_mm": 4.0,
            "mirror_plane_1": "YZ",
            "mirror_plane_2": "YZ",
            "original_x_mm": 8.0,
            "original_y_mm": 6.0,
        })


# ── spec metadata ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cls,expected_name,expected_feature", [
    (VariablePattern,        "variable_pattern",        "variable_pattern"),
    (NestedPattern,          "nested_pattern",          "nested_pattern"),
    (MirrorAboutTwoPlanes,   "mirror_about_two_planes", "mirror_quad"),
])
def test_advanced_pattern_spec_metadata(cls, expected_name, expected_feature):
    spec = cls.spec
    assert spec.name == expected_name
    assert spec.category == "modify/pattern"
    assert spec.level == "macro"
    assert expected_feature in spec.produces_features
    # All three must declare a volume_changed post-condition.
    pc_kinds = {pc.kind for pc in spec.post_conditions}
    assert "volume_changed" in pc_kinds, (
        f"{expected_name}: expected post_condition 'volume_changed', "
        f"got {pc_kinds}"
    )
