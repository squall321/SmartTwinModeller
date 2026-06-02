"""thermal_bc_tag — atomic. Attach a thermal boundary condition to faces.

Thermal FEA (steady-state or transient) needs per-face boundary conditions:

    body._pd_thermal_bcs : list[dict] = [
        {
            "kind":          "fixed_temp" | "heat_flux" | "convection" | "radiation",
            "value":         float,
            "h_coefficient": float | None,
            "ambient_t_c":   float | None,
            "face_centers":  [(x, y, z), ...],
            "face_areas":    [a, ...],
            "selector":      {... pydantic dump ...},
        },
        ...
    ]

Value semantics (consumed by the FEM thermal exporter):
  * "fixed_temp"  — value = temperature in °C (Dirichlet).
  * "heat_flux"   — value = flux in W/m² (Neumann).
  * "convection"  — value = ambient temp in °C; h_coefficient (W/m²·K)
                    and ambient_t_c are also required.
  * "radiation"   — value = emissivity (0..1); ambient_t_c is the
                    surrounding sink temperature in °C.

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


def get_thermal_bcs(body: Any) -> list[dict[str, Any]]:
    """Safe getter — never mutates the body."""
    return list(getattr(body, "_pd_thermal_bcs", []) or [])


def _set_thermal_bcs(body: Any, bcs: list[dict[str, Any]]) -> None:
    if body is None:
        return
    try:
        body._pd_thermal_bcs = bcs
    except Exception:
        pass


@skill(
    name="thermal_bc_tag",
    category="fem_cae",
    level="atomic",
    summary="Tag faces with a thermal boundary condition — fixed temperature, "
            "heat flux, convection, or radiation. The record is stored on "
            "body._pd_thermal_bcs and consumed by downstream thermal FEM "
            "exporters. Body topology unchanged.",
    selector_kinds=["faces"],
    history_rules={"thermal_bc_faces": HistoryRule.MODIFIED_INHERIT},
    produces_features=["fem_thermal_boundary_condition"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match", "fm.invalid_thermal_bc"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ThermalBcTag(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        bc_kind: Literal["fixed_temp", "heat_flux", "convection", "radiation"]
        value: float
        h_coefficient: float | None = Field(default=None)
        ambient_t_c: float | None = Field(default=None)

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
                f"thermal_bc_tag: selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )

        # Per-kind validation.
        if args.bc_kind == "convection":
            if args.h_coefficient is None or args.h_coefficient <= 0.0:
                raise ValueError(
                    "thermal_bc_tag: bc_kind='convection' requires "
                    "h_coefficient > 0 (W/m²·K)"
                )
            if args.ambient_t_c is None:
                raise ValueError(
                    "thermal_bc_tag: bc_kind='convection' requires ambient_t_c (°C)"
                )
        if args.bc_kind == "radiation":
            if not (0.0 <= args.value <= 1.0):
                raise ValueError(
                    f"thermal_bc_tag: bc_kind='radiation' requires "
                    f"value (emissivity) in [0, 1] (got {args.value})"
                )
            if args.ambient_t_c is None:
                raise ValueError(
                    "thermal_bc_tag: bc_kind='radiation' requires ambient_t_c (°C)"
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
            "kind":          args.bc_kind,
            "value":         float(args.value),
            "h_coefficient": (None if args.h_coefficient is None
                              else float(args.h_coefficient)),
            "ambient_t_c":   (None if args.ambient_t_c is None
                              else float(args.ambient_t_c)),
            "face_centers":  face_centers,
            "face_areas":    face_areas,
            "selector":      args.face_selector.model_dump(),
        }
        existing = list(get_thermal_bcs(body))
        existing.append(record)
        _set_thermal_bcs(body, existing)

        history = EntityHistoryMap(
            rules={"thermal_bc_faces": HistoryRule.MODIFIED_INHERIT},
        )
        return SkillResult(body=body, history=history)
