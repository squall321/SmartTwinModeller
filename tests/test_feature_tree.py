"""feature_tree (Track 3-3) — dependency graph over plan steps.

Pins:
  * box+hole+fillet intuition: fillet depends on box (evidence='history'),
    hole depends on box, and NO hole<->fillet edge when selectors don't
    overlap;
  * arg_reference edges: tag_face -> tagged-selector consumer; positional
    overlap (a hole's position inside a later step's edges_by_position bbox);
  * EMPTY-history steps get kind='sequence' / evidence='unknown' edges —
    HONEST degradation, never a fabricated 'history' claim;
  * static mode (execution_result=None) degrades to arg_reference + unknown;
  * suppress_closure transitivity; closure(root create step) == entire plan;
  * run_plan_suffix + snapshot_body_before are a GENUINE incremental
    primitive (suffix from the recorded intermediate == full run);
  * duplicate step ids refused (fm.feature_tree_duplicate_step_ids);
  * the tree is strict-JSON-safe.
"""
from __future__ import annotations

import json

import pytest

from phone_designer.plan.executor import PlanExecutor
from phone_designer.plan.feature_tree import (
    build_feature_tree,
    run_plan_suffix,
    snapshot_body_before,
    suppress_closure,
)
from phone_designer.plan.model import Plan


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    shape = body.wrapped if hasattr(body, "wrapped") else body
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def _plan_box_hole_fillet() -> Plan:
    """The intuition-pin plan: fillet's selector (Z corner edges, length
    10±1) can NEVER match anything the hole created (rim circumference
    12.57, wall seam 5.0), so hole and fillet are independent mutators of
    the box."""
    return Plan.model_validate({
        "schema_version": 1,
        "plan_name": "bhf",
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": 40.0, "width_mm": 40.0,
                      "height_mm": 10.0}},
            {"id": "h", "skill": "hole",
             "args": {"position": [10.0, 10.0, 10.0], "diameter_mm": 6.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
            {"id": "f", "skill": "fillet_edges_by_predicate",
             "args": {"selector": {
                          "kind": "and",
                          "left": {"kind": "axis_aligned_edges", "axis": "Z"},
                          "right": {"kind": "edges_by_length",
                                    "min": 9.0, "max": 11.0}},
                      "radius_mm": 2.0}},
        ],
    })


def _plan_tag_chain() -> Plan:
    """box -> tag_face(TOP) -> extrude_pocket(tagged TOP): the pocket
    statically REFERENCES the tag the tag_face step created."""
    return Plan.model_validate({
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
    })


def _parent_ids(tree, step_id):
    return [e["step_id"] for e in tree[step_id]["parents"]]


def _child_ids(tree, step_id):
    return [e["step_id"] for e in tree[step_id]["children"]]


# ---------------------------------------------------------------------------
# executor recording (the additive extension feature_tree consumes)
# ---------------------------------------------------------------------------

def test_executor_records_step_histories():
    plan = _plan_box_hole_fillet()
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"
    assert sorted(result.step_histories) == ["b", "f", "h"]
    box_hist = result.step_histories["b"]
    assert box_hist["rules"] == {"output_faces": "generated_new"}
    assert len(box_hist["new_entities"]) == 6      # 6 box faces recorded
    # hole records rules but no entity lists — the honest hot-path reality
    assert result.step_histories["h"]["rules"]["consumed_volume"] == "consumed"
    assert result.step_histories["h"]["new_entities"] == []


# ---------------------------------------------------------------------------
# the intuition pin
# ---------------------------------------------------------------------------

def test_intuition_pin_fillet_depends_on_box_not_hole():
    plan = _plan_box_hole_fillet()
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"
    tree = build_feature_tree(plan, result)

    # box is the root
    assert tree["b"]["parents"] == []
    assert tree["b"]["evidence"] is None

    # hole depends on box, via recorded history rules (creator chain)
    assert _parent_ids(tree, "h") == ["b"]
    assert tree["h"]["parents"][0]["evidence"] == "history"

    # fillet depends on box — NOT on the hole (no selector overlap)
    assert _parent_ids(tree, "f") == ["b"]
    assert tree["f"]["parents"][0]["evidence"] == "history"
    assert "f" not in _child_ids(tree, "h")
    assert "h" not in _parent_ids(tree, "f")


