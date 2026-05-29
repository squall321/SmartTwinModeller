"""dimple — atomic. Small spherical or conical bump in a sheet face.

A dimple is a small formed bump on a sheet — the sheet metal is locally
deformed into a dome/cone. Used as a low-cost positive feature for
registration, friction lock, or stiffening.

Geometry approximation:
    A conical dimple is a small cone (frustum) protruding from the top
    surface of the sheet, fused onto the sheet body. A spherical dimple is
    a hemispherical cap. We do not model the corresponding crater on the
    underside (real sheet metal would have one) — this skill represents the
    raw additive volume change for downstream simulation.

Args:
    center_x_mm, center_y_mm: in-plane position of the dimple center.
    diameter_mm: base diameter of the dimple.
    height_mm: protrusion height above the top surface.
    shape: "spherical" | "conical" — geometry primitive used.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult
from phone_designer.skills.modify_sheet.bend_edge import _fuse, _shape_bbox


def _build_spherical_dimple(
    *, center_xy: tuple[float, float], z0: float,
    radius_mm: float, height_mm: float,
):
    """Build a spherical cap of base radius `radius_mm` and height `height_mm`
    sitting on the plane z=z0.

    The cap is a portion of a sphere intersected with a half-space z >= z0.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Pnt

    # Sphere of radius R s.t. cap of height h has base radius r:
    #     R = (r² + h²) / (2h)
    # Sphere center is at (cx, cy, z0 + h - R) (i.e. below the surface so cap
    # protrudes by exactly `height_mm`).
    cx, cy = center_xy
    if height_mm <= 0:
        raise RuntimeError("dimple: height must be positive")
    R = (radius_mm * radius_mm + height_mm * height_mm) / (2.0 * height_mm)
    center_z = z0 + height_mm - R
    sphere_maker = BRepPrimAPI_MakeSphere(gp_Pnt(cx, cy, center_z), R)
    sphere_maker.Build()
    if not sphere_maker.IsDone():
        raise RuntimeError("dimple: sphere build failed")
    sphere = sphere_maker.Shape()

    # Intersect with half-space z >= z0, bounded above by z = z0 + height + ε.
    # A finite tall box centered on (cx, cy, z0 + height/2) works.
    margin = radius_mm + height_mm + 1.0
    box_corner = gp_Pnt(cx - margin, cy - margin, z0)
    box_maker = BRepPrimAPI_MakeBox(
        box_corner, 2.0 * margin, 2.0 * margin, height_mm + margin,
    )
    box_maker.Build()
    if not box_maker.IsDone():
        raise RuntimeError("dimple: bounding box failed")
    box = box_maker.Shape()

    common = BRepAlgoAPI_Common(sphere, box)
    common.Build()
    if not common.IsDone():
        raise RuntimeError("dimple: spherical cap intersection failed")
    return common.Shape()


def _build_conical_dimple(
    *, center_xy: tuple[float, float], z0: float,
    radius_mm: float, height_mm: float,
):
    """Build a cone (apex up, base on z=z0). The apex is at height_mm above
    z0; the base is a circle of radius `radius_mm`.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    cx, cy = center_xy
    if height_mm <= 0:
        raise RuntimeError("dimple: height must be positive")
    axis = gp_Ax2(gp_Pnt(cx, cy, z0), gp_Dir(0.0, 0.0, 1.0))
    # R1 = base radius (z=z0), R2 = top radius (z=z0+height). Pointy apex
    # uses R2 = 0; we use a small flat 5% of base radius to avoid degenerate
    # apex (which OCCT can build but is sharper than realistic stamping).
    R1 = radius_mm
    R2 = max(radius_mm * 0.05, 0.01)
    maker = BRepPrimAPI_MakeCone(axis, R1, R2, height_mm)
    maker.Build()
    if not maker.IsDone():
        raise RuntimeError("dimple: cone build failed")
    return maker.Shape()


@skill(
    name="dimple",
    category="modify/sheet",
    level="atomic",
    summary="Small spherical or conical bump formed on a sheet face. Adds a "
            "protruding cap (spherical) or cone above the top surface at "
            "(center_x, center_y).",
    selector_kinds=[],
    history_rules={
        "dimple_face": HistoryRule.GENERATED_NEW,
        "host_top":    HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.input_is_flat_sheet",
    ],
    produces_features=["dimple"],
    preserves=["sheet_thickness"],
    manufacturing={
        "sheet_metal_stamp": {"min_fillet_r_mm": 0.2,
                              "extras": {"forming_depth_max_mm": 5.0}},
    },
    failure_modes=[
        "fm.dimple_outside_sheet",
        "fm.dimple_height_excessive",
    ],
    cost_hint=0.15,
    post_conditions=[PostCondition(kind="volume_changed", min_delta_mm3=0.01)],
)
class Dimple(SkillBase):
    class Args(BaseModel):
        center_x_mm: float
        center_y_mm: float
        diameter_mm: float = Field(gt=0.0, le=200.0)
        height_mm: float = Field(gt=0.0, le=50.0)
        shape: Literal["spherical", "conical"] = "spherical"

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part

        shape = body.wrapped if hasattr(body, "wrapped") else body
        xmin, ymin, zmin, xmax, ymax, zmax = _shape_bbox(shape)
        z_top = zmax

        radius = args.diameter_mm / 2.0
        if args.shape == "spherical":
            bump = _build_spherical_dimple(
                center_xy=(args.center_x_mm, args.center_y_mm),
                z0=z_top, radius_mm=radius, height_mm=args.height_mm,
            )
        else:
            bump = _build_conical_dimple(
                center_xy=(args.center_x_mm, args.center_y_mm),
                z0=z_top, radius_mm=radius, height_mm=args.height_mm,
            )

        fused = _fuse(shape, bump)

        history = EntityHistoryMap(
            rules={
                "dimple_face": HistoryRule.GENERATED_NEW,
                "host_top":    HistoryRule.MODIFIED_INHERIT,
            },
        )
        return SkillResult(body=Part(fused), history=history)
