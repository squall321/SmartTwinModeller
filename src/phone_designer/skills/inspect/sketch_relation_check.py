"""sketch_relation_check — VERIFY-ONLY constraint-lite 2D sketch relation check.

The verification half of constraint-lite 2D sketching (roadmap 3-5, part 1 —
ships unconditionally; the SOLVER is a separate later go/no-go decision). Takes
a ``SketchSpec`` plus a list of declared relations and reports, per relation,
whether the CURRENT geometry already satisfies it and by how much it misses
(the residual). **Zero solving happens here** — geometry is never moved. This
is explicitly NOT a parametric sketcher and must not be marketed as one.

Relations reference COMPOSITE-sketch segment indices (``kind='composite'``,
segments of line/arc/spline). Any other SketchSpec kind (circle, rectangle,
polygon, …) has no segment list to index into → structured refusal
``fm.relations_need_composite`` (rebuild the profile as a composite to check
relations on it).

Relation kinds (discriminated union defined HERE, not in _sketch.py):
  - horizontal{segment_index}            residual in **deg** (tilt off the X axis)
  - vertical{segment_index}              residual in **deg** (tilt off the Y axis)
  - parallel{a,b}                        residual in **deg** (angle between, folded to [0,90])
  - perpendicular{a,b}                   residual in **deg** (|90 − angle between|)
  - equal_length{a,b}                    residual in **mm**  (| |a| − |b| |)
  - distance{a,b,value_mm}               residual in **mm**  (|min-distance(a,b) − value|)
  - length{segment_index,value_mm}       residual in **mm**  (| |seg| − value |)
  - coincident_endpoints{a_end,b_start}  residual in **mm**  (gap seg[a_end].end ↔ seg[b_start].start)

``distance`` measures the MINIMUM distance between the two segments treated as
closed point sets (0 if they touch or cross); for the parallel opposite sides
of a rectangle this equals the perpendicular separation — the analytic value.

Honesty gates:
  - Direction/length relations are defined for LINE segments only. An arc or
    spline target → ``fm.relation_needs_line`` (a silent chord approximation
    would report a fake angle/length). ``coincident_endpoints`` accepts any
    segment kind (every segment has start/end).
  - A zero-length line has no direction → ``fm.degenerate_segment`` for the
    four angle relations.
  - An out-of-range index → ``fm.bad_segment_index``.

DOF MODEL (stated so the verdict is auditable):
  A composite is a CLOSED polyline chain (its own pydantic validator enforces
  seg[i].end == seg[i+1].start and last.end == first.start), so the chain has
  exactly ``n_points == n_segments`` distinct junction points. We count:

      dof_total = 2 × n_points                (x,y per junction point)
                + 1 per arc segment           (its radius)
                + 2 per spline interior point (interior fit points move freely)

  and treat every RESIDUAL-SATISFIED declared relation as removing exactly
  1 DOF. ``constrained_verdict`` = 'under' | 'well' | 'over' by comparing
  n_constraints_satisfied against dof_total. This is a **HEURISTIC**
  (grade='heuristic'): it ignores the 3 rigid-body modes, ignores constraint
  redundancy/rank (two relations can be the same equation), and counts
  coincident_endpoints as 1 although it geometrically removes 2. A real DOF
  analysis needs a constraint solver's Jacobian rank — deliberately out of
  scope for this verify-only skill.

Read-only and body-less: may be invoked with body=None (pure-data, like
catalog_lookup); the input body — when given — is returned unchanged.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_EPS_LEN = 1e-9  # mm — below this a line segment has no direction


# ── RelationSpec — discriminated union (lives HERE; _sketch.py is sketch-core's) ──


class HorizontalRelation(BaseModel):
    kind: Literal["horizontal"] = "horizontal"
    segment_index: int = Field(ge=0)


class VerticalRelation(BaseModel):
    kind: Literal["vertical"] = "vertical"
    segment_index: int = Field(ge=0)


class _PairRelation(BaseModel):
    a: int = Field(ge=0)
    b: int = Field(ge=0)

    @model_validator(mode="after")
    def _distinct(self):
        if self.a == self.b:
            raise ValueError(
                f"{getattr(self, 'kind', 'pair relation')}: a and b must be "
                f"distinct segment indices (both are {self.a})")
        return self


class ParallelRelation(_PairRelation):
    kind: Literal["parallel"] = "parallel"


class PerpendicularRelation(_PairRelation):
    kind: Literal["perpendicular"] = "perpendicular"


class EqualLengthRelation(_PairRelation):
    kind: Literal["equal_length"] = "equal_length"


class DistanceRelation(_PairRelation):
    kind: Literal["distance"] = "distance"
    value_mm: float = Field(ge=0, description="Target minimum distance between "
                                              "the two segments, mm.")


class LengthRelation(BaseModel):
    kind: Literal["length"] = "length"
    segment_index: int = Field(ge=0)
    value_mm: float = Field(gt=0, description="Target segment length, mm.")


class CoincidentEndpointsRelation(BaseModel):
    kind: Literal["coincident_endpoints"] = "coincident_endpoints"
    a_end: int = Field(ge=0, description="Segment whose END point is checked.")
    b_start: int = Field(ge=0, description="Segment whose START point is checked.")


RelationSpec = Annotated[
    Union[
        HorizontalRelation,
        VerticalRelation,
        ParallelRelation,
        PerpendicularRelation,
        EqualLengthRelation,
        DistanceRelation,
        LengthRelation,
        CoincidentEndpointsRelation,
    ],
    Field(discriminator="kind"),
]


# ── 2D helpers (pure math on the spec coordinates — mm) ─────────────────────


def _pt_seg_dist(p, a, b) -> float:
    """Distance from point p to segment [a,b]."""
    ax, ay = a
    bx, by = b
    px, py = p
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    if l2 < _EPS_LEN * _EPS_LEN:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _segs_cross(p1, p2, p3, p4) -> bool:
    """True on a PROPER crossing (touching/collinear cases are handled by the
    endpoint-distance minimum going to 0 anyway)."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _seg_seg_dist(p1, p2, p3, p4) -> float:
    """Minimum distance between segments [p1,p2] and [p3,p4] as point sets."""
    if _segs_cross(p1, p2, p3, p4):
        return 0.0
    return min(
        _pt_seg_dist(p1, p3, p4),
        _pt_seg_dist(p2, p3, p4),
        _pt_seg_dist(p3, p1, p2),
        _pt_seg_dist(p4, p1, p2),
    )


