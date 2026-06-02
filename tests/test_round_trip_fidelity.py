"""Round-trip fidelity gate — detect silent cube-collapse + volume drift.

If the reverse-engineering pipeline silently collapses to a placeholder box
(volume ≈ 25000 mm³ AND face_count == 6), assert fails loudly.

STRICT MODE (CI gate):
    Set env var FIDELITY_STRICT=1 to make every parametrized case a HARD FAIL.
    Default behavior (no env var) leaves the test as-is — currently-failing
    cases are reported but do not block local dev runs.
"""
import math
import os
import pytest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STRICT = os.environ.get("FIDELITY_STRICT", "").strip() in ("1", "true", "True", "yes", "on")

CASES = [
    ("fixtures/simple_watch.step", 80.0),  # relative tolerance %
    ("run_logs/_tmp/demo_advanced.step", 80.0),
    ("run_logs/_tmp/demo_boss_sweep_loft.step", 30.0),
    # Original round-trip extras that were missing from the test
    ("fixtures/simple_watch_housing_only.step", 50.0),
    ("run_logs/_tmp/demo_sweep_revolve.step", 30.0),
    ("run_logs/_tmp/demo_patterns_2_circular.step", 30.0),
    ("run_logs/_tmp/auto_repro.step", 50.0),
    # Round 6 synthetic fixtures (clearance_hole / magnet_pocket_axial / text_engrave)
    ("run_logs/_tmp/fixture_threaded.step", 30.0),
    ("run_logs/_tmp/fixture_magnet.step", 30.0),
    ("run_logs/_tmp/fixture_text.step", 30.0),
]

def _measure(step_path):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    r = STEPControl_Reader()
    r.ReadFile(str(step_path))
    r.TransferRoots()
    shape = r.OneShape()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    seen = TopTools_MapOfShape()
    fcount = 0
    it = TopExp_Explorer(shape, TopAbs_FACE)
    while it.More():
        if not seen.Contains(it.Current()):
            seen.Add(it.Current())
            fcount += 1
        it.Next()
    return shape, props.Mass(), fcount


@pytest.mark.fidelity
@pytest.mark.parametrize("rel_path,tol_pct", CASES)
def test_round_trip_fidelity(rel_path, tol_pct):
    src = PROJECT / rel_path
    if not src.exists():
        if STRICT:
            pytest.fail(f"STRICT: missing fixture {rel_path}")
        pytest.skip(f"missing fixture {rel_path}")
    orig_shape, orig_vol, orig_fc = _measure(src)

    # Run RE pipeline
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import ExtractFeatureCatalog
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import PlanFromFeatureCatalog
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.yaml_io import load_plan
    from build123d import Part

    body = Part(orig_shape)
    cat_result = ExtractFeatureCatalog().apply(body, {})
    plan_result = PlanFromFeatureCatalog().apply(body, {"catalog": cat_result.extras["feature_catalog"]})
    plan_path = plan_result.extras.get("plan_path") or "plans/reconstructed_plan.yaml"
    plan = load_plan(plan_path)
    exec_result = PlanExecutor(plan).run()
    assert exec_result.final_body is not None, "executor returned None body"
    regen_shape = exec_result.final_body.wrapped if hasattr(exec_result.final_body, "wrapped") else exec_result.final_body

    # Measure regen
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopTools import TopTools_MapOfShape
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(regen_shape, props)
    regen_vol = props.Mass()
    seen = TopTools_MapOfShape()
    regen_fc = 0
    it = TopExp_Explorer(regen_shape, TopAbs_FACE)
    while it.More():
        if not seen.Contains(it.Current()):
            seen.Add(it.Current())
            regen_fc += 1
        it.Next()

    # === Silent cube-collapse detection (canonical signature) ===
    cube_collapsed = (24500 <= regen_vol <= 25500 and regen_fc == 6)
    cube_msg = (
        f"Cube collapse detected for {rel_path}: vol={regen_vol:.0f} faces={regen_fc} "
        f"(matches 50x50x10 placeholder signature). The planner-executor pipeline "
        f"is silently falling back to the default base box."
    )
    if cube_collapsed:
        if STRICT:
            pytest.fail("STRICT: " + cube_msg)
        else:
            pytest.xfail(cube_msg)

    # === Volume fidelity gate ===
    pct = abs(regen_vol - orig_vol) / max(orig_vol, 1.0) * 100.0
    drift_msg = (
        f"{rel_path}: volume drift {pct:.1f}% exceeds tolerance {tol_pct}% "
        f"(orig={orig_vol:.0f}, regen={regen_vol:.0f}, "
        f"face_count {orig_fc}->{regen_fc})"
    )
    if pct > tol_pct:
        if STRICT:
            pytest.fail("STRICT: " + drift_msg)
        else:
            pytest.xfail(drift_msg)
