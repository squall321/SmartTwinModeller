"""wireless_charging_coil_seat — atomic.

Catalog-driven Qi wireless-charging receiver-coil seat. Subtracts a shallow
cylindrical pocket sized to the coil's outer diameter and stack thickness from
catalogs/qi_coils/<family>.yaml; optionally drops an additional ferrite-shield
recess (same outer diameter, extra depth `ferrite_t_mm`) behind the coil seat
to seat the magnetic shield disc.

v1 supports planar +Z / -Z target faces.
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
    name="wireless_charging_coil_seat",
    category="modify/pocket",
    level="atomic",
    summary="Qi wireless-charging receiver-coil pocket. Subtracts a cylinder of "
            "(outer_d × thickness) from catalogs/qi_coils/<family>.yaml. With "
            "`with_ferrite=True` an additional same-diameter ferrite-shield "
            "recess (`ferrite_t_mm` deep) is sunk behind the coil seat. v1 "
            "supports planar +Z/-Z faces.",
    selector_kinds=["faces"],
    history_rules={
        "target_face":      HistoryRule.MODIFIED_INHERIT,
        "coil_seat_wall":   HistoryRule.GENERATED_NEW,
        "coil_seat_floor":  HistoryRule.GENERATED_NEW,
        "consumed_volume":  HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.coil_spec_in_catalog",
        "pc.depth_less_than_body_thickness",
    ],
    produces_features=["wireless_coil_seat"],
    preserves=["outer_envelope_outside_pocket"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5,
                              "extras": {"flat_floor_required": True}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.coil_spec_unknown",
        "fm.pocket_exits_body",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class WirelessChargingCoilSeat(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        position_xy: tuple[float, float] = Field(
            default=(0.0, 0.0),
            description="face-local (x, y) of coil-seat center, offset from face centroid.",
        )
        coil_spec: str = Field(
            description="Catalog key (e.g. 'Qi_A11', 'Qi_A28').",
        )
        catalog_family: str = Field(
            default="wpc",
            description="Catalog filename stem under catalogs/qi_coils/.",
        )
        with_ferrite: bool = Field(
            default=True,
            description="Add an additional ferrite-shield recess behind the coil seat.",
        )
        ferrite_t_mm: float = Field(
            default=0.2, gt=0.0, le=10.0,
            description="Depth of the ferrite-shield recess (only used if with_ferrite).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("qi_coils", args.catalog_family)
        coils = catalog.get("coils", {})
        if args.coil_spec not in coils:
            raise ValueError(
                f"wireless_charging_coil_seat: unknown coil_spec "
                f"'{args.coil_spec}' in family '{args.catalog_family}' — "
                f"catalog keys: {sorted(coils.keys())}"
            )
        entry = coils[args.coil_spec]
        outer_d = float(entry["outer_d_mm"])
        coil_t = float(entry["thickness_mm"])

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                f"wireless_charging_coil_seat: face_selector matched 0 — "
                f"{args.face_selector.model_dump()}"
            )
        target = faces[0]
        center = _face_center(target)
        normal = _face_normal_at_center(target)
        if abs(normal[2]) < 0.9:
            raise NotImplementedError(
                "wireless_charging_coil_seat v1: planar +Z/-Z target faces only"
            )

        nz_sign = 1.0 if normal[2] > 0 else -1.0
        cx = center[0] + args.position_xy[0]
        cy = center[1] + args.position_xy[1]
        # Cut direction: INTO the body (opposite face normal).
        axis_dir = gp_Dir(0.0, 0.0, -nz_sign)

        # Stage 1: coil seat — opens AT the face, depth = coil_t.
        seat_base = gp_Pnt(cx, cy, center[2])
        seat_ax = gp_Ax2(seat_base, axis_dir)
        seat_mk = BRepPrimAPI_MakeCylinder(seat_ax, outer_d / 2.0, coil_t)
        seat_mk.Build()
        if not seat_mk.IsDone():
            raise RuntimeError(
                "wireless_charging_coil_seat: coil seat cylinder build failed"
            )
        cut_a = BRepAlgoAPI_Cut(shape, seat_mk.Shape())
        cut_a.Build()
        if not cut_a.IsDone():
            raise RuntimeError(
                "wireless_charging_coil_seat: coil seat boolean cut failed"
            )
        new_shape = cut_a.Shape()

        # Stage 2 (optional): ferrite-shield recess of `ferrite_t_mm`,
        # starting at the floor of the coil seat (depth coil_t into the body).
        if args.with_ferrite:
            ferrite_base = gp_Pnt(cx, cy, center[2] - nz_sign * coil_t)
            ferrite_ax = gp_Ax2(ferrite_base, axis_dir)
            ferrite_mk = BRepPrimAPI_MakeCylinder(
                ferrite_ax, outer_d / 2.0, args.ferrite_t_mm,
            )
            ferrite_mk.Build()
            if not ferrite_mk.IsDone():
                raise RuntimeError(
                    "wireless_charging_coil_seat: ferrite recess cylinder build failed"
                )
            cut_b = BRepAlgoAPI_Cut(new_shape, ferrite_mk.Shape())
            cut_b.Build()
            if not cut_b.IsDone():
                raise RuntimeError(
                    "wireless_charging_coil_seat: ferrite recess boolean cut failed"
                )
            new_shape = cut_b.Shape()

        history = EntityHistoryMap(
            rules={
                "target_face":     HistoryRule.MODIFIED_INHERIT,
                "coil_seat_wall":  HistoryRule.GENERATED_NEW,
                "coil_seat_floor": HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
