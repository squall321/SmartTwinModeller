"""Pattern / array skill family — linear / circular / mirror / path."""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pattern.circular_pattern import CircularPattern
from phone_designer.skills.modify_pattern.linear_pattern import LinearPattern
from phone_designer.skills.modify_pattern.mirror_feature import MirrorFeature
from phone_designer.skills.modify_pattern.path_pattern import PathPattern


# ── helpers ──────────────────────────────────────────────────────────────────


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _top_face_selector():
    """faces_by_normal pointing +Z, tight tolerance — selects the top of a box."""
    from phone_designer.skills._selectors import FacesByNormalSelector
    return FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0)


def _flat_top_pockets(body, box_top_z: float = 10.0) -> list:
    """Return list of pocket-floor faces strictly inside the body.

    A pocket cut into a +Z top face has a FLOOR whose outward normal is +Z
    (facing the opening), but whose Z-center is strictly below the host
    face. We filter by normal ≈ +Z and cz < box_top_z - epsilon to
    exclude the box top itself.
    """
    from phone_designer.skills._resolvers import _all_faces, _face_center, _face_normal_at_center
    pockets = []
    for f in _all_faces(body.wrapped):
        nx, ny, nz = _face_normal_at_center(f)
        cx, cy, cz = _face_center(f)
        # Pocket floor: normal ≈ +Z and Z strictly below host top.
        if nz > 0.9 and cz < box_top_z - 0.1:
            pockets.append((cx, cy, cz))
    return pockets


def _make_box(L=50.0, W=50.0, H=10.0):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


# ── linear_pattern ───────────────────────────────────────────────────────────


def test_linear_pattern_five_holes_volume():
    """5-hole row on box top, verify volume reduction = 5 × (π r² d) within 1%."""
    box = _make_box()
    v_before = _volume(box)

    diameter = 2.0
    depth = 4.0
    count = 5
    r = LinearPattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": diameter,
        "operation": "pocket",
        "feature_depth_mm": depth,
        "count": count,
        "spacing_mm": 6.0,
        "direction": "X",
        "start_offset_x_mm": -12.0,
        "start_offset_y_mm": 0.0,
    })
    v_after = _volume(r.body)
    actual_loss = v_before - v_after
    expected_loss = count * math.pi * (diameter / 2.0) ** 2 * depth
    rel = abs(actual_loss - expected_loss) / expected_loss
    assert rel < 0.01, (
        f"linear_pattern volume loss: expected {expected_loss:.3f}, "
        f"got {actual_loss:.3f} (rel err {rel:.4f})"
    )


def test_linear_pattern_rejects_count_one():
    box = _make_box()
    with pytest.raises(Exception):
        LinearPattern().apply(box, {
            "face_selector": _top_face_selector().model_dump(),
            "profile_diameter_mm": 2.0,
            "operation": "pocket",
            "feature_depth_mm": 3.0,
            "count": 1,                # below min
            "spacing_mm": 5.0,
            "direction": "X",
        })


def test_linear_pattern_missing_depth_for_pocket():
    box = _make_box()
    with pytest.raises(ValueError, match="feature_depth_mm"):
        LinearPattern().apply(box, {
            "face_selector": _top_face_selector().model_dump(),
            "profile_diameter_mm": 2.0,
            "operation": "pocket",         # depth missing
            "count": 3,
            "spacing_mm": 5.0,
            "direction": "X",
        })


# ── circular_pattern ─────────────────────────────────────────────────────────


def test_circular_pattern_six_holes_full_ring():
    """6-hole ring (full 360°) on box top — expect 6 pocket bottoms."""
    box = _make_box()
    r = CircularPattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 2.5,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "count": 6,
        "pitch_radius_mm": 15.0,
        "start_angle_deg": 0.0,
        "total_sweep_deg": 360.0,
    })
    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == 6, (
        f"full-ring circular_pattern expected 6 pocket bottoms, got "
        f"{len(pocks)}: {pocks}"
    )


def test_circular_pattern_six_holes_arc_180():
    """6-hole arc (180°) — expect 6 pocket bottoms; both endpoints included."""
    box = _make_box()
    r = CircularPattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 2.5,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "count": 6,
        "pitch_radius_mm": 15.0,
        "start_angle_deg": 0.0,
        "total_sweep_deg": 180.0,
    })
    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == 6, (
        f"arc circular_pattern expected 6 pocket bottoms, got "
        f"{len(pocks)}: {pocks}"
    )


