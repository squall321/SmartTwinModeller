"""plan_edit (Track 3-3) — suppress/insert + incremental rebuild.

THE SHIP GATE (plan section 3-3): a 5-step plan (box + 2 holes + fillet +
pocket) with hole#2 suppressed via plan_edit must be BYTE-EQUIVALENT to
executing the manually-edited plan from scratch — volume to 1e-9 AND the
same STEP geometry (bytes compared after normalizing ONLY the two documented
process-volatile artifacts: the FILE_NAME timestamp and OCCT's per-process
'STEP translator N' product counter — every geometry byte must match).

Also pinned:
  * suppressing the root create step REFUSES (fm.plan_edit_empty_plan) —
    the closure covers the entire plan, and an empty plan is not a result;
  * REORDER is refused by design (fm.plan_edit_reorder_unsupported,
    roadmap REJECT #3: CSG cut-then-boss != boss-then-cut);
  * incremental accounting is honest (reused_steps / reexecuted_steps /
    fallback reasons), never a fake 'incremental' label;
  * the freeze guard makes a MISSED dependency visible: a fillet that
    genuinely touched hole#2's rim edges fails its freeze check after the
    suppression instead of silently producing different geometry;
  * every fm.* refusal declared by the skill is reachable.
"""
from __future__ import annotations

import json
import re

import pytest

