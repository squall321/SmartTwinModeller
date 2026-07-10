"""sketch_solve_lite — the constraint SOLVER half of sketch_relation_check.

Ship-gate pins (roadmap 3-5 part 2):
  * WELL-CONSTRAINED convergence set (>=10 cases: dimensioned rectangles,
    line-arc slot chains, right triangle, parallelogram, rhombus, L-shape,
    5-deg skewed quad) -> convergence rate >= 90% (the roadmap gate);
  * ARC-FLIP: solved arcs keep their original sense (ccw flag unchanged AND
    chord direction not reversed) — warm start prevents the classic flip;
  * warm-start no-op: already-satisfying geometry barely moves (<1e-6 mm);
  * under-constrained -> solves + label 'under_constrained_non_unique';
    over-constrained -> solves + label 'over_constrained_best_fit';
  * UNCONVERGED IS HONEST: conflicting relations -> ok=True, converged=False,
    per-relation residuals reported, never an exception;
  * converged is decided by an INDEPENDENT sketch_relation_check re-run on
    solved_sketch (cross-verified here through that skill's public API);
  * refusals: fm.too_large (cap 30), and the checker-delegated
    fm.relations_need_composite / fm.bad_segment_index /
    fm.relation_needs_line / fm.degenerate_segment;
  * strict-JSON extras; body-less sentinel; read-only body identity.
"""
from __future__ import annotations

import json
import math

import pytest
from pydantic import TypeAdapter

from phone_designer.skills.inspect.sketch_solve_lite import SketchSolveLite
from phone_designer.skills.modify_pocket._sketch import SketchSpec


def _solve(sketch, relations, **kw):
    res = SketchSolveLite().apply(
        None, {"sketch": sketch, "relations": relations, **kw})
    return res.extras["sketch_solve"]


# ── fixtures / builders ──────────────────────────────────────────────────────

def _poly(pts):
    """Closed polyline composite from shared vertices (closure exact)."""
    n = len(pts)
    return {"kind": "composite", "segments": [
        {"kind": "line",
         "start": [float(pts[i][0]), float(pts[i][1])],
         "end": [float(pts[(i + 1) % n][0]), float(pts[(i + 1) % n][1])]}
        for i in range(n)]}


def _rect_relations(w, h):
    return [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "horizontal", "segment_index": 2},
        {"kind": "vertical", "segment_index": 1},
        {"kind": "vertical", "segment_index": 3},
        {"kind": "equal_length", "a": 0, "b": 2},
        {"kind": "equal_length", "a": 1, "b": 3},
        {"kind": "length", "segment_index": 0, "value_mm": w},
        {"kind": "length", "segment_index": 1, "value_mm": h},
    ]  # 8 relations == 8 DOF of a 4-line chain -> 'well_constrained'


def _slot(v, r):
    """line / arc / line / arc slot from 4 shared vertices."""
    return {"kind": "composite", "segments": [
        {"kind": "line", "start": v[0], "end": v[1]},
        {"kind": "arc", "start": v[1], "end": v[2], "radius": r, "ccw": True},
        {"kind": "line", "start": v[2], "end": v[3]},
        {"kind": "arc", "start": v[3], "end": v[0], "radius": r, "ccw": True},
    ]}


def _slot_relations(length, width):
    return [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "horizontal", "segment_index": 2},
        {"kind": "equal_length", "a": 0, "b": 2},
        {"kind": "length", "segment_index": 0, "value_mm": length},
        {"kind": "length", "segment_index": 2, "value_mm": length},
        {"kind": "distance", "a": 0, "b": 2, "value_mm": width},
        {"kind": "coincident_endpoints", "a_end": 0, "b_start": 1},
        {"kind": "coincident_endpoints", "a_end": 1, "b_start": 2},
        {"kind": "coincident_endpoints", "a_end": 2, "b_start": 3},
        {"kind": "coincident_endpoints", "a_end": 3, "b_start": 0},
    ]  # 10 relations == 2*4 + 2 arc radii = 10 DOF -> 'well_constrained'


