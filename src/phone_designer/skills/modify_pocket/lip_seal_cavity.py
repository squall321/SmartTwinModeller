"""lip_seal_cavity — atomic.

Cylindrical housing cavity for a radial shaft lip seal per ISO 6194
(``catalogs/seals/iso6194.yaml``). Subtracts a cylindrical pocket of
``housing_d_mm × width_mm`` along the given axis, centered axially at
``axial_position_mm``. The cavity is the bore that the seal's outer rubber
case press-fits into.

Args:
    axis_origin:        (x,y,z) world point on the shaft / bore axis.
    axis_direction:     (x,y,z) axis direction (normalized internally).
    seal_spec:          catalog key, e.g. "10x22x7", "20x32x7".
    axial_position_mm:  signed distance from ``axis_origin`` along
                        ``axis_direction`` at which the cavity is centered
                        axially.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _load(family, name):
    import yaml, pathlib
    root = pathlib.Path(__file__).resolve().parents[4]
    return yaml.safe_load((root / "catalogs" / family / f"{name}.yaml").read_text())


def _transform_shape_to_axis(shape, axis_origin, axis_direction):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf

    ax, ay, az = axis_direction
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if norm < 1e-12:
        raise ValueError("axis_direction is zero")
    ax, ay, az = ax / norm, ay / norm, az / norm

    target = gp_Ax3(gp_Pnt(*axis_origin), gp_Dir(ax, ay, az))
    default = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    t = gp_Trsf()
    t.SetTransformation(target, default)
    xf = BRepBuilderAPI_Transform(shape, t, True)
    xf.Build()
    if not xf.IsDone():
        raise RuntimeError("lip_seal_cavity axis transform failed")
    return xf.Shape()


@skill(
    name="lip_seal_cavity",
    category="modify/pocket",
    level="atomic",
    summary="Catalog-keyed cylindrical pocket for a radial shaft lip seal "
            "(ISO 6194 / iso6194.yaml). Subtracts housing_d × width along the "
            "given axis, centered axially at axial_position_mm.",
    selector_kinds=[],
    history_rules={
        "cavity_wall":     HistoryRule.GENERATED_NEW,
        "cavity_walls":    HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.seal_spec_known",
        "pc.axis_direction_nonzero",
    ],
    produces_features=["lip_seal_cavity", "cylindrical_pocket"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis": {"min_wall_mm": 0.5},
        "turning":   {"min_wall_mm": 0.3, "extras": {"H8_recommended": True}},
    },
    failure_modes=[
        "fm.unknown_seal_spec",
        "fm.cavity_exits_body",
    ],
    cost_hint=0.18,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class LipSealCavity(SkillBase):
    class Args(BaseModel):
        axis_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
        seal_spec: str = Field(..., description='catalog key, e.g. "10x22x7"')
        axial_position_mm: float = Field(default=0.0)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        catalog = _load("seals", "iso6194")["seals"]
        if args.seal_spec not in catalog:
            raise ValueError(
                f"lip_seal_cavity: unknown seal_spec '{args.seal_spec}'. "
                f"Known: {sorted(catalog.keys())}"
            )
        spec = catalog[args.seal_spec]
        housing_d = float(spec["housing_d_mm"])
        width = float(spec["width_mm"])

        if housing_d <= 0.0 or width <= 0.0:
            raise ValueError(
                f"lip_seal_cavity: invalid catalog values "
                f"housing_d={housing_d}, width={width}"
            )

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # Cavity in local frame: axis +Z, centered axially at axial_position.
        z_min = args.axial_position_mm - width / 2.0
        ax = gp_Ax2(gp_Pnt(0.0, 0.0, z_min), gp_Dir(0.0, 0.0, 1.0))
        cavity_local = BRepPrimAPI_MakeCylinder(ax, housing_d / 2.0, width).Shape()

        cavity_world = _transform_shape_to_axis(
            cavity_local, args.axis_origin, args.axis_direction,
        )

        cut = BRepAlgoAPI_Cut(shape, cavity_world)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("lip_seal_cavity cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "cavity_wall":     HistoryRule.GENERATED_NEW,
                "cavity_walls":    HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
