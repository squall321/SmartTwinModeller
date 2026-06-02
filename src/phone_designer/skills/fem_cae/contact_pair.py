"""contact_pair — atomic. Tag two face groups as a FEM contact pair.

Assembly FEM needs explicit contact pairs between mating face sets (master /
slave or self-contact). This skill records the pair on the body:

    body._pd_contacts : list[dict] = [
        {
            "kind":              "bonded" | "frictionless" | "frictional" | "rough",
            "friction_coef":     float | None,
            "a_face_centers":    [(x, y, z), ...],
            "a_face_areas":      [a, ...],
            "b_face_centers":    [(x, y, z), ...],
            "b_face_areas":      [a, ...],
            "selector_a":        {... pydantic dump ...},
            "selector_b":        {... pydantic dump ...},
        },
        ...
    ]

The contact kind maps to the standard Abaqus / Calculix vocabulary:
  * "bonded"        — tied contact (no relative motion).
  * "frictionless"  — normal contact only (μ=0).
  * "frictional"    — Coulomb friction with friction_coef.
  * "rough"         — no slip, μ → ∞.

Body topology is not modified — metadata only.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def get_contacts(body: Any) -> list[dict[str, Any]]:
    """Safe getter — never mutates the body."""
    return list(getattr(body, "_pd_contacts", []) or [])


def _set_contacts(body: Any, contacts: list[dict[str, Any]]) -> None:
    if body is None:
        return
    try:
        body._pd_contacts = contacts
    except Exception:
        pass


@skill(
    name="contact_pair",
    category="fem_cae",
    level="atomic",
    summary="Tag two face groups as a FEM contact pair (bonded / frictionless "
            "/ frictional / rough). The record is appended to body._pd_contacts "
            "and consumed by the assembly FEM exporter. Body topology unchanged.",
    selector_kinds=["faces"],
    history_rules={"contact_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_contact_pair"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match", "fm.invalid_friction"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ContactPair(SkillBase):
    class Args(BaseModel):
        face_selector_a: SelectorRef
        face_selector_b: SelectorRef
        contact_kind: Literal["bonded", "frictionless", "frictional", "rough"]
        friction_coef: float = Field(default=0.3, ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_area,
            _face_center,
            resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces_a = resolve_faces(shape, args.face_selector_a, body=body)
        faces_b = resolve_faces(shape, args.face_selector_b, body=body)
        if not faces_a:
            raise RuntimeError(
                f"contact_pair: selector_a matched 0 faces — "
                f"{args.face_selector_a.model_dump()}"
            )
        if not faces_b:
            raise RuntimeError(
                f"contact_pair: selector_b matched 0 faces — "
                f"{args.face_selector_b.model_dump()}"
            )

        if args.contact_kind == "frictional" and args.friction_coef <= 0.0:
            raise ValueError(
                "contact_pair: contact_kind='frictional' requires "
                f"friction_coef > 0 (got {args.friction_coef})"
            )

        def _bundle(faces):
            centers: list[tuple[float, float, float]] = []
            areas: list[float] = []
            for f in faces:
                try:
                    c = _face_center(f)
                    a = _face_area(f)
                except Exception:
                    continue
                centers.append((round(c[0], 4), round(c[1], 4), round(c[2], 4)))
                areas.append(round(a, 4))
            return centers, areas

        a_centers, a_areas = _bundle(faces_a)
        b_centers, b_areas = _bundle(faces_b)

        record = {
            "kind":           args.contact_kind,
            "friction_coef":  (float(args.friction_coef)
                               if args.contact_kind in ("frictional",)
                               else None),
            "a_face_centers": a_centers,
            "a_face_areas":   a_areas,
            "b_face_centers": b_centers,
            "b_face_areas":   b_areas,
            "selector_a":     args.face_selector_a.model_dump(),
            "selector_b":     args.face_selector_b.model_dump(),
        }
        existing = list(get_contacts(body))
        existing.append(record)
        _set_contacts(body, existing)

        history = EntityHistoryMap(
            rules={"contact_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
