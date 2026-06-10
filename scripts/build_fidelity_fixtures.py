"""Regenerate every round-trip fidelity fixture into fixtures/generated/.

The CASES in tests/test_round_trip_fidelity.py used to point at gitignored
run_logs/_tmp/*.step artifacts, so the strict fidelity gate failed on any
fresh checkout. This script rebuilds all 10 fixtures deterministically with
build123d + the project's own atomic skills (logic ported from the original
untracked builders in run_logs/_tmp/ and fixtures/make_simple_watch.py).

Fixtures produced (all under fixtures/generated/):
  simple_watch.step              -- XDE watch assembly (fixtures/make_simple_watch.py)
  simple_watch_housing_only.step -- housing only (same source)
  demo_advanced.step             -- box + hex/slot/blended pockets (demo_advanced.py)
  demo_boss_sweep_loft.step      -- box + swept boss + loft boss (demo_boss_sweep_loft.py)
  demo_sweep_revolve.step        -- box + swept pockets + revolve groove/ring
  demo_patterns_2_circular.step  -- box + linear + circular pocket patterns
  auto_repro.step                -- RE pipeline output for the housing (phase3 scenario)
  fixture_threaded.step          -- box + M3 clearance hole (build_round6_fixtures.py)
  fixture_magnet.step            -- box + N42_D5x2 magnet pocket (same source)
  fixture_text.step              -- box + 'AB' text engrave (same source)

Exit codes:
  0 — every fixture written and non-empty.
  1 — at least one builder raised; the failing fixture names are listed.

Usage (from repo root):
    venv/Scripts/python.exe scripts/build_fidelity_fixtures.py [--out-dir fixtures/generated]
"""
from __future__ import annotations

import argparse
import math
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def _ensure_import_path() -> None:
    """Allow running the script directly without `pip install -e .`."""
    src = PROJECT / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    fixtures = PROJECT / "fixtures"
    if fixtures.exists() and str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))


_ensure_import_path()


# ---------------------------------------------------------------------------
# Shared helpers


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    shape = body.wrapped if hasattr(body, "wrapped") else body
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _write_step(body, out_path: Path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    shape = body.wrapped if hasattr(body, "wrapped") else body
    writer = STEPControl_Writer()
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError(f"STEP transfer failed for {out_path.name}")
    if writer.Write(str(out_path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed for {out_path.name}")


def _read_step(path: Path):
    """STEP → build123d Part (same loader the fidelity test uses)."""
    from build123d import Part
    from OCP.STEPControl import STEPControl_Reader
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    return Part(reader.OneShape())


# ---------------------------------------------------------------------------
# Builders — one function per fixture file. Each returns the written Path.


def build_simple_watch(out_dir: Path) -> list[Path]:
    """Watch assembly + housing-only — reuse fixtures/make_simple_watch.py."""
    import make_simple_watch as msw

    housing = msw.make_housing()
    parts = [
        ("housing", housing),
        ("display", msw.make_display()),
        ("battery", msw.make_battery()),
        ("crown", msw.make_crown()),
        ("lug_pair", msw.make_lug_pair()),
    ]
    assembly_path = out_dir / "simple_watch.step"
    msw.write_xde_step(parts, assembly_path)

    housing_path = out_dir / "simple_watch_housing_only.step"
    msw.write_single_step(housing, housing_path, name="housing")
    return [assembly_path, housing_path]


def build_demo_advanced(out_dir: Path) -> list[Path]:
    """40x40x10 box + hex pocket (vertex fillet) + slot pocket + blended pocket."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.extrude_pocket import ExtrudePocket
    from phone_designer.skills.modify_pocket.extrude_pocket_blended import (
        ExtrudePocketBlended,
    )

    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 40.0, "height_mm": 10.0,
    }).body

    # hex pocket with vertex fillet (offset to the -X side)
    r_hex = 4.0
    verts = [
        (r_hex * math.cos(math.radians(60 * i)),
         r_hex * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]
    body = ExtrudePocket().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "polygon", "vertices": verts,
            "corner_fillet_r": 0.4,
            "center_x_mm": -10.0, "center_y_mm": 0.0,
        },
        "depth_mm": 2.5,
    }).body

    # slot pocket on +X side
    body = ExtrudePocket().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "slot",
            "length_mm": 12.0, "width_mm": 4.0,
            "center_x_mm": 10.0, "center_y_mm": 0.0,
            "rotation_deg": 30.0,
        },
        "depth_mm": 2.0,
    }).body

    # circular blended pocket at center
    body = ExtrudePocketBlended().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {
            "kind": "circle", "diameter_mm": 6.0,
            "center_x_mm": 0.0, "center_y_mm": 12.0,
        },
        "depth_mm": 2.5,
        "floor_blend_r_mm": 0.4,
        "opening_blend_r_mm": 0.2,
    }).body

    out = out_dir / "demo_advanced.step"
    _write_step(body, out)
    return [out]


def build_demo_boss_sweep_loft(out_dir: Path) -> list[Path]:
    """50x50x10 box + swept polyline boss + loft boss."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_boss.loft_boss_between_sketches import (
        LoftBossBetweenSketches,
    )
    from phone_designer.skills.modify_boss.swept_boss_along_curve import (
        SweptBossAlongCurve,
    )

    body = Box().apply(None, {
        "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0,
    }).body

    # swept polyline boss — L-shaped ridge on top
    body = SweptBossAlongCurve().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (-15.0, -10.0, 10.0),
            (-15.0, -10.0, 14.0),
            (-5.0, -10.0, 14.0),
            (-5.0, 5.0, 14.0),
        ],
        "path_type": "polyline",
        "top_blend_r_mm": 0.3,
    }).body

    # loft boss — circle (D=10) → smaller circle (D=4), height 5
    body = LoftBossBetweenSketches().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "lower_sketch": {"kind": "circle", "diameter_mm": 10.0,
                          "center_x_mm": 10.0, "center_y_mm": 10.0},
        "upper_sketch": {"kind": "circle", "diameter_mm": 4.0,
                          "center_x_mm": 10.0, "center_y_mm": 10.0},
        "height_mm": 5.0,
        "base_blend_r_mm": 0.4,
    }).body

    out = out_dir / "demo_boss_sweep_loft.step"
    _write_step(body, out)
    return [out]


