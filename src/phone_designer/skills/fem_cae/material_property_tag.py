"""material_property_tag — atomic. Attach material properties to a body or face.

A linear-elastic isotropic material description (Young's modulus, Poisson's
ratio, density, yield strength) is stored on the body for downstream FEM /
CAE consumption:

    body._pd_material = {
        "youngs_modulus_mpa": float,
        "poisson":            float,
        "density_kg_m3":      float,
        "yield_mpa":          float,
        "scope":              "body" | "faces",
        "face_centers":       [(x,y,z), ...],   # only when scope=="faces"
        "selector":           {... pydantic dump ...},
    }

The selector argument doubles as the scope discriminator:

  * If the selector matches **all** of the body's faces, scope is "body".
  * If the selector matches a strict subset, scope is "faces" and the
    matched face centroids are recorded so the FEM exporter can produce
    per-face element sets.

Body topology is not modified — metadata only.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def get_material(body: Any) -> dict[str, Any] | None:
    """Safe getter — returns None when the body has no material tag yet."""
    return getattr(body, "_pd_material", None)


def _set_material(body: Any, mat: dict[str, Any]) -> None:
    if body is None:
        return
    try:
        body._pd_material = mat
    except Exception:
        pass


@skill(
    name="material_property_tag",
    category="fem_cae",
    level="atomic",
    summary="Tag the body (or specific faces) with linear-elastic isotropic "
            "material properties — Young's modulus, Poisson's ratio, density, "
            "and yield strength — consumed by downstream FEM exporters. "
            "Body topology is not modified.",
    selector_kinds=["faces"],
    history_rules={"material_scope": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_material"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match", "fm.invalid_material_property"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class MaterialPropertyTag(SkillBase):
    class Args(BaseModel):
        face_or_body_selector: SelectorRef
        youngs_modulus_mpa: float = Field(gt=0.0)
        poisson: float = Field(default=0.3, ge=-1.0, le=0.5)
        density_kg_m3: float = Field(default=7850.0, gt=0.0)
        yield_mpa: float = Field(default=200.0, gt=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import (
            _all_faces,
            _face_center,
            resolve_faces,
        )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        all_faces = _all_faces(shape)
        try:
            matched = resolve_faces(shape, args.face_or_body_selector, body=body)
        except Exception:
            matched = []

        if not matched:
            raise RuntimeError(
                f"material_property_tag: selector matched 0 faces — "
                f"{args.face_or_body_selector.model_dump()}"
            )

        # Scope = body if every face matched, else faces (subset).
        scope = "body" if len(matched) >= len(all_faces) else "faces"

        face_centers: list[tuple[float, float, float]] = []
        if scope == "faces":
            for f in matched:
                try:
                    c = _face_center(f)
                except Exception:
                    continue
                face_centers.append(
                    (round(c[0], 4), round(c[1], 4), round(c[2], 4)),
                )

        material = {
            "youngs_modulus_mpa": float(args.youngs_modulus_mpa),
            "poisson":            float(args.poisson),
            "density_kg_m3":      float(args.density_kg_m3),
            "yield_mpa":          float(args.yield_mpa),
            "scope":              scope,
            "face_centers":       face_centers,
            "selector":           args.face_or_body_selector.model_dump(),
        }
        _set_material(body, material)

        history = EntityHistoryMap(
            rules={"material_scope": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
