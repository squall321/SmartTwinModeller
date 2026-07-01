"""path_array_orientation — atomic. Array a seed body along a 3D path, each
instance oriented to the LOCAL PATH TANGENT (the full 3D Frenet-ish frame).

Use for features that must *ride* a curved rim: chain links following a loop,
treads climbing a helical ramp, cleats on a curved rail, studs around a torus
rim, coils of a spring. The seed is rigidly re-posed at every station so its
local +Z points along the path tangent — it banks/turns with the curve.

DISTINCT from ``path_pattern`` (macro): that skill PROJECTS the path onto XY
(drops Z entirely), keeps a FIXED axis-aligned feature orientation, and is
locked to a circular {pocket,hole,boss} carved into a selected face. This skill
keeps the FULL 3D path (Z included) and rotates the WHOLE seed solid to the
tangent frame — a genuine 3D path array of an arbitrary body.

Pipeline (raw OCCT):
  1. Build a wire from ``path_points`` (>=2). ``curve_type='polyline'`` (default)
     threads the points exactly (BRepBuilderAPI_MakePolygon); ``'bspline'``
     fits one smooth C2 B-spline (GeomAPI_PointsToBSpline) for banked curves.
  2. Cast the wire to ``TopoDS.Wire_s`` (THE HANG TRAP — a bare Shape() fed to
     BRepAdaptor_CompCurve/ThruSections hangs) and wrap in
     ``BRepAdaptor_CompCurve`` so multi-segment polylines read as one curve.
  3. Station by ARC LENGTH: s_i = i * L / (count-1) for i in 0..count-1 (both
     ends land). ``GCPnts_AbscissaPoint`` maps each arclength to a curve
     parameter. ``CompCurve.D1(u, P, T)`` gives the point P and tangent T.
  4. Build a target ``gp_Ax3(P, dir(T))`` (tangent = local +Z) and a fixed
     source frame at the origin; ``gp_Trsf.SetTransformation(target, src)`` is
     the rigid re-pose. ``align_to_tangent=False`` falls back to a pure
     translation P (no rotation) — a plain 3D path distribution.
  5. Deep-copy the seed to each station (``apply_transform_shape``); ``fuse``
     (BRepAlgoAPI_Fuse, falls back to a compound if any fuse fails) or keep a
     multi-body ``build_compound``. is_solid gated on volume>1e-6.

Verified law (seed = 4×4×4 box, vol 64):
  * straight path [0,0,0]->[40,0,0], count=5 (stations 0,10,20,30,40; gap 10 >
    box 4 -> disjoint) -> compound/fused vol = 5*64 = 320.
  * quarter-circle path (R=40), count=5 -> 5 disjoint instances, vol = 320.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

_EPS_VOL = 1e-6


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(float(g.Mass()))


def _bbox_mm(shape) -> list[float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:  # noqa: BLE001
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return [0.0, 0.0, 0.0]
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return [round(xmax - xmin, 3), round(ymax - ymin, 3), round(zmax - zmin, 3)]


def _build_path_wire(points: list[tuple[float, float, float]], curve_type: str):
    """points (>=2) -> a single TopoDS.Wire_s.

    THE HANG TRAP: the wire MUST be down-cast with ``TopoDS.Wire_s`` before it
    is handed to ``BRepAdaptor_CompCurve`` (or any ThruSections / MakePipeShell /
    CompCurve consumer) — a bare ``TopoDS_Shape`` from ``.Wire()`` / ``.Shape()``
    otherwise hangs the kernel forever.
    """
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS

    if curve_type == "bspline":
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
        from OCP.GeomAbs import GeomAbs_C2
        from OCP.GeomAPI import GeomAPI_PointsToBSpline
        from OCP.TColgp import TColgp_Array1OfPnt

        arr = TColgp_Array1OfPnt(1, len(points))
        for i, p in enumerate(points):
            arr.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
        # degree_min=3 (or fewer if too few pts), degree_max=min(8, n-1); C2.
        deg_max = min(8, max(1, len(points) - 1))
        deg_min = min(3, deg_max)
        curve = GeomAPI_PointsToBSpline(
            arr, deg_min, deg_max, GeomAbs_C2, 1e-6).Curve()
        edge = BRepBuilderAPI_MakeEdge(curve).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        return TopoDS.Wire_s(wire)

    # polyline — thread the points exactly.
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    wire = poly.Wire()
    if wire is None or wire.IsNull():
        raise ValueError(
            "fm.path_build_failed: polyline wire is null (all path_points "
            "coincide?).")
    return TopoDS.Wire_s(wire)


def _station_transform(P, T, align_to_tangent: bool):
    """Rigid transform placing the seed's origin frame at station (P, tangent T).

    tangent T -> local +Z of the target frame; ``align_to_tangent=False`` uses a
    pure translation to P (no rotation).
    """
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    trsf = gp_Trsf()
    if not align_to_tangent or T.Magnitude() < 1e-9:
        trsf.SetTranslation(gp_Vec(P.X(), P.Y(), P.Z()))
        return trsf
    # tangent becomes the frame's main direction (local +Z).
    target = gp_Ax3(P, gp_Dir(T))
    src = gp_Ax3(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0))
    trsf.SetTransformation(target, src)
    return trsf


def _fuse_all(shapes: list):
    """Fuse shapes pairwise. Returns (shape, ok); ok=False if any fuse fails."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    if not shapes:
        return None, False
    acc = shapes[0]
    for nxt in shapes[1:]:
        try:
            f = BRepAlgoAPI_Fuse(acc, nxt)
            f.Build()
            if not f.IsDone():
                return None, False
            acc = f.Shape()
            if acc is None or acc.IsNull():
                return None, False
        except Exception:  # noqa: BLE001
            return None, False
    return acc, True


