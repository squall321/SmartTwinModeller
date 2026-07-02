"""plan_reexecute — saved plan + parameter overrides → re-run + deltas (2-2).

THE pin: a box-with-pocket plan whose pocket is derived from a `wall`
parameter; overriding wall +0.2 mm produces the ANALYTIC volume change

    V(w) = L*W*H - (L-2w)(W-2w)(H-w)
    V(2.0)  = 12000 - 36*26*8    = 4512.000
    V(2.2)  = 12000 - 35.6*25.6*7.8 = 4891.392   (Δ = +379.392 mm³)

Also pinned: bbox delta on a length override, structured refusals
(fm.bad_args / fm.plan_load_failed / fm.expr_error), LOOSE-mode selector
drift surfaced as an HONEST warning, and the param-less plain re-run.
"""
from __future__ import annotations

import pytest

from phone_designer.plan.model import FreezeMeta, ParameterDef, Plan, Step
from phone_designer.plan.yaml_io import save_plan
from phone_designer.skills.reverse_engineer.plan_reexecute import PlanReexecute


def _pocket_plan() -> Plan:
    return Plan(
        plan_name="param_box_pocket",
        parameters={
            "length": ParameterDef(value=40.0, description="outer length"),
            "width":  ParameterDef(value=30.0),
            "height": ParameterDef(value=10.0),
            "wall":   ParameterDef(value=2.0, description="wall thickness"),
        },
        steps=[
            Step(id="s_base", skill="box", args={
                "length_mm": {"$expr": "length"},
                "width_mm":  {"$expr": "width"},
                "height_mm": {"$expr": "height"},
            }),
            Step(id="s_pocket", skill="extrude_pocket_world", args={
                "world_origin": [0.0, 0.0, {"$expr": "height"}],
                "axis_dir": [0.0, 0.0, 1.0],
                "length_mm": {"$expr": "length - 2*wall"},
                "width_mm":  {"$expr": "width - 2*wall"},
                "depth_mm":  {"$expr": "height - wall"},
            }),
        ],
    )


def _vol(L, W, H, w) -> float:
    return L * W * H - (L - 2 * w) * (W - 2 * w) * (H - w)


def _reexec(**args):
    return PlanReexecute().apply(None, args).extras["reexecute"]


def test_wall_override_volume_change_analytic(tmp_path):
    p = tmp_path / "plan.yaml"
    save_plan(_pocket_plan(), p)
    r = _reexec(plan_path=str(p), parameter_overrides={"wall": 2.2})

    assert r["ok"] and r["grade"] == "reexecuted" and r["mode"] == "loose"
    assert r["plan_source"] == "path"
    assert r["parameters_base"]["wall"] == pytest.approx(2.0)
    assert r["parameters_resolved"]["wall"] == pytest.approx(2.2)
    assert r["overrides_applied"] == {"wall": 2.2}

    # analytic pins (mm³)
    assert r["baseline"]["volume_mm3"] == pytest.approx(
        _vol(40, 30, 10, 2.0), abs=0.01)            # 4512.000
    assert r["variant"]["volume_mm3"] == pytest.approx(
        _vol(40, 30, 10, 2.2), abs=0.01)            # 4891.392
    assert r["deltas"]["volume_mm3"] == pytest.approx(379.392, abs=0.01)
    assert r["deltas"]["volume_pct"] == pytest.approx(
        100.0 * 379.392 / 4512.0, abs=0.01)

    # wall change does NOT move the outer envelope
    assert r["deltas"]["bbox_mm"] == [0.0, 0.0, 0.0]
    assert r["baseline"]["is_solid"] and r["variant"]["is_solid"]
    assert r["baseline"]["outcome"] == "PASS"
    assert r["variant"]["outcome"] == "PASS"
    # honest label rides INSIDE the artifact
    assert any("LOOSE" in c for c in r["caveats"])
    # strict-JSON-safe: the whole artifact serializes with allow_nan=False
    import json
    json.dumps(r, allow_nan=False)


def test_length_override_moves_bbox_inline_plan():
    plan_dict = _pocket_plan().model_dump(mode="json", exclude_none=True)
    r = _reexec(plan=plan_dict, parameter_overrides={"length": 50.0})
    assert r["plan_source"] == "inline" and r["ok"]
    assert r["baseline"]["volume_mm3"] == pytest.approx(4512.0, abs=0.01)
    assert r["variant"]["volume_mm3"] == pytest.approx(
        _vol(50, 30, 10, 2.0), abs=0.01)            # 5432.000
    assert r["deltas"]["volume_mm3"] == pytest.approx(920.0, abs=0.01)
    assert r["deltas"]["bbox_mm"] == [10.0, 0.0, 0.0]
    assert r["baseline"]["bbox_mm"] == [40.0, 30.0, 10.0]
    assert r["variant"]["bbox_mm"] == [50.0, 30.0, 10.0]


