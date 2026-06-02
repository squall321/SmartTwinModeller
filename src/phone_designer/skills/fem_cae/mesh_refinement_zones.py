"""mesh_refinement_zones — atomic. Tag faces for adaptive mesh refinement.

Many FEM solvers (Abaqus, Calculix, Gmsh) allow per-region element-size
control. This skill annotates the body with a refinement zone so downstream
mesher choices a smaller characteristic element size near the selected
faces:

    body._pd_mesh_refinement : list[dict] = [
        {
            "factor":         float,                # element size divisor (>= 1)
            "face_centers":   [(x, y, z), ...],
            "face_areas":     [a, ...],
            "selector":       {... pydantic dump ...},
        },
        ...
    ]

The actual size selection is the exporter's job — e.g.
``base_element_size_mm / factor`` near each tagged face. Body topology is
not touched; this is metadata only, analogous to ``boundary_condition_tag``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def get_mesh_refinement(body: Any) -> list[dict[str, Any]]:
    """Safe getter — never mutates the body."""
    return list(getattr(body, "_pd_mesh_refinement", []) or [])


def _set_mesh_refinement(body: Any, zones: list[dict[str, Any]]) -> None:
    if body is None:
        return
    try:
        body._pd_mesh_refinement = zones
    except Exception:
        pass


@skill(
    name="mesh_refinement_zones",
    category="fem_cae",
    level="atomic",
    summary="Tag selected faces with a mesh refinement factor. At export "
            "time the FEM mesher reduces the element size in these zones by "
            "the factor. Body topology is unchanged; metadata only.",
    selector_kinds=["faces"],
    history_rules={"refined_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_mesh_refinement_zone"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match", "fm.invalid_refinement_factor"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MeshRefinementZones(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        refinement_factor: float = Field(default=2.0, ge=1.0, le=64.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _face_area,
            _face_center,
            resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"mesh_refinement_zones: selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )

        if args.refinement_factor < 1.0:
            raise ValueError(
                f"mesh_refinement_zones: refinement_factor must be >= 1.0 "
                f"(got {args.refinement_factor})"
            )

        face_centers: list[tuple[float, float, float]] = []
        face_areas: list[float] = []
        for f in faces:
            try:
                c = _face_center(f)
                a = _face_area(f)
            except Exception:
                continue
            face_centers.append((round(c[0], 4), round(c[1], 4), round(c[2], 4)))
            face_areas.append(round(a, 4))

        record = {
            "factor":       float(args.refinement_factor),
            "face_centers": face_centers,
            "face_areas":   face_areas,
            "selector":     args.face_selector.model_dump(),
        }
        existing = list(get_mesh_refinement(body))
        existing.append(record)
        _set_mesh_refinement(body, existing)

        history = EntityHistoryMap(
            rules={"refined_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