@skill(
    name="path_array_orientation",
    category="transform",
    level="atomic",
    summary="Array a seed solid along a 3D path curve, each instance re-posed to "
            "the LOCAL PATH TANGENT (full 3D frame — seed's +Z follows the "
            "tangent). Build the wire from path_points (>=2): curve_type='polyline' "
            "(exact) | 'bspline' (smooth C2). Station by ARC LENGTH "
            "(GCPnts_AbscissaPoint) so both ends land; point+tangent via "
            "CompCurve.D1; gp_Ax3->gp_Trsf rigid re-pose. Unlike path_pattern "
            "(XY-projected, Z dropped, fixed orientation, feature-only) this keeps "
            "the full 3D path and rotates the WHOLE body. fuse=True unions copies "
            "(BRepAlgoAPI_Fuse; compound fallback). align_to_tangent=False = pure "
            "translation. is_solid gated on volume>0.",
    selector_kinds=[],
    history_rules={"body_faces": HistoryRule.MODIFIED_INHERIT,
                   "seam_edges": HistoryRule.GENERATED_NEW},
    produces_features=[],
    preserves=[],
    manufacturing={},
    failure_modes=["fm.path_no_body", "fm.path_too_few_points",
                   "fm.path_zero_length", "fm.path_build_failed",
                   "fm.path_array_failed"],
    cost_hint=0.25,
    result_grade="measured",
    post_conditions=[PostCondition(kind="body_present")],
)
class PathArrayOrientation(SkillBase):
    class Args(BaseModel):
        path_points: list[tuple[float, float, float]] = Field(
            min_length=2,
            description="Ordered 3D control points [[x,y,z],...] (>=2) defining the "
                        "path. Full 3D — Z is NOT dropped.")
        count: int = Field(
            ge=1,
            description="Number of instances. Stationed at equal arc-length "
                        "intervals; i=0 at the path start, i=count-1 at the end. "
                        "count=1 places the single seed at the path start.")
        align_to_tangent: bool = Field(
            default=True,
            description="True (default) = rotate each instance so its local +Z "
                        "follows the path tangent. False = translate only (no "
                        "rotation) — a plain 3D distribution.")
        fuse: bool = Field(
            default=True,
            description="True (default) = union all instances (BRepAlgoAPI_Fuse; "
                        "compound fallback if a fuse fails). False = return a "
                        "multi-body compound.")
        curve_type: Literal["polyline", "bspline"] = Field(
            default="polyline",
            description="'polyline' (default) threads the points exactly; 'bspline' "
                        "fits one smooth C2 B-spline for banked/curved paths.")

        @field_validator("path_points")
        @classmethod
        def _min_points(cls, v):
            if len(v) < 2:
                raise ValueError(
                    "fm.path_too_few_points: need >=2 path_points to define a path.")
            return v

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_AbscissaPoint
        from OCP.gp import gp_Pnt, gp_Vec

        from phone_designer.skills.assembly._compound import (
            apply_transform_shape,
            build_compound,
        )

        if body is None:
            raise ValueError(
                "fm.path_no_body: path_array_orientation needs an input seed body.")
        shape = body.wrapped if hasattr(body, "wrapped") else body

        pts = [(float(p[0]), float(p[1]), float(p[2])) for p in args.path_points]

        # ── build the path wire (cast to Wire_s — hang trap guard) ───────────
        try:
            wire = _build_path_wire(pts, args.curve_type)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.path_build_failed: {type(exc).__name__}: {exc}")

        cc = BRepAdaptor_CompCurve(wire)
        u0 = cc.FirstParameter()
        total_len = float(GCPnts_AbscissaPoint.Length_s(cc))
        if total_len <= 1e-9:
            raise ValueError(
                "fm.path_zero_length: path total arc-length is ~0 (all "
                "path_points coincide).")

        # ── station by arc length; re-pose the seed at each station ──────────
        try:
            copies = []
            for i in range(args.count):
                if args.count == 1:
                    s = 0.0
                else:
                    s = total_len * i / (args.count - 1)
                # map arclength -> curve parameter, then point + tangent.
                u = GCPnts_AbscissaPoint(cc, s, u0).Parameter()
                P = gp_Pnt()
                T = gp_Vec()
                cc.D1(u, P, T)
                trsf = _station_transform(P, T, args.align_to_tangent)
                copies.append(apply_transform_shape(shape, trsf))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"fm.path_array_failed: {type(exc).__name__}: {exc}")

        # ── fuse (union) or keep a multi-body compound ───────────────────────
        if len(copies) == 1:
            out, fused = copies[0], True
        elif args.fuse:
            out, fused = _fuse_all(copies)
            if not fused or out is None or out.IsNull():
                out = build_compound(copies)
                fused = False
        else:
            out, fused = build_compound(copies), False

        vol = _volume(out)
        if out is None or out.IsNull() or vol <= _EPS_VOL:
            raise ValueError(
                f"fm.path_array_failed: result volume {vol:.6g}mm³ ≈ 0.")

        return SkillResult(
            body=Part(out),
            history=EntityHistoryMap(rules={
                "body_faces": HistoryRule.MODIFIED_INHERIT,
                "seam_edges": HistoryRule.GENERATED_NEW,
            }),
            extras={"transform": {
                "op": "path_array_orientation",
                "count": args.count,
                "curve_type": args.curve_type,
                "align_to_tangent": args.align_to_tangent,
                "fuse": args.fuse,
                "fused": fused,
                "n_bodies": 1 if fused else len(copies),
                "path_length_mm": round(total_len, 3),
                "n_path_points": len(pts),
                "volume_mm3": round(vol, 3),
                "bbox_mm": _bbox_mm(out),
            }},
        )
