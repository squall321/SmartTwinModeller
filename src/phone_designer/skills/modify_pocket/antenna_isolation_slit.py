"""antenna_isolation_slit — atomic. Metal-frame antenna gap with polymer infill flag.

Mid-frame "band" antennas use thin slits in the metal rail to break the
conductive loop into resonator segments. Each slit is a narrow rectangular
cut perpendicular to the long axis of the host edge, traversing the rail
through-and-through. The visible gap is typically backfilled with a
non-conductive polymer (the ``polymer_fill`` flag records intent — the
polymer body itself is added by a separate `polymer_inlay` skill).

Geometry (raw OCCT, axis-aligned edge along the rail v1):
    rail-edge axis  ∈ {X, Y, Z}
    slit_box = MakeBox(slit_length × slit_width × through)
        - slit_length  = along the rail edge (consumed band length)
        - slit_width   = perpendicular, in the rail's cross-section plane
        - through      = far enough to traverse the host volume

The slit is centered at the midpoint of the selected edge. Width is split
half on each side of the rail centerline. The `polymer_fill` flag is stored
in history extras for downstream consumers.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="antenna_isolation_slit",
    category="modify/pocket",
    level="atomic",
    summary="Narrow rectangular through-cut across a metal-frame edge to break "
            "the antenna loop into resonator segments. v1: axis-aligned host "
            "edge. polymer_fill is a downstream-consumer flag.",
    selector_kinds=["edges"],
    history_rules={
        "host_edge":       HistoryRule.MODIFIED_INHERIT,
        "slit_walls":      HistoryRule.GENERATED_NEW,
        "consumed_volume": HistoryRule.CONSUMED,
    },
    preconditions=[
        "pc.edge_selector_matches_one",
        "pc.slit_length_lt_edge_length",
    ],
    produces_features=["antenna_isolation_slit"],
    preserves=["outer_envelope_outside_slit"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.3,
                              "extras": {"slit_width_recommended_mm": 0.5}},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0,
                              "extras": {"post_machining_recommended": True,
                                         "polymer_overmold_after_cast": True}},
        # injection_mold: 절연 frame 자체가 plastic 이라 부적용
    },
    failure_modes=[
        "fm.slit_outside_body",
        "fm.slit_too_long_for_edge",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class AntennaIsolationSlit(SkillBase):
    class Args(BaseModel):
        edge_selector: SelectorRef
        slit_length_mm: float = Field(default=4.0, gt=0, le=50,
                                       description="slit length along the rail "
                                                   "edge axis")
        slit_width_mm: float = Field(default=0.5, gt=0, le=5,
                                      description="slit width perpendicular to "
                                                  "the rail edge axis")
        polymer_fill: bool = Field(default=True,
                                    description="record-only flag: whether the "
                                                "slit should be backfilled "
                                                "with polymer by a downstream "
                                                "polymer_inlay skill")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        from phone_designer.skills._resolvers import resolve_edges

        shape = body.wrapped if hasattr(body, "wrapped") else body
        edges = resolve_edges(shape, args.edge_selector)
        if not edges:
            raise RuntimeError(
                f"antenna_isolation_slit: edge_selector matched 0 — "
                f"{args.edge_selector.model_dump()}"
            )
        edge = edges[0]

        # Endpoints
        from OCP.BRep import BRep_Tool
        from OCP.TopExp import TopExp
        v1 = TopExp.FirstVertex_s(edge)
        v2 = TopExp.LastVertex_s(edge)
        p1 = BRep_Tool.Pnt_s(v1)
        p2 = BRep_Tool.Pnt_s(v2)
        dx = abs(p1.X() - p2.X())
        dy = abs(p1.Y() - p2.Y())
        dz = abs(p1.Z() - p2.Z())
        EPS = 1e-6
        if dx > EPS and dy < EPS and dz < EPS:
            axis, edge_len = "X", dx
        elif dy > EPS and dx < EPS and dz < EPS:
            axis, edge_len = "Y", dy
        elif dz > EPS and dx < EPS and dy < EPS:
            axis, edge_len = "Z", dz
        else:
            raise NotImplementedError(
                f"antenna_isolation_slit v1: edge must be axis-aligned "
                f"(delta=({dx:.3f},{dy:.3f},{dz:.3f}))"
            )

        if args.slit_length_mm >= edge_len:
            raise ValueError(
                f"antenna_isolation_slit: slit_length_mm ({args.slit_length_mm}) "
                f">= host edge length ({edge_len:.3f}) — would consume entire "
                f"rail"
            )

        # Midpoint of the edge — slit centered along the edge axis here.
        mid = ((p1.X() + p2.X()) / 2.0,
               (p1.Y() + p2.Y()) / 2.0,
               (p1.Z() + p2.Z()) / 2.0)

        # Body bbox along the two cross-section axes — pick the SHORTER
        # transverse extent as the slit-width axis, the OTHER as through.
        # The slit width is centered on the body cross-section center (the
        # rail centerline), not the edge endpoint, so the cut sits inside
        # the rail wall instead of straddling its corner.
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb, True)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        body_x = xmax - xmin
        body_y = ymax - ymin
        body_z = zmax - zmin
        body_cx = (xmin + xmax) / 2.0
        body_cy = (ymin + ymax) / 2.0
        body_cz = (zmin + zmax) / 2.0

        L = args.slit_length_mm
        W = args.slit_width_mm
        overshoot = 0.05

        if axis == "X":
            # length axis = X; width = thinner of Y/Z, through = the other
            if body_y <= body_z:
                w_axis = "Y"
            else:
                w_axis = "Z"
            lo_x = mid[0] - L / 2.0
            hi_x = mid[0] + L / 2.0
            if w_axis == "Y":
                lo_y = body_cy - W / 2.0
                hi_y = body_cy + W / 2.0
                lo_z = zmin - overshoot
                hi_z = zmax + overshoot
            else:
                lo_z = body_cz - W / 2.0
                hi_z = body_cz + W / 2.0
                lo_y = ymin - overshoot
                hi_y = ymax + overshoot
        elif axis == "Y":
            if body_x <= body_z:
                w_axis = "X"
            else:
                w_axis = "Z"
            lo_y = mid[1] - L / 2.0
            hi_y = mid[1] + L / 2.0
            if w_axis == "X":
                lo_x = body_cx - W / 2.0
                hi_x = body_cx + W / 2.0
                lo_z = zmin - overshoot
                hi_z = zmax + overshoot
            else:
                lo_z = body_cz - W / 2.0
                hi_z = body_cz + W / 2.0
                lo_x = xmin - overshoot
                hi_x = xmax + overshoot
        else:  # Z
            if body_x <= body_y:
                w_axis = "X"
            else:
                w_axis = "Y"
            lo_z = mid[2] - L / 2.0
            hi_z = mid[2] + L / 2.0
            if w_axis == "X":
                lo_x = body_cx - W / 2.0
                hi_x = body_cx + W / 2.0
                lo_y = ymin - overshoot
                hi_y = ymax + overshoot
            else:
                lo_y = body_cy - W / 2.0
                hi_y = body_cy + W / 2.0
                lo_x = xmin - overshoot
                hi_x = xmax + overshoot

        mk = BRepPrimAPI_MakeBox(gp_Pnt(lo_x, lo_y, lo_z),
                                 gp_Pnt(hi_x, hi_y, hi_z))
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError("antenna_isolation_slit: slit box build failed")
        tool = mk.Shape()

        cut = BRepAlgoAPI_Cut(shape, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("antenna_isolation_slit: body cut failed")
        new_shape = cut.Shape()

        history = EntityHistoryMap(
            rules={
                "host_edge":       HistoryRule.MODIFIED_INHERIT,
                "slit_walls":      HistoryRule.GENERATED_NEW,
                "consumed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(
            body=Part(new_shape),
            history=history,
            extras={"polymer_fill": bool(args.polymer_fill)},
        )