def build_demo_sweep_revolve(out_dir: Path) -> list[Path]:
    """50x50x20 box + swept polyline/bspline pockets + revolve groove + boss ring."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_boss.revolve_boss import RevolveBoss
    from phone_designer.skills.modify_pocket.revolve_pocket import RevolvePocket
    from phone_designer.skills.modify_pocket.swept_pocket_along_curve import (
        SweptPocketAlongCurve,
    )

    body = Box().apply(None, {
        "length_mm": 50.0, "width_mm": 50.0, "height_mm": 20.0,
    }).body

    # swept polyline pocket — L-shape across top
    body = SweptPocketAlongCurve().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 4.0},
        "path_points": [
            (-15.0, -15.0, 20.0),
            (-15.0, -15.0, 17.0),
            (-15.0, 10.0, 17.0),
            (10.0, 10.0, 17.0),
        ],
        "path_type": "polyline",
    }).body

    # swept B-spline pocket — smooth S-curve
    body = SweptPocketAlongCurve().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "profile_sketch": {"kind": "circle", "diameter_mm": 3.0},
        "path_points": [
            (-15.0, 0.0, 20.0),
            (-7.0, 5.0, 18.0),
            (7.0, -5.0, 18.0),
            (15.0, 0.0, 20.0),
        ],
        "path_type": "bspline",
    }).body

    # revolved annular groove on top — small rectangle profile
    body = RevolvePocket().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 1.5, "width_mm": 1.0,
            "center_x_mm": 18.0, "center_y_mm": -0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 360.0,
    }).body

    # revolved boss ring above the top
    body = RevolveBoss().apply(body, {
        "profile_sketch": {
            "kind": "rectangle",
            "length_mm": 2.0, "width_mm": 2.0,
            "center_x_mm": 22.0, "center_y_mm": 0.5,
        },
        "axis_origin": (0.0, 0.0, 20.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "angle_deg": 360.0,
    }).body

    out = out_dir / "demo_sweep_revolve.step"
    _write_step(body, out)
    return [out]


def build_demo_patterns_2_circular(out_dir: Path) -> list[Path]:
    """50x50x10 box + linear pocket row + circular pocket ring (patterns demo
    through step 2 — the fidelity CASE only uses the _2_circular snapshot)."""
    from phone_designer.skills._selectors import FacesByNormalSelector
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pattern.circular_pattern import CircularPattern
    from phone_designer.skills.modify_pattern.linear_pattern import LinearPattern

    top = FacesByNormalSelector(direction=(0.0, 0.0, 1.0), tol_deg=5.0).model_dump()

    body = Box().apply(None, {"length_mm": 50, "width_mm": 50, "height_mm": 10}).body

    # linear_pattern: 5 pocket holes
    body = LinearPattern().apply(body, {
        "face_selector": top,
        "profile_diameter_mm": 2.0,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "count": 5,
        "spacing_mm": 6.0,
        "direction": "X",
        "start_offset_x_mm": -12.0,
        "start_offset_y_mm": -15.0,
    }).body

    # circular_pattern: 6 pocket holes (full ring)
    body = CircularPattern().apply(body, {
        "face_selector": top,
        "profile_diameter_mm": 2.5,
        "operation": "pocket",
        "feature_depth_mm": 4.0,
        "count": 6,
        "pitch_radius_mm": 12.0,
        "center_x_mm": 0.0,
        "center_y_mm": 5.0,
        "start_angle_deg": 0.0,
        "total_sweep_deg": 360.0,
    }).body

    out = out_dir / "demo_patterns_2_circular.step"
    _write_step(body, out)
    return [out]


def build_auto_repro(out_dir: Path) -> list[Path]:
    """RE pipeline output for the watch housing — same provenance as the
    phase3_reverse_engineer scenario (`phone-designer reproduce`), run
    in-process: extract catalog → plan → execute → export."""
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import load_plan
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        PlanFromFeatureCatalog,
    )

    housing_path = out_dir / "simple_watch_housing_only.step"
    if not housing_path.exists():
        raise RuntimeError(
            "simple_watch_housing_only.step missing — build_simple_watch must run first"
        )
    body = _read_step(housing_path)

    cat_result = ExtractFeatureCatalog().apply(body, {})
    plan_result = PlanFromFeatureCatalog().apply(
        body, {"catalog": cat_result.extras["feature_catalog"]},
    )
    plan_path = plan_result.extras.get("plan_path") or "plans/reconstructed_plan.yaml"
    plan = load_plan(plan_path)
    exec_result = PlanExecutor(plan).run()
    if exec_result.final_body is None:
        raise RuntimeError("auto_repro: executor returned None body")

    out = out_dir / "auto_repro.step"
    _write_step(exec_result.final_body, out)
    return [out]


def build_fixture_threaded(out_dir: Path) -> list[Path]:
    """Box 30x30x10 + M3 clearance hole (through) at top center."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.clearance_hole import ClearanceHole

    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 10.0,
    }).body
    body = ClearanceHole().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "thread_spec": "M3",
        "fit": "medium",
        "depth_mm": 10.0,
        "through": True,
    }).body
    out = out_dir / "fixture_threaded.step"
    _write_step(body, out)
    return [out]


