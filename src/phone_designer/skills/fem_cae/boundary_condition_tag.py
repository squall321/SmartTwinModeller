"""boundary_condition_tag — atomic. Attach a boundary-condition record to a face.

FEM solvers need to know which faces are clamped, loaded, etc. This skill
records the BC on the body as a plain Python attribute that ``export_mesh_for_fem``
(or a downstream Abaqus / Calculix exporter) can read:

    body._pd_bcs : list[dict] = [
        {
          "kind": "fixed" | "pinned" | "force" | "pressure" | "temperature",
          "value": float | tuple | None,
          "face_centers": [(x,y,z), ...],
          "face_areas":   [a, ...],
          "selector":     {... pydantic dump ...},
        },
        ...
    ]

Body topology is not touched — this is a metadata-only operation analogous to
``compose.tag_face``.

``value`` semantics (interpreted later by the FEM exporter):
  * "fixed"       — value ignored (full clamp).
  * "pinned"      — value ignored (translation locked, rotation free).
  * "force"       — value = (Fx, Fy, Fz) in newtons (per face area or total —
                    the exporter decides).
  * "pressure"    — value = scalar in MPa, positive = outward.
  * "temperature" — value = scalar in °C.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def get_bcs(body: Any) -> list[dict[str, Any]]:
    """Safe getter — never mutates the body."""
    return list(getattr(body, "_pd_bcs", []) or [])


def _set_bcs(body: Any, bcs: list[dict[str, Any]]) -> None:
    if body is None:
        return
    try:
        body._pd_bcs = bcs
    except Exception:
        pass


@skill(
    name="boundary_condition_tag",
    category="fem_cae",
    level="atomic",
    summary="Tag faces matched by a selector with a boundary condition "
            "(fixed / pinned / force / pressure / temperature). The record is "
            "stored on body._pd_bcs and consumed by downstream FEM exporters. "
            "Body topology is not modified.",
    selector_kinds=["faces"],
    history_rules={"bc_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_boundary_condition"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match", "fm.bc_value_missing"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class BoundaryConditionTag(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        bc_kind: Literal["fixed", "pinned", "force", "pressure", "temperature"]
        value: float | tuple[float, float, float] | None = Field(default=None)

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
                f"boundary_condition_tag: selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )

        # Validate value vs kind.
        if args.bc_kind in ("force",):
            if args.value is None or not isinstance(args.value, tuple) or len(args.value) != 3:
                raise ValueError(
                    "boundary_condition_tag: bc_kind='force' requires "
                    "value=(Fx, Fy, Fz)",
                )
        elif args.bc_kind in ("pressure", "temperature"):
            if args.value is None or not isinstance(args.value, (int, float)):
                raise ValueError(
                    f"boundary_condition_tag: bc_kind={args.bc_kind!r} "
                    "requires scalar value",
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

        # Normalize value for storage.
        stored_value: Any
        if isinstance(args.value, tuple):
            stored_value = [float(v) for v in args.value]
        elif args.value is None:
            stored_value = None
        else:
            stored_value = float(args.value)

        record = {
            "kind": args.bc_kind,
            "value": stored_value,
            "face_centers": face_centers,
            "face_areas": face_areas,
            "selector": args.face_selector.model_dump(),
        }

        existing = list(get_bcs(body))
        existing.append(record)
        _set_bcs(body, existing)

        history = EntityHistoryMap(
            rules={"bc_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
