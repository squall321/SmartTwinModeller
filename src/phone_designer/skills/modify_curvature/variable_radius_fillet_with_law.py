"""variable_radius_fillet_with_law — atomic.

Apply a fillet whose radius VARIES ALONG a single edge (or set of edges) by
sampling a user-defined "law" function `r(t)` over `t ∈ [0, 1]`.

Difference vs `variable_radius_fillet`:
  - `variable_radius_fillet`: N edges, each gets a CONSTANT radius from a list
    that cycles. Radius does not change along an edge.
  - `variable_radius_fillet_with_law`: ONE law `r(t)` evaluated at `samples`
    parameter values; each matched edge gets the SAME law applied along its
    own length. Result is a smooth radius variation along the edge spine.

Law kinds:
  - "linear":     law_params = {"r_start": float, "r_end": float}
  - "polynomial": law_params = {"coeffs": [c0, c1, c2, ...]}  → r(t) = Σ cₖ · t^k
  - "sin":        law_params = {"offset", "amplitude", "phase_deg", "cycles"}
                   → r(t) = offset + amplitude · sin(2π·cycles·t + phase_rad)
  - "expression": law_params = {"r_expr": "0.5 + 0.5*sin(2*pi*t)"}
                   evaluated via simpleeval. `__` is rejected. Allowed funcs:
                   sin/cos/tan/exp/sqrt/abs. Names: pi, e.

Implementation: OCCT's `BRepFilletAPI_MakeFillet.Add(UandR, edge)` overload #5
accepts a `TColgp_Array1OfPnt2d` of `(u, r)` pairs where `u ∈ [0, 1]` is the
normalized parameter along the spine and `r` is the radius. We sample the
law at `samples` evenly-spaced points and feed them in directly. This is the
most reliable variable-radius API in OCCT 7.7 — no Law_Function object plumb-
ing required, and it works first-try.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from phone_designer.skills._history import (
    EntityHistoryMap,
    EntityRef,
    HistoryRule,
    SelectorFreeze,
)
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


LawKind = Literal["linear", "polynomial", "sin", "expression"]


def _safe_eval_r_expr(expr: str, t: float) -> float:
    """Evaluate `expr` with `t` bound. Whitelisted funcs / names. `__` rejected.

    Module-local copy of the safe-eval helper in `_sketch_to_solid.py` —
    duplicated here to avoid cross-package coupling (modify_curvature →
    modify_pocket would be an unwanted dependency).
    """
    if "__" in expr:
        raise ValueError(f"law r_expr contains forbidden '__': {expr!r}")
    from simpleeval import SimpleEval
    funcs = {
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "exp": math.exp, "sqrt": math.sqrt, "abs": abs,
    }
    ev = SimpleEval(functions=funcs)
    ev.names = {"pi": math.pi, "e": math.e, "t": float(t)}
    try:
        return float(ev.eval(expr))
    except Exception as e:
        raise ValueError(
            f"law r_expr={expr!r} failed at t={t}: {e}"
        ) from e


def _eval_law(law_kind: LawKind, law_params: dict, t: float) -> float:
    """Evaluate the chosen law at `t ∈ [0, 1]`. Returns radius (mm).

    Raises ValueError with a clear message on missing / malformed params.
    """
    if law_kind == "linear":
        r0 = float(law_params["r_start"])
        r1 = float(law_params["r_end"])
        return r0 + (r1 - r0) * t

    if law_kind == "polynomial":
        coeffs = law_params["coeffs"]
        if not isinstance(coeffs, (list, tuple)) or not coeffs:
            raise ValueError(f"polynomial law needs non-empty coeffs list")
        s = 0.0
        pw = 1.0
        for c in coeffs:
            s += float(c) * pw
            pw *= t
        return s

    if law_kind == "sin":
        off = float(law_params.get("offset", 0.5))
        amp = float(law_params.get("amplitude", 0.5))
        phase = math.radians(float(law_params.get("phase_deg", 0.0)))
        cycles = float(law_params.get("cycles", 1.0))
        return off + amp * math.sin(2.0 * math.pi * cycles * t + phase)

    if law_kind == "expression":
        expr = str(law_params["r_expr"])
        return _safe_eval_r_expr(expr, t)

    raise ValueError(f"unknown law_kind: {law_kind!r}")


def _validate_law_params(law_kind: LawKind, params: dict) -> None:
    """Cheap structural validation up-front so failures don't only emerge mid-fillet."""
    if law_kind == "linear":
        for k in ("r_start", "r_end"):
            if k not in params:
                raise ValueError(f"linear law requires '{k}' in law_params")
    elif law_kind == "polynomial":
        if "coeffs" not in params or not params["coeffs"]:
            raise ValueError("polynomial law requires non-empty 'coeffs'")
    elif law_kind == "sin":
        # all keys have defaults; nothing required
        pass
    elif law_kind == "expression":
        if "r_expr" not in params:
            raise ValueError("expression law requires 'r_expr'")
        if "__" in str(params["r_expr"]):
            raise ValueError("expression r_expr contains forbidden '__'")
    else:
        raise ValueError(f"unknown law_kind: {law_kind!r}")


