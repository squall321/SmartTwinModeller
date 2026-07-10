"""Track 3-3 SHIP-CONDITION pins — plan-as-feature-tree incremental rebuild.

The roadmap's ship condition, pinned in full (extends tests/test_plan_edit.py,
which pins plan #1 = the 5-step box plan at edit position k=2):

  * BYTE-EQUIVALENCE on >=2 different plans x >=2 edit positions — the
    incremental rebuild's STEP bytes must equal a full from-scratch re-run of
    the edited plan (bytes compared after normalizing ONLY the two documented
    process-volatile artifacts: FILE_NAME timestamp + OCCT's per-process
    'STEP translator N' product counter; every geometry byte matches):
      - plan #1 (box+2 holes+fillet+pocket), edit position k=3 (suppress the
        rim fillet)  [k=2 pinned in tests/test_plan_edit.py];
      - plan #2 (cylinder+2 holes+pocket), edit positions k=1 (suppress a
        hole) and k=3 (insert a fillet).
  * SUPPRESS A HOLE STEP -> volume increases by EXACTLY the hole's analytic
    cylinder volume (pi*r^2*depth; probe-measured error 6.7e-13 mm^3, pinned
    at 1e-9).
  * INSERT A FILLET STEP MID-PLAN -> byte-equivalent to authoring the plan
    with that fillet from scratch.
  * hot-path history coverage guard: every skill the corpus RE-plan audit
    found on the hot path declares non-empty history_rules (the registry
    metadata the audit's 100% coverage rests on cannot silently regress).
"""
from __future__ import annotations

import math
import re

from phone_designer.plan.executor import PlanExecutor
from phone_designer.plan.model import Plan
from phone_designer.skills.reverse_engineer.plan_edit import PlanEdit


# ---------------------------------------------------------------------------
# helpers (mirrors tests/test_plan_edit.py — kept local so this file stands
# alone under tests/plan/)
# ---------------------------------------------------------------------------

def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    shape = body.wrapped if hasattr(body, "wrapped") else body
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def _step_bytes(body, path) -> bytes:
    """STEP file bytes with ONLY the documented process-volatile artifacts
    normalized (FILE_NAME timestamp + OCCT per-process product counter).
    All geometry bytes are compared verbatim."""
    from phone_designer.skills.io.step_export_v2 import _write_plain
    shape = body.wrapped if hasattr(body, "wrapped") else body
    _write_plain(shape, path)
    raw = path.read_bytes()
    raw = re.sub(rb"\d{4}-\d{2}-\d{2}T[0-9:.+\-]*", b"<TS>", raw)
    raw = re.sub(rb"(Open CASCADE STEP translator [0-9.]+) \d+",
                 rb"\1 <N>", raw)
    return raw


def _edit(args: dict):
    result = PlanEdit().apply(None, args)
    return result, result.extras["plan_edit"]


def _run_full(plan_dict: dict):
    """Full from-scratch re-run of a plan — the byte-equivalence reference."""
    plan = Plan.model_validate(plan_dict)
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS", [
        (s.id, s.failure.message) for s in plan.steps if s.failure]
    return result.final_body


def _plan_box() -> dict:
    """Plan #1 — same 5-step ship-gate plan as tests/test_plan_edit.py."""
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


