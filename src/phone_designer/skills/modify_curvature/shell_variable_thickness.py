"""shell_variable_thickness — atomic. Shell with thickness varying per face.

Hollow a solid by removing zero or more "open" faces and offsetting the
remaining surface inward by a per-face thickness. Useful when one wall of an
enclosure (e.g., the camera deck) must be thicker than the others.

Args:
    face_thickness_map: list of {face_selector: SelectorRef, thickness_mm: float}
        — explicit thickness for each face matched by `face_selector`.
        Faces NOT in this map either become "open" (removed) or take
        `default_thickness_mm` if it is provided.
    default_thickness_mm: float | None = None
        — if None, faces not in the map are removed (open shell).
        — if set, faces not in the map are shelled at this thickness.

Implementation strategy (FALLBACK, documented):
    OCCT `BRepOffsetAPI_MakeThickSolid.MakeThickSolidByJoin` only accepts a
    SINGLE scalar offset — there is no per-face thickness parameter in the
    public OCP binding. We therefore:

    1. Group entries by `thickness_mm` value.
    2. Pick a "base" group: the group with the most faces (or the one whose
       value equals `default_thickness_mm`). That defines the OCCT shell pass.
       Open faces (faces not in any group, when default_thickness_mm is None)
       are added to the OCCT "closing faces" list for the base pass.
    3. For every OTHER thickness value, run a *correction* operation:
       extrude a thin slab on each member face, inward by
       (group_thickness - base_thickness). If positive, the slab is fused
       to the shell interior (thickening that wall); if negative, it is
       subtracted from the inner cavity (thinning that wall).

    This is approximate when faces share edges with different thicknesses,
    and may leave a small step at the boundary — acceptable for v1 because
    the documented contract is "best effort variable thickness". Tests
    verify the thickness is correct at the face centers.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap, HistoryRule
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._selectors import SelectorRef
from phone_designer.skills._spec import SkillBase, SkillResult


class FaceThicknessEntry(BaseModel):
    face_selector: SelectorRef
    thickness_mm: float = Field(gt=0.0, le=20.0)


def _shell_with_closing_faces(
    shape,
    closing_faces: list,
    offset_mm: float,
    tol: float = 1e-3,
):
    """OCCT MakeThickSolidByJoin wrapper.

    `closing_faces` are removed from the shell (become open). Returns the
    resulting TopoDS_Shape. Sign convention: positive `offset_mm` removes
    material INWARD (standard shell direction).
    """
    from OCP.BRepOffset import BRepOffset_Mode
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.GeomAbs import GeomAbs_JoinType
    from OCP.TopTools import TopTools_ListOfShape

    closing_list = TopTools_ListOfShape()
    for f in closing_faces:
        closing_list.Append(f)

    maker = BRepOffsetAPI_MakeThickSolid()
    try:
        # MakeThickSolidByJoin uses *negative* offset to shell inward by
        # the given thickness (positive=outward growth in OCCT convention).
        maker.MakeThickSolidByJoin(
            shape,
            closing_list,
            -float(offset_mm),
            tol,
            BRepOffset_Mode.BRepOffset_Skin,
            False,                        # Intersection
            False,                        # SelfInter
            GeomAbs_JoinType.GeomAbs_Arc,
            False,                        # RemoveIntEdges
        )
    except Exception as e:
        raise RuntimeError(
            f"shell_variable_thickness: MakeThickSolidByJoin raised: {e}"
        ) from e
    if not maker.IsDone():
        raise RuntimeError(
            f"shell_variable_thickness: MakeThickSolidByJoin IsDone=False "
            f"(offset={offset_mm} mm, closing_faces={len(closing_faces)})"
        )
    return maker.Shape()


def _face_outward_normal(face) -> tuple[float, float, float]:
    """Return unit outward-normal of `face` (orientation-aware)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        u_mid = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
        v_mid = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
        pnt = gp_Pnt()
        d1u = gp_Vec()
        d1v = gp_Vec()
        surf.D1(u_mid, v_mid, pnt, d1u, d1v)
        n = d1u.Crossed(d1v)
        nn = n.Magnitude()
        if nn < 1e-12:
            raise RuntimeError(
                "shell_variable_thickness: degenerate face normal"
            )
        nx, ny, nz = n.X() / nn, n.Y() / nn, n.Z() / nn
    else:
        pl = surf.Plane()
        d = pl.Axis().Direction()
        nx, ny, nz = d.X(), d.Y(), d.Z()

    if face.Orientation() == TopAbs_REVERSED:
        nx, ny, nz = -nx, -ny, -nz
    return (nx, ny, nz)


