"""geometry_deviation — geometric ground truth vs a reference STEP.

Read-only. Imports the skill module directly (not via registry) so the
tests pass before the orchestrator registers the skill in the manifest.

Honest tolerance notes:
  - Box faces are planar → tessellation chord error is 0, so box deviations
    are measured (near-)exactly.
  - Sphere meshes at deflection d lie *inside* the true sphere by ≤ d, so a
    radial offset of 0.2 mm measures as hausdorff ∈ [0.2, 0.2 + d].
"""
from __future__ import annotations

from pathlib import Path

from phone_designer.skills.inspect.geometry_deviation import GeometryDeviation


def _write_step(part, path: Path) -> str:
    """build123d Part → STEP file. Returns the path as str."""
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    shape = part.wrapped if hasattr(part, "wrapped") else part
    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    assert status == IFSelect_ReturnStatus.IFSelect_RetDone
    write_status = writer.Write(str(path))
    assert write_status == IFSelect_ReturnStatus.IFSelect_RetDone
    return str(path)


def _box(length: float, width: float, height: float):
    """Box with its base on z=0 — offsetting only the height moves only the
    top face, so the expected hausdorff equals the height delta."""
    from build123d import Align, Box

    return Box(length, width, height,
               align=(Align.CENTER, Align.CENTER, Align.MIN))


# ──────────────────────────────────────────────────────────────────────────────
# identical body vs itself


def test_identical_box_vs_itself_is_zero(tmp_path):
    box = _box(40, 30, 20)
    ref = _write_step(box, tmp_path / "ref_box.step")

    r = GeometryDeviation().apply(box, {"reference_step_path": ref})
    g = r.extras["geometry_deviation"]

    assert g["hausdorff_mm"] < 1e-6          # well below the 0.2 deflection
    assert g["rms_mm"] < 1e-6
    assert abs(g["volume_delta_pct"]) < 1e-6
    assert abs(g["surface_area_delta_pct"]) < 1e-6
    assert g["fallback_fraction"] == 0.0
    assert g["sample_counts"]["body_to_ref"] > 0
    assert g["sample_counts"]["ref_to_body"] > 0
    # read-only — body unchanged
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# 100 mm cube vs cube with the top face offset +0.5 mm


def test_cube_with_offset_face_hausdorff_and_volume(tmp_path):
    ref_cube = _box(100, 100, 100)
    body = _box(100, 100, 100.5)
    ref = _write_step(ref_cube, tmp_path / "ref_cube.step")

    r = GeometryDeviation().apply(body, {"reference_step_path": ref})
    g = r.extras["geometry_deviation"]

    # Top face moved +0.5 mm; planar meshes are exact, so hausdorff ≈ 0.5.
    assert 0.4 <= g["hausdorff_mm"] <= 0.6
    # Volume: 100*100*100.5 vs 100³ → +0.5 % (GProp is exact).
    assert abs(g["volume_delta_pct"] - 0.5) < 0.01
    # Area: 2*(100*100) + 4*(100*100.5) vs 6*(100*100) → +0.333 %.
    assert abs(g["surface_area_delta_pct"] - 1.0 / 3.0) < 0.01
    assert r.body is body


# ──────────────────────────────────────────────────────────────────────────────
# sphere r=10 vs r=10.2 — curved surfaces, mesh error matters


def test_sphere_radius_offset(tmp_path):
    from build123d import Sphere

    body = Sphere(10.2)
    ref = _write_step(Sphere(10.0), tmp_path / "ref_sphere.step")

    r = GeometryDeviation().apply(body, {
        "reference_step_path": ref,
        "linear_deflection_mm": 0.05,
    })
    g = r.extras["geometry_deviation"]

    # True radial deviation is 0.2 mm everywhere. Mesh vertices lie ON the
    # spheres while chords sag inward by ≤ 0.05, so the measured hausdorff
    # is bounded by [0.2 - 0.05, 0.2 + 0.05]; we allow a small numeric pad.
    assert 0.14 <= g["hausdorff_mm"] <= 0.26
    assert 0.10 <= g["rms_mm"] <= 0.26
    assert g["p95_mm"] <= g["hausdorff_mm"] + 1e-9
    # Volume: (10.2³ - 10³)/10³ = +6.1208 % (exact GProp, STEP roundtrip).
    assert abs(g["volume_delta_pct"] - 6.1208) < 0.05
    # Area: (10.2² - 10²)/10² = +4.04 %.
    assert abs(g["surface_area_delta_pct"] - 4.04) < 0.05
    assert r.body is body


# ──────────────────────────────────────────────────────────────────────────────
# align='rigid' (phase-0) — transform_4x4 applied to the reference


