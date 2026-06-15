"""register_bodies — best-fit rigid transform B -> A. (phase-1, 2026-06-14)

Imports the skill module directly (not via registry) so it passes before the
orchestrator registers the @skill in the manifest.

Ground-truth strategy (task spec item 3):
  - Apply a KNOWN gp_Trsf (rotate 37deg about a tilted axis + translate) to a
    corpus STEP, register the moved body B onto the original A, and assert the
    recovered transform INVERTS the motion. Verification is geometric: the
    emitted transform_4x4 maps B onto A, so applying it to a reference set to
    B (geometry_deviation applies the matrix to its reference) and comparing
    against body A collapses the hausdorff to ~0. rmsd_mm < 0.05 on 3+
    prismatic files.
  - Near-symmetric parts (square plate; the corpus C_0603 / QFN chips have two
    equal principal moments) set axis_ambiguous=True. With both catalogs the
    feature-centroid Kabsch fallback recovers the orientation the inertia
    tensor cannot.
"""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from phone_designer.skills.inspect.geometry_deviation import GeometryDeviation
from phone_designer.skills.inspect.register_bodies import (
    RegisterBodies,
    _is_ambiguous,
    _kabsch,
    _mass_frame,
    _occt_shape,
)

_CORPUS = Path(__file__).resolve().parents[2] / "corpus" / "oem"

# 3+ prismatic files with distinct principal moments (unambiguous).
_PRISMATIC = [
    _CORPUS / "industrial" / "freecad__L_shaped_5_holes_Plate.step",
    _CORPUS / "industrial" / "freecad__T_shaped_5_holes_Plate.step",
    _CORPUS / "industrial" / "freecad__2020_corner_bracket.step",
]


# ──────────────────────────────────────────────────────────────────────────────
# OCCT helpers


