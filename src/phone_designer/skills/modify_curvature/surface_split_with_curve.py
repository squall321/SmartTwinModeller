"""surface_split_with_curve — atomic. Split a face by a curve/edge lying on it.

The caller supplies a face selector and a polyline `points` (≥2 XYZ points)
that traces the desired split. We:
    1. Project each point onto the resolved face's surface so the split
       curve lies exactly on the surface (BRep edges that don't lie on the
       face's geometry would be rejected by BRepFeat_SplitShape).
    2. Build a polyline edge through the projected points.
    3. Drive `BRepFeat_SplitShape` with the edge mapped onto the face.
    4. Return the result shape — face count must increase (the source face
       is replaced by ≥ 2 sub-faces).

This is best-effort for planar/quasi-planar faces; for highly curved faces
the projected polyline may not stay exactly on the surface and the splitter
may reject the edge — caller should handle the resulting RuntimeError.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


def _project_point_on_face(face, xyz: tuple[float, float, float]):
    """Project a 3D point onto the face's surface; return gp_Pnt of nearest point.

    Falls back to the raw point if projection fails.
    """
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.gp import gp_Pnt

    raw = gp_Pnt(*xyz)
    try:
        surface = BRep_Tool.Surface_s(face)
        proj = GeomAPI_ProjectPointOnSurf(raw, surface)
        if proj.NbPoints() >= 1:
            return proj.NearestPoint()
    except Exception:
        pass
    return raw


def _make_polyline_edge_on_face(face, points_xyz: list[tuple[float, float, float]]):
    """Build a single polyline TopoDS_Edge (sequential line segments) through the
    projected points; return a compound of those edges (so SplitShape can take
    a single Compound argument). Edges are projected to lie on the face.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if len(points_xyz) < 2:
        raise ValueError("surface_split_with_curve: need ≥2 points")

    projected = [_project_point_on_face(face, p) for p in points_xyz]

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)

    edges_added = 0
    for i in range(len(projected) - 1):
        p1, p2 = projected[i], projected[i + 1]
        if p1.Distance(p2) < 1e-7:
            continue   # skip zero-length segment
        me = BRepBuilderAPI_MakeEdge(p1, p2)
        if not me.IsDone():
            continue
        builder.Add(comp, me.Edge())
        edges_added += 1

    if edges_added == 0:
        raise RuntimeError(
            "surface_split_with_curve: no usable polyline edges (all segments degenerate)"
        )
    return comp


@skill(
    name="surface_split_with_curve",
    category="modify/curvature",
    level="atomic",
    summary="Split a single face by a polyline curve that lies on the face. "
            "Projects each point onto the face's surface, builds a polyline "
            "compound of edges, and applies BRepFeat_SplitShape so the source "
            "face is partitioned into ≥2 sub-faces.",
    selector_kinds=["faces"],
    history_rules={
        "source_face":  HistoryRule.SPLIT_BRANCH,
        "split_edges":  HistoryRule.GENERATED_NEW,
    },
    preconditions=[
        "pc.face_selector_matches_one",
        "pc.polyline_has_two_points",
    ],
    produces_features=["face_split"],
    preserves=["overall_volume"],
    manufacturing={
        "cnc_5axis": {"min_fillet_r_mm": 0.3},
    },
    failure_modes=[
        "fm.split_shape_rejected_edge",
        "fm.polyline_off_surface",
    ],
    cost_hint=0.25,
    post_conditions=[PostCondition(kind="face_count_changed")],
)
class SurfaceSplitWithCurve(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef
        points: list[tuple[float, float, float]] = Field(
            min_length=2,
            description="Polyline of ≥2 XYZ points; projected onto the face.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepFeat import BRepFeat_SplitShape

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise RuntimeError(
                "surface_split_with_curve: face_selector matched 0 faces"
            )
        target_face = faces[0]

        split_compound = _make_polyline_edge_on_face(target_face, args.points)

        splitter = BRepFeat_SplitShape(shape)
        try:
            splitter.Add(split_compound, target_face)
        except Exception as exc:
            raise RuntimeError(
                f"surface_split_with_curve: SplitShape.Add failed: {exc} "
                "(curve may not lie exactly on face surface)"
            ) from exc

        try:
            splitter.Build()
        except Exception as exc:
            raise RuntimeError(
                f"surface_split_with_curve: SplitShape.Build failed: {exc}"
            ) from exc

        if not splitter.IsDone():
            raise RuntimeError(
                "surface_split_with_curve: BRepFeat_SplitShape did not complete"
            )
        new_shape = splitter.Shape()

        history = EntityHistoryMap(
            rules={
                "source_face": HistoryRule.SPLIT_BRANCH,
                "split_edges": HistoryRule.GENERATED_NEW,
            },
        )
        return SkillResult(body=Part(new_shape), history=history)