def test_override_of_unknown_parameter_is_fm_expr_error(tmp_path):
    p = tmp_path / "plan.yaml"
    save_plan(_pocket_plan(), p)
    with pytest.raises(ValueError, match=r"fm\.expr_error.*undefined"):
        _reexec(plan_path=str(p), parameter_overrides={"wal": 2.2})


def test_overrides_on_paramless_plan_refused():
    plan = Plan(plan_name="v1", steps=[
        Step(id="s1", skill="box",
             args={"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0})])
    with pytest.raises(ValueError,
                       match=r"fm\.expr_error.*no parameter table"):
        _reexec(plan=plan.model_dump(mode="json", exclude_none=True),
                parameter_overrides={"wall": 3.0})


def test_bad_args_and_load_failed_refusals(tmp_path):
    with pytest.raises(ValueError, match=r"fm\.bad_args"):
        _reexec(parameter_overrides={})                 # neither source
    with pytest.raises(ValueError, match=r"fm\.bad_args"):
        _reexec(plan_path="x.yaml", plan={"plan_name": "x", "steps": []})
    with pytest.raises(ValueError, match=r"fm\.plan_load_failed"):
        _reexec(plan_path=str(tmp_path / "does_not_exist.yaml"))
    # schema-invalid inline plan — raw pydantic cause preserved
    with pytest.raises(ValueError, match=r"fm\.plan_load_failed"):
        _reexec(plan={"plan_name": "x"})                # missing steps


def test_paramless_plan_plain_rerun_zero_delta():
    plan = Plan(plan_name="v1", steps=[
        Step(id="s1", skill="box",
             args={"length_mm": 20.0, "width_mm": 10.0, "height_mm": 5.0})])
    r = _reexec(plan=plan.model_dump(mode="json", exclude_none=True))
    assert r["ok"]
    assert r["parameters_base"] is None and r["parameters_resolved"] is None
    assert r["baseline"]["volume_mm3"] == pytest.approx(1000.0, abs=0.01)
    assert r["deltas"]["volume_mm3"] == pytest.approx(0.0, abs=1e-6)
    assert r["deltas"]["bbox_mm"] == [0.0, 0.0, 0.0]
    assert r["warnings"] == [] and r["selector_drift"] == []


def test_selector_drift_surfaced_as_honest_warning():
    # a saved plan whose fillet step carries a STALE freeze (as if captured
    # on a different geometry) — LOOSE mode must re-capture AND report drift,
    # never absorb it silently. This is the SolidWorks rebuild-error analogue.
    plan = Plan(
        plan_name="drift",
        steps=[
            Step(id="s1", skill="box",
                 args={"length_mm": 20.0, "width_mm": 20.0,
                       "height_mm": 4.0}),
            Step(id="s2", skill="fillet_edges_by_predicate",
                 args={"selector": {"kind": "axis_aligned_edges",
                                    "axis": "Z"},
                       "radius_mm": 1.0},
                 selector_freeze=FreezeMeta(matched_count=99,
                                            topology_signature="stale")),
        ],
    )
    r = _reexec(plan=plan.model_dump(mode="json", exclude_none=True))
    assert len(r["selector_drift"]) >= 1
    assert r["selector_drift"][0]["step_id"] == "s2"
    assert any("selector drift" in w for w in r["warnings"])
    assert any("rebuild-error" in w for w in r["warnings"])
    # the plan still rebuilt (LOOSE re-capture) and the fillet removed material
    assert r["variant"]["outcome"] == "PASS"
    assert r["variant"]["volume_mm3"] < 20.0 * 20.0 * 4.0


def test_run_baseline_false_uses_recorded_metrics_or_says_so():
    plan = Plan(plan_name="v1", steps=[
        Step(id="s1", skill="box",
             args={"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0})])
    r = _reexec(plan=plan.model_dump(mode="json", exclude_none=True),
                run_baseline=False)
    # this plan carries no recorded metrics — deltas honestly None + caveat
    assert r["baseline"]["ran"] is False
    assert r["baseline"]["volume_mm3"] is None
    assert r["deltas"]["volume_mm3"] is None
    assert any("recorded" in c or "unavailable" in c for c in r["caveats"])
    assert r["variant"]["volume_mm3"] == pytest.approx(1000.0, abs=0.01)