from phone_designer.plan.executor import PlanExecutor
from phone_designer.plan.model import Plan
from phone_designer.skills.reverse_engineer.plan_edit import PlanEdit


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _plan5() -> dict:
    """The ship-gate plan: box + 2 holes + fillet + pocket. The fillet's
    length filter (10±1) excludes everything the holes create (rim
    circumference 12.57, wall seam 5.0) — genuinely independent."""
    return {
        "schema_version": 1,
        "plan_name": "ship_gate_5step",
        "steps": [
            {"id": "base", "skill": "box",
             "args": {"length_mm": 60.0, "width_mm": 40.0,
                      "height_mm": 10.0}},
            {"id": "h1", "skill": "hole",
             "args": {"position": [15.0, 10.0, 10.0], "diameter_mm": 4.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
            {"id": "h2", "skill": "hole",
             "args": {"position": [-15.0, -10.0, 10.0], "diameter_mm": 4.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
            {"id": "rim", "skill": "fillet_edges_by_predicate",
             "args": {"selector": {
                          "kind": "and",
                          "left": {"kind": "axis_aligned_edges", "axis": "Z"},
                          "right": {"kind": "edges_by_length",
                                    "min": 9.0, "max": 11.0}},
                      "radius_mm": 2.0}},
            {"id": "pkt", "skill": "extrude_pocket",
             "args": {"face_selector": {"kind": "faces_by_normal",
                                        "direction": [0.0, 0.0, 1.0]},
                      "sketch": {"kind": "rectangle", "length_mm": 16.0,
                                 "width_mm": 8.0},
                      "depth_mm": 3.0}},
        ],
    }


def _edit(args: dict):
    result = PlanEdit().apply(None, args)
    return result, result.extras["plan_edit"]


def _run_manual(plan_dict: dict):
    plan = Plan.model_validate(plan_dict)
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS", [
        (s.id, s.failure.message) for s in plan.steps if s.failure]
    return result.final_body


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    shape = body.wrapped if hasattr(body, "wrapped") else body
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def _step_bytes(body, path) -> bytes:
    """STEP file bytes with ONLY the documented process-volatile artifacts
    normalized: FILE_NAME timestamp + OCCT's per-process product counter.
    All geometry bytes are compared verbatim."""
    from phone_designer.skills.io.step_export_v2 import _write_plain
    shape = body.wrapped if hasattr(body, "wrapped") else body
    _write_plain(shape, path)
    raw = path.read_bytes()
    raw = re.sub(rb"\d{4}-\d{2}-\d{2}T[0-9:.+\-]*", b"<TS>", raw)
    raw = re.sub(rb"(Open CASCADE STEP translator [0-9.]+) \d+",
                 rb"\1 <N>", raw)
    return raw


# ---------------------------------------------------------------------------
# THE SHIP GATE — suppress hole#2, byte-equivalent to the manual plan
# ---------------------------------------------------------------------------

def test_ship_gate_suppress_h2_byte_equivalent_to_manual_plan(tmp_path):
    result, info = _edit(
        {"plan": _plan5(), "op": "suppress", "step_id": "h2"})

    assert info["ok"] is True
    assert [c["step_id"] for c in info["suppressed_closure"]] == ["h2"]
    rebuild = info["rebuild"]
    assert rebuild["mode"] == "incremental"
    assert rebuild["reused_steps"] == 2          # base, h1 NOT re-executed
    assert rebuild["reexecuted_steps"] == 2      # rim, pkt only
    assert rebuild["outcome"] == "PASS"
    assert rebuild["freeze_mismatch_count"] == 0
    assert [s["id"] for s in info["edited_plan"]["steps"]] == [
        "base", "h1", "rim", "pkt"]

    manual = _plan5()
    manual["steps"] = [s for s in manual["steps"] if s["id"] != "h2"]
    manual_body = _run_manual(manual)

    # volume to 1e-9 (absolute, ~2.4e4 mm3 bodies — probe-measured diff 0.0)
    assert abs(_volume(result.body) - _volume(manual_body)) <= 1e-9

    # same STEP geometry, byte-compared
    edited_bytes = _step_bytes(result.body, tmp_path / "edited.step")
    manual_bytes = _step_bytes(manual_body, tmp_path / "manual.step")
    assert edited_bytes == manual_bytes

    # the whole report is strict-JSON-safe
    json.dumps(info, allow_nan=False)


def test_full_rebuild_requested_matches_manual_plan(tmp_path):
    # exercise the plan_path load route + rebuild='full' in one go
    from phone_designer.plan.yaml_io import save_plan
    p = tmp_path / "plan5.yaml"
    save_plan(Plan.model_validate(_plan5()), p)

    result, info = _edit({"plan_path": str(p), "op": "suppress",
                          "step_id": "h2", "rebuild": "full"})
    assert info["plan_source"] == "path"
    assert info["rebuild"]["mode"] == "full"
    assert info["rebuild"]["requested"] == "full"
    assert info["rebuild"]["reused_steps"] == 0
    assert info["rebuild"]["reason"] == "full re-run requested"

    manual = _plan5()
    manual["steps"] = [s for s in manual["steps"] if s["id"] != "h2"]
    manual_body = _run_manual(manual)
    assert abs(_volume(result.body) - _volume(manual_body)) <= 1e-9


def test_insert_mid_plan_byte_equivalent_to_manual_plan(tmp_path):
    new_hole = {"id": "h3", "skill": "hole",
                "args": {"position": [0.0, 15.0, 10.0], "diameter_mm": 4.0,
                         "depth_mm": 5.0, "direction": "-Z"}}
    result, info = _edit({"plan": _plan5(), "op": "insert",
                          "new_step": new_hole, "position": 2})

    assert info["ok"] is True
    assert info["inserted"] == {"step_id": "h3", "position": 2}
    rebuild = info["rebuild"]
    assert rebuild["mode"] == "incremental"
    assert rebuild["reused_steps"] == 2          # base, h1 reused
    assert rebuild["reexecuted_steps"] == 4      # h3, h2, rim, pkt
    assert rebuild["outcome"] == "PASS"
    assert [s["id"] for s in info["edited_plan"]["steps"]] == [
        "base", "h1", "h3", "h2", "rim", "pkt"]

    manual = _plan5()
    manual["steps"] = (manual["steps"][:2] + [dict(new_hole)]
                       + manual["steps"][2:])
    manual_body = _run_manual(manual)
    assert abs(_volume(result.body) - _volume(manual_body)) <= 1e-9
    assert (_step_bytes(result.body, tmp_path / "edited.step")
            == _step_bytes(manual_body, tmp_path / "manual.step"))


def test_suppress_dependent_chain_and_empty_suffix():
    """Suppressing a tag_face drags its tagged-selector consumer along
    (arg_reference closure); the remaining plan ends at the reused prefix,
    exercising the empty-suffix incremental path."""
    plan = {
        "schema_version": 1,
        "plan_name": "tag_chain",
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": 30.0, "width_mm": 20.0, "height_mm": 8.0}},
            {"id": "tag", "skill": "tag_face",
             "args": {"selector": {"kind": "faces_by_normal",
                                   "direction": [0.0, 0.0, 1.0]},
                      "tag": "TOP"}},
            {"id": "pkt", "skill": "extrude_pocket",
             "args": {"face_selector": {"kind": "tagged", "tag": "TOP"},
                      "sketch": {"kind": "rectangle", "length_mm": 10.0,
                                 "width_mm": 6.0},
                      "depth_mm": 3.0}},
        ],
    }
    result, info = _edit({"plan": plan, "op": "suppress", "step_id": "tag"})
    assert [c["step_id"] for c in info["suppressed_closure"]] == ["tag", "pkt"]
    assert info["rebuild"]["mode"] == "incremental"
    assert info["rebuild"]["reused_steps"] == 1
    assert info["rebuild"]["reexecuted_steps"] == 0
    assert info["rebuild"]["outcome"] == "PASS"
    assert abs(_volume(result.body) - 30.0 * 20.0 * 8.0) <= 1e-9


def test_missed_dependency_surfaces_as_freeze_mismatch_not_silence():
    """HONEST-GUARD PIN (probe-discovered): with min-length 9 and NO max,
    the fillet's selector ALSO matches the holes' rim circles (circumference
    12.57) — the fillet genuinely depends on h2, which the rules-only
    history cannot see. Suppressing h2 must then FAIL the rebuild with a
    visible FreezeMismatch on the fillet step — never silently produce
    different geometry under an ok=True label."""
    plan = _plan5()
    plan["steps"][3]["args"]["selector"]["right"] = {
        "kind": "edges_by_length", "min": 9.0}      # max removed on purpose
    result, info = _edit({"plan": plan, "op": "suppress", "step_id": "h2"})

    assert info["ok"] is False
    assert info["rebuild"]["outcome"] == "FAIL"
    rim_report = [s for s in info["step_report"] if s["id"] == "rim"][0]
    assert rim_report["status"] == "fail"
    assert "count" in (rim_report["error"] or "")   # FreezeMismatch message
    assert any("FreezeMismatch" in w or "dependency" in w
               for w in info["warnings"])


# ---------------------------------------------------------------------------
# structured refusals — every declared fm.* reachable
# ---------------------------------------------------------------------------

def _tiny_plan() -> dict:
    return {
        "schema_version": 1,
        "plan_name": "tiny",
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": 5.0, "width_mm": 5.0, "height_mm": 5.0}},
            {"id": "h", "skill": "hole",
             "args": {"position": [0.0, 0.0, 5.0], "diameter_mm": 2.0,
                      "depth_mm": 2.0, "direction": "-Z"}},
        ],
    }


def test_reorder_refused_by_design():
    with pytest.raises(ValueError, match="fm.plan_edit_reorder_unsupported"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "reorder"})