def _rigid_4x4(axis, angle_deg, translation):
    """Build a 4x4 row-major rigid matrix: rotate angle_deg about ``axis``
    (a unit 3-vector) through the origin, then translate."""
    import math

    import numpy as np

    ax = np.asarray(axis, dtype=float)
    ax = ax / np.linalg.norm(ax)
    x, y, z = ax
    th = math.radians(angle_deg)
    c, s, t = math.cos(th), math.sin(th), 1.0 - math.cos(th)
    R = np.array([
        [t * x * x + c,     t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c,     t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
    ])
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = np.asarray(translation, dtype=float)
    return M


def test_rigid_identity_4x4_is_zero(tmp_path):
    box = _box(40, 30, 20)
    ref = _write_step(box, tmp_path / "ref_id.step")

    r = GeometryDeviation().apply(box, {
        "reference_step_path": ref,
        "align": "rigid",
        "transform_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
    })
    g = r.extras["geometry_deviation"]

    # Identity transform leaves the reference untouched -> perfect self-match.
    assert g["hausdorff_mm"] < 1e-6
    assert g["rms_mm"] < 1e-6
    assert g["align"] == "rigid"
    assert r.body is box


def test_rigid_recovers_rotated_translated_body(tmp_path):
    import numpy as np
    from build123d import Location

    # The reference is the body rotated 37deg about a tilted axis + translated.
    # Applying the INVERSE rigid transform to the reference must cancel it,
    # so the measured hausdorff collapses back to ~0 (proves rigid works).
    box = _box(40, 30, 20)

    axis = (1.0, 1.0, 1.0)
    angle = 37.0
    trans = (13.0, -7.5, 4.25)
    M = _rigid_4x4(axis, angle, trans)
    M_inv = np.linalg.inv(M)

    # Build the moved reference with build123d's own rigid Location so the
    # STEP carries the exact forward transform M (rotation about origin, then
    # translation). Location composes left-to-right: translate ∘ rotate.
    rot = Location((0, 0, 0), axis, angle)
    moved = (Location(trans) * rot) * box
    ref = _write_step(moved, tmp_path / "ref_moved_rigid.step")

    # Without alignment the rotation+translation dominates.
    r_none = GeometryDeviation().apply(box, {"reference_step_path": ref})
    assert r_none.extras["geometry_deviation"]["hausdorff_mm"] > 5.0

    # Applying M_inv to the reference cancels the forward motion.
    r = GeometryDeviation().apply(box, {
        "reference_step_path": ref,
        "align": "rigid",
        "transform_4x4": M_inv.tolist(),
    })
    g = r.extras["geometry_deviation"]
    assert g["hausdorff_mm"] < 1e-3        # rigid round-trip, near-exact
    assert g["rms_mm"] < 1e-3
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# per_region (phase-0) — a single 0.5mm face offset localizes to one region


def test_per_region_localizes_single_face_offset(tmp_path):
    # Two stacked unit-area regions far apart along X: a 0.5mm top-face
    # offset on a 100x100x100 cube. Centroids at the two end faces should
    # show the deviation bucketed to the moved (top) face's neighborhood.
    ref_cube = _box(100, 100, 100)
    body = _box(100, 100, 100.5)
    ref = _write_step(ref_cube, tmp_path / "ref_region.step")

    # Centroid near the TOP face (z≈100) vs near the BOTTOM face (z≈0).
    top_centroid = [0.0, 0.0, 100.0]
    bottom_centroid = [0.0, 0.0, 0.0]

    r = GeometryDeviation().apply(body, {
        "reference_step_path": ref,
        "region_centroids": [top_centroid, bottom_centroid],
    })
    g = r.extras["geometry_deviation"]

    assert "per_region" in g
    pr = g["per_region"]
    assert len(pr) == 2
    assert pr[0]["centroid"] == top_centroid
    assert pr[1]["centroid"] == bottom_centroid
    # The 0.5mm offset is on the top face -> top region carries the deviation,
    # bottom region stays ~0.
    assert pr[0]["max_mm"] > 0.4
    assert pr[1]["max_mm"] < 0.05
    assert pr[0]["n"] > 0
    assert pr[1]["n"] > 0


def test_per_region_absent_by_default(tmp_path):
    # Back-compat: no region_centroids -> the key is omitted entirely.
    box = _box(40, 30, 20)
    ref = _write_step(box, tmp_path / "ref_noreg.step")
    r = GeometryDeviation().apply(box, {"reference_step_path": ref})
    assert "per_region" not in r.extras["geometry_deviation"]


# ──────────────────────────────────────────────────────────────────────────────
# align='bbox_center' — world-frame original vs box-local rebuild


def test_align_bbox_center_cancels_translation(tmp_path):
    from build123d import Location

    body = _box(40, 30, 20)
    moved = Location((37.0, -12.5, 5.0)) * _box(40, 30, 20)
    ref = _write_step(moved, tmp_path / "ref_moved.step")

    # Without alignment the offset dominates.
    r_none = GeometryDeviation().apply(body, {"reference_step_path": ref})
    assert r_none.extras["geometry_deviation"]["hausdorff_mm"] > 10.0

    # bbox_center alignment recovers the perfect match.
    r = GeometryDeviation().apply(body, {
        "reference_step_path": ref,
        "align": "bbox_center",
    })
    g = r.extras["geometry_deviation"]
    assert g["hausdorff_mm"] < 1e-6
    assert abs(g["volume_delta_pct"]) < 1e-6
    assert r.body is body