def _angle_between_deg(da, db) -> float:
    """Angle between two directions folded to [0, 90] deg."""
    la = math.hypot(*da)
    lb = math.hypot(*db)
    cosang = abs(da[0] * db[0] + da[1] * db[1]) / (la * lb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosang))))


# ── skill ────────────────────────────────────────────────────────────────────


@skill(
    name="sketch_relation_check",
    category="inspect",
    level="atomic",
    summary="VERIFY-ONLY constraint-lite 2D relation check on a composite "
            "SketchSpec: per declared relation (horizontal/vertical/parallel/"
            "perpendicular/equal_length/distance/length/coincident_endpoints, "
            "indices = composite segment indices) reports satisfied + residual "
            "(deg for angle kinds, mm for length kinds) + tolerance_used, plus "
            "a DOF summary (2 DOF per chain junction + arc radius + spline "
            "interior points) with an under/well/over constrained_verdict that "
            "is a LABELED HEURISTIC (grade='heuristic' — a real DOF analysis "
            "needs a solver). ZERO solving — geometry is never moved; not a "
            "parametric sketcher. Body-less/read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["relation_check"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.relations_need_composite", "fm.bad_segment_index",
                   "fm.relation_needs_line", "fm.degenerate_segment"],
    cost_hint=0.02,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchRelationCheck(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec = Field(
            description="The 2D profile to verify. Relations index into "
                        "composite segments, so kind MUST be 'composite' "
                        "(line/arc/spline segments); any other kind is refused "
                        "with fm.relations_need_composite.")
        relations: list[RelationSpec] = Field(
            default_factory=list,
            description="Declared relations to verify against the CURRENT "
                        "geometry. Empty list → DOF summary only.")
        tol_mm: float = Field(
            default=1e-3, gt=0,
            description="Length-residual tolerance, mm (equal_length/distance/"
                        "length/coincident_endpoints). Default 1e-3 mm = the "
                        "sketch chain-closure tolerance.")
        tol_deg: float = Field(
            default=0.01, gt=0,
            description="Angle-residual tolerance, deg (horizontal/vertical/"
                        "parallel/perpendicular).")

    # — per-relation measurement (raises the fm.* refusals) —

    def _seg(self, segments, idx: int, rel_kind: str, role: str):
        if not (0 <= idx < len(segments)):
            raise ValueError(
                f"fm.bad_segment_index: {rel_kind}.{role}={idx} is out of "
                f"range — the composite has {len(segments)} segments "
                f"(valid indices 0..{len(segments) - 1}).")
        return segments[idx]

    def _line_dir(self, segments, idx: int, rel_kind: str, role: str):
        """(dx, dy) of a LINE segment — refuses arc/spline and zero length."""
        seg = self._seg(segments, idx, rel_kind, role)
        if seg.kind != "line":
            raise ValueError(
                f"fm.relation_needs_line: {rel_kind} is defined for LINE "
                f"segments only; segment {idx} is kind '{seg.kind}' (a chord "
                f"approximation would be a fake measurement — refused). Only "
                f"coincident_endpoints accepts arc/spline segments.")
        dx = seg.end[0] - seg.start[0]
        dy = seg.end[1] - seg.start[1]
        if math.hypot(dx, dy) < _EPS_LEN:
            raise ValueError(
                f"fm.degenerate_segment: segment {idx} has zero length — its "
                f"direction is undefined, so {rel_kind} cannot be evaluated.")
        return dx, dy

    def _line_len(self, segments, idx: int, rel_kind: str, role: str) -> float:
        seg = self._seg(segments, idx, rel_kind, role)
        if seg.kind != "line":
            raise ValueError(
                f"fm.relation_needs_line: {rel_kind} is defined for LINE "
                f"segments only; segment {idx} is kind '{seg.kind}' (arc/"
                f"spline arc-length is not what a 2D length constraint "
                f"means — refused).")
        return math.hypot(seg.end[0] - seg.start[0], seg.end[1] - seg.start[1])

    def _check_one(self, rel, segments, tol_mm: float, tol_deg: float) -> dict:
        k = rel.kind
        row: dict[str, Any]
        if k == "horizontal":
            dx, dy = self._line_dir(segments, rel.segment_index, k, "segment_index")
            resid = math.degrees(math.atan2(abs(dy), abs(dx)))
            row = {"targets": [rel.segment_index], "residual_unit": "deg",
                   "measured_tilt_deg": round(resid, 6)}
            tol = tol_deg
        elif k == "vertical":
            dx, dy = self._line_dir(segments, rel.segment_index, k, "segment_index")
            resid = math.degrees(math.atan2(abs(dx), abs(dy)))
            row = {"targets": [rel.segment_index], "residual_unit": "deg",
                   "measured_tilt_deg": round(resid, 6)}
            tol = tol_deg
        elif k == "parallel":
            da = self._line_dir(segments, rel.a, k, "a")
            db = self._line_dir(segments, rel.b, k, "b")
            ang = _angle_between_deg(da, db)
            resid = ang
            row = {"targets": [rel.a, rel.b], "residual_unit": "deg",
                   "measured_angle_between_deg": round(ang, 6)}
            tol = tol_deg
        elif k == "perpendicular":
            da = self._line_dir(segments, rel.a, k, "a")
            db = self._line_dir(segments, rel.b, k, "b")
            ang = _angle_between_deg(da, db)
            resid = abs(90.0 - ang)
            row = {"targets": [rel.a, rel.b], "residual_unit": "deg",
                   "measured_angle_between_deg": round(ang, 6)}
            tol = tol_deg
        elif k == "equal_length":
            la = self._line_len(segments, rel.a, k, "a")
            lb = self._line_len(segments, rel.b, k, "b")
            resid = abs(la - lb)
            row = {"targets": [rel.a, rel.b], "residual_unit": "mm",
                   "measured_len_a_mm": round(la, 6),
                   "measured_len_b_mm": round(lb, 6)}
            tol = tol_mm
        elif k == "distance":
            sa = self._seg(segments, rel.a, k, "a")
            sb = self._seg(segments, rel.b, k, "b")
            for seg, idx, role in ((sa, rel.a, "a"), (sb, rel.b, "b")):
                if seg.kind != "line":
                    raise ValueError(
                        f"fm.relation_needs_line: distance is defined between "
                        f"LINE segments only; segment {idx} ({role}) is kind "
                        f"'{seg.kind}'.")
            d = _seg_seg_dist(sa.start, sa.end, sb.start, sb.end)
            resid = abs(d - rel.value_mm)
            row = {"targets": [rel.a, rel.b], "residual_unit": "mm",
                   "measured_distance_mm": round(d, 6),
                   "target_mm": rel.value_mm}
            tol = tol_mm
        elif k == "length":
            ln = self._line_len(segments, rel.segment_index, k, "segment_index")
            resid = abs(ln - rel.value_mm)
            row = {"targets": [rel.segment_index], "residual_unit": "mm",
                   "measured_length_mm": round(ln, 6),
                   "target_mm": rel.value_mm}
            tol = tol_mm
        else:  # coincident_endpoints — any segment kind (all have start/end)
            sa = self._seg(segments, rel.a_end, k, "a_end")
            sb = self._seg(segments, rel.b_start, k, "b_start")
            gap = math.hypot(sa.end[0] - sb.start[0], sa.end[1] - sb.start[1])
            resid = gap
            row = {"targets": [rel.a_end, rel.b_start], "residual_unit": "mm",
                   "measured_gap_mm": round(gap, 6)}
            tol = tol_mm

        return {
            "kind": k,
            "satisfied": bool(resid <= tol),
            "residual": round(resid, 6),
            "tolerance_used": tol,
            **row,
        }

    def _apply(self, body: Any, args: Args) -> SkillResult:
        sketch = args.sketch
        if isinstance(sketch, dict):  # defensive — apply() already validated
            sketch = TypeAdapter(SketchSpec).validate_python(sketch)

        if getattr(sketch, "kind", None) != "composite":
            raise ValueError(
                f"fm.relations_need_composite: relations reference composite-"
                f"sketch segment indices, but the sketch kind is "
                f"'{getattr(sketch, 'kind', '?')}'. Rebuild the profile as "
                f"kind='composite' (line/arc/spline segments) to check "
                f"relations on it.")

        segments = sketch.segments
        n = len(segments)

        rows = [self._check_one(rel, segments, args.tol_mm, args.tol_deg)
                for rel in args.relations]
        n_sat = sum(1 for r in rows if r["satisfied"])

        # DOF model (see module docstring — simple, defensible, HEURISTIC):
        # closed chain (validated by CompositeSketch) → n_points == n_segments.
        extra_dof = 0
        for seg in segments:
            if seg.kind == "arc":
                extra_dof += 1                                # radius
            elif seg.kind == "spline":
                extra_dof += 2 * max(0, len(seg.points) - 2)  # interior points
        dof_total = 2 * n + extra_dof

        if n_sat < dof_total:
            verdict = "under"
        elif n_sat == dof_total:
            verdict = "well"
        else:
            verdict = "over"

        extras: dict[str, Any] = {
            "relation_check": {
                "sketch_kind": "composite",
                "relations": rows,
                "n_relations": len(rows),
                "n_satisfied": n_sat,
                "residual_grade": "measured",  # exact math on the spec coords
                "solved": False,  # verify-only — geometry was never moved
                "dof": {
                    "n_segments": n,
                    "n_points": n,
                    "dof_total": dof_total,
                    "dof_model": (
                        "closed polyline chain: 2 DOF per distinct junction "
                        "point (n_points == n_segments; CompositeSketch "
                        "enforces endpoint sharing) + 1 per arc radius + 2 per "
                        "spline interior fit point. Rigid-body modes are NOT "
                        "subtracted; each satisfied relation counts as "
                        "removing exactly 1 DOF."),
                    "n_constraints_declared": len(rows),
                    "n_constraints_satisfied": n_sat,
                    "constrained_verdict": verdict,
                    "grade": "heuristic",
                    "note": (
                        "count-based heuristic verdict — a real DOF analysis "
                        "needs a constraint solver (Jacobian rank: redundancy "
                        "detection; coincident_endpoints actually removes 2 "
                        "DOF). This skill verifies only; it does not solve."),
                },
            },
        }

        # Body-less pure-data skill (catalog_lookup pattern): a sentinel keeps
        # the 'body_present' post-condition satisfied when body=None.
        out_body = body if body is not None else _RelationSentinel()

        return SkillResult(
            body=out_body,
            history=EntityHistoryMap(),
            extras=extras,
        )


class _RelationSentinel:
    """No-op body sentinel — lets the body-less relation check run with
    body=None while still satisfying the 'body_present' post-condition."""
    __slots__ = ()