_EXACT_RECT = _poly([(0, 0), (20, 0), (20, 10), (0, 10)])

# 5-deg skewed quad (same analytic fixture family as the checker's tests).
_SK = 5.0
_BX = 20.0 * math.cos(math.radians(_SK))
_BY = 20.0 * math.sin(math.radians(_SK))

_PERTURBED_SLOT = _slot(
    [[0.3, -0.2], [20.4, 0.3], [19.7, 10.4], [-0.2, 9.8]], 5.5)

# ── SHIP GATE: the well-constrained convergence set (>=10 cases) ─────────────

WELL_CASES = [
    ("rect_20x10_perturbed",
     _poly([(0.0, 0.0), (20.4, -0.3), (19.8, 10.5), (-0.4, 9.7)]),
     _rect_relations(20, 10)),
    ("rect_40x15_skew3",
     _poly([(0.0, 0.0), (39.95, 2.09), (39.5, 17.1), (-0.5, 15.0)]),
     _rect_relations(40, 15)),
    ("square_10_eq_chain",
     _poly([(0.3, -0.2), (10.4, 0.3), (9.8, 10.2), (-0.3, 9.9)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "vertical", "segment_index": 1},
      {"kind": "horizontal", "segment_index": 2},
      {"kind": "vertical", "segment_index": 3},
      {"kind": "equal_length", "a": 0, "b": 1},
      {"kind": "equal_length", "a": 1, "b": 2},
      {"kind": "equal_length", "a": 2, "b": 3},
      {"kind": "length", "segment_index": 0, "value_mm": 10}]),
    ("rect_20x10_via_distance",
     _poly([(0.2, 0.4), (20.5, -0.2), (19.6, 10.3), (-0.4, 9.8)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "horizontal", "segment_index": 2},
      {"kind": "vertical", "segment_index": 1},
      {"kind": "vertical", "segment_index": 3},
      {"kind": "length", "segment_index": 0, "value_mm": 20},
      {"kind": "length", "segment_index": 1, "value_mm": 10},
      {"kind": "distance", "a": 0, "b": 2, "value_mm": 10},
      {"kind": "distance", "a": 1, "b": 3, "value_mm": 20}]),
    ("right_triangle_20x10",
     _poly([(0.2, -0.3), (20.5, 0.4), (-0.2, 10.3)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "vertical", "segment_index": 2},
      {"kind": "perpendicular", "a": 0, "b": 2},
      {"kind": "length", "segment_index": 0, "value_mm": 20},
      {"kind": "length", "segment_index": 2, "value_mm": 10},
      {"kind": "length", "segment_index": 1, "value_mm": 22.360680}]),
    ("parallelogram_20_slant",
     _poly([(0.2, 0.3), (20.4, -0.2), (24.7, 10.4), (4.6, 9.8)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "horizontal", "segment_index": 2},
      {"kind": "parallel", "a": 1, "b": 3},
      {"kind": "equal_length", "a": 0, "b": 2},
      {"kind": "equal_length", "a": 1, "b": 3},
      {"kind": "length", "segment_index": 0, "value_mm": 20},
      {"kind": "length", "segment_index": 1, "value_mm": 11.180340},
      {"kind": "distance", "a": 0, "b": 2, "value_mm": 10}]),
    ("slot_20x10_r5p5_line_arc_chain", _PERTURBED_SLOT,
     _slot_relations(20, 10)),
    ("slot_30x8_r4p5_line_arc_chain",
     _slot([[0.4, 0.2], [29.6, -0.3], [30.3, 8.4], [-0.3, 7.7]], 4.5),
     _slot_relations(30, 8)),
    ("skewed_quad_5deg_to_rect",
     _poly([(0, 0), (_BX, _BY), (_BX, _BY + 10), (0, 10)]),
     _rect_relations(20, 10)),
    ("rect_5x5_tiny",
     _poly([(0.1, 0.2), (5.2, -0.1), (4.9, 5.15), (-0.15, 4.9)]),
     _rect_relations(5, 5)),
    ("l_shape_6_lines",
     _poly([(0.3, -0.2), (20.4, 0.3), (19.8, 5.3), (8.4, 4.7),
            (7.7, 12.3), (-0.4, 11.8)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "vertical", "segment_index": 1},
      {"kind": "horizontal", "segment_index": 2},
      {"kind": "vertical", "segment_index": 3},
      {"kind": "horizontal", "segment_index": 4},
      {"kind": "vertical", "segment_index": 5},
      {"kind": "length", "segment_index": 0, "value_mm": 20},
      {"kind": "length", "segment_index": 1, "value_mm": 5},
      {"kind": "length", "segment_index": 2, "value_mm": 12},
      {"kind": "length", "segment_index": 3, "value_mm": 7},
      {"kind": "length", "segment_index": 4, "value_mm": 8},
      {"kind": "length", "segment_index": 5, "value_mm": 12}]),
    ("rhombus_side10_height8",
     _poly([(0.2, 0.3), (10.3, -0.2), (15.7, 8.3), (6.2, 7.8)]),
     [{"kind": "horizontal", "segment_index": 0},
      {"kind": "parallel", "a": 0, "b": 2},
      {"kind": "parallel", "a": 1, "b": 3},
      {"kind": "equal_length", "a": 0, "b": 1},
      {"kind": "equal_length", "a": 1, "b": 2},
      {"kind": "equal_length", "a": 2, "b": 3},
      {"kind": "length", "segment_index": 0, "value_mm": 10},
      {"kind": "distance", "a": 0, "b": 2, "value_mm": 8}]),
]


