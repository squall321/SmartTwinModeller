"""vent_membrane_pocket — atomic. Gore-style acoustic vent pocket.

Two-stage acoustic vent feature:
  1. Through hole at `mounting_d_mm` — the actual air passage.
  2. A thin recessed counterbore (depth = `boss_height_mm`, diameter =
     `membrane_d_mm`) for the membrane disc to seat against.

Catalog entries (catalogs/membranes/<family>.yaml) supply the membrane disc
diameter, the through-hole mounting diameter, and the recess (boss) height.

v1 supports planar ±Z target faces. The membrane recess opens AT the face; the
through hole continues through the body.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._resolvers import (
    _face_center,
    _face_normal_at_center,
    resolve_faces,
)
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family: str, name: str):
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


@skill(
    name="vent_membrane_pocket",
    category="modify/pocket",
    level="atomic",
    summary="Acoustic vent feature for a Gore-style protective membrane: a "
            "through hole at `mounting_d` plus a thin recessed counterbore at "
            "`membrane_d` (depth = `boss_height`) that seats the membrane disc. "
            "Catalog-driven (catalogs/membranes/<family>.yaml). v1 supports "
            "planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "vent_through":     HistoryRule.GENERATED_NEW,
        "membrane_recess":  HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.membrane_spec_in_catalog",
    ],
    produces_features=["vent_through_hole", "membrane_recess"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"counterbore_then_drill": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_for_recess": True}},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.membrane_spec_unknown",
        "fm.recess_outside_body",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class VentMembranePocket(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of vent center",
        )
        membrane_spec: str = Field(
            description="Catalog key (e.g. 'PMF_F25', 'Acoustic_Vent_LV85')",
        )
        catalog_family: str = Field(
            default="gore",
            description="Catalog subfolder under catalogs/membranes/",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("membranes", args.catalog_family)
        membranes = catalog.get("membranes", {})
        if args.membrane_spec not in membranes:
            raise ValueError(
                f"vent_membrane_pocket: unknown membrane_spec "
                f"'{args.membrane_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(membranes.keys())}"
            )
        entry = membranes[args.membrane_spec]
        membrane_d = float(entry["membrane_d_mm"])
        mounting_d = float(entry["mounting_d_mm"])
        boss_h = float(entry["boss_height_mm"])

        if mounting_d >= membrane_d:
            raise ValueError(
                f"vent_membrane_pocket: catalog mounting_d ({mounting_d}) "
                f">= membrane_d ({membrane_d}) — disc would fall through"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"vent_membrane_pocket: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "vent_membrane_pocket v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        # Axis grows INTO the body (opposite of face normal).
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        # Stage 1: thin membrane recess (counterbore) — opens AT the face.
        recess_base = gp_Pnt(cx, cy, center[2])
        recess_ax = gp_Ax2(recess_base, axis_dir)
        recess_mk = BRepPrimAPI_MakeCylinder(recess_ax, membrane_d / 2.0, boss_h)
        recess_mk.Build()
        if not recess_mk.IsDone():
            raise RuntimeError(
                "vent_membrane_pocket: membrane recess cylinder build failed"
            )
        cut_a = BRepAlgoAPI_Cut(shape, recess_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError(
                "vent_membrane_pocket: recess boolean cut failed"
            )
        new_shape = cut_a.Shape()

        # Stage 2: through hole at mounting diameter. Cylinder starts 1mm
        # above the face and is 200mm long so it cleanly traverses the body.
        through_overrun = 1.0
        through_len = 200.0 + through_overrun
        through_base = gp_Pnt(
            cx, cy, center[2] + nz_sign * through_overrun,
        )
        through_ax = gp_Ax2(through_base, axis_dir)
        through_mk = BRepPrimAPI_MakeCylinder(
            through_ax, mounting_d / 2.0, through_len,
        )
        through_mk.Build()
        if not through_mk.IsDone():
            raise RuntimeError(
                "vent_membrane_pocket: through cylinder build failed"
            )
        cut_b = BRepAlgoAPI_Cut(new_shape, through_mk.Shape())
        cut_b.Build()
        if not cut_b.IsDone():
            raise RuntimeError(
                "vent_membrane_pocket: through-hole boolean cut failed"
            )
        new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "vent_through":    HistoryRule.GENERATED_NEW,
                "membrane_recess": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