def _plan_cyl() -> dict:
    """Plan #2 — a DIFFERENT plan (cylinder base + 2 holes + pocket). All
    features are spatially disjoint: holes at x=+/-12 (spans [-15,-9] and
    [10,14]), pocket x in [-5,5] — genuinely independent mutators."""
    return {
        "schema_version": 1,
        "plan_name": "ship_gate_cyl",
        "steps": [
            {"id": "c", "skill": "cylinder",
             "args": {"radius_mm": 20.0, "height_mm": 12.0}},
            {"id": "ha", "skill": "hole",
             "args": {"position": [-12.0, 0.0, 12.0], "diameter_mm": 6.0,
                      "depth_mm": 6.0, "direction": "-Z"}},
            {"id": "hb", "skill": "hole",
             "args": {"position": [12.0, 0.0, 12.0], "diameter_mm": 4.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
            {"id": "pkt", "skill": "extrude_pocket",
             "args": {"face_selector": {"kind": "faces_by_normal",
                                        "direction": [0.0, 0.0, 1.0]},
                      "sketch": {"kind": "rectangle", "length_mm": 10.0,
                                 "width_mm": 6.0},
                      "depth_mm": 3.0}},
        ],
    }


#: bottom-rim fillet for plan #2 — selects ONLY the z=0 circle, so it never
#: disturbs the top face downstream steps operate on (no freeze drift).
_BOTTOM_FILLET = {
    "id": "fil", "skill": "fillet_edges_by_predicate",
    "args": {"selector": {"kind": "edges_by_position",
                          "bbox": {"min": [-21.0, -21.0, -0.5],
                                   "max": [21.0, 21.0, 0.5]}},
             "radius_mm": 1.5},
}


# ---------------------------------------------------------------------------
# byte-equivalence: plan #2 x two edit positions
# ---------------------------------------------------------------------------

def test_cyl_plan_suppress_hole_k1_byte_equivalent(tmp_path):
    """Plan #2, edit position k=1: suppress hole 'ha'. Incremental rebuild
    (only steps 1..N re-executed from the recorded intermediate) must be
    byte-equivalent to a full from-scratch run of the edited plan."""
    result, info = _edit(
        {"plan": _plan_cyl(), "op": "suppress", "step_id": "ha"})

    assert info["ok"] is True
    assert [c["step_id"] for c in info["suppressed_closure"]] == ["ha"]
    rebuild = info["rebuild"]
    assert rebuild["mode"] == "incremental"
    assert rebuild["first_affected_index"] == 1
    assert rebuild["reused_steps"] == 1          # cylinder NOT re-executed
    assert rebuild["reexecuted_steps"] == 2      # hb, pkt only
    assert rebuild["outcome"] == "PASS"
    assert rebuild["freeze_mismatch_count"] == 0

    manual = _plan_cyl()
    manual["steps"] = [s for s in manual["steps"] if s["id"] != "ha"]
    full_body = _run_full(manual)

    assert abs(_volume(result.body) - _volume(full_body)) <= 1e-9
    assert (_step_bytes(result.body, tmp_path / "incr.step")
            == _step_bytes(full_body, tmp_path / "full.step"))


def test_cyl_plan_insert_fillet_k3_byte_equivalent(tmp_path):
    """Plan #2, edit position k=3 — THE ROADMAP FILLET PIN: inserting a
    fillet step mid-plan equals authoring the plan with it from scratch."""
    result, info = _edit(
        {"plan": _plan_cyl(), "op": "insert",
         "new_step": dict(_BOTTOM_FILLET), "position": 3})

    assert info["ok"] is True
    assert info["inserted"] == {"step_id": "fil", "position": 3}
    rebuild = info["rebuild"]
    assert rebuild["mode"] == "incremental"
    assert rebuild["reused_steps"] == 3          # c, ha, hb reused
    assert rebuild["reexecuted_steps"] == 2      # fil, pkt only
    assert rebuild["outcome"] == "PASS"
    assert rebuild["freeze_mismatch_count"] == 0
    assert [s["id"] for s in info["edited_plan"]["steps"]] == [
        "c", "ha", "hb", "fil", "pkt"]

    authored = _plan_cyl()
    authored["steps"] = (authored["steps"][:3] + [dict(_BOTTOM_FILLET)]
                         + authored["steps"][3:])
    full_body = _run_full(authored)

    assert abs(_volume(result.body) - _volume(full_body)) <= 1e-9
    assert (_step_bytes(result.body, tmp_path / "incr.step")
            == _step_bytes(full_body, tmp_path / "full.step"))


# ---------------------------------------------------------------------------
# byte-equivalence: plan #1 at a SECOND edit position (k=3; k=2 is pinned in
# tests/test_plan_edit.py)
# ---------------------------------------------------------------------------

def test_box_plan_suppress_fillet_k3_byte_equivalent(tmp_path):
    result, info = _edit(
        {"plan": _plan_box(), "op": "suppress", "step_id": "rim"})

    assert info["ok"] is True
    assert [c["step_id"] for c in info["suppressed_closure"]] == ["rim"]
    rebuild = info["rebuild"]
    assert rebuild["mode"] == "incremental"
    assert rebuild["first_affected_index"] == 3
    assert rebuild["reused_steps"] == 3          # base, h1, h2 reused
    assert rebuild["reexecuted_steps"] == 1      # pkt only
    assert rebuild["outcome"] == "PASS"
    assert rebuild["freeze_mismatch_count"] == 0

    manual = _plan_box()
    manual["steps"] = [s for s in manual["steps"] if s["id"] != "rim"]
    full_body = _run_full(manual)

    assert abs(_volume(result.body) - _volume(full_body)) <= 1e-9
    assert (_step_bytes(result.body, tmp_path / "incr.step")
            == _step_bytes(full_body, tmp_path / "full.step"))


# ---------------------------------------------------------------------------
# exact hole-volume pin
# ---------------------------------------------------------------------------

def test_suppress_hole_restores_exact_analytic_hole_volume():
    """Suppressing hole 'h2' (d=4, depth=5, flat-bottom cylinder cut, fully
    inside material, disjoint from every other feature) increases the volume
    by EXACTLY pi*r^2*depth. Probe-measured error 6.7e-13 mm^3 on a
    ~2.35e4 mm^3 body — pinned at 1e-9."""
    base_plan = Plan.model_validate(_plan_box())
    base_result = PlanExecutor(base_plan).run()
    assert base_result.outcome == "PASS"
    v_base = _volume(base_result.final_body)

    result, info = _edit(
        {"plan": _plan_box(), "op": "suppress", "step_id": "h2"})
    assert info["ok"] is True
    assert info["rebuild"]["mode"] == "incremental"

    analytic = math.pi * (4.0 / 2.0) ** 2 * 5.0     # 62.83185307179586
    assert abs((_volume(result.body) - v_base) - analytic) <= 1e-9


# ---------------------------------------------------------------------------
# hot-path history-coverage guard (audit regression pin)
# ---------------------------------------------------------------------------

#: Skills the Track 3-3 corpus audit found on the RE-plan hot path (7 corpus
#: plans: 3 preserve_brep + 4 box over kicad/pythonocc parts). Audit result:
#: 10/10 PASS steps recorded a NON-EMPTY EntityHistoryMap = 100% coverage.
_HOT_PATH_SKILLS = (
    "hole",
    "extrude_pocket",
    "tap_drill_hole",
    "place_freeform_solid",
)


def test_hot_path_skills_declare_history_rules():
    """The audit's 100% hot-path coverage rests on these skills filling
    EntityHistoryMap — their registry history_rules declarations must stay
    non-empty (a silent regression here would degrade feature-tree edges to
    'unknown' without anyone noticing)."""
    from phone_designer.skills import export_manifest  # noqa: F401
    from phone_designer.skills._registry import registry

    for name in _HOT_PATH_SKILLS:
        spec = registry.get(name)
        assert spec.history_rules, (
            f"hot-path skill '{name}' no longer declares history_rules — "
            f"feature-tree evidence for it would silently degrade")