def test_well_constrained_set_convergence_rate_ge_90_percent():
    """THE ship gate: >=90% convergence on >=10 well-constrained cases."""
    assert len(WELL_CASES) >= 10
    outcomes = {}
    for name, sketch, rels in WELL_CASES:
        out = _solve(sketch, rels)
        assert out["ok"] is True
        # every case is built with n_relations == dof_total by construction.
        assert out["constraint_label"] == "well_constrained", name
        outcomes[name] = bool(out["converged"])
    rate = sum(outcomes.values()) / len(outcomes)
    print(f"\nwell-constrained convergence: {sum(outcomes.values())}"
          f"/{len(outcomes)} = {rate:.1%}  {outcomes}")
    assert rate >= 0.9, f"ship gate missed: {rate:.1%} — {outcomes}"


def test_converged_case_is_verified_by_relation_check_itself():
    """converged must equal an INDEPENDENT sketch_relation_check verdict."""
    name, sketch, rels = WELL_CASES[0]
    out = _solve(sketch, rels)
    assert out["converged"] is True
    assert out["residual_norm"] < 0.05  # mixed-unit norm of satisfied rows
    # solved_sketch revalidates as a real SketchSpec (chain closure etc.).
    solved = TypeAdapter(SketchSpec).validate_python(out["solved_sketch"])
    assert solved.kind == "composite"
    # cross-verify through the OTHER skill's public API — no self-scoring.
    from phone_designer.skills.inspect.sketch_relation_check import (
        SketchRelationCheck,
    )
    rc = SketchRelationCheck().apply(
        None, {"sketch": out["solved_sketch"], "relations": rels})
    check = rc.extras["relation_check"]
    assert check["n_satisfied"] == len(rels)
    # DOF bookkeeping reused from the checker: 8-DOF rect fully satisfied.
    assert out["dof"]["total"] == 8
    assert out["dof"]["after"] == 0
    assert out["dof"]["before"] > out["dof"]["after"]
    assert out["dof"]["grade"] == "heuristic"


def test_moved_deltas_are_warm_start_scale_not_teleports():
    # perturbations are <=0.6 mm, so the nearest solution is a few mm at most.
    _, sketch, rels = WELL_CASES[0]
    out = _solve(sketch, rels)
    assert out["converged"] is True
    assert out["moved"], "a perturbed sketch must report moved entities"
    max_delta = max(m["delta"] for m in out["moved"])
    assert max_delta < 5.0, f"teleported by {max_delta} mm from warm start"
    for m in out["moved"]:
        assert m["unit"] == "mm" and m["delta"] > 0


