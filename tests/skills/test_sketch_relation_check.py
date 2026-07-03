"""sketch_relation_check — verify-only constraint-lite 2D relation check.

Pins (roadmap 3-5 part 1 — ships unconditionally; the solver is a later,
separate decision):
  * rectangle composite + horizontal/vertical/equal_length → ALL satisfied,
    residuals ~0, correct residual_unit per kind;
  * a 5-deg skewed quad → the horizontal residual IS the analytic skew angle
    (5.0 deg), and perpendicular(0,1) misses by the same 5.0 deg;
  * distance relation measured vs analytic (rectangle side separation);
  * over-declared relations → constrained_verdict 'over' (and under/well are
    reachable), the verdict labeled grade='heuristic' with a solver note;
  * unknown segment index → fm.bad_segment_index;
  * non-composite sketch → fm.relations_need_composite;
  * angle relation on an arc → fm.relation_needs_line (no silent chord fake);
  * zero-length line in an angle relation → fm.degenerate_segment;
  * whole extras payload strict-JSON-safe (json.dumps allow_nan=False);
  * body-less (body=None) and read-only (given body returned unchanged).
"""
from __future__ import annotations

import json
import math

import pytest

from phone_designer.skills.inspect.sketch_relation_check import SketchRelationCheck


def _check(sketch, relations, **kw):
    res = SketchRelationCheck().apply(
        None, {"sketch": sketch, "relations": relations, **kw})
    return res.extras["relation_check"]


# ── fixtures ─────────────────────────────────────────────────────────────────

# 20 × 10 axis-aligned rectangle: 0=bottom, 1=right, 2=top, 3=left.
_RECT = {"kind": "composite", "segments": [
    {"kind": "line", "start": [0, 0], "end": [20, 0]},
    {"kind": "line", "start": [20, 0], "end": [20, 10]},
    {"kind": "line", "start": [20, 10], "end": [0, 10]},
    {"kind": "line", "start": [0, 10], "end": [0, 0]},
]}

# Quad whose bottom edge is skewed 5 deg off horizontal (analytic ground truth).
_SKEW_DEG = 5.0
_BX = 20.0 * math.cos(math.radians(_SKEW_DEG))
_BY = 20.0 * math.sin(math.radians(_SKEW_DEG))
_SKEWED = {"kind": "composite", "segments": [
    {"kind": "line", "start": [0, 0], "end": [_BX, _BY]},           # 0: skewed 5°
    {"kind": "line", "start": [_BX, _BY], "end": [_BX, _BY + 10]},  # 1: vertical
    {"kind": "line", "start": [_BX, _BY + 10], "end": [0, 10]},
    {"kind": "line", "start": [0, 10], "end": [0, 0]},
]}

# D-shape: 0=line (diameter), 1=arc — for the arc refusal + arc DOF (+1 radius).
_DSHAPE = {"kind": "composite", "segments": [
    {"kind": "line", "start": [-10, 0], "end": [10, 0]},
    {"kind": "arc", "start": [10, 0], "end": [-10, 0], "radius": 10, "ccw": True},
]}


# ── satisfied residuals on exact geometry ────────────────────────────────────

def test_rectangle_h_v_equal_length_all_satisfied_residuals_zero():
    rc = _check(_RECT, [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "horizontal", "segment_index": 2},
        {"kind": "vertical", "segment_index": 1},
        {"kind": "vertical", "segment_index": 3},
        {"kind": "equal_length", "a": 0, "b": 2},
        {"kind": "equal_length", "a": 1, "b": 3},
    ])
    assert rc["n_relations"] == 6 and rc["n_satisfied"] == 6
    for row in rc["relations"]:
        assert row["satisfied"] is True
        assert row["residual"] == pytest.approx(0.0, abs=1e-9)
    # units + tolerance per kind: deg for h/v, mm for equal_length.
    units = {r["kind"]: (r["residual_unit"], r["tolerance_used"])
             for r in rc["relations"]}
    assert units["horizontal"] == ("deg", 0.01)
    assert units["vertical"] == ("deg", 0.01)
    assert units["equal_length"] == ("mm", 1e-3)


def test_parallel_and_perpendicular_on_rectangle():
    rc = _check(_RECT, [
        {"kind": "parallel", "a": 0, "b": 2},
        {"kind": "perpendicular", "a": 0, "b": 1},
        {"kind": "parallel", "a": 0, "b": 1},   # sides ARE perpendicular → 90°
    ])
    par, perp, bad = rc["relations"]
    assert par["satisfied"] and par["residual"] == pytest.approx(0.0, abs=1e-9)
    assert perp["satisfied"] and perp["residual"] == pytest.approx(0.0, abs=1e-9)
    assert bad["satisfied"] is False
    assert bad["residual"] == pytest.approx(90.0, abs=1e-6)
    assert bad["residual_unit"] == "deg"