def test_bad_args_exactly_one_plan_source():
    with pytest.raises(ValueError, match="fm.bad_args"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "plan_path": "x.yaml",
                                "op": "suppress", "step_id": "b"})
    with pytest.raises(ValueError, match="fm.bad_args"):
        PlanEdit().apply(None, {"op": "suppress", "step_id": "b"})


def test_bad_args_suppress_needs_step_id():
    with pytest.raises(ValueError, match="fm.bad_args"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "suppress"})


def test_bad_args_insert_needs_new_step_and_position():
    with pytest.raises(ValueError, match="fm.bad_args"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "position": 1})
    with pytest.raises(ValueError, match="fm.bad_args"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": {"id": "x", "skill": "box",
                                             "args": {}}})


def test_plan_load_failed_missing_file(tmp_path):
    with pytest.raises(ValueError, match="fm.plan_load_failed"):
        PlanEdit().apply(None, {"plan_path": str(tmp_path / "nope.yaml"),
                                "op": "suppress", "step_id": "b"})


def test_unknown_suppress_step_refused():
    with pytest.raises(ValueError, match="fm.plan_edit_unknown_step"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "suppress",
                                "step_id": "ghost"})


def test_suppress_root_refuses_empty_plan():
    """PINNED DECISION: suppressing the root create step suppresses the
    ENTIRE closure — plan_edit refuses to produce an empty plan and names
    the closure in the refusal."""
    with pytest.raises(ValueError, match="fm.plan_edit_empty_plan"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "suppress",
                                "step_id": "b"})


def test_insert_invalid_step_schema():
    with pytest.raises(ValueError, match="fm.plan_edit_invalid_step"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": {"id": "x"},   # no skill
                                "position": 1})


def test_insert_duplicate_id_refused():
    with pytest.raises(ValueError, match="fm.plan_edit_duplicate_id"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": {"id": "h", "skill": "box",
                                             "args": {"length_mm": 1.0,
                                                      "width_mm": 1.0,
                                                      "height_mm": 1.0}},
                                "position": 1})


def test_insert_unknown_skill_refused():
    with pytest.raises(ValueError, match="fm.plan_edit_unknown_skill"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": {"id": "x",
                                             "skill": "definitely_not_a_skill",
                                             "args": {}},
                                "position": 1})


def test_insert_bad_position_refused():
    step = {"id": "x", "skill": "box",
            "args": {"length_mm": 1.0, "width_mm": 1.0, "height_mm": 1.0}}
    with pytest.raises(ValueError, match="fm.plan_edit_bad_position"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": step, "position": 99})
    with pytest.raises(ValueError, match="fm.plan_edit_bad_position"):
        PlanEdit().apply(None, {"plan": _tiny_plan(), "op": "insert",
                                "new_step": step, "position": -1})


def test_expr_error_from_parametric_plan_unmasked():
    plan = {
        "schema_version": 2,
        "plan_name": "bad_param",
        "parameters": {"L": {"value": "undefined_name + 1"}},
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": {"$expr": "L"}, "width_mm": 5.0,
                      "height_mm": 5.0}},
            {"id": "h", "skill": "hole",
             "args": {"position": [0.0, 0.0, 5.0], "diameter_mm": 2.0,
                      "depth_mm": 2.0, "direction": "-Z"}},
        ],
    }
    with pytest.raises(ValueError, match="fm.expr_error"):
        PlanEdit().apply(None, {"plan": plan, "op": "suppress",
                                "step_id": "h"})
