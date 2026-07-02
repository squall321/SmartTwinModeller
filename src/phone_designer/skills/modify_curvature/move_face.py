"""move_face — atomic. Offset a selected PLANAR face along its normal, healing
the adjacent walls (Fusion Press-Pull / SolidWorks Move Face).

Direct-edit op: pick ONE planar face via ``face_selector`` (expect exactly 1),
read its plane + outward normal, and grow/shrink the body by prisming that face
along the normal:

    distance_mm > 0  → prism the face OUTWARD (+normal) and FUSE onto the body
                       (the face translates outward, walls extend, volume grows).
    distance_mm < 0  → prism the face INWARD  (−normal) and CUT from the body
                       (the face translates inward,  walls recede,  volume shrinks).

Approach (robust, no BRepFeat state machine): build a prism SOLID from the
selected face's OWN geometry (BRepPrimAPI_MakePrism on the face) along the
outward normal by |distance_mm|, then BRepAlgoAPI_Fuse (grow) or _Cut (shrink)
with the base solid. For a planar face this is exactly the swept slab between
the old and new face positions, so the neighbouring side walls heal to the new
plane automatically. This is the same prism-from-face technique
``shell_variable_thickness`` uses for its correction slabs and
``extrude_pocket_world`` uses for its cutting tool.

Contract:
  * face_selector must resolve to exactly ONE face and that face must be PLANAR
    (a Move Face on a curved face is a different, ill-posed operation — refused
    with fm.face_not_planar).
  * every wall adjacent to the moved face must be PRISMATIC, i.e. parallel to
    the move direction (perpendicular to the moved face). Only then does the
    prism-slab Fuse/Cut equal true Move Face semantics. Drafted/tapered walls
    (e.g. a cone frustum's lateral face) would silently yield a stepped pad or
    knife-edge pocket instead of extending the wall — refused with
    fm.adjacent_wall_not_prismatic.
  * distance_mm == 0 is a no-op error (fm.zero_distance).
  * is_solid gated: the result must have volume > 1e-6 and grow (fuse) or shrink
    (cut) monotonically — verified by the volume_increased/volume_decreased-style
    check inside _apply (a fuse that removed volume, or a cut that added it, is a
    face-orientation bug and raises).

Analytic proof: a 20×20×20 box (vol 8000 mm³), move its TOP face (+Z normal)
by +5 → 8000 + 20·20·5 = 10000 mm³; by −5 → 8000 − 20·20·5 = 6000 mm³.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _planar_face_outward_normal(face) -> tuple[float, float, float]:
    """Unit outward-normal of a PLANAR ``face`` (orientation-aware).

    Raises RuntimeError if the face is not planar — Move Face on a curved
    face is ill-posed (there is no single normal to offset along).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        raise ValueError(
            "fm.face_not_planar: move_face requires a PLANAR face; the selected "
            f"face is surface type {int(surf.GetType())} (not a plane)."
        )
    d = surf.Plane().Axis().Direction()
    nx, ny, nz = d.X(), d.Y(), d.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return (nx, ny, nz)