def test_warm_start_noop_on_already_satisfying_geometry():
    out = _solve(_EXACT_RECT, _rect_relations(20, 10))
    assert out["converged"] is True
    # geometry already satisfies everything — nothing moves beyond noise.
    max_delta = max((m["delta"] for m in out["moved"]), default=0.0)
    assert max_delta < 1e-6
    assert out["n_satisfied_before"] == 8


# ── ARC-FLIP pins — warm start preserves arc sense ───────────────────────────

def _chord(seg):
    return (seg["end"][0] - seg["start"][0], seg["end"][1] - seg["start"][1])


def test_arc_flip_slot_solved_arcs_keep_sense():
    out = _solve(_PERTURBED_SLOT, _slot_relations(20, 10))
    assert out["converged"] is True
    solved = out["solved_sketch"]
    for i in (1, 3):  # the two arc segments
        orig, sol = _PERTURBED_SLOT["segments"][i], solved["segments"][i]
        assert sol["kind"] == "arc"
        # sense pin 1: the ccw flag is never a solver variable.
        assert sol["ccw"] == orig["ccw"]
        # sense pin 2: the chord did not reverse — same bulge side.
        co, cs = _chord(orig), _chord(sol)
        assert co[0] * cs[0] + co[1] * cs[1] > 0
        # radius untouched (no relation kind constrains a radius).
        assert sol["radius"] == pytest.approx(orig["radius"], abs=1e-9)


def test_arc_flip_dshape_underconstrained_keeps_sense():
    dshape = {"kind": "composite", "segments": [
        {"kind": "line", "start": [-9.6, 0.3], "end": [10.4, -0.2]},
        {"kind": "arc", "start": [10.4, -0.2], "end": [-9.6, 0.3],
         "radius": 10.5, "ccw": True},
    ]}
    rels = [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "length", "segment_index": 0, "value_mm": 20},
        {"kind": "coincident_endpoints", "a_end": 0, "b_start": 1},
        {"kind": "coincident_endpoints", "a_end": 1, "b_start": 0},
    ]  # 4 declared < 5 DOF (2*2 junctions + arc radius) -> under-constrained
    out = _solve(dshape, rels)
    assert out["converged"] is True
    assert out["constraint_label"] == "under_constrained_non_unique"
    orig, sol = dshape["segments"][1], out["solved_sketch"]["segments"][1]
    assert sol["ccw"] is True
    co, cs = _chord(orig), _chord(sol)
    assert co[0] * cs[0] + co[1] * cs[1] > 0


# ── under / over constrained honesty ─────────────────────────────────────────

def test_under_constrained_solves_with_non_unique_label():
    out = _solve(
        _poly([(0.0, 0.0), (20.4, -0.3), (19.8, 10.5), (-0.4, 9.7)]),
        [{"kind": "horizontal", "segment_index": 0},
         {"kind": "length", "segment_index": 0, "value_mm": 20}])
    assert out["converged"] is True
    assert out["constraint_label"] == "under_constrained_non_unique"
    assert "under_constrained_non_unique" in out["honest_note"]
    assert "NOT a parametric sketcher" in out["honest_note"]


def test_over_constrained_consistent_relations_best_fit_label():
    rels = _rect_relations(20, 10) + [{"kind": "parallel", "a": 0, "b": 2}]
    out = _solve(
        _poly([(0.0, 0.0), (20.4, -0.3), (19.8, 10.5), (-0.4, 9.7)]), rels)
    # 9 declared > 8 DOF but mutually consistent -> best fit still converges.
    assert out["constraint_label"] == "over_constrained_best_fit"
    assert "over_constrained_best_fit" in out["honest_note"]
    assert out["converged"] is True


