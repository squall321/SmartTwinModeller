"""pmi_dimension_callout — atomic, read-only.

Attach a PMI dimension callout (linear / angular / diameter / radius) between
two resolved entities. The callout is recorded as a dict in
``body._pd_pmi_dimensions`` for later export by ``export_step_ap242_pmi``.

Each dimension stores:
    {
        "kind": "linear" | "angular" | "diameter" | "radius",
        "nominal": float,
        "upper_tol": float,
        "lower_tol": float,
        "entity_a": selector_dict,
        "entity_b": selector_dict | None,
        "datum_refs": [str, ...],
        "anchor_a": [x,y,z],   # face/edge center used as anchor (best-effort)
        "anchor_b": [x,y,z] | None,
        "unit": "mm" | "deg",
    }

extras["pmi_dimension"] = the newly added entry,
extras["pmi_dimensions_total"] = len(body._pd_pmi_dimensions) after append.

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


def _coerce_selector(s: Any) -> SelectorBase | None:
    if s is None:
        return None
    if isinstance(s, SelectorBase):
        return s
    if isinstance(s, dict):
        return selector_from_dict(s)
    raise TypeError(f"unsupported selector type: {type(s).__name__}")


def _selector_to_dict(s: SelectorBase | None) -> dict | None:
    if s is None:
        return None
    try:
        return s.model_dump(mode="json")
    except Exception:
        return {"kind": getattr(s, "kind", "unknown")}


def _resolve_anchor(shape, sel: SelectorBase | None) -> list[float] | None:
    """Best-effort anchor: try face center, then edge midpoint. None on failure."""
    if sel is None:
        return None
    try:
        from phone_designer.skills._resolvers import (
            _face_center,
            resolve_edges,
            resolve_faces,
        )
        faces = resolve_faces(shape, sel)
        if faces:
            c = _face_center(faces[0])
            return [round(c[0], 6), round(c[1], 6), round(c[2], 6)]
        edges = resolve_edges(shape, sel)
        if edges:
            from OCP.BRep import BRep_Tool
            from OCP.TopExp import TopExp
            e = edges[0]
            p1 = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(e))
            p2 = BRep_Tool.Pnt_s(TopExp.LastVertex_s(e))
            return [
                round((p1.X() + p2.X()) / 2.0, 6),
                round((p1.Y() + p2.Y()) / 2.0, 6),
                round((p1.Z() + p2.Z()) / 2.0, 6),
            ]
    except Exception:
        pass
    return None


@skill(
    name="pmi_dimension_callout",
    category="pmi",
    level="atomic",
    summary="Attach a PMI dimension callout (linear/angular/diameter/radius) "
            "with nominal + bilateral tolerance between two resolved entities. "
            "Stored on body._pd_pmi_dimensions for AP242 export. Read-only.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "edges_by_length", "axis_aligned_edges", "edges_on_face",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["pmi_dimension"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class PmiDimensionCallout(SkillBase):
    class Args(BaseModel):
        entity_a_selector: dict
        entity_b_selector: dict | None = None
        kind: Literal["linear", "angular", "diameter", "radius"]
        nominal_value: float
        upper_tol: float = 0.0
        lower_tol: float = 0.0
        datum_refs: list[str] = Field(default_factory=list)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("pmi_dimension_callout: body is None")

        shape = _occt_shape(body)
        sel_a = _coerce_selector(args.entity_a_selector)
        sel_b = _coerce_selector(args.entity_b_selector)

        anchor_a = _resolve_anchor(shape, sel_a)
        anchor_b = _resolve_anchor(shape, sel_b) if sel_b is not None else None

        unit = "deg" if args.kind == "angular" else "mm"

        entry = {
            "kind": args.kind,
            "nominal": float(args.nominal_value),
            "upper_tol": float(args.upper_tol),
            "lower_tol": float(args.lower_tol),
            "entity_a": _selector_to_dict(sel_a),
            "entity_b": _selector_to_dict(sel_b),
            "datum_refs": list(args.datum_refs),
            "anchor_a": anchor_a,
            "anchor_b": anchor_b,
            "unit": unit,
        }

        try:
            existing = list(getattr(body, "_pd_pmi_dimensions", []) or [])
        except Exception:
            existing = []
        existing.append(entry)
        try:
            body._pd_pmi_dimensions = existing
        except Exception:
            pass

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "pmi_dimension": entry,
                "pmi_dimensions_total": len(existing),
            },
        )