def _check_adjacent_walls_prismatic(shape, face, nx: float, ny: float, nz: float,
                                    tol: float = 1e-3) -> None:
    """Refuse unless every face sharing an edge with ``face`` is PRISMATIC —
    parallel to the move direction (nx,ny,nz) along the shared edge.

    The prism-slab Fuse/Cut used by move_face equals true Move Face semantics
    ONLY under this condition; a tapered/drafted neighbour wall would silently
    produce a stepped pad (grow) or knife-edge pocket (shrink). Raises
    ``fm.adjacent_wall_not_prismatic`` with the worst wall tilt in degrees.
    """
    import math

    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    e2f = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, e2f)

    worst = 0.0
    it = TopExp_Explorer(face, TopAbs_EDGE)
    while it.More():
        edge = TopoDS.Edge_s(it.Current())
        it.Next()
        if BRep_Tool.Degenerated_s(edge):
            continue
        if not e2f.Contains(edge):
            continue
        for nb in e2f.FindFromKey(edge):
            nb_face = TopoDS.Face_s(nb)
            if nb_face.IsSame(face):
                continue
            # sample the neighbour's surface normal along the shared edge via
            # its pcurve; the wall is prismatic iff normal ⟂ move direction.
            c2d = BRepAdaptor_Curve2d(edge, nb_face)
            u0, u1 = c2d.FirstParameter(), c2d.LastParameter()
            gpf = BRepGProp_Face(nb_face)
            for k in range(5):
                uv = c2d.Value(u0 + (u1 - u0) * (k + 0.5) / 5.0)
                p = gp_Pnt()
                v = gp_Vec()
                gpf.Normal(uv.X(), uv.Y(), p, v)
                mag = v.Magnitude()
                if mag < 1e-12:
                    continue  # surface singularity — no usable normal here
                dot = abs((v.X() * nx + v.Y() * ny + v.Z() * nz) / mag)
                worst = max(worst, dot)
    if worst > tol:
        ang = math.degrees(math.asin(min(1.0, worst)))
        raise ValueError(
            "fm.adjacent_wall_not_prismatic: move_face v1 only heals side walls "
            "parallel to the move direction (perpendicular to the moved face); "
            f"a neighbour wall is tilted {ang:.1f}° from the sweep direction — "
            "the prism-slab boolean would produce a stepped pad / knife-edge "
            "pocket, not a Move Face.")


