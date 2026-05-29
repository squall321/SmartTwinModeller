"""datum_plane_assign — atomic, read-only.

Tag one or more faces as datum planes (A/B/C/... per ASME Y14.5 datum
reference frame) and return their EntityRef-like description (bbox center,
normal, area). The body is not mutated; the assignments live in
``extras["datums"]``.

This is a *naming/labelling* skill — LLM uses it to anchor downstream GD&T
calls (gdt_flatness, gdt_perpendicularity, etc.) by referring to "datum A"
in human-readable plans.

Args:
    assignments: list of dicts: ``[{"label": "A", "face_selector": {...}},
                                    {"label": "B", "face_selector": {...}}, ...]``

extras schema:
    {
      "datums": {
        "A": {
          "face_center": [x,y,z],
          "normal": [x,y,z],
          "area_mm2": float,
          "selector": dict,
          "is_planar": bool
        },
        "B": {...},
        ...
      },
      "datum_count": int,
      "warnings": [str, ...]
    }

body unchanged (post ``body_present``).
"""
from __future__ import annotations

from typing import Any

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


@skill(
    name="datum_plane_assign",
    category="inspect",
    level="atomic",
    summary="Label one or more faces as datum planes (A/B/C/...) for GD&T "
            "reference frame. Records face center, normal, and area in "
            "extras['datums'][label]. Read-only — body unchanged.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["datum_assignment"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class DatumPlaneAssign(SkillBase):
    class Args(BaseModel):
        assignments: list[dict] = Field(default_factory=list)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_area,
            _face_center,
            _face_normal_at_center,
            resolve_faces,
        )

        shape = _occt_shape(body)

        datums: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        for entry in args.assignments:
            label = entry.get("label")
            sel_raw = entry.get("face_selector")
            if not label or sel_raw is None:
                warnings.append(
                    f"assignment missing 'label' or 'face_selector': {entry}"
                )
                continue
            try:
                sel = _coerce_selector(sel_raw)
            except Exception as ex:
                warnings.append(
                    f"datum {label}: invalid selector ({type(ex).__name__}: {ex})"
                )
                continue
            try:
                faces = resolve_faces(shape, sel, body=body)
            except Exception as ex:
                warnings.append(
                    f"datum {label}: resolve failed ({type(ex).__name__}: {ex})"
                )
                continue
            if not faces:
                warnings.append(
                    f"datum {label}: face_selector matched 0 faces (kind={sel.kind})"
                )
                continue

            f = faces[0]
            c = _face_center(f)
            n = _face_normal_at_center(f)
            a = _face_area(f)
            is_planar = not (n == (0.0, 0.0, 0.0))

            datums[str(label)] = {
                "face_center": [round(c[0], 6), round(c[1], 6), round(c[2], 6)],
                "normal": [round(n[0], 6), round(n[1], 6), round(n[2], 6)],
                "area_mm2": round(float(a), 6),
                "selector": _selector_to_dict(sel),
                "is_planar": bool(is_planar),
            }

        extras = {
            "datums": datums,
            "datum_count": len(datums),
            "warnings": warnings,
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
