"""pmi_weld_symbol — atomic, read-only.

Attach an ISO 2553 / AWS A2.4 weld symbol to one or more edges resolved by a
selector. Appended as a dict entry in ``body._pd_welds``.

Each record stores:
    {
        "weld_kind": "fillet" | "groove" | "spot" | "seam",
        "leg_length_mm": float,
        "length_mm": float,
        "all_around": bool,
        "edge_selector": selector_dict,
        "edge_anchors": [[x,y,z], ...],   # midpoints of matched edges
        "edge_count": int,
    }

extras["pmi_weld"] = the appended record,
extras["pmi_welds_total"] = total count after append.

body unchanged — ``body_present`` post-condition.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorBase, selector_from_dict
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _coerce_selector(s: Any) -> SelectorBase:
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _selector_to_dict(s: SelectorBase) -> dict:
    try:
        return s.model_dump(mode="json")
    except Exception:
        return {"kind": getattr(s, "kind", "unknown")}


def _edge_midpoint(edge) -> list[float] | None:
    try:
        from OCP.BRep import BRep_Tool
        from OCP.TopExp import TopExp
        p1 = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge))
        p2 = BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge))
        return [
            round((p1.X() + p2.X()) / 2.0, 6),
            round((p1.Y() + p2.Y()) / 2.0, 6),
            round((p1.Z() + p2.Z()) / 2.0, 6),
        ]
    except Exception:
        return None


@skill(
    name="pmi_weld_symbol",
    category="pmi",
    level="atomic",
    summary="Attach an ISO 2553 weld symbol (fillet/groove/spot/seam + leg + "
            "length + all-around) to edges. Stored on body._pd_welds for "
            "AP242 export. Read-only.",
    selector_kinds=[
        "edges_by_length", "axis_aligned_edges", "edges_on_face",
        "edges_concave_only", "edges_convex_only", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["pmi_weld_symbol"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match"],
    cost_hint=0.03,
    post_conditions=[PostCondition(kind="body_present")],
)
class PmiWeldSymbol(SkillBase):
    class Args(BaseModel):
        edge_selector: dict
        weld_kind: Literal["fillet", "groove", "spot", "seam"]
        leg_length_mm: float = Field(gt=0.0, le=100.0)
        length_mm: float = Field(gt=0.0, le=10000.0)
        all_around: bool = False

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("pmi_weld_symbol: body is None")
        from phone_designer.skills._resolvers import resolve_edges

        shape = _occt_shape(body)
        sel = _coerce_selector(args.edge_selector)
        edges = resolve_edges(shape, sel)
        if not edges:
            raise ValueError(
                f"pmi_weld_symbol: edge_selector matched 0 edges "
                f"(kind={sel.kind})"
            )

        anchors: list[list[float]] = []
        for e in edges:
            mp = _edge_midpoint(e)
            if mp is not None:
                anchors.append(mp)

        record = {
            "weld_kind": args.weld_kind,
            "leg_length_mm": float(args.leg_length_mm),
            "length_mm": float(args.length_mm),
            "all_around": bool(args.all_around),
            "edge_selector": _selector_to_dict(sel),
            "edge_anchors": anchors,
            "edge_count": len(edges),
        }

        try:
            existing = list(getattr(body, "_pd_welds", []) or [])
        except Exception:
            existing = []
        existing.append(record)
        try:
            body._pd_welds = existing
        except Exception:
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "pmi_weld": record,
                "pmi_welds_total": len(existing),
            },
        )
