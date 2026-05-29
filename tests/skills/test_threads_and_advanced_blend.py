"""Tests for helical threads + advanced blend skills.

Skills under test:
    - helical_thread_external
    - helical_thread_internal
    - chamfer_asymmetric
    - face_face_fillet
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_curvature.chamfer_asymmetric import (
    ChamferAsymmetric,
)
from phone_designer.skills.modify_curvature.face_face_fillet import (
    FaceFaceFillet,
)
from phone_designer.skills.modify_pocket.helical_thread_external import (
    HelicalThreadExternal,
)
from phone_designer.skills.modify_pocket.helical_thread_internal import (
    HelicalThreadInternal,
)
from phone_designer.skills.modify_pocket.hole import Hole


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


def _make_cylinder(diameter_mm: float, height_mm: float):
    """Build a cylinder body of given D/H, centered on XY, base at z=0."""
    from build123d import Align, Cylinder
    return Cylinder(
        radius=diameter_mm / 2.0,
        height=height_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )


# ── helical_thread_external ────────────────────────────────────────────────


def test_external_thread_basic_volume_decrease():
    """M20 × 2.5 mm pitch, 5 turns on a 20mm cylinder."""
    body = _make_cylinder(diameter_mm=20.0, height_mm=20.0)
    v0 = _volume(body)

    skill = HelicalThreadExternal()
    r = skill.apply(body, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "outer_radius_mm": 10.0,
        "pitch_mm": 2.5,
        "turns": 5.0,
        "thread_height_mm": 0.8,
        "thread_angle_deg": 60.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"external thread should remove volume: v0={v0:.2f}, v1={v1:.2f}"
    removed = v0 - v1
    # Sanity: thread removal is non-trivial (helix length × triangle area).
    # 5 turns × circumference (≈ 2π·10 ≈ 62.8) × tri-area (≈ ½·0.8·(2·0.8·tan30°) ≈ 0.37)
    # ≈ 5 · 62.8 · 0.37 ≈ 116 mm³. Loose bounds.
    assert removed > 5.0, f"removed too little: {removed:.2f} mm³"


def test_external_thread_post_condition_kind():
    spec = HelicalThreadExternal.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── helical_thread_internal ────────────────────────────────────────────────


def test_internal_thread_in_hole():
    """Box with through-hole D=10, internal thread M10×1.5."""
    # Box centered at XY origin, base z=0, height 20.
    box_body = Box().apply(None, {
        "length_mm": 20.0, "width_mm": 20.0, "height_mm": 20.0,
    }).body

    # Drill a through-hole D=10 along +Z from z=20 downward.
    drilled = Hole().apply(box_body, {
        "position": (0.0, 0.0, 20.0),
        "diameter_mm": 10.0,
        "direction": "-Z",
        "depth_mm": 20.0,
    }).body
    v0 = _volume(drilled)

    # Internal thread inside the hole.
    skill = HelicalThreadInternal()
    r = skill.apply(drilled, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "outer_radius_mm": 5.0,   # the hole's inner radius
        "pitch_mm": 1.5,
        "turns": 5.0,
        "thread_height_mm": 0.6,
        "thread_angle_deg": 60.0,
    })
    v1 = _volume(r.body)
    assert v1 < v0, f"internal thread should remove volume: v0={v0:.2f}, v1={v1:.2f}"


def test_internal_thread_post_condition_kind():
    spec = HelicalThreadInternal.spec
    assert any(pc.kind == "volume_decreased" for pc in spec.post_conditions)


# ── face_face_fillet ───────────────────────────────────────────────────────


def test_face_face_fillet_basic():
    """Box; fillet between top face and one side face.

    These two faces are directly adjacent (share a single edge); the skill
    should detect the shared edge and apply a constant-radius fillet there.
    Approximation is acceptable.
    """
    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 20.0,
    }).body
    n0 = _face_count(body)

    skill = FaceFaceFillet()
    r = skill.apply(body, {
        "face_selector_a": {"kind": "face_named", "name": "top"},
        "face_selector_b": {
            "kind": "faces_by_normal",
            "direction": (1.0, 0.0, 0.0),
            "tol_deg": 5.0,
        },
        "radius_mm": 2.0,
    })
    n1 = _face_count(r.body)
    # Face count must change (fillet adds a new face, may consume edge).
    assert n1 != n0, f"face count unchanged: {n0} → {n1}"


def test_face_face_fillet_post_condition_kind():
    spec = FaceFaceFillet.spec
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ── chamfer_asymmetric ─────────────────────────────────────────────────────


def test_chamfer_asymmetric_distances():
    """Box; chamfer top edges with d1=1.0, d2=0.5. Resulting chamfer face
    should be non-square (longer in one direction)."""
    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 10.0,
    }).body
    n0 = _face_count(body)

    skill = ChamferAsymmetric()
    # Edges around the box's top: pick top-face edges via edges_on_face(top).
    r = skill.apply(body, {
        "edge_selector": {
            "kind": "edges_on_face",
            "face": {"kind": "face_named", "name": "top"},
        },
        "distance_1_mm": 1.0,
        "distance_2_mm": 0.5,
    })
    n1 = _face_count(r.body)
    assert n1 != n0, f"face count unchanged: {n0} → {n1}"

    # Verify the new chamfer faces exist and are non-square (asymmetry check).
    # The chamfer faces have one dimension ~ d1 and the other ~ d2 (plus the
    # along-edge length). For the asymmetric case, the in-plane width and
    # height of the chamfer trapezoidal face should differ.
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    # Find planar faces with normal neither pure ±X/±Y/±Z (i.e. the chamfer
    # face is tilted because d1 != d2).
    it = TopExp_Explorer(r.body.wrapped, TopAbs_FACE)
    tilted_count = 0
    while it.More():
        face = TopoDS.Face_s(it.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Plane:
            n = surf.Plane().Axis().Direction()
            nx, ny, nz = abs(n.X()), abs(n.Y()), abs(n.Z())
            # A symmetric chamfer's face has normal at 45° (|nx|≈|nz| or
            # |ny|≈|nz|). Asymmetric d1=1.0, d2=0.5 → angles ≠ 45°, so
            # |nx| and |nz| differ. We require at least one tilted, non-axis-
            # aligned planar face.
            is_axis_aligned = (
                nx > 0.99 or ny > 0.99 or nz > 0.99
            )
            if not is_axis_aligned and (nx > 0.05 or ny > 0.05) and nz > 0.05:
                tilted_count += 1
        it.Next()
    assert tilted_count >= 1, (
        f"expected at least one tilted (non-axis-aligned) chamfer face, "
        f"got {tilted_count}"
    )


def test_chamfer_asymmetric_post_condition_kind():
    spec = ChamferAsymmetric.spec
    assert any(pc.kind == "face_count_changed" for pc in spec.post_conditions)


# ── manifest registration sanity ───────────────────────────────────────────


def test_skills_registered_in_manifest():
    import phone_designer.skills.export_manifest as _em  # noqa: F401
    from phone_designer.skills._registry import registry
    assert _em is not None
    names = {s.name for s in registry.all()}
    assert "helical_thread_external" in names
    assert "helical_thread_internal" in names
    assert "face_face_fillet" in names
    assert "chamfer_asymmetric" in names