# ── analytic residuals on off geometry ──────────────────────────────────────

def test_skewed_quad_horizontal_residual_is_the_skew_angle():
    rc = _check(_SKEWED, [
        {"kind": "horizontal", "segment_index": 0},   # off by exactly 5°
        {"kind": "vertical", "segment_index": 1},     # truly vertical
        {"kind": "perpendicular", "a": 0, "b": 1},    # also off by exactly 5°
    ])
    h, v, p = rc["relations"]
    assert h["satisfied"] is False
    assert h["residual"] == pytest.approx(_SKEW_DEG, abs=1e-6)
    assert h["residual_unit"] == "deg"
    assert v["satisfied"] is True and v["residual"] == pytest.approx(0.0, abs=1e-9)
    assert p["satisfied"] is False
    assert p["residual"] == pytest.approx(_SKEW_DEG, abs=1e-6)
    # only the satisfied one counts toward the constraint tally.
    assert rc["n_satisfied"] == 1
    assert rc["dof"]["n_constraints_declared"] == 3
    assert rc["dof"]["n_constraints_satisfied"] == 1


def test_distance_measured_vs_analytic():
    rc = _check(_RECT, [
        # bottom↔top separation is analytically 10 mm.
        {"kind": "distance", "a": 0, "b": 2, "value_mm": 10.0},
        {"kind": "distance", "a": 0, "b": 2, "value_mm": 12.0},  # off by 2
        # adjacent sides touch → measured 0.
        {"kind": "distance", "a": 0, "b": 1, "value_mm": 0.0},
    ])
    ok, off, touch = rc["relations"]
    assert ok["satisfied"] and ok["residual"] == pytest.approx(0.0, abs=1e-9)
    assert ok["measured_distance_mm"] == pytest.approx(10.0, abs=1e-9)
    assert off["satisfied"] is False
    assert off["residual"] == pytest.approx(2.0, abs=1e-9)
    assert off["residual_unit"] == "mm" and off["tolerance_used"] == 1e-3
    assert touch["satisfied"] and touch["measured_distance_mm"] == pytest.approx(0.0)


def test_length_and_coincident_endpoints():
    rc = _check(_RECT, [
        {"kind": "length", "segment_index": 0, "value_mm": 20.0},
        {"kind": "length", "segment_index": 0, "value_mm": 21.5},   # off by 1.5
        {"kind": "coincident_endpoints", "a_end": 0, "b_start": 1},  # chained
        {"kind": "coincident_endpoints", "a_end": 0, "b_start": 2},  # 10 mm gap
    ])
    good, off, coin, gap = rc["relations"]
    assert good["satisfied"] and good["measured_length_mm"] == pytest.approx(20.0)
    assert off["satisfied"] is False
    assert off["residual"] == pytest.approx(1.5, abs=1e-9)
    assert coin["satisfied"] and coin["residual"] == pytest.approx(0.0, abs=1e-9)
    assert gap["satisfied"] is False
    assert gap["residual"] == pytest.approx(10.0, abs=1e-9)
    assert gap["residual_unit"] == "mm"


# ── DOF summary + heuristic verdict ──────────────────────────────────────────

def test_dof_summary_rectangle_and_verdict_bands():
    # Rectangle: 4 line segments, closed chain → 4 junction points → 8 DOF.
    base = [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "horizontal", "segment_index": 2},
        {"kind": "vertical", "segment_index": 1},
        {"kind": "vertical", "segment_index": 3},
        {"kind": "equal_length", "a": 0, "b": 2},
        {"kind": "equal_length", "a": 1, "b": 3},
        {"kind": "length", "segment_index": 0, "value_mm": 20.0},
        {"kind": "length", "segment_index": 1, "value_mm": 10.0},
    ]  # 8 satisfied relations == dof_total → 'well'

    under = _check(_RECT, base[:2])
    assert under["dof"]["n_segments"] == 4
    assert under["dof"]["dof_total"] == 8
    assert under["dof"]["constrained_verdict"] == "under"

    well = _check(_RECT, base)
    assert well["n_satisfied"] == 8
    assert well["dof"]["constrained_verdict"] == "well"