@skill(
    name="variable_radius_fillet_with_law",
    category="modify/fillet",
    level="atomic",
    summary="Apply a fillet with radius that varies SMOOTHLY ALONG each matched "
            "edge according to a user-supplied law r(t), t∈[0,1]. Laws: linear, "
            "polynomial, sin, or arbitrary expression. Uses OCCT's "
            "TColgp_Array1OfPnt2d (u,r) sampling for the radius-evolution API.",
    selector_kinds=["edges"],
    history_rules={
        "target_edges":   HistoryRule.CONSUMED,
        "result_faces":   HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    produces_features=["variable_law_fillet_faces"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_5axis": {"min_fillet_r_mm": 0.3, "extras": {"variable_radius": True}},
        "cnc_3axis": {"min_fillet_r_mm": 0.5, "extras": {"variable_radius": True}},
    },
    failure_modes=[
        "fm.no_match",
        "fm.law_eval_failed",
        "fm.fillet_failed",
        "fm.radius_non_positive",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class VariableRadiusFilletWithLaw(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        law_kind: LawKind
        law_params: dict
        samples: int = Field(default=10, ge=3, le=200)

        @model_validator(mode="after")
        def _law_ok(self):
            _validate_law_params(self.law_kind, self.law_params)
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
        from OCP.gp import gp_Pnt2d
        from OCP.TColgp import TColgp_Array1OfPnt2d

        from phone_designer.skills._resolvers import (
            _edge_endpoints,
            _edge_length,
            resolve_edges,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.edge_selector)
        if not edges:
            raise RuntimeError(
                f"variable_radius_fillet_with_law: edge_selector matched 0 "
                f"edges — {args.edge_selector.model_dump()}"
            )

        # Pre-sample the law at `samples` (u, r) pairs. We do this ONCE and
        # reuse for every matched edge — that's the whole point: same law-curve
        # applied uniformly to a contour of edges.
        n = int(args.samples)
        u_r_pairs: list[tuple[float, float]] = []
        for i in range(n):
            u = i / (n - 1) if n > 1 else 0.0
            try:
                r = _eval_law(args.law_kind, args.law_params, u)
            except Exception as e:
                raise RuntimeError(
                    f"variable_radius_fillet_with_law: law eval failed at "
                    f"t={u} ({args.law_kind}): {e}"
                ) from e
            if not (math.isfinite(r)) or r <= 0:
                raise RuntimeError(
                    f"variable_radius_fillet_with_law: law produced non-"
                    f"positive radius r={r} at t={u}"
                )
            u_r_pairs.append((u, float(r)))

        # Stable edge ordering — same key as the constant-radius sibling for
        # determinism. Doesn't change the behavior (every edge gets the same
        # law) but matters for the selector_freeze refs.
        def _sort_key(e):
            p1, p2 = _edge_endpoints(e)
            return (
                round((p1.X() + p2.X()) / 2, 3),
                round((p1.Y() + p2.Y()) / 2, 3),
                round((p1.Z() + p2.Z()) / 2, 3),
            )
        sorted_edges = sorted(edges, key=_sort_key)

        maker = BRepFilletAPI_MakeFillet(shape)
        for e in sorted_edges:
            arr = TColgp_Array1OfPnt2d(1, n)
            for i, (u, r) in enumerate(u_r_pairs, start=1):
                arr.SetValue(i, gp_Pnt2d(u, r))
            try:
                maker.Add(arr, e)
            except Exception as ex:
                raise RuntimeError(
                    f"variable_radius_fillet_with_law: MakeFillet.Add(UandR, "
                    f"edge) raised on one edge: {ex}"
                ) from ex
        try:
            maker.Build()
        except Exception as ex:
            raise RuntimeError(
                f"variable_radius_fillet_with_law: MakeFillet.Build raised "
                f"({len(sorted_edges)} edges, {n} samples, law={args.law_kind}): "
                f"{ex}"
            ) from ex
        if not maker.IsDone():
            raise RuntimeError(
                f"variable_radius_fillet_with_law: MakeFillet did not complete "
                f"({len(sorted_edges)} edges, {n} samples, law={args.law_kind})"
            )
        new_shape = maker.Shape()

        # freeze refs — same scheme as variable_radius_fillet
        refs = []
        for e in sorted_edges:
            p1, p2 = _edge_endpoints(e)
            center = (
                round((p1.X() + p2.X()) / 2, 3),
                round((p1.Y() + p2.Y()) / 2, 3),
                round((p1.Z() + p2.Z()) / 2, 3),
            )
            refs.append(EntityRef(
                tag=None, kind="edge",
                bbox_center=center, measure=round(_edge_length(e), 3),
            ))

        history = EntityHistoryMap(
            rules={
                "target_edges":   HistoryRule.CONSUMED,
                "result_faces":   HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=SelectorFreeze.from_entities(refs),
        )
