"""datum_target_assign — atomic, read-only.

ISO 5459 datum target: tag one or more faces with a datum reference letter
(A..F), a precedence (primary=1, secondary=2, tertiary=3, ...) and a
"target_kind" (point | line | area). The classical ASME / ISO datum target
symbol is a circle split horizontally with the datum letter on the bottom
and the target number on top; here we collapse that into a single
``body._pd_datums`` dict so downstream FCF / GD&T skills can resolve the
datum reference frame.

Storage:
    body._pd_datums[label] = {
        "face_idx":   int,     # first matched face's index in _all_faces
        "precedence": int,     # 1 = primary, 2 = secondary, ...
        "kind":       "point" | "line" | "area",
        "face_indices": [i, ...],  # all matched faces
        "center":     [x,y,z],
        "normal":     [x,y,z],
        "area_mm2":   float,
        "is_planar":  bool,
    }

extras schema:
    {"datum_target": {
        "label": "A",
        "precedence": 1,
        "kind": "area",
        "face_idx": int,
        "face_count": int,
        "center": [x,y,z],
        "normal": [x,y,z],
        "area_mm2": float
     },
     "datums_total": int      # cumulative count after this assignment
    }

body unchanged (post ``body_present``).
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


@skill(
    name="datum_target_assign",
    category="inspect",
    level="atomic",
    summary="Tag face(s) as an ISO 5459 datum target (A..F, precedence "
            "1/2/3..., kind point/line/area). Stored on body._pd_datums for "
            "downstream FCF / GD&T resolution. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["datum_target"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class DatumTargetAssign(SkillBase):
    class Args(BaseModel):
        face_selector: dict
        datum_label: Literal["A", "B", "C", "D", "E", "F"]
        precedence: int = Field(default=1, ge=1, le=10)
        target_kind: Literal["point", "line", "area"] = "area"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces,
            _face_area,
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        shape = _occt_shape(body)
        sel = _coerce_selector(args.face_selector)
        matched = resolve_faces(shape, sel, body=body)
        if not matched:
            raise ValueError(
                f"datum_target_assign: face_selector matched 0 faces "
                f"(kind={sel.kind})"
            )

        # Index the matched faces against the canonical _all_faces ordering.
        all_faces = _all_faces(shape)
        # Identity by Python object id (TopoDS_Shape compares as IsSame).
        face_indices: list[int] = []
        for mf in matched:
            for i, af in enumerate(all_faces):
                try:
                    if af.IsSame(mf):
                        face_indices.append(i)
                        break
                except Exception:
                    pass

        first = matched[0]
        try:
            center = _face_center(first)
        except Exception:
            center = (0.0, 0.0, 0.0)
        try:
            normal = _face_normal_at_center(first)
        except Exception:
            normal = (0.0, 0.0, 0.0)
        try:
            area = float(_face_area(first))
        except Exception:
            area = 0.0
        is_planar = normal != (0.0, 0.0, 0.0)

        entry = {
            "face_idx": face_indices[0] if face_indices else -1,
            "face_indices": face_indices,
            "precedence": int(args.precedence),
            "kind": args.target_kind,
            "center": [round(c, 6) for c in center],
            "normal": [round(c, 6) for c in normal],
            "area_mm2": round(area, 6),
            "is_planar": bool(is_planar),
        }

        # Store on body — survives propagation since SkillBase.apply copies
        # _pd_* attributes via tag propagation chain.
        try:
            existing = dict(getattr(body, "_pd_datums", {}) or {})
        except Exception:
            existing = {}
        existing[args.datum_label] = entry
        try:
            body._pd_datums = existing
        except Exception:
            pass

        extras = {
            "datum_target": {
                "label": args.datum_label,
                "precedence": int(args.precedence),
                "kind": args.target_kind,
                "face_idx": entry["face_idx"],
                "face_count": len(face_indices),
                "center": entry["center"],
                "normal": entry["normal"],
                "area_mm2": entry["area_mm2"],
            },
            "datums_total": len(existing),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
