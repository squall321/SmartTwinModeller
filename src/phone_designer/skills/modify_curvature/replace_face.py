"""replace_face — retarget a solid's selected PLANAR face to a new plane.

The SolidWorks *Replace Face* / *Move Face (offset)* op, robust v1. Pick ONE
planar target face (via ``face_selector``), then define the REPLACEMENT plane —
either:
  * ``offset_mm`` — offset the selected face's OWN plane along its outward
    normal (positive = outward/grow, negative = inward/shrink), or
  * ``plane_origin_mm`` + ``plane_normal`` — a caller-given absolute plane.

The replacement plane is used as an unbounded cutting Tool on the solid
(``BRepAlgoAPI_Splitter``, exactly as ``split_body``), and the piece on the
solid's MATERIAL side of that plane is kept — so the adjacent walls are
re-trimmed to the new boundary automatically.

Which side is "material"? The selected face's OUTWARD normal points AWAY from
the solid (into free space). The kept piece is therefore the one whose centroid
lies on the −outward-normal side of the replacement plane. This is what makes
"replace the top (z=20) face with a plane at z=17" SHORTEN a box to z∈[0,17]
(vol 6800 for a 20×20×20 box) rather than keep the thin z∈[17,20] slab.

Scope of v1 (honest limits):
  * The target face must be PLANAR (a plane surface). A curved target is
    refused (fm.target_face_not_planar).
  * The replacement is a single PLANE. This covers offset (parallel) and tilt
    (rotated) retargets — the common Replace/Move-Face cases — but NOT
    replacing a face with a curved surface. That is out of scope for v1.
  * ``face_selector`` must resolve to exactly ONE face (its outward normal
    fixes the material side). More/less than one is refused.

is_solid gated on kept volume > _EPS_VOL. A replacement plane that misses the
body entirely (leaves the solid on one side) is a structured refusal, not a
zero-volume "body".
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

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


def _centroid(shape) -> tuple[float, float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _planar_face_plane(face):
    """(origin, outward_normal) of a PLANAR face, or None if not planar.

    outward normal respects the face's TopAbs orientation (a REVERSED face
    shares its surface with the solid but its true outward normal is flipped).
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return None
    pl = surf.Plane()
    n = pl.Axis().Direction()
    nx, ny, nz = n.X(), n.Y(), n.Z()
    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    # face centroid (a stable point ON the plane)
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    c = g.CentreOfMass()
    return ((c.X(), c.Y(), c.Z()), (nx, ny, nz))