def _load_step(path: Path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    r = STEPControl_Reader()
    assert r.ReadFile(str(path)) == IFSelect_RetDone
    r.TransferRoots()
    return r.OneShape()


def _write_step(shape, path: Path) -> str:
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    s = shape.wrapped if hasattr(shape, "wrapped") else shape
    w = STEPControl_Writer()
    assert w.Transfer(s, STEPControl_StepModelType.STEPControl_AsIs) == (
        IFSelect_ReturnStatus.IFSelect_RetDone)
    assert w.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone
    return str(path)


def _moved(shape, angle_deg=37.0, axis=(1.0, 1.0, 1.0), trans=(13.0, -7.5, 4.25),
           pivot=(3.0, 2.0, 1.0)):
    """Apply rotate(angle about axis through pivot) then translate."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(*pivot), gp_Dir(*axis)), math.radians(angle_deg))
    tr = gp_Trsf()
    tr.SetTranslation(gp_Vec(*trans))
    comb = gp_Trsf()
    comb.Multiply(tr)
    comb.Multiply(rot)
    return BRepBuilderAPI_Transform(shape, comb, True).Shape()


def _verify_hausdorff(body_a, shape_b, transform_4x4) -> float:
    """The emitted transform maps B -> A. geometry_deviation applies the
    matrix to its REFERENCE, so set reference = B and body = A: applying the
    B->A transform to the B reference must land it on A. Returns hausdorff_mm.
    """
    with tempfile.TemporaryDirectory() as d:
        ref_b = _write_step(shape_b, Path(d) / "b.step")
        g = GeometryDeviation().apply(body_a, {
            "reference_step_path": ref_b,
            "align": "rigid",
            "transform_4x4": transform_4x4,
            "linear_deflection_mm": 0.1,
        }).extras["geometry_deviation"]
    return g["hausdorff_mm"]


# ──────────────────────────────────────────────────────────────────────────────
# Known gp_Trsf recovered to rmsd < 0.05 on 3+ prismatic files


@pytest.mark.parametrize("path", _PRISMATIC, ids=lambda p: p.name)
def test_recovers_known_rigid_on_prismatic_corpus(path):
    assert path.exists(), f"missing corpus file: {path}"
    A = _load_step(path)
    B = _moved(A)

    reg = RegisterBodies().apply(B, {"reference_shape": A}).extras["registration"]

    # Distinct principal moments -> unambiguous principal alignment. The
    # phase-4 ICP refine is default-on and may tighten the residual further
    # (it never regresses the principal seat), so 'icp' is also acceptable.
    assert reg["axis_ambiguous"] is False
    assert reg["method"] in ("principal", "icp")
    assert reg["rmsd_mm"] < 0.05

    # Geometric ground truth: the recovered transform inverts the motion.
    h = _verify_hausdorff(A, B, reg["transform_4x4"])
    assert h < 0.05, f"recovered transform left hausdorff={h}"


def test_identity_registration_is_near_zero():
    # Registering a body onto itself yields ~identity with sub-mm residual.
    A = _load_step(_PRISMATIC[0])
    reg = RegisterBodies().apply(A, {"reference_shape": A}).extras["registration"]
    assert reg["rmsd_mm"] < 0.05
    T = np.array(reg["transform_4x4"])
    # Rotation ~ identity, translation ~ 0.
    assert np.allclose(T[:3, :3], np.eye(3), atol=1e-3)
    assert np.allclose(T[:3, 3], 0.0, atol=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# Near-symmetric corpus chips -> axis_ambiguous=True


@pytest.mark.parametrize("name", [
    "kicad__C_0603_1608Metric.step",
    "kicad__QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm.step",
])
def test_near_symmetric_chip_flags_axis_ambiguous(name):
    path = _CORPUS / name
    assert path.exists(), f"missing corpus file: {path}"
    A = _load_step(path)
    _c, vals, _vecs = _mass_frame(_occt_shape(A))
    # Two near-equal principal moments -> ambiguous by construction.
    assert _is_ambiguous(vals) is True

    reg = RegisterBodies().apply(A, {"reference_shape": A}).extras["registration"]
    assert reg["axis_ambiguous"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Feature fallback: a near-symmetric square plate the principal stage cannot
# orient, disambiguated by asymmetric feature centroids -> method='feature'.


def _holes_catalog(points):
    return {"holes": [
        {"entry_origin": [float(p[0]), float(p[1]), float(p[2])],
         "diameter_mm": 3.0, "diameters_mm": [3.0]}
        for p in points
    ]}


def test_feature_fallback_disambiguates_symmetric_plate():
    from build123d import Axis, Box, Pos

    # Square plate -> two equal principal moments -> axis_ambiguous.
    A_body = Box(20.0, 20.0, 4.0)
    # Asymmetric, non-coplanar hole pattern that breaks the symmetry.
    A_pts = np.array([[2, 3, 0.5], [15, 4, 2.0], [6, 16, 1.0], [14, 17, 3.0]],
                     dtype=float)

    angle = 37.0
    trans = np.array([13.0, -7.5, 4.25])
    th = math.radians(angle)
    c, s = math.cos(th), math.sin(th)
    Rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    B_pts = (Rz @ A_pts.T).T + trans

    B_body = Pos(*trans) * (A_body.rotate(Axis.Z, angle))

    ca = _holes_catalog(A_pts)
    cb = _holes_catalog(B_pts)

    reg = RegisterBodies().apply(B_body, {
        "reference_shape": A_body,
        "catalog_a": ca,
        "catalog_b": cb,
    }).extras["registration"]

    assert reg["axis_ambiguous"] is True
    assert reg["method"] == "feature"
    assert reg["rmsd_mm"] < 0.05

    # The recovered transform maps B's holes back onto A's holes.
    T = np.array(reg["transform_4x4"])
    mapped = (T[:3, :3] @ B_pts.T).T + T[:3, 3]
    assert np.max(np.linalg.norm(mapped - A_pts, axis=1)) < 0.05


def test_feature_fallback_not_used_without_catalogs():
    # Same symmetric plate but NO catalogs -> stays on the principal estimate.
    from build123d import Box

    A_body = Box(20.0, 20.0, 4.0)
    reg = RegisterBodies().apply(
        A_body, {"reference_shape": A_body}).extras["registration"]
    # No catalogs -> never the feature path. The geometry-only stages
    # (principal / its phase-4 ICP refine / isotropic bbox_fallback) are fine.
    assert reg["method"] in ("principal", "icp", "bbox_fallback")
    assert reg["axis_ambiguous"] is True


# ──────────────────────────────────────────────────────────────────────────────
# bbox_fallback: a fully isotropic body (cube/sphere) -> pure translation.


def test_isotropic_body_uses_bbox_fallback():
    from build123d import Pos, Sphere

    A_body = Sphere(10.0)
    B_body = Pos(7.0, -3.0, 5.0) * Sphere(10.0)

    reg = RegisterBodies().apply(
        B_body, {"reference_shape": A_body}).extras["registration"]
    assert reg["method"] == "bbox_fallback"
    T = np.array(reg["transform_4x4"])
    # Pure translation -> identity rotation, translation = ca - cb.
    assert np.allclose(T[:3, :3], np.eye(3), atol=1e-9)
    # The translation maps B's centroid onto A's (both at their own centers).
    assert reg["rmsd_mm"] < 1e-3


# ──────────────────────────────────────────────────────────────────────────────
# Kabsch unit check + read-only body + arg validation


def test_kabsch_recovers_known_rotation():
    th = math.radians(50.0)
    c, s = math.cos(th), math.sin(th)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    t = np.array([4.0, -2.0, 9.0])
    P = np.array([[0, 0, 0], [5, 0, 1], [0, 5, 2], [5, 5, 3]], dtype=float)
    Q = (R @ P.T).T + t
    Rf, tf, rmsd = _kabsch(P, Q)
    assert rmsd < 1e-9
    assert np.allclose(Rf, R, atol=1e-9)
    assert np.allclose(tf, t, atol=1e-9)


def test_body_is_unchanged():
    from build123d import Box

    body = Box(10, 8, 6)
    res = RegisterBodies().apply(body, {"reference_shape": Box(10, 8, 6)})
    assert res.body is body


def test_missing_reference_raises():
    from build123d import Box

    with pytest.raises(ValueError):
        RegisterBodies().apply(Box(1, 1, 1), {})


# ──────────────────────────────────────────────────────────────────────────────
# phase-4 (2026-06-15) — ICP + symmetry multi-start + the honest downgrade.
#
# register_bodies' principal seed leaves the residual rotation FREE on inertia-
# degenerate parts (cylinders/plates: two equal moments) and a discrete
# ambiguity on globally-symmetric parts. The ICP refine (reusing
# geometry_deviation's _TriGrid closest-point-on-triangle, no new dep) locks
# that residual; symmetry multi-start picks the MIN-residual seating; and when
# NO seating resolves (genuinely-ambiguous part) the skill emits
# registration_confidence='low' rather than a confidently-WRONG transform.


def _rotated_about_axis(shape, axis, deg, pivot=(0.0, 0.0, 0.0)):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(*pivot), gp_Dir(*axis)), math.radians(deg))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def test_icp_improves_degenerate_seed_cylinder():
    """A cylinder rotated about its OWN symmetry axis is inertia-degenerate:
    the principal seed cannot pin the rotation. ICP locks it — the geometric
    residual drops materially vs the principal-only path, and the seating
    stays geometrically valid (rmsd small, confidence not 'low')."""
    from build123d import Cylinder

    A = Cylinder(radius=8.0, height=30.0)
    B = _rotated_about_axis(A.wrapped, (0.0, 0.0, 1.0), 57.0)

    no_icp = RegisterBodies().apply(
        B, {"reference_shape": A, "icp_refine": False}).extras["registration"]
    with_icp = RegisterBodies().apply(
        B, {"reference_shape": A, "icp_refine": True}).extras["registration"]

    # Degenerate part -> flagged ambiguous in both runs.
    assert no_icp["axis_ambiguous"] is True
    assert with_icp["axis_ambiguous"] is True
    # ICP ran and reported its own residual.
    assert with_icp["icp_rmsd_mm"] is not None
    assert with_icp["method"] == "icp"
    # ICP did not regress and materially tightened the seed.
    assert with_icp["rmsd_mm"] <= no_icp["rmsd_mm"] + 1e-9
    assert with_icp["rmsd_mm"] < 0.5 * no_icp["rmsd_mm"] + 1e-6
    # A geometrically-valid seat -> not the 'low' honest fallback.
    assert with_icp["registration_confidence"] in ("high", "medium")


def test_symmetry_multistart_picks_min_hausdorff_square_plate():
    """A square plate rotated 90 deg about Z is globally symmetric: several
    discrete seatings are plausible. Multi-start must pick the one that
    superimposes the parts -> applying the recovered transform collapses the
    hausdorff to ~0 (NOT a flipped/offset wrong seating)."""
    from build123d import Box

    A = Box(20.0, 20.0, 3.0)
    B_shape = _rotated_about_axis(A.wrapped, (0.0, 0.0, 1.0), 90.0)

    reg = RegisterBodies().apply(
        B_shape, {"reference_shape": A}).extras["registration"]
    assert reg["axis_ambiguous"] is True

    # The recovered B->A transform maps B back onto A: hausdorff ~ 0.
    # (_verify_hausdorff writes shape_b via _write_step, which accepts a raw
    # TopoDS_Shape, and applies the matrix to that reference.)
    h = _verify_hausdorff(A, B_shape, reg["transform_4x4"])
    assert h < 0.05, f"multi-start picked a wrong seating, hausdorff={h}"
    assert reg["registration_confidence"] in ("high", "medium")


def test_unresolvable_part_downgrades_to_low_confidence_not_wrong_transform():
    """A cone vs a cylinder: inertia-degenerate AND no rigid motion
    superimposes them. The HONEST fallback fires — registration_confidence
    ='low' + axis_ambiguous=True, with a LARGE normalised residual — rather
    than a confidently-wrong transform reported as trustworthy."""
    from build123d import Cone, Cylinder

    A = Cylinder(radius=10.0, height=30.0)
    B = Cone(bottom_radius=10.0, top_radius=2.0, height=30.0)

    reg = RegisterBodies().apply(
        B, {"reference_shape": A}).extras["registration"]

    assert reg["axis_ambiguous"] is True
    # No seating resolves -> the residual stays large relative to the part.
    assert reg["rmsd_norm"] is not None and reg["rmsd_norm"] > 0.02
    assert reg["registration_confidence"] == "low"
    # The numeric confidence is clamped down so callers do not trust it.
    assert reg["confidence"] <= 0.3


def test_icp_is_time_boxed():
    """The ICP loop honours its iteration cap (the KR600 900s lesson)."""
    from phone_designer.skills.inspect.register_bodies import _ICP_MAX_ITERS

    assert _ICP_MAX_ITERS <= 20


@pytest.mark.parametrize("path", [
    _CORPUS / "industrial" / "freecad__608ZZ_Ball_Bearing.step",
])
def test_corpus_bearing_rotated_about_axis_resolves(path):
    """A real corpus bearing (a cylinder — inertia-degenerate) rotated about
    its symmetry axis: ICP locks the residual to well under 1% bbox and the
    seating is high-confidence (rmsd < 1% bbox after ICP, per the task)."""
    assert path.exists(), f"missing corpus file: {path}"
    A = _load_step(path)
    B = _rotated_about_axis(A, (0.0, 0.0, 1.0), 57.0)

    reg = RegisterBodies().apply(B, {"reference_shape": A}).extras["registration"]
    assert reg["axis_ambiguous"] is True
    assert reg["method"] == "icp"
    assert reg["rmsd_norm"] is not None and reg["rmsd_norm"] < 0.01
    assert reg["registration_confidence"] == "high"


def test_low_confidence_propagates_through_compare_parts():
    """compare_parts must surface register_bodies' registration_confidence so a
    degenerate / ambiguous pair is flagged low-confidence rather than yielding
    a confidently-WRONG delta. Two DIFFERENT bearings (both inertia-degenerate)
    have no exact rigid superposition -> 'low' propagates verbatim."""
    from phone_designer.skills.reverse_engineer.compare_parts import CompareParts

    a = _CORPUS / "industrial" / "freecad__608ZZ_Ball_Bearing.step"
    b = _CORPUS / "industrial" / "freecad__623ZZ_Ball_Bearing.step"
    if not (a.exists() and b.exists()):
        pytest.skip("corpus bearings not present")

    out = CompareParts().apply(
        None, {"part_a_path": str(a), "part_b_path": str(b)}
    ).extras["part_comparison"]
    reg = out["registration"]
    assert reg is not None
    # The new fields are carried through compare_parts unchanged.
    assert "registration_confidence" in reg
    assert reg["registration_confidence"] == "low"
    assert reg["axis_ambiguous"] is True
