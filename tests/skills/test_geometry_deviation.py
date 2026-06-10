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
