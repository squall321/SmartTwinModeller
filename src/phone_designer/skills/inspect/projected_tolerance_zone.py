"""projected_tolerance_zone — atomic, read-only.

ISO 1101 / ASME Y14.5 projected tolerance zone (⊕ P modifier). For a
threaded / press-fit hole that engages a mating part, the position
tolerance is enforced not at the hole itself but at a virtual cylindrical
zone projected ``projection_length_mm`` above the entry face (the height
of the mating fastener / dowel).

This skill tags the cylindrical hole face with the projection metadata so
that downstream FCF / position-tolerance verifiers can extend the actual
hole axis by ``projection_length_mm`` and compare it against the diametral
tolerance zone of ``tolerance_mm``.

Storage:
    body._pd_pgtolerance = [
        {"face_idx": int,
         "axis_origin": [x,y,z],
         "axis_direction": [x,y,z],
         "hole_radius_mm": float,
         "projection_length_mm": float,
         "tolerance_mm": float,
         "projected_axis_tip": [x,y,z]  # axis_origin + dir * projection_length
        },
        ...
    ]

extras["projected_tolerance"] mirrors the appended entry + the list index.

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


def _cylinder_params(face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Cylinder:
        raise ValueError(
            "projected_tolerance_zone requires a cylindrical hole face "
            "(threaded / press-fit hole surface)"
        )
    cyl = surf.Cylinder()
    ax = cyl.Axis().Direction()
    loc = cyl.Location()
    return (
        (loc.X(), loc.Y(), loc.Z()),
        (ax.X(), ax.Y(), ax.Z()),
        float(cyl.Radius()),
    )


@skill(
    name="projected_tolerance_zone",
    category="inspect",
    level="atomic",
    summary="ISO 1101 projected tolerance zone (⊕P) — tag a cylindrical "
            "hole face with projection_length / tolerance metadata so the "
            "position tolerance is enforced at a virtual axis extension. "
            "Read-only.",
    selector_kinds=[
        "faces_by_normal", "faces_by_area", "face_named", "tagged",
        "and", "or", "not", "first_n", "largest_n",
    ],
    history_rules={},
    produces_features=["projected_tolerance_zone"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.no_match"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class ProjectedToleranceZone(SkillBase):
    class Args(BaseModel):
        hole_face_selector: dict
        projection_length_mm: float = Field(gt=0.0, le=500.0)
        tolerance_mm: float = Field(ge=0.0, le=10.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces, resolve_faces

        shape = _occt_shape(body)
        sel = _coerce_selector(args.hole_face_selector)
        matched = resolve_faces(shape, sel, body=body)
        if not matched:
            raise ValueError(
                f"projected_tolerance_zone: hole_face_selector matched 0 "
                f"faces (kind={sel.kind})"
            )
        face = matched[0]

        origin, axis, radius = _cylinder_params(face)
        tip = (
            origin[0] + axis[0] * args.projection_length_mm,
            origin[1] + axis[1] * args.projection_length_mm,
            origin[2] + axis[2] * args.projection_length_mm,
        )

        all_faces = _all_faces(shape)
        face_idx = -1
        for i, af in enumerate(all_faces):
            try:
                if af.IsSame(face):
                    face_idx = i
                    break
            except Exception:
                pass

        entry = {
            "face_idx": face_idx,
            "axis_origin": [round(c, 6) for c in origin],
            "axis_direction": [round(c, 6) for c in axis],
            "hole_radius_mm": round(radius, 6),
            "projection_length_mm": float(args.projection_length_mm),
            "tolerance_mm": float(args.tolerance_mm),
            "projected_axis_tip": [round(c, 6) for c in tip],
        }

        try:
            existing = list(getattr(body, "_pd_pgtolerance", []) or [])
        except Exception:
            existing = []
        existing.append(entry)
        try:
            body._pd_pgtolerance = existing
        except Exception:
            pass

        extras = {
            "projected_tolerance": entry,
            "index": len(existing) - 1,
            "total": len(existing),
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras=extras,
        )
