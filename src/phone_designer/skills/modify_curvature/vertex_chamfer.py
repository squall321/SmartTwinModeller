"""vertex_chamfer — atomic.

Chamfer a single vertex (corner) by cutting it off with a plane.

Args:
    vertex_position: (x, y, z) of the target vertex (mm). The nearest vertex
        on the body is selected.
    distance_mm: distance from the vertex along the cut-plane normal — the
        cutting plane is positioned `distance_mm` away from the vertex toward
        the body interior.

Implementation:
    1. Enumerate all body vertices, pick the one closest to vertex_position.
    2. Enumerate edges incident to that vertex; for each, take the unit
       direction pointing from the vertex toward the OTHER endpoint.
    3. Average those directions and normalize → `n_avg` (points roughly into
       the body, away from the corner).
    4. Build a cutting plane at `vertex + distance_mm · n_avg` with normal
       `-n_avg` (so the plane's positive half-space is the corner side).
    5. Build a half-space solid on the corner side via BRepPrimAPI_MakeHalfSpace
       with a reference point at the vertex (the half-space contains the
       reference point).
    6. Cut the half-space from the body with BRepAlgoAPI_Cut — removes the
       corner, leaves a planar chamfer face.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import (
    EntityHistoryMap,
    EntityRef,
    HistoryRule,
    SelectorFreeze,
)
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


@skill(
    name="vertex_chamfer",
    category="modify/chamfer",
    level="atomic",
    summary="Chamfer a single vertex (corner) by cutting it off with a plane "
            "positioned distance_mm from the vertex along the average of "
            "incident edge directions.",
    selector_kinds=[],   # no selector — direct (x,y,z) lookup
    history_rules={
        "target_vertex":  HistoryRule.CONSUMED,
        "result_face":    HistoryRule.GENERATED_NEW,
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
    },
    preconditions=[
        "pc.vertex_exists_at_position",
        "pc.distance_less_than_half_shortest_incident_edge",
    ],
    produces_features=["chamfer_face_vertex"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "sheet_metal_stamp": {"min_wall_mm": 0.5},
    },
    failure_modes=[
        "fm.no_vertex_near_position",
        "fm.no_incident_edges",
        "fm.self_intersection_if_distance_too_large",
        "fm.boolean_cut_failed",
    ],
    cost_hint=0.20,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class VertexChamfer(SkillBase):
    class Args(BaseModel):
        vertex_position: tuple[float, float, float] = Field(
            description="approximate (x,y,z) of vertex to chamfer; nearest is used"
        )
        distance_mm: float = Field(gt=0.0, le=20.0,
                                   description="cut-plane offset from the vertex")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRep import BRep_Tool
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_MapOfShape

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # 1. Find all unique vertices, pick the one nearest to vertex_position.
        target = args.vertex_position
        best_v = None
        best_p = None
        best_d2 = float("inf")
        seen_v = TopTools_MapOfShape()
        vit = TopExp_Explorer(shape, TopAbs_VERTEX)
        while vit.More():
            v_raw = vit.Current()
            if not seen_v.Contains(v_raw):
                seen_v.Add(v_raw)
                v = TopoDS.Vertex_s(v_raw)
                p = BRep_Tool.Pnt_s(v)
                dx = p.X() - target[0]
                dy = p.Y() - target[1]
                dz = p.Z() - target[2]
                d2 = dx * dx + dy * dy + dz * dz
                if d2 < best_d2:
                    best_d2 = d2
                    best_v = v
                    best_p = p
            vit.Next()

        if best_v is None or best_p is None:
            raise RuntimeError(
                f"vertex_chamfer: no vertices on body (target={target})"
            )

        nearest_dist = math.sqrt(best_d2)
        # Generous tolerance: chamfer is a coarse op, the user gives an
        # approximate (x,y,z). Reject only if nothing reasonable exists.
        if nearest_dist > 5.0:
            raise RuntimeError(
                f"vertex_chamfer: nearest vertex is {nearest_dist:.3f} mm "
                f"from {target} (>5 mm threshold)"
            )

        vx, vy, vz = best_p.X(), best_p.Y(), best_p.Z()

        # 2. Find edges incident to best_v; collect direction toward other end.
        dirs: list[tuple[float, float, float]] = []
        seen_e = TopTools_MapOfShape()
        eit = TopExp_Explorer(shape, TopAbs_EDGE)
        while eit.More():
            e_raw = eit.Current()
            if not seen_e.Contains(e_raw):
                seen_e.Add(e_raw)
                e = TopoDS.Edge_s(e_raw)
                # explore the edge's vertices to test incidence + grab the other end
                sub_vs = []
                sit = TopExp_Explorer(e, TopAbs_VERTEX)
                while sit.More():
                    sub_vs.append(TopoDS.Vertex_s(sit.Current()))
                    sit.Next()
                # dedup by IsSame
                contains_target = any(sv.IsSame(best_v) for sv in sub_vs)
                if contains_target:
                    for sv in sub_vs:
                        if not sv.IsSame(best_v):
                            sp = BRep_Tool.Pnt_s(sv)
                            dx = sp.X() - vx
                            dy = sp.Y() - vy
                            dz = sp.Z() - vz
                            mag = math.sqrt(dx * dx + dy * dy + dz * dz)
                            if mag > 1e-9:
                                dirs.append((dx / mag, dy / mag, dz / mag))
                            break
            eit.Next()

        if not dirs:
            raise RuntimeError(
                f"vertex_chamfer: no incident edges found for vertex at "
                f"({vx:.3f}, {vy:.3f}, {vz:.3f})"
            )

        # 3. Average direction (into the body, away from the corner).
        ax = sum(d[0] for d in dirs) / len(dirs)
        ay = sum(d[1] for d in dirs) / len(dirs)
        az = sum(d[2] for d in dirs) / len(dirs)
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if mag < 1e-9:
            raise RuntimeError(
                f"vertex_chamfer: incident edges sum to ~0 vector "
                f"(vertex is not a corner?) — {len(dirs)} edges"
            )
        nx, ny, nz = ax / mag, ay / mag, az / mag

        # 4. Cutting plane center: at vertex + distance·n.
        cx = vx + args.distance_mm * nx
        cy = vy + args.distance_mm * ny
        cz = vz + args.distance_mm * nz

        # 5. Build the plane. Normal points away from the corner (+n),
        #    so the half-space side that contains the original vertex is the
        #    "corner" side — we will cut that side off.
        pl = gp_Pln(gp_Ax3(gp_Pnt(cx, cy, cz), gp_Dir(nx, ny, nz)))

        # Need to embed the plane as a face (BRepPrimAPI_MakeHalfSpace requires
        # a face, not a raw gp_Pln). Build an unbounded planar face.
        mkf = BRepBuilderAPI_MakeFace(pl)
        if not mkf.IsDone():
            raise RuntimeError("vertex_chamfer: BRepBuilderAPI_MakeFace failed")
        plane_face = mkf.Face()

        # 6. Half-space containing the vertex (the corner side).
        ref_pt = gp_Pnt(vx, vy, vz)
        hs = BRepPrimAPI_MakeHalfSpace(plane_face, ref_pt)
        try:
            hs.Build()
        except Exception:
            pass
        cutter = hs.Solid()

        # 7. Boolean cut: body - cutter = body with corner removed.
        cut = BRepAlgoAPI_Cut(shape, cutter)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError(
                f"vertex_chamfer: BRepAlgoAPI_Cut failed at vertex "
                f"({vx:.3f}, {vy:.3f}, {vz:.3f}), distance={args.distance_mm}"
            )
        new_shape = cut.Shape()

        # Freeze: single vertex entity.
        refs = [
            EntityRef(
                tag=None, kind="vertex",
                bbox_center=(round(vx, 3), round(vy, 3), round(vz, 3)),
                measure=round(args.distance_mm, 3),
            )
        ]
        freeze = SelectorFreeze.from_entities(refs)

        history = EntityHistoryMap(
            rules={
                "target_vertex":  HistoryRule.CONSUMED,
                "result_face":    HistoryRule.GENERATED_NEW,
                "adjacent_faces": HistoryRule.MODIFIED_INHERIT,
            },
        )

        return SkillResult(
            body=Part(new_shape),
            history=history,
            selector_freeze=freeze,
        )