def _face_correction_slab(face, base_t: float, group_t: float):
    """Build a "correction slab" spanning depths between base_t and group_t,
    measured inward from `face`.

    Returns (slab_shape, sign) where:
        sign = +1  → group_t > base_t  → slab spans base_t..group_t inward,
                     should be FUSED onto the shell (thickens that wall).
        sign = -1  → group_t < base_t  → slab spans group_t..base_t inward,
                     should be CUT from the shell (thins that wall).
        (None, 0) when |group_t - base_t| ~ 0.

    Strategy: translate `face` inward by min(base_t, group_t), then extrude
    inward by |group_t - base_t|. This produces a slab that does NOT overlap
    the outer surface region 0..min(base_t, group_t) — only the differential
    region that needs to be added or removed.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Trsf, gp_Vec

    delta = float(group_t) - float(base_t)
    if abs(delta) < 1e-9:
        return None, 0

    nx, ny, nz = _face_outward_normal(face)
    inx, iny, inz = -nx, -ny, -nz

    start_depth = min(float(base_t), float(group_t))
    extent = abs(delta)

    # Translate the face inward by start_depth.
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(inx * start_depth, iny * start_depth, inz * start_depth))
    tform = BRepBuilderAPI_Transform(face, trsf, True)
    if not tform.IsDone():
        raise RuntimeError(
            "shell_variable_thickness: correction face translate failed"
        )
    translated = tform.Shape()

    # Extrude inward by `extent`.
    vec = gp_Vec(inx * extent, iny * extent, inz * extent)
    prism = BRepPrimAPI_MakePrism(translated, vec, False, True)
    if not prism.IsDone():
        raise RuntimeError(
            "shell_variable_thickness: correction-slab MakePrism failed"
        )
    sign = 1 if delta > 0 else -1
    return prism.Shape(), sign


def _build_hollow_shell_no_open(shape, thickness_mm: float):
    """Build a fully-closed hollow shell by inward-offset + cut.

    Used when `default_thickness_mm` is set and no faces are flagged as open
    — OCCT's `MakeThickSolidByJoin` with no closing faces returns just an
    offset shell (faces only, no solid), so we manually subtract the inner
    offset solid from the original.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepOffset import BRepOffset_Mode
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.GeomAbs import GeomAbs_JoinType
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    maker = BRepOffsetAPI_MakeOffsetShape()
    try:
        maker.PerformByJoin(
            shape,
            -float(thickness_mm),
            1e-3,
            BRepOffset_Mode.BRepOffset_Skin,
            False,
            False,
            GeomAbs_JoinType.GeomAbs_Arc,
            False,
        )
    except Exception as e:
        raise RuntimeError(
            f"shell_variable_thickness: inward offset failed: {e}"
        ) from e
    if not maker.IsDone():
        raise RuntimeError(
            "shell_variable_thickness: inward offset IsDone=False "
            f"(thickness={thickness_mm} mm)"
        )

    offset_shape = maker.Shape()
    shell_it = TopExp_Explorer(offset_shape, TopAbs_SHELL)
    if not shell_it.More():
        raise RuntimeError(
            "shell_variable_thickness: inward offset produced no shell"
        )
    inner_shell = TopoDS.Shell_s(shell_it.Current())
    mks = BRepBuilderAPI_MakeSolid(inner_shell)
    if not mks.IsDone():
        raise RuntimeError(
            "shell_variable_thickness: MakeSolid from inner shell failed"
        )
    inner_solid = mks.Solid()

    cut = BRepAlgoAPI_Cut(shape, inner_solid)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError(
            "shell_variable_thickness: cut (outer − inner_offset) failed"
        )
    return cut.Shape()


