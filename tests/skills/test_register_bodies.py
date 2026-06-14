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

    # Distinct principal moments -> unambiguous principal alignment.
    assert reg["axis_ambiguous"] is False
    assert reg["method"] == "principal"
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
    assert reg["method"] in ("principal", "bbox_fallback")
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