def test_over_declared_relations_verdict_over_and_heuristic_label():
    # 9 satisfied relations on an 8-DOF rectangle → 'over'.
    rels = [
        {"kind": "horizontal", "segment_index": 0},
        {"kind": "horizontal", "segment_index": 2},
        {"kind": "vertical", "segment_index": 1},
        {"kind": "vertical", "segment_index": 3},
        {"kind": "equal_length", "a": 0, "b": 2},
        {"kind": "equal_length", "a": 1, "b": 3},
        {"kind": "length", "segment_index": 0, "value_mm": 20.0},
        {"kind": "length", "segment_index": 1, "value_mm": 10.0},
        {"kind": "parallel", "a": 0, "b": 2},
    ]
    rc = _check(_RECT, rels)
    assert rc["n_satisfied"] == 9
    dof = rc["dof"]
    assert dof["dof_total"] == 8
    assert dof["constrained_verdict"] == "over"
    # the verdict is a labeled heuristic, never a solver claim.
    assert dof["grade"] == "heuristic"
    assert "solver" in dof["note"]
    assert rc["solved"] is False


def test_arc_and_spline_shape_dof_extras():
    # D-shape: 2 segments (line + arc), 2 junctions → 2·2 + 1 (arc radius) = 5.
    rc = _check(_DSHAPE, [
        {"kind": "coincident_endpoints", "a_end": 0, "b_start": 1}])
    assert rc["dof"]["n_segments"] == 2
    assert rc["dof"]["dof_total"] == 5
    assert rc["relations"][0]["satisfied"] is True  # arcs OK for coincidence

    # spline with 4 fit points → 2 interior points → +4: 2·2 + 4 = 8.
    spline_loop = {"kind": "composite", "segments": [
        {"kind": "spline", "points": [[0, 0], [3, 2], [7, 2], [10, 0]]},
        {"kind": "line", "start": [10, 0], "end": [0, 0]},
    ]}
    rc2 = _check(spline_loop, [])
    assert rc2["dof"]["dof_total"] == 8
    assert rc2["dof"]["n_constraints_declared"] == 0
    assert rc2["dof"]["constrained_verdict"] == "under"


# ── structured refusals ──────────────────────────────────────────────────────

def test_unknown_segment_index_refused():
    with pytest.raises(ValueError, match="fm.bad_segment_index"):
        _check(_RECT, [{"kind": "horizontal", "segment_index": 99}])


def test_non_composite_sketch_refused():
    with pytest.raises(ValueError, match="fm.relations_need_composite"):
        _check({"kind": "circle", "diameter_mm": 10},
               [{"kind": "horizontal", "segment_index": 0}])


def test_angle_relation_on_arc_refused_no_chord_fake():
    with pytest.raises(ValueError, match="fm.relation_needs_line"):
        _check(_DSHAPE, [{"kind": "horizontal", "segment_index": 1}])
    with pytest.raises(ValueError, match="fm.relation_needs_line"):
        _check(_DSHAPE, [{"kind": "length", "segment_index": 1, "value_mm": 5}])


def test_zero_length_line_in_angle_relation_refused():
    degen = {"kind": "composite", "segments": [
        {"kind": "line", "start": [0, 0], "end": [10, 0]},
        {"kind": "line", "start": [10, 0], "end": [10, 0]},   # zero length
        {"kind": "line", "start": [10, 0], "end": [0, 0]},
    ]}
    with pytest.raises(ValueError, match="fm.degenerate_segment"):
        _check(degen, [{"kind": "horizontal", "segment_index": 1}])


def test_self_pair_relation_rejected_by_args_schema():
    # parallel(a=0, b=0) is meaningless — refused at pydantic validation.
    with pytest.raises(Exception, match="distinct"):
        _check(_RECT, [{"kind": "parallel", "a": 0, "b": 0}])


# ── contracts: strict JSON + body-less/read-only ─────────────────────────────

def test_extras_strict_json_safe_and_bodyless_readonly():
    res = SketchRelationCheck().apply(None, {
        "sketch": _SKEWED,
        "relations": [
            {"kind": "horizontal", "segment_index": 0},
            {"kind": "distance", "a": 1, "b": 3, "value_mm": 19.0},
            {"kind": "coincident_endpoints", "a_end": 3, "b_start": 0},
        ],
    })
    # strict-JSON house rule: no inf/nan anywhere in the payload.
    json.dumps(res.extras["relation_check"], allow_nan=False)
    # body-less call returns a sentinel (non-None) body.
    assert res.body is not None

    # read-only: a given body is returned unchanged (identity).
    from phone_designer.skills.create.box import Box
    box = Box().apply(None, {"length_mm": 5, "width_mm": 5, "height_mm": 5}).body
    res2 = SketchRelationCheck().apply(box, {"sketch": _RECT, "relations": []})
    assert res2.body is box