# ── mirror_feature ───────────────────────────────────────────────────────────


def test_mirror_feature_yz_plane():
    """Pocket at (5, 0) + mirror across YZ → pockets at (5,0) and (-5,0)."""
    box = _make_box()
    r = MirrorFeature().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 3.0,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "count": 2,
        "mirror_plane": "YZ",
        "mirror_origin": (0.0, 0.0, 0.0),
        "original_x_mm": 5.0,
        "original_y_mm": 0.0,
    })
    pocks = _flat_top_pockets(r.body)
    assert len(pocks) == 2, f"mirror_feature expected 2 pocket bottoms, got {len(pocks)}"
    # Verify centers are roughly (+5, 0) and (-5, 0). Box centered at (0,0).
    xs = sorted(p[0] for p in pocks)
    assert xs[0] == pytest.approx(-5.0, abs=0.05), f"left pocket x={xs[0]}"
    assert xs[1] == pytest.approx(+5.0, abs=0.05), f"right pocket x={xs[1]}"
    # Y centers ≈ 0
    for p in pocks:
        assert abs(p[1]) < 0.05, f"pocket Y not 0: {p}"


# ── path_pattern ─────────────────────────────────────────────────────────────


def test_path_pattern_square_corners_count_8():
    """4-point polyline (square corners), count=8 → expect 8 distinct pockets.

    The path is a closed-ish square traversal (4 corners + return to first).
    With count=8, k=0 and k=7 sit at the two endpoints; the other 6 are
    distributed evenly along the 4 segments.
    """
    box = _make_box()
    # Square at radius 10 around the center of the top face.
    pts = [
        (-10.0, -10.0, 10.0),
        ( 10.0, -10.0, 10.0),
        ( 10.0,  10.0, 10.0),
        (-10.0,  10.0, 10.0),
        (-10.0, -10.0, 10.0),    # close the loop
    ]
    r = PathPattern().apply(box, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 2.0,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "path_points": pts,
        "count": 8,
    })
    pocks = _flat_top_pockets(r.body)
    # With a closed loop and count=8, the last sample lands on top of the
    # first, so two cuts coincide — distinct pocket bottoms = 7.
    # If the user wants 8 distinct features they should pass an open path
    # (no return-to-first) — verify that path with the equivalent 4 corners.
    assert len(pocks) >= 7, (
        f"path_pattern (closed loop) expected ≥7 distinct pockets, got "
        f"{len(pocks)}"
    )

    # Open polyline (no return-to-first): exactly 8 distinct pockets.
    pts_open = [
        (-10.0, -10.0, 10.0),
        ( 10.0, -10.0, 10.0),
        ( 10.0,  10.0, 10.0),
        (-10.0,  10.0, 10.0),
    ]
    box2 = _make_box()
    r2 = PathPattern().apply(box2, {
        "face_selector": _top_face_selector().model_dump(),
        "profile_diameter_mm": 2.0,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "path_points": pts_open,
        "count": 8,
    })
    pocks2 = _flat_top_pockets(r2.body)
    assert len(pocks2) == 8, (
        f"path_pattern (open) expected 8 distinct pockets, got "
        f"{len(pocks2)}: {pocks2}"
    )


# ── spec / manifest ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("cls,expected_name,expected_feature", [
    (LinearPattern,   "linear_pattern",   "linear_pattern"),
    (CircularPattern, "circular_pattern", "circular_pattern"),
    (MirrorFeature,   "mirror_feature",   "mirror_pair"),
    (PathPattern,     "path_pattern",     "path_pattern"),
])
def test_pattern_spec_metadata(cls, expected_name, expected_feature):
    spec = cls.spec
    assert spec.name == expected_name
    assert spec.category == "modify/pattern"
    assert spec.level == "macro"
    assert expected_feature in spec.produces_features


def test_manifest_includes_patterns():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {"linear_pattern", "circular_pattern", "mirror_feature", "path_pattern"}
    assert expected <= names, f"missing pattern skills: {expected - names}"