@skill(
    name="shell_variable_thickness",
    category="modify/curvature",
    level="atomic",
    summary="Hollow a solid into a shell with per-face thickness. Faces in the "
            "thickness map are walled at the given thickness; other faces are "
            "either removed (open) or take a default thickness. Useful for "
            "enclosures whose camera deck or back cover must be thicker than "
            "the side walls.",
    selector_kinds=["faces"],
    history_rules={
        "all_faces":      HistoryRule.MODIFIED_INHERIT,
        "shell_walls":    HistoryRule.GENERATED_NEW,
        "removed_volume": HistoryRule.CONSUMED,
    },
    produces_features=["shell"],
    preserves=["outer_envelope"],
    manufacturing={
        "cnc_3axis":         {"min_wall_mm": 0.5},
        "injection_mold_pa": {"min_wall_mm": 0.8, "min_draft_deg": 0.5},
        "die_cast_al":       {"min_wall_mm": 1.0, "min_draft_deg": 1.0},
    },
    failure_modes=[
        "fm.shell_self_intersection",
        "fm.shell_thickness_too_large",
        "fm.no_face_matched",
    ],
    cost_hint=0.5,
    post_conditions=[PostCondition(kind="volume_decreased")],
)
class ShellVariableThickness(SkillBase):
    class Args(BaseModel):
        face_thickness_map: list[FaceThicknessEntry] = Field(default_factory=list)
        default_thickness_mm: float | None = Field(default=None)

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from build123d import Part
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

        from phone_designer.skills._resolvers import resolve_faces

        shape = body.wrapped if hasattr(body, "wrapped") else body

        # ── 1. resolve every map entry to its face(s) ───────────────────────
        resolved: list[tuple[list, float]] = []  # (faces, thickness)
        for entry in args.face_thickness_map:
            faces = resolve_faces(shape, entry.face_selector, body=body)
            if not faces:
                raise RuntimeError(
                    f"shell_variable_thickness: face_selector matched 0 — "
                    f"{entry.face_selector.model_dump()}"
                )
            resolved.append((faces, float(entry.thickness_mm)))

        # face id → assigned thickness (priority = order in map; later wins).
        # We hash the OCCT face directly — TopoDS_Face supports __hash__ in
        # current OCP. Equality is location-aware so the same face object
        # always hashes consistently.
        thickness_by_id: dict[int, float] = {}
        face_by_id: dict[int, Any] = {}
        for faces, t in resolved:
            for f in faces:
                fid = hash(f)
                thickness_by_id[fid] = t
                face_by_id[fid] = f

        # ── 2. classify EVERY face on the body ──────────────────────────────
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_MapOfShape

        all_faces = []
        seen = TopTools_MapOfShape()
        it = TopExp_Explorer(shape, TopAbs_FACE)
        while it.More():
            f = it.Current()
            if not seen.Contains(f):
                seen.Add(f)
                all_faces.append(TopoDS.Face_s(f))
            it.Next()

        open_faces = []      # not in map, default is None → these are removed
        groups: dict[float, list] = {}  # thickness → list of faces
        if args.default_thickness_mm is not None:
            default_t = float(args.default_thickness_mm)
            groups[default_t] = []

        for f in all_faces:
            fid = hash(f)
            if fid in thickness_by_id:
                t = thickness_by_id[fid]
                groups.setdefault(t, []).append(f)
            else:
                if args.default_thickness_mm is None:
                    open_faces.append(f)
                else:
                    groups[float(args.default_thickness_mm)].append(f)

        if not groups:
            raise RuntimeError(
                "shell_variable_thickness: no thickness assigned to any face — "
                "supply face_thickness_map and/or default_thickness_mm"
            )

        # ── 3. pick the base thickness (most members) ───────────────────────
        if args.default_thickness_mm is not None \
                and float(args.default_thickness_mm) in groups:
            base_t = float(args.default_thickness_mm)
        else:
            base_t = max(groups.keys(), key=lambda t: len(groups[t]))

        # ── 4. base hollow shell ────────────────────────────────────────────
        # `MakeThickSolidByJoin` is the OCCT way when at least one face is
        # "open" (becomes the shell opening). When the user wants a fully
        # closed hollow shell (default thickness set, no open faces), OCCT
        # returns only an offset surface (not a hollow solid), so we manually
        # subtract the inward-offset solid from the original.
        if open_faces:
            shelled = _shell_with_closing_faces(shape, open_faces, base_t)
        else:
            shelled = _build_hollow_shell_no_open(shape, base_t)

        # ── 5. correction passes for every non-base group ───────────────────
        # For each face in a non-base group we build a "correction slab" that
        # spans only the DIFFERENTIAL depth between base_t and group_t — see
        # `_face_correction_slab`. Fuse if thicker than base, cut if thinner.
        result_shape = shelled
        for t, faces in groups.items():
            if abs(t - base_t) < 1e-9:
                continue
            for f in faces:
                slab, sign = _face_correction_slab(f, base_t, t)
                if slab is None:
                    continue
                if sign > 0:
                    # thicker than base: add slab inward → fuse ON shell
                    op = BRepAlgoAPI_Fuse(result_shape, slab)
                else:
                    # thinner than base: remove slab inward → cut FROM shell
                    op = BRepAlgoAPI_Cut(result_shape, slab)
                op.Build()
                if not op.IsDone():
                    raise RuntimeError(
                        f"shell_variable_thickness: correction op failed for "
                        f"thickness {t} (delta={delta:+.3f} mm)"
                    )
                result_shape = op.Shape()

        history = EntityHistoryMap(
            rules={
                "all_faces":      HistoryRule.MODIFIED_INHERIT,
                "shell_walls":    HistoryRule.GENERATED_NEW,
                "removed_volume": HistoryRule.CONSUMED,
            },
        )
        return SkillResult(body=Part(result_shape), history=history)