def build_fixture_magnet(out_dir: Path) -> list[Path]:
    """Box 30x30x8 + N42_D5x2 magnet pocket at top center."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.magnet_pocket_axial import (
        MagnetPocketAxial,
    )

    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 8.0,
    }).body
    body = MagnetPocketAxial().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "magnet_spec": "N42_D5x2",
        "retention": "press",
    }).body
    out = out_dir / "fixture_magnet.step"
    _write_step(body, out)
    return [out]


def build_fixture_text(out_dir: Path) -> list[Path]:
    """Box 40x20x10 + engrave 'AB' depth 0.5 on top."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.text_engrave import TextEngrave

    body = Box().apply(None, {
        "length_mm": 40.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    body = TextEngrave().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "text": "AB",
        "font_name": "Arial",
        "font_size_mm": 6.0,
        "depth_mm": 0.5,
    }).body
    out = out_dir / "fixture_text.step"
    _write_step(body, out)
    return [out]


# Order matters: build_auto_repro consumes simple_watch_housing_only.step.
BUILDERS = [
    build_simple_watch,
    build_demo_advanced,
    build_demo_boss_sweep_loft,
    build_demo_sweep_revolve,
    build_demo_patterns_2_circular,
    build_auto_repro,
    build_fixture_threaded,
    build_fixture_magnet,
    build_fixture_text,
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=PROJECT / "fixtures" / "generated")
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for builder in BUILDERS:
        name = builder.__name__
        print(f">>> {name} ...", flush=True)
        try:
            for path in builder(out_dir):
                size = path.stat().st_size
                if size == 0:
                    raise RuntimeError(f"{path.name} written but empty")
                vol = _volume(_read_step(path))
                print(f"    {path.name}: {size} bytes, vol={vol:.1f} mm^3")
        except Exception:
            failures.append(name)
            traceback.print_exc()

    if failures:
        print(f"\nFAILED builders: {', '.join(failures)}")
        return 1
    print(f"\nAll fidelity fixtures written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
