"""sketch_solve_lite — constraint-lite 2D sketch SOLVER (roadmap 3-5, part 2).

The solver half of ``sketch_relation_check`` (the verify-only half shipped
first). Takes the SAME composite ``SketchSpec`` plus the SAME ``RelationSpec``
list the checker accepts and moves the geometry to minimise EXACTLY the
residuals ``sketch_relation_check`` reports: the objective function literally
calls ``SketchRelationCheck._check_one`` per relation on candidate geometry
(no parallel residual implementation), and the DOF numbers are read from that
skill's own DOF block (no parallel DOF count).

Method: ``scipy.optimize.least_squares`` (TRF, central differences),
WARM-STARTED from the input geometry — the roadmap's core trick: the input is
assumed near-solution, so the solver only closes small residuals. Warm start
is also what prevents arc-sense flips: the ``ccw`` flag is never a variable
and endpoint moves stay small, so a solved arc keeps bulging on the same side
as the input arc. Because every relation kind is translation-invariant (no
anchor/fix relation exists), the relation residuals have a rigid null space;
tiny warm-start anchor residuals (weight 1e-3, see ``_ANCHOR_W``) select the
null-space solution NEAREST THE INPUT and bias the relation residuals only
O(1e-6) — ``converged`` is judged solely on the checker's residuals.

Parameter vector (matches the checker's DOF model exactly):
  * 2 per chain junction point — junction i := segments[i].start; segments
    SHARE junction variables, so chain closure holds by construction;
  * 1 per arc radius — no relation kind constrains a radius, so its gradient
    is zero and the warm-start value is kept (if solved endpoints stretch a
    chord past 2·r, the radius is raised to chord/2 and that bump is
    reported honestly in ``moved`` + ``honest_note``);
  * 2 per spline interior fit point.

HONESTY (in the output AND here, per the roadmap):
  * This is NOT a parametric sketcher. No anchor/fix relation exists, so
    absolute position is never determined: an under-constrained solve returns
    ONE of infinitely many solutions — the one nearest the warm start — and
    is labeled ``under_constrained_non_unique``.
  * Over-constrained input (declared relations > heuristic DOF) is still
    solved in the least-squares sense → ``over_constrained_best_fit``.
  * UNCONVERGED IS AN HONEST RESULT: ``converged=false`` with the
    per-relation residuals of the best point found — never an exception.
    Conflicting relations (length=20 AND length=25 on one segment) land here.
  * The under/well/over label reuses the checker's count heuristic
    (1 relation = 1 DOF, rigid-body modes not subtracted, redundancy/rank
    ignored) and stays graded 'heuristic'.
  * ``converged`` is decided by RE-RUNNING ``sketch_relation_check`` on the
    solved sketch — the same public verdict anyone else would measure.

Size gate: composites above ``max_entities`` segments (hard cap 30) are
refused with ``fm.too_large`` — the lite solver is for small profiles.
Input-geometry refusals (``fm.relations_need_composite``,
``fm.bad_segment_index``, ``fm.relation_needs_line``,
``fm.degenerate_segment``) are delegated to the checker's pre-verify pass.

Read-only and body-less like the checker: may run with body=None; a given
body is returned unchanged (the sketch data is the artifact).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, TypeAdapter
from scipy.optimize import least_squares

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.inspect.sketch_relation_check import (
    RelationSpec,
    SketchRelationCheck,
    _RelationSentinel,
)
from phone_designer.skills.modify_pocket._sketch import SketchSpec

_HARD_CAP = 30          # segments — the "lite" in sketch_solve_lite
_PENALTY = 1.0e3        # finite residual when a candidate is degenerate
_EPS_MOVE = 1e-9        # mm — below this a delta is not reported as "moved"
_DIFF_STEP = 1e-4       # relative FD step — big enough to step OVER the
#                         checker's 1e-6 residual rounding (SNR ~2000:1 for
#                         ~20 mm coordinates), small enough that the central
#                         ('3-point') difference bias is O(step²) ≈ 1e-6.
_ANCHOR_W = 1e-3        # warm-start anchor weight. EVERY relation kind is
#                         translation-invariant (none constrains absolute
#                         position), so the relation residuals have a rigid
#                         null space; without an anchor, FD noise leaks solver
#                         steps into it (observed ~18 mm drift). The anchor
#                         residuals w·(x−x0) pick the null-space solution
#                         NEAREST THE WARM START and bias the relation
#                         residuals only O(w²·‖x−x0‖) ≈ 1e-6 — three orders
#                         below the 1e-3/0.01 satisfaction tolerances.
#                         converged is still judged SOLELY on the checker's
#                         relation residuals (anchor excluded).


class _SegView:
    """Lightweight candidate segment for residual evaluation ONLY.

    ``SketchRelationCheck._check_one`` reads .kind/.start/.end (and splines
    carry .points); pydantic validation (arc chord ≤ 2r, chain closure) is
    deliberately NOT run per iteration — the final solved sketch is rebuilt
    through the real ``SketchSpec`` validators before being returned.
    """

    __slots__ = ("kind", "start", "end", "points", "radius", "ccw")

    def __init__(self, kind, start, end, points=None, radius=None, ccw=True):
        self.kind = kind
        self.start = start
        self.end = end
        self.points = points
        self.radius = radius
        self.ccw = ccw


def _layout(segments) -> tuple[np.ndarray, list[int], list[tuple[int, int]]]:
    """Warm-start vector + layout: junctions, then arc radii, then spline
    interior points. junction i := segments[i].start (closed chain)."""
    x0: list[float] = []
    for seg in segments:
        x0.extend(seg.start)
    arc_idx = [i for i, s in enumerate(segments) if s.kind == "arc"]
    for i in arc_idx:
        x0.append(segments[i].radius)
    spline_layout: list[tuple[int, int]] = []
    for i, s in enumerate(segments):
        if s.kind == "spline":
            interior = s.points[1:-1]
            spline_layout.append((i, len(interior)))
            for p in interior:
                x0.extend(p)
    return np.asarray(x0, dtype=float), arc_idx, spline_layout


def _unpack(x, n, arc_idx, spline_layout):
    """x → (junction points, {seg_i: radius}, {seg_i: interior points})."""
    pts = [(float(x[2 * i]), float(x[2 * i + 1])) for i in range(n)]
    off = 2 * n
    radii = {}
    for k, seg_i in enumerate(arc_idx):
        radii[seg_i] = float(x[off + k])
    off += len(arc_idx)
    interiors = {}
    for seg_i, m in spline_layout:
        interiors[seg_i] = [
            (float(x[off + 2 * j]), float(x[off + 2 * j + 1])) for j in range(m)
        ]
        off += 2 * m
    return pts, radii, interiors


def _views(x, segments, arc_idx, spline_layout) -> list[_SegView]:
    n = len(segments)
    pts, radii, interiors = _unpack(x, n, arc_idx, spline_layout)
    out: list[_SegView] = []
    for i, seg in enumerate(segments):
        s, e = pts[i], pts[(i + 1) % n]
        if seg.kind == "line":
            out.append(_SegView("line", s, e))
        elif seg.kind == "arc":
            out.append(_SegView("arc", s, e, radius=radii[i], ccw=seg.ccw))
        else:  # spline
            out.append(_SegView("spline", s, e, points=[s, *interiors[i], e]))
    return out


@skill(
    name="sketch_solve_lite",
    category="inspect",
    level="atomic",
    summary="Constraint-lite 2D sketch SOLVER — the solver half of "
            "sketch_relation_check (same composite SketchSpec, same relation "
            "schema): scipy least_squares (TRF) WARM-STARTED from the input "
            "geometry minimises exactly the residuals sketch_relation_check "
            "reports, then re-verifies the solved sketch with that checker. "
            "NOT a parametric sketcher: no anchor relation exists, so an "
            "under-constrained solve is one of infinitely many solutions "
            "(nearest warm start, labeled under_constrained_non_unique); "
            "over-constrained input is least-squares best-fit (labeled "
            "over_constrained_best_fit); the under/well/over label is the "
            "checker's count HEURISTIC. Unconverged is an HONEST result "
            "(converged=false + per-relation residuals), never an exception. "
            "Warm start preserves arc sense (ccw is never a variable). "
            "Cap 30 segments (fm.too_large). Body-less/read-only.",
    selector_kinds=[],
    history_rules={},
    produces_features=["sketch_solve"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.too_large", "fm.relations_need_composite",
                   "fm.bad_segment_index", "fm.relation_needs_line",
                   "fm.degenerate_segment"],
    cost_hint=0.05,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class SketchSolveLite(SkillBase):
    class Args(BaseModel):
        sketch: SketchSpec = Field(
            description="The 2D profile to solve. Same contract as "
                        "sketch_relation_check: kind MUST be 'composite' "
                        "(line/arc/spline segments) or the checker's "
                        "fm.relations_need_composite refusal is raised. The "
                        "input geometry is the WARM START — it should already "
                        "be near the intended solution.")
        relations: list[RelationSpec] = Field(
            default_factory=list,
            description="Relations to SOLVE FOR — exactly the schema "
                        "sketch_relation_check accepts. Empty list → nothing "
                        "to solve; geometry is returned unchanged.")
        max_entities: int = Field(
            default=_HARD_CAP, ge=1, le=_HARD_CAP,
            description="Segment-count cap (hard cap 30). Composites above "
                        "this are refused with fm.too_large — the lite solver "
                        "is for small profiles.")
        max_iter: int = Field(
            default=100, ge=1, le=2000,
            description="least_squares max_nfev (TRF counts step evaluations, "
                        "not finite-difference Jacobian evaluations).")
        tol_mm: float = Field(
            default=1e-3, gt=0,
            description="Length-residual satisfaction tolerance, mm — same "
                        "meaning as in sketch_relation_check.")
        tol_deg: float = Field(
            default=0.01, gt=0,
            description="Angle-residual satisfaction tolerance, deg — same "
                        "meaning as in sketch_relation_check.")

    # — helpers —

    def _verify(self, checker, sketch_dict, rel_dicts, args) -> dict:
        """One sketch_relation_check pass — THE residual/DOF ground truth."""
        res = checker.apply(None, {
            "sketch": sketch_dict,
            "relations": rel_dicts,
            "tol_mm": args.tol_mm,
            "tol_deg": args.tol_deg,
        })
        return res.extras["relation_check"]

    def _rebuild(self, x, sketch, arc_idx, spline_layout):
        """Final x → validated CompositeSketch (+ honest radius bumps)."""
        segments = sketch.segments
        n = len(segments)
        pts, radii, interiors = _unpack(x, n, arc_idx, spline_layout)
        rnd = lambda p: [round(p[0], 9), round(p[1], 9)]  # noqa: E731
        seg_dicts: list[dict[str, Any]] = []
        radius_bumps: list[dict[str, Any]] = []
        for i, seg in enumerate(segments):
            s, e = rnd(pts[i]), rnd(pts[(i + 1) % n])
            if seg.kind == "line":
                seg_dicts.append({"kind": "line", "start": s, "end": e})
            elif seg.kind == "arc":
                r = radii[i]
                chord = math.hypot(e[0] - s[0], e[1] - s[1])
                if chord > 2 * r:
                    # no relation kind constrains a radius, so the solver
                    # never moves it; if the solved endpoints stretched the
                    # chord past 2r, raise r to chord/2 (semicircle) and say so.
                    radius_bumps.append({
                        "segment": i, "radius_before": round(r, 9),
                        "radius_after": round(chord / 2, 9),
                        "reason": "solved chord exceeded 2*radius; radius "
                                  "raised to chord/2 (no relation kind "
                                  "constrains an arc radius)"})
                    r = chord / 2
                seg_dicts.append({"kind": "arc", "start": s, "end": e,
                                  "radius": round(r, 9), "ccw": seg.ccw})
            else:  # spline
                mid = [rnd(p) for p in interiors[i]]
                seg_dicts.append({"kind": "spline", "points": [s, *mid, e]})
        solved = TypeAdapter(SketchSpec).validate_python({
            "kind": "composite",
            "segments": seg_dicts,
            "corner_fillet_r": sketch.corner_fillet_r,
            "center_x_mm": sketch.center_x_mm,
            "center_y_mm": sketch.center_y_mm,
        })
        return solved, radius_bumps

    def _moved(self, original, solved) -> list[dict[str, Any]]:
        moved: list[dict[str, Any]] = []

        def add(entity: str, delta: float):
            if delta > _EPS_MOVE:
                moved.append({"entity": entity, "delta": round(delta, 6),
                              "unit": "mm"})

        for i, (o, s) in enumerate(zip(original.segments, solved.segments)):
            add(f"point[{i}]", math.hypot(s.start[0] - o.start[0],
                                          s.start[1] - o.start[1]))
            if o.kind == "arc":
                add(f"segment[{i}].radius", abs(s.radius - o.radius))
            elif o.kind == "spline":
                for j, (op, sp) in enumerate(
                        zip(o.points[1:-1], s.points[1:-1]), start=1):
                    add(f"segment[{i}].points[{j}]",
                        math.hypot(sp[0] - op[0], sp[1] - op[1]))
        return moved

    # — skill body —

    def _apply(self, body: Any, args: Args) -> SkillResult:
        sketch = args.sketch
        if isinstance(sketch, dict):  # defensive — apply() already validated
            sketch = TypeAdapter(SketchSpec).validate_python(sketch)

        checker = SketchRelationCheck()
        sketch_dict = sketch.model_dump()
        rel_dicts = [r.model_dump() for r in args.relations]

        # 1) pre-verify the INPUT — delegates fm.relations_need_composite /
        #    fm.bad_segment_index / fm.relation_needs_line /
        #    fm.degenerate_segment to the checker (single source of truth).
        rc_before = self._verify(checker, sketch_dict, rel_dicts, args)

        segments = sketch.segments
        n = len(segments)
        if n > args.max_entities:
            raise ValueError(
                f"fm.too_large: composite has {n} segments > "
                f"max_entities={args.max_entities} (hard cap {_HARD_CAP}) — "
                f"sketch_solve_lite is for small profiles; split the sketch "
                f"or use sketch_relation_check (verify-only, uncapped).")

        dof_total = rc_before["dof"]["dof_total"]
        n_rel = len(args.relations)
        deficit = dof_total - n_rel
        if deficit < 0:
            label = "over_constrained_best_fit"
        elif deficit > 0:
            label = "under_constrained_non_unique"
        else:
            label = "well_constrained"

        x0, arc_idx, spline_layout = _layout(segments)
        rels = list(args.relations)
        tol_mm, tol_deg = args.tol_mm, args.tol_deg
        penalty_hits = 0

        def fun(x):
            nonlocal penalty_hits
            segs = _views(x, segments, arc_idx, spline_layout)
            anchor = _ANCHOR_W * (x - x0)   # see _ANCHOR_W note above
            try:
                rel_res = np.asarray(
                    [checker._check_one(r, segs, tol_mm, tol_deg)["residual"]
                     for r in rels],
                    dtype=float)
            except ValueError:
                # candidate degenerated (e.g. a line collapsed under an angle
                # relation) — large FINITE residuals push the solver back.
                penalty_hits += 1
                rel_res = np.full(len(rels), _PENALTY)
            return np.concatenate([rel_res, anchor])

        solver_error: str | None = None
        solver_info: dict[str, Any] = {
            "method": "scipy.optimize.least_squares(method='trf')",
            "warm_start": True,
            "diff_step": _DIFF_STEP,
            "anchor_weight": _ANCHOR_W,
            "anchor_note": "relation kinds are all translation-invariant; "
                           "tiny warm-start anchor residuals pick the "
                           "null-space solution nearest the input and bias "
                           "relation residuals only O(1e-6) — converged is "
                           "judged on the checker's residuals alone.",
            "max_iter": args.max_iter,
        }

        if n_rel == 0:
            x_fin = x0
            solver_info.update({"nfev": 0, "status": None,
                                "message": "no relations declared — nothing "
                                           "to solve; geometry unchanged"})
        else:
            try:
                ls = least_squares(fun, x0, method="trf", jac="3-point",
                                   diff_step=_DIFF_STEP,
                                   ftol=1e-12, xtol=1e-12, gtol=1e-12,
                                   max_nfev=args.max_iter)
                x_fin = ls.x
                solver_info.update({"nfev": int(ls.nfev),
                                    "status": int(ls.status),
                                    "message": str(ls.message)})
                if not np.all(np.isfinite(x_fin)):
                    solver_error = ("least_squares returned non-finite "
                                    "parameters — warm start kept")
                    x_fin = x0
            except Exception as exc:  # honest unconverged — raw error surfaced
                x_fin = x0
                solver_error = f"{type(exc).__name__}: {exc}"
                solver_info.update({"nfev": None, "status": None,
                                    "message": solver_error})
        solver_info["penalty_hits"] = penalty_hits

        # 2) rebuild through the REAL SketchSpec validators.
        rebuild_error: str | None = None
        radius_bumps: list[dict[str, Any]] = []
        try:
            solved, radius_bumps = self._rebuild(
                x_fin, sketch, arc_idx, spline_layout)
        except Exception as exc:  # honest fallback — input geometry kept
            rebuild_error = f"{type(exc).__name__}: {exc}"
            solved = sketch

        # 3) re-verify the SOLVED sketch with sketch_relation_check itself —
        #    converged is that checker's public verdict, not ours.
        verify_error: str | None = None
        try:
            rc_after = self._verify(
                checker, solved.model_dump(), rel_dicts, args)
        except ValueError as exc:
            # solved geometry the checker refuses (e.g. collapsed segment) —
            # revert to the input (whose pre-verify already passed).
            verify_error = f"{type(exc).__name__}: {exc}"
            solved = sketch
            rc_after = rc_before

        n_sat_after = rc_after["n_satisfied"]
        residuals_after = [r["residual"] for r in rc_after["relations"]]
        residual_norm = math.sqrt(sum(r * r for r in residuals_after))
        residual_norm_before = math.sqrt(
            sum(r["residual"] ** 2 for r in rc_before["relations"]))
        hard_fail = solver_error or rebuild_error or verify_error
        converged = bool(n_sat_after == n_rel and not hard_fail)

        moved = self._moved(sketch, solved)

        notes = [
            "NOT a parametric sketcher: no anchor/fix relation exists, so "
            "absolute position is never constrained; the under/well/over "
            "label is sketch_relation_check's count heuristic "
            "(grade='heuristic').",
        ]
        if label == "under_constrained_non_unique":
            notes.append("under_constrained_non_unique: this solution is one "
                         "of infinitely many — warm start picked the one "
                         "nearest the input geometry.")
        elif label == "over_constrained_best_fit":
            notes.append("over_constrained_best_fit: more declared relations "
                         "than heuristic DOF — least-squares best fit "
                         "reported.")
        if n_rel == 0:
            notes.append("no relations declared — nothing to solve; "
                         "vacuously converged; geometry returned unchanged.")
        elif converged:
            notes.append(f"converged: all {n_rel} relations satisfied within "
                         f"tolerance per sketch_relation_check re-verify.")
        else:
            notes.append(f"NOT converged: {n_rel - n_sat_after} of {n_rel} "
                         f"relations remain unsatisfied — per-relation "
                         f"residuals of the best point found are reported. "
                         f"This is an honest result, not an error.")
        for err in (solver_error, rebuild_error, verify_error):
            if err:
                notes.append(f"solver fallback (input geometry kept): {err}")
        if radius_bumps:
            notes.append(f"{len(radius_bumps)} arc radius bump(s) applied — "
                         f"see radius_bumps.")
        notes.append("residual_norm mixes units exactly as "
                     "sketch_relation_check residuals do (mm for length "
                     "kinds, deg for angle kinds).")

        extras: dict[str, Any] = {
            "sketch_solve": {
                "ok": True,
                "converged": converged,
                "residual_norm": round(residual_norm, 9),
                "residual_norm_before": round(residual_norm_before, 9),
                "constraint_label": label,
                "dof": {
                    "total": dof_total,
                    "before": dof_total - rc_before["n_satisfied"],
                    "after": dof_total - n_sat_after,
                    "grade": "heuristic",
                    "note": rc_after["dof"]["note"],
                },
                "n_relations": n_rel,
                "n_satisfied_before": rc_before["n_satisfied"],
                "n_satisfied_after": n_sat_after,
                "relations": rc_after["relations"],
                "solved_sketch": solved.model_dump(mode="json"),
                "moved": moved,
                "radius_bumps": radius_bumps,
                "solver": solver_info,
                "honest_note": " ".join(notes),
            },
        }

        # Body-less pure-data skill (sketch_relation_check pattern).
        out_body = body if body is not None else _RelationSentinel()

        return SkillResult(
            body=out_body,
            history=EntityHistoryMap(),
            extras=extras,
        )