def _prism_from_face(face, nx: float, ny: float, nz: float, extent: float):
    """Extrude ``face`` along the unit vector (nx,ny,nz) by ``extent`` (>0),
    returning a prism SOLID (the swept slab between the two face positions)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    vec = gp_Vec(nx * extent, ny * extent, nz * extent)
    prism = BRepPrimAPI_MakePrism(face, vec, False, True)
    if not prism.IsDone():
        raise RuntimeError("move_face: BRepPrimAPI_MakePrism failed on the face.")
    shp = prism.Shape()
    if shp.IsNull():
        raise RuntimeError("move_face: prism produced a null shape.")
    return shp


@skill(
    name="move_face",
    category="modify/curvature",
    level="atomic",
    summary="Offset/translate ONE selected PLANAR face along its outward normal "
            "by distance_mm (signed), healing the adjacent side walls to the new "
            "plane (Fusion Press-Pull / SolidWorks Move Face) — walls heal ONLY "
            "when they are parallel to the move direction; drafted/tapered "
            "neighbour walls are refused (fm.adjacent_wall_not_prismatic). "
            "distance_mm>0 prisms the face OUTWARD and FUSES (volume grows); "
            "distance_mm<0 prisms INWARD and CUTS (volume shrinks). Implemented by "
            "extruding the face's own geometry into a slab and Fuse/Cut with the "
            "base solid. face_selector must match exactly one planar face; "
            "is_solid gated.",
    selector_kinds=["faces"],
    history_rules={
        "moved_face":  HistoryRule.MODIFIED_INHERIT,
        "side_walls":  HistoryRule.MODIFIED_INHERIT,
        "swept_walls": HistoryRule.GENERATED_NEW,
    },
    preconditions=["pc.face_selector_matches_one", "pc.face_is_planar"],
    produces_features=["moved_face"],
    preserves=["side_wall_planarity"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
    },
    failure_modes=[
        "fm.zero_distance",
        "fm.no_face_matched",
        "fm.multiple_faces_matched",
        "fm.face_not_planar",
        "fm.adjacent_wall_not_prismatic",
        "fm.move_face_boolean_failed",
        "fm.volume_wrong_direction",
    ],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class MoveFace(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef = Field(
            description="Selector resolving to EXACTLY ONE planar face to move "
                        "(e.g. faces_by_normal direction=[0,0,1] for the top face).")
        distance_mm: float = Field(
            description="Signed offset along the face's OUTWARD normal. "
                        ">0 grows the body (face moves outward, walls extend); "
                        "<0 shrinks it (face moves inward, walls recede).")

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces

        if body is None:
            raise ValueError("fm.move_face_boolean_failed: move_face needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        dist = float(args.distance_mm)
        if abs(dist) < 1e-9:
            raise ValueError("fm.zero_distance: distance_mm must be non-zero.")

        base_vol = _volume(shape)
        if base_vol <= _EPS_VOL:
            raise ValueError(
                f"fm.move_face_boolean_failed: input body is not a solid "
                f"(volume {base_vol:.6g} mm³ ≈ 0)."
            )

        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise ValueError(
                f"fm.no_face_matched: face_selector matched 0 faces — "
                f"{args.face_selector.model_dump()}"
            )
        if len(faces) > 1:
            raise ValueError(
                f"fm.multiple_faces_matched: face_selector matched {len(faces)} "
                f"faces; move_face requires exactly 1. Tighten the selector."
            )
        face = faces[0]

        # Outward normal of the planar face (raises fm.face_not_planar if curved).
        nx, ny, nz = _planar_face_outward_normal(face)

        # Prismatic-wall guard: the prism-slab Fuse/Cut below equals true Move
        # Face semantics ONLY when every adjacent wall is parallel to the sweep
        # direction. Refuse tapered/drafted neighbours instead of silently
        # producing a stepped pad / knife-edge pocket.
        _check_adjacent_walls_prismatic(shape, face, nx, ny, nz)

        # Build the swept slab from the face's own geometry, along the outward
        # normal for a grow (dist>0) or inward for a shrink (dist<0). extent is
        # always positive; the sign chooses the direction and the boolean.
        if dist > 0:
            sx, sy, sz = nx, ny, nz          # extrude OUTWARD
        else:
            sx, sy, sz = -nx, -ny, -nz       # extrude INWARD
        slab = _prism_from_face(face, sx, sy, sz, abs(dist))

        try:
            if dist > 0:
                op = BRepAlgoAPI_Fuse(shape, slab)   # grow
            else:
                op = BRepAlgoAPI_Cut(shape, slab)    # shrink
            op.Build()
            if not op.IsDone():
                raise RuntimeError("boolean IsDone=False")
            out = op.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.move_face_boolean_failed: {type(exc).__name__}: {exc}"
            )

        if out is None or out.IsNull():
            raise ValueError("fm.move_face_boolean_failed: boolean produced null shape.")

        new_vol = _volume(out)
        if new_vol <= _EPS_VOL:
            raise ValueError(
                f"fm.move_face_boolean_failed: result volume {new_vol:.6g} mm³ ≈ 0."
            )

        # Monotonicity gate: a fuse MUST grow, a cut MUST shrink. A violation
        # means the normal/orientation was wrong (silent no-op or inverted op).
        delta = new_vol - base_vol
        if dist > 0 and delta <= _EPS_VOL:
            raise ValueError(
                f"fm.volume_wrong_direction: distance_mm>0 should grow the body "
                f"but volume changed by {delta:+.6g} mm³."
            )
        if dist < 0 and delta >= -_EPS_VOL:
            raise ValueError(
                f"fm.volume_wrong_direction: distance_mm<0 should shrink the body "
                f"but volume changed by {delta:+.6g} mm³."
            )

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "moved_face":  HistoryRule.MODIFIED_INHERIT,
                "side_walls":  HistoryRule.MODIFIED_INHERIT,
                "swept_walls": HistoryRule.GENERATED_NEW,
            }),
            extras={"move_face": {
                "op": "fuse" if dist > 0 else "cut",
                "distance_mm": round(dist, 6),
                "normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                "base_volume_mm3": round(base_vol, 3),
                "result_volume_mm3": round(new_vol, 3),
                "delta_mm3": round(delta, 3),
            }},
        )