def test_conflicting_relations_unconverged_is_honest_not_exception():
    rels = [
        {"kind": "length", "segment_index": 0, "value_mm": 20.0},
        {"kind": "length", "segment_index": 0, "value_mm": 25.0},  # impossible
    ]
    out = _solve(_EXACT_RECT, rels)  # never raises
    assert out["ok"] is True
    assert out["converged"] is False
    # least-squares best fit splits the conflict: ~22.5 -> residuals ~2.5 each.
    resid = sorted(r["residual"] for r in out["relations"])
    assert resid[0] == pytest.approx(2.5, abs=0.5)
    assert resid[1] == pytest.approx(2.5, abs=0.5)
    assert 3.0 < out["residual_norm"] < 4.0
    assert "NOT converged" in out["honest_note"]
    assert "honest result" in out["honest_note"]


def test_empty_relations_vacuous_noop():
    out = _solve(_EXACT_RECT, [])
    assert out["converged"] is True
    assert out["moved"] == []
    assert out["n_relations"] == 0
    assert "nothing to solve" in out["honest_note"]


# ── structured refusals ──────────────────────────────────────────────────────

def test_too_large_composite_refused():
    n = 31  # hard cap is 30 segments
    pts = [(50 * math.cos(2 * math.pi * k / n),
            50 * math.sin(2 * math.pi * k / n)) for k in range(n)]
    with pytest.raises(ValueError, match="fm.too_large"):
        _solve(_poly(pts), [])


def test_custom_max_entities_lower_cap_and_hard_cap():
    # 6-segment L-shape refused under a caller-lowered cap of 4.
    with pytest.raises(ValueError, match="fm.too_large"):
        _solve(WELL_CASES[10][1], [], max_entities=4)
    # the hard cap itself is schema-enforced: 31 is not an accepted value.
    with pytest.raises(Exception, match="less than or equal to 30"):
        _solve(_EXACT_RECT, [], max_entities=31)


def test_checker_refusals_are_delegated_not_reimplemented():
    with pytest.raises(ValueError, match="fm.relations_need_composite"):
        _solve({"kind": "circle", "diameter_mm": 10},
               [{"kind": "horizontal", "segment_index": 0}])
    with pytest.raises(ValueError, match="fm.bad_segment_index"):
        _solve(_EXACT_RECT, [{"kind": "horizontal", "segment_index": 99}])
    dshape = {"kind": "composite", "segments": [
        {"kind": "line", "start": [-10, 0], "end": [10, 0]},
        {"kind": "arc", "start": [10, 0], "end": [-10, 0],
         "radius": 10, "ccw": True},
    ]}
    with pytest.raises(ValueError, match="fm.relation_needs_line"):
        _solve(dshape, [{"kind": "horizontal", "segment_index": 1}])
    degen = {"kind": "composite", "segments": [
        {"kind": "line", "start": [0, 0], "end": [10, 0]},
        {"kind": "line", "start": [10, 0], "end": [10, 0]},  # zero length
        {"kind": "line", "start": [10, 0], "end": [0, 0]},
    ]}
    with pytest.raises(ValueError, match="fm.degenerate_segment"):
        _solve(degen, [{"kind": "horizontal", "segment_index": 1}])


# ── contracts: strict JSON + body-less/read-only ─────────────────────────────

def test_extras_strict_json_safe_and_bodyless_readonly():
    res = SketchSolveLite().apply(None, {
        "sketch": _poly([(0.0, 0.0), (20.4, -0.3), (19.8, 10.5), (-0.4, 9.7)]),
        "relations": _rect_relations(20, 10),
    })
    # strict-JSON house rule: no inf/nan anywhere in the payload.
    json.dumps(res.extras["sketch_solve"], allow_nan=False)
    # honesty lives INSIDE the artifact, not only in docs.
    assert "NOT a parametric sketcher" in res.extras["sketch_solve"]["honest_note"]
    # body-less call returns a sentinel (non-None) body.
    assert res.body is not None

    # read-only: a given body is returned unchanged (identity).
    from phone_designer.skills.create.box import Box
    box = Box().apply(None, {"length_mm": 5, "width_mm": 5, "height_mm": 5}).body
    res2 = SketchSolveLite().apply(
        box, {"sketch": _EXACT_RECT, "relations": []})
    assert res2.body is box
