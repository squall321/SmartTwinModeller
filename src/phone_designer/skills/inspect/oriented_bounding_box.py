"""oriented_bounding_box — atomic, read-only.

The smallest box ENCLOSING the part, aligned to the part's own axes (not the world
axes) — SolidWorks/Fusion/NX "Bounding Box". Returns the OBB centre, its three
orthonormal axis directions, and the size along each. Unlike the axis-aligned box
(``mass_properties`` centroid + a world AABB), the OBB gives the true minimal stock
size for an arbitrarily-posed part — the answer to stock/blank sizing, packaging,
nesting, and "will it fit".

Uses OCCT ``BRepBndLib.AddOBB_s(shape, Bnd_OBB, useTriangulation, isOptimal,
useShapeTolerance)``. ``optimal=True`` (default) runs the slower, tighter fit; the
world AABB is included alongside for reference. Read-only — body unchanged.

extras schema::

    {
      "obb": {"center": [x,y,z],
              "axes": [[ax,ay,az], [bx,by,bz], [cx,cy,cz]],   # orthonormal
              "size_mm": [la, lb, lc],                         # full extents
              "half_size_mm": [...], "volume_mm3": la*lb*lc},
      "aabb": {"size_mm": [dx,dy,dz], "min": [...], "max": [...]}
    }
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


@skill(
    name="oriented_bounding_box",
    category="inspect",
    level="atomic",
    summary="Read-only: the minimal ORIENTED bounding box (OBB) — smallest box "
            "aligned to the part's own axes (BRepBndLib.AddOBB_s). Returns center, "
            "three orthonormal axis directions, and size along each — the true "
            "minimal stock size for an arbitrarily-posed part (stock/blank sizing, "
            "packaging, nesting, fit). World AABB included for reference. "
            "optimal=True runs the tighter/slower fit. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["oriented_bounding_box"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.empty_body"],
    cost_hint=0.05,
    post_conditions=[PostCondition(kind="body_present")],
)
class OrientedBoundingBox(SkillBase):
    class Args(BaseModel):
        optimal: bool = Field(
            default=True,
            description="True (default) = the tighter, slower optimal OBB fit; "
                        "False = the fast approximate fit.")
        use_triangulation: bool = Field(
            default=True,
            description="Use the existing mesh triangulation for the fit (faster on "
                        "already-tessellated bodies).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from OCP.Bnd import Bnd_Box, Bnd_OBB
        from OCP.BRepBndLib import BRepBndLib

        if body is None:
            raise ValueError("fm.empty_body: oriented_bounding_box needs a body.")
        shape = _occt_shape(body)

        obb = Bnd_OBB()
        BRepBndLib.AddOBB_s(shape, obb, args.use_triangulation, args.optimal, True)
        if obb.IsVoid():
            raise ValueError("fm.empty_body: OBB is void — body has no geometry.")

        c = obb.Center()
        xd, yd, zd = obb.XDirection(), obb.YDirection(), obb.ZDirection()
        # HSize = HALF extent along each local axis; full size = 2×.
        ha, hb, hc = obb.XHSize(), obb.YHSize(), obb.ZHSize()
        la, lb, lc = 2.0 * ha, 2.0 * hb, 2.0 * hc

        aabb = Bnd_Box()
        try:
            BRepBndLib.AddOptimal_s(shape, aabb)
        except Exception:
            BRepBndLib.Add_s(shape, aabb)
        if aabb.IsVoid():
            axmin = axmax = [0.0, 0.0, 0.0]
            adx = ady = adz = 0.0
        else:
            xmn, ymn, zmn, xmx, ymx, zmx = aabb.Get()
            axmin = [round(xmn, 4), round(ymn, 4), round(zmn, 4)]
            axmax = [round(xmx, 4), round(ymx, 4), round(zmx, 4)]
            adx, ady, adz = xmx - xmn, ymx - ymn, zmx - zmn

        def _v(d):
            return [round(d.X(), 6), round(d.Y(), 6), round(d.Z(), 6)]

        extras = {
            "obb": {
                "center": [round(c.X(), 4), round(c.Y(), 4), round(c.Z(), 4)],
                "axes": [_v(xd), _v(yd), _v(zd)],
                "size_mm": [round(la, 4), round(lb, 4), round(lc, 4)],
                "half_size_mm": [round(ha, 4), round(hb, 4), round(hc, 4)],
                "volume_mm3": round(la * lb * lc, 4),
            },
            "aabb": {
                "size_mm": [round(adx, 4), round(ady, 4), round(adz, 4)],
                "min": axmin,
                "max": axmax,
            },
            "optimal": args.optimal,
        }
        return SkillResult(body=body, history=EntityHistoryMap(), extras=extras)