def test_children_mirror_parents():
    plan = _plan_box_hole_fillet()
    result = PlanExecutor(plan).run()
    tree = build_feature_tree(plan, result)
    for sid, node in tree.items():
        for edge in node["parents"]:
            back = tree[edge["step_id"]]["children"]
            assert any(
                e["step_id"] == sid and e["evidence"] == edge["evidence"]
                for e in back
            ), f"{edge['step_id']} -> {sid} missing from children"


# ---------------------------------------------------------------------------
# arg_reference evidence
# ---------------------------------------------------------------------------

def test_tag_reference_edge_and_history_entity_match():
    plan = _plan_tag_chain()
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"
    tree = build_feature_tree(plan, result)

    # pocket statically references the tag the tag step created
    pkt_edges = {e["step_id"]: e["evidence"] for e in tree["pkt"]["parents"]}
    assert pkt_edges["tag"] == "arg_reference"
    assert pkt_edges["b"] == "history"          # creator chain

    # tag_face's recorded inherited EntityRef (the top face) geometrically
    # matches an entity the box recorded as new — entity-level history edge
    tag_edges = {e["step_id"]: e["evidence"] for e in tree["tag"]["parents"]}
    assert tag_edges == {"b": "history"}


def test_positional_overlap_creates_arg_reference_edge():
    """A fillet whose edges_by_position bbox contains the hole's position
    DOES depend on the hole ('unless selector overlaps' half of the pin)."""
    plan = Plan.model_validate({
        "schema_version": 1,
        "plan_name": "overlap",
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": 40.0, "width_mm": 40.0,
                      "height_mm": 10.0}},
            {"id": "h", "skill": "hole",
             "args": {"position": [10.0, 10.0, 10.0], "diameter_mm": 6.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
            {"id": "f", "skill": "fillet_edges_by_predicate",
             "args": {"selector": {"kind": "edges_by_position",
                                   "bbox": {"min": [6.0, 6.0, 9.5],
                                            "max": [14.0, 14.0, 10.5]}},
                      "radius_mm": 1.0}},
        ],
    })
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"      # the rim fillet genuinely applies
    tree = build_feature_tree(plan, result)
    f_edges = {e["step_id"]: e["evidence"] for e in tree["f"]["parents"]}
    assert f_edges["h"] == "arg_reference"
    # and the suppression closure of the hole now drags the fillet along
    ids = [c["step_id"] for c in suppress_closure(tree, "h")]
    assert ids == ["h", "f"]


# ---------------------------------------------------------------------------
# honest degradation
# ---------------------------------------------------------------------------

def test_empty_history_step_gets_unknown_sequence_edge():
    plan = Plan.model_validate({
        "schema_version": 1,
        "plan_name": "empty_hist",
        "steps": [
            {"id": "b", "skill": "box",
             "args": {"length_mm": 20.0, "width_mm": 20.0,
                      "height_mm": 10.0}},
            # read-only inspect skill returning EntityHistoryMap() — EMPTY
            {"id": "obb", "skill": "oriented_bounding_box", "args": {}},
            {"id": "h", "skill": "hole",
             "args": {"position": [0.0, 0.0, 10.0], "diameter_mm": 4.0,
                      "depth_mm": 5.0, "direction": "-Z"}},
        ],
    })
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"
    tree = build_feature_tree(plan, result)

    # honest degradation: order is all we know for the empty-history step
    assert tree["obb"]["history_recorded"] is False
    assert tree["obb"]["parents"] == [
        {"step_id": "b", "evidence": "unknown", "kind": "sequence"}
    ]
    assert tree["obb"]["evidence"] == "unknown"

    # the mutator AFTER it still hangs off the creator, not the inspect step
    assert _parent_ids(tree, "h") == ["b"]
    assert suppress_closure(tree, "obb") == [
        {"step_id": "obb", "via": None, "evidence": None}
    ]