@skill(
    name="replace_face",
    category="modify/curvature",
    level="atomic",
    summary="Retarget a solid's selected PLANAR face to a new plane (SolidWorks "
            "Replace/Move Face). face_selector picks ONE planar face; the "
            "replacement plane is either its own plane offset by offset_mm along "
            "its outward normal (−=shrink, +=grow), or an absolute "
            "plane_origin_mm+plane_normal. The plane cuts the solid "
            "(BRepAlgoAPI_Splitter) and the MATERIAL-side piece is kept, "
            "re-trimming the adjacent walls. E.g. replace a 20³ box's top "
            "(z=20) with a plane at z=17 → z∈[0,17], vol 6800. Planar target + "
            "planar replacement only (v1). is_solid gated on kept volume>0.",
    selector_kinds=["faces"],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "replaced_face": HistoryRule.GENERATED_NEW},
    preconditions=["pc.face_selector_matches_one_planar"],
    produces_features=["replace_face"],
    preserves=[],
    manufacturing={},
    failure_modes=[
        "fm.no_body",
        "fm.face_selector_no_match",
        "fm.face_selector_ambiguous",
        "fm.target_face_not_planar",
        "fm.zero_plane_normal",
        "fm.replace_underspecified",
        "fm.replace_failed",
        "fm.replace_no_cut",
    ],
    cost_hint=0.3,
    post_conditions=[PostCondition(kind="body_present")],
)
class ReplaceFace(SkillBase):
    class Args(BaseModel):
        face_selector: SelectorRef = Field(
            description="Selects the ONE planar face whose plane is being "
                        "retargeted. Its outward normal fixes the material side.")
        offset_mm: float | None = Field(
            default=None,
            description="Offset the SELECTED face's own plane by this along its "
                        "outward normal. Negative shrinks the solid, positive "
                        "grows it. Mutually exclusive with plane_origin_mm/normal.")
        plane_origin_mm: tuple[float, float, float] | None = Field(
            default=None,
            description="A point on the ABSOLUTE replacement plane. Provide with "
                        "plane_normal instead of offset_mm.")
        plane_normal: tuple[float, float, float] | None = Field(
            default=None,
            description="Normal of the ABSOLUTE replacement plane. Provide with "
                        "plane_origin_mm instead of offset_mm.")

        @model_validator(mode="after")
        def _one_replacement_mode(self):
            has_offset = self.offset_mm is not None
            has_plane = (self.plane_origin_mm is not None
                         and self.plane_normal is not None)
            partial_plane = (
                (self.plane_origin_mm is not None) != (self.plane_normal is not None)
            )
            if partial_plane:
                raise ValueError(
                    "fm.replace_underspecified: plane_origin_mm and plane_normal "
                    "must be given together.")
            if has_offset == has_plane:
                raise ValueError(
                    "fm.replace_underspecified: provide EXACTLY one of offset_mm "
                    "OR (plane_origin_mm + plane_normal).")
            return self

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopTools import TopTools_ListOfShape

        from phone_designer.skills._resolvers import resolve_faces
        from phone_designer.skills.assembly._compound import (
            build_compound,
            iter_solid_components,
        )

        if body is None:
            raise ValueError("fm.no_body: replace_face needs an input body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        # ── 1. resolve the ONE planar target face ────────────────────────────
        faces = resolve_faces(shape, args.face_selector, body=body)
        if not faces:
            raise ValueError(
                "fm.face_selector_no_match: face_selector matched 0 faces.")
        if len(faces) > 1:
            raise ValueError(
                f"fm.face_selector_ambiguous: face_selector matched {len(faces)} "
                "faces; replace_face needs exactly one (its outward normal fixes "
                "the material side).")
        plane = _planar_face_plane(faces[0])
        if plane is None:
            raise ValueError(
                "fm.target_face_not_planar: the selected face is not planar; "
                "replace_face v1 retargets planar faces only.")
        (fx, fy, fz), (fnx, fny, fnz) = plane

        # ── 2. build the replacement plane (origin + unit normal) ────────────
        if args.offset_mm is not None:
            # offset the face's own plane along its OUTWARD normal.
            ox = fx + fnx * args.offset_mm
            oy = fy + fny * args.offset_mm
            oz = fz + fnz * args.offset_mm
            nx, ny, nz = fnx, fny, fnz  # already unit (from gp_Dir)
        else:
            ox, oy, oz = args.plane_origin_mm
            nx, ny, nz = args.plane_normal
            nmag = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nmag < 1e-12:
                raise ValueError(
                    "fm.zero_plane_normal: plane_normal is a zero vector.")
            nx, ny, nz = nx / nmag, ny / nmag, nz / nmag

        # ── 3. cut the solid with the (unbounded) replacement plane ──────────
        pln = gp_Pln(gp_Ax3(gp_Pnt(ox, oy, oz), gp_Dir(nx, ny, nz)))
        tool_face = BRepBuilderAPI_MakeFace(pln).Face()
        try:
            args_l = TopTools_ListOfShape()
            args_l.Append(shape)
            tools_l = TopTools_ListOfShape()
            tools_l.Append(tool_face)
            splitter = BRepAlgoAPI_Splitter()
            splitter.SetArguments(args_l)
            splitter.SetTools(tools_l)
            splitter.Build()
            if not splitter.IsDone():
                raise RuntimeError("Splitter IsDone=False")
            result = splitter.Shape()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"fm.replace_failed: {type(exc).__name__}: {exc}")

        pieces = [s for s in iter_solid_components(result) if _volume(s) > _EPS_VOL]
        if not pieces:
            raise ValueError("fm.replace_failed: no solid pieces after the cut.")

        # ── 4. keep the MATERIAL side (opposite the face's outward normal) ───
        # material lies on the −outward-normal half-space. A piece's centroid
        # projected onto the outward normal (measured from a point on the new
        # plane) is NEGATIVE on the material side.
        def side(piece) -> float:
            cx, cy, cz = _centroid(piece)
            return (cx - ox) * fnx + (cy - oy) * fny + (cz - oz) * fnz

        material = [p for p in pieces if side(p) < 0.0]
        if not material:
            # every piece is on the outward side → the plane did not cut into
            # the body toward the material (e.g. an outward offset that grows
            # past the solid, or a plane that missed it entirely).
            raise ValueError(
                "fm.replace_no_cut: the replacement plane left no material-side "
                f"piece ({len(pieces)} piece(s) all on the outward side of the "
                "selected face). Check the offset sign / plane orientation.")

        out = material[0] if len(material) == 1 else build_compound(material)
        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(f"fm.replace_failed: kept volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "replaced_face": HistoryRule.GENERATED_NEW}),
            extras={"transform": {
                "op": "replace_face",
                "target_face_origin_mm": [round(fx, 6), round(fy, 6), round(fz, 6)],
                "target_face_normal": [round(fnx, 6), round(fny, 6), round(fnz, 6)],
                "replacement_origin_mm": [round(ox, 6), round(oy, 6), round(oz, 6)],
                "replacement_normal": [round(nx, 6), round(ny, 6), round(nz, 6)],
                "mode": "offset" if args.offset_mm is not None else "plane",
                "offset_mm": args.offset_mm,
                "n_pieces_total": len(pieces),
                "n_pieces_kept": len(material),
                "volume_mm3": round(vol, 3),
            }},
        )