def test_static_mode_degrades_honestly():
    """No execution result: no 'history' claims — only arg_reference and
    unknown/sequence edges; create steps stay roots."""
    tree = build_feature_tree(_plan_tag_chain(), None)
    assert all(node["history_recorded"] is None for node in tree.values())
    assert tree["b"]["parents"] == []
    assert tree["tag"]["parents"] == [
        {"step_id": "b", "evidence": "unknown", "kind": "sequence"}
    ]
    pkt_edges = {e["step_id"]: e["evidence"] for e in tree["pkt"]["parents"]}
    assert pkt_edges["tag"] == "arg_reference"
    assert "history" not in {
        e["evidence"] for n in tree.values() for e in n["parents"]
    }


# ---------------------------------------------------------------------------
# closure semantics
# ---------------------------------------------------------------------------

def test_suppress_closure_of_root_is_entire_plan():
    plan = _plan_box_hole_fillet()
    result = PlanExecutor(plan).run()
    tree = build_feature_tree(plan, result)
    ids = [c["step_id"] for c in suppress_closure(tree, "b")]
    assert ids == ["b", "h", "f"]


def test_suppress_closure_transitive_via_tag_chain():
    plan = _plan_tag_chain()
    result = PlanExecutor(plan).run()
    tree = build_feature_tree(plan, result)
    closure = suppress_closure(tree, "tag")
    assert [c["step_id"] for c in closure] == ["tag", "pkt"]
    assert closure[0] == {"step_id": "tag", "via": None, "evidence": None}
    assert closure[1]["via"] == "tag"
    assert closure[1]["evidence"] == "arg_reference"


def test_suppress_closure_unknown_id_raises():
    plan = _plan_box_hole_fillet()
    tree = build_feature_tree(plan, None)
    with pytest.raises(KeyError):
        suppress_closure(tree, "nope")


# ---------------------------------------------------------------------------
# incremental primitive
# ---------------------------------------------------------------------------

def test_run_plan_suffix_from_snapshot_matches_full_run():
    baseline_plan = _plan_box_hole_fillet()
    baseline = PlanExecutor(baseline_plan).run()
    assert baseline.outcome == "PASS"
    v_full = _volume(baseline.final_body)

    # snapshot before the fillet (index 2) = recorded body after the hole
    snapshot, ok = snapshot_body_before(baseline, 2)
    assert ok and snapshot is not None

    fresh = _plan_box_hole_fillet()
    suffix = run_plan_suffix(fresh, 2, initial_body=snapshot)
    assert suffix.outcome == "PASS"
    assert abs(_volume(suffix.final_body) - v_full) <= 1e-9

    # index 0 = "no body yet" is an available snapshot by definition
    assert snapshot_body_before(baseline, 0) == (None, True)
    # an empty suffix passes the seed body straight through
    empty = run_plan_suffix(fresh, len(fresh.steps), initial_body=snapshot)
    assert empty.outcome == "PASS"
    assert empty.final_body is snapshot


# ---------------------------------------------------------------------------
# guards + JSON safety
# ---------------------------------------------------------------------------

def test_duplicate_step_ids_refused():
    plan = Plan.model_validate({
        "schema_version": 1,
        "plan_name": "dupes",
        "steps": [
            {"id": "a", "skill": "box",
             "args": {"length_mm": 5.0, "width_mm": 5.0, "height_mm": 5.0}},
            {"id": "a", "skill": "box",
             "args": {"length_mm": 6.0, "width_mm": 6.0, "height_mm": 6.0}},
        ],
    })
    with pytest.raises(ValueError, match="fm.feature_tree_duplicate_step_ids"):
        build_feature_tree(plan, None)


def test_tree_is_strict_json_safe():
    plan = _plan_tag_chain()
    result = PlanExecutor(plan).run()
    tree = build_feature_tree(plan, result)
    json.dumps(tree, allow_nan=False)   # raises on inf/nan/non-serializable
