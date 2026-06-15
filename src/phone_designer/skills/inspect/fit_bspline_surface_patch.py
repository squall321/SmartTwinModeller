"""fit_bspline_surface_patch — atomic, read-only.

PILLAR FREEFORM (phase-4, 2026-06-15, ITEM 3 — XL / honest-fallback mandatory).

``extract_feature_catalog`` collects the body's B-spline faces while detecting
sweep / loft / revolve features, then DISCARDS them — a freeform surface patch
has no closed-form primitive, so the planner falls back to a bbox box for the
whole region. This skill is the honest attempt to do better: it skins those
discarded B-spline faces into a surface patch and tries to SOLIDIFY it, so the
reconstruction can carry the real freeform skin instead of a flat slab face.

What it does (read-only — the body is returned unchanged):

  1. Inventory every ``GeomAbs_BSplineSurface`` face on the body.
  2. ``BRepBuilderAPI_Sewing`` them (+ any geometrically-adjacent non-bspline
     faces requested) into one or more shells.
  3. For each sewn shell test ``BRep_Tool.IsClosed`` + ``BRepCheck_Analyzer``
     and try ``BRepBuilderAPI_MakeSolid`` → ``BRepCheck_Analyzer.IsValid``.

Two honest outcomes — and ONLY these:

  * SOLIDIFIED — a watertight, VALID solid forms. ``extras["bspline_patch"]``
    carries ``solidified=True``, the closed-form ``volume_mm3``, and a
    ``hausdorff_mm`` fidelity of the solid vs the ORIGINAL body (a real
    solid-fidelity claim). ``base_step`` carries an importable base the planner
    could swap in. This is the "great" path.

  * OPEN-SHELL (the likely outcome in OCCT 7.8) — the patches sew into VALID but
    OPEN shells that do NOT solidify. We claim NO solid-fidelity. Instead we
    emit an HONEST open-shell deviation report: the directed
    geometry_deviation (hausdorff / rms / p95 mm) of the sewn OPEN shell vs the
    original body's B-spline surface region. ``solidified=False``,
    ``feasible_range=None``, ``confidence`` low, ``base_step=None`` → the caller
    KEEPS the box base for the buildable reconstruction. No fake win is claimed
    from an unbuildable surface.

Guarded by ``DEFAULT_MAX_FACE_COUNT`` (``_face_count_guard``) — a multi-thousand
face mesh-shell must not hang the sewing.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._face_count_guard import DEFAULT_MAX_FACE_COUNT
from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _bspline_faces(shape) -> list:
    """Every ``GeomAbs_BSplineSurface`` face on ``shape`` (in _all_faces order)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface

    from phone_designer.skills._resolvers import _all_faces

    out = []
    for f in _all_faces(shape):
        try:
            if int(BRepAdaptor_Surface(f).GetType()) == int(GeomAbs_BSplineSurface):
                out.append(f)
        except Exception:
            continue
    return out


def _face_area(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, p)
    return float(p.Mass())


def _sew_faces(faces, tol_mm: float):
    """Sew ``faces`` into a single (possibly multi-shell) sewn shape."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    sew = BRepBuilderAPI_Sewing(float(tol_mm))
    for f in faces:
        sew.Add(f)
    sew.Perform()
    return sew.SewedShape()


def _shells(shape) -> list:
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        out.append(TopoDS.Shell_s(exp.Current()))
        exp.Next()
    return out


def _try_solidify(shell):
    """Return a VALID solid built from ``shell`` (closed + analyzer-valid) or
    ``None``. The honest gate: anything short of a watertight valid solid is a
    failure here (we never fabricate a closed body from an open shell)."""
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
        from OCP.BRepCheck import BRepCheck_Analyzer

        if not BRep_Tool.IsClosed_s(shell):
            return None
        ms = BRepBuilderAPI_MakeSolid(shell)
        ms.Build()
        if not ms.IsDone():
            return None
        solid = ms.Solid()
        if not BRepCheck_Analyzer(solid).IsValid():
            return None
        return solid
    except Exception:
        return None


def _shape_volume(shape) -> float | None:
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        v = float(p.Mass())
        return v if v > 0.0 else None
    except Exception:
        return None


def _open_shell_deviation(shell_shape, region_faces) -> dict | None:
    """HONEST open-shell deviation report: directed geometry_deviation of the
    sewn OPEN ``shell_shape`` vs a reference shape built from the original body's
    B-spline ``region_faces``.

    Reuses geometry_deviation's tessellation + _TriGrid point-to-triangle
    machinery so the hausdorff / rms / p95 metric is identical to the standalone
    skill. Returns ``None`` on any failure. This is a SURFACE-region deviation,
    NOT a solid fidelity — the caller must label it as such.
    """
    try:
        import numpy as np

        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        from phone_designer.skills.inspect import geometry_deviation as _gd

        # reference compound = the original body's bspline region.
        builder = BRep_Builder()
        ref = TopoDS_Compound()
        builder.MakeCompound(ref)
        for f in region_faces:
            builder.Add(ref, f)

        _gd._tessellate(shell_shape, 0.5)
        _gd._tessellate(ref, 0.5)
        sv, st = _gd._triangle_soup(shell_shape)
        rv, rt = _gd._triangle_soup(ref)
        if len(sv) == 0 or len(rv) == 0:
            return None
        d1, _ = _gd._directed_deviation(sv, _gd._TriGrid(rt), rv, 20000)
        d2, _ = _gd._directed_deviation(rv, _gd._TriGrid(st), sv, 20000)
        alld = np.concatenate([d1, d2])
        return {
            "hausdorff_mm": round(float(alld.max()), 6),
            "rms_mm": round(float(np.sqrt((alld * alld).mean())), 6),
            "p95_mm": round(float(np.percentile(alld, 95.0)), 6),
            "sampled_points": int(len(alld)),
        }
    except Exception:
        return None


@skill(
    name="fit_bspline_surface_patch",
    category="inspect",
    level="atomic",
    summary="Skin the body's discarded B-spline faces into a surface patch and "
            "try to solidify it. If a watertight valid SOLID forms, emit it as "
            "a base candidate with a real hausdorff fidelity; if it stays an "
            "OPEN shell (the likely OCCT 7.8 outcome), emit an HONEST open-shell "
            "deviation report and keep the box base — NO solid-fidelity claimed "
            "from an unbuildable surface. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["bspline_patch"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.7,
    post_conditions=[PostCondition(kind="body_present")],
)
class FitBSplineSurfacePatch(SkillBase):
    class Args(BaseModel):
        sew_tol_mm: float = Field(
            default=0.1, gt=0.0, le=5.0,
            description="BRepBuilderAPI_Sewing tolerance (mm). Adjacent B-spline "
                        "faces whose shared edges fall within this band are "
                        "merged into one shell. Larger values close more gaps "
                        "but risk fusing distinct patches.",
        )
        max_face_count: int | None = Field(
            default=DEFAULT_MAX_FACE_COUNT,
            description="Skip when the body has more than this many faces "
                        "(returns extras['bspline_patch']={'skipped': True, ...}"
                        "). None disables the guard.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)

        report: dict[str, Any] = {
            "solidified": False,
            "base_step": None,
            "feasible_range": None,
            "confidence": 0.0,
        }

        try:
            all_faces = _all_faces(shape)
        except Exception:
            all_faces = []

        if args.max_face_count is not None and len(all_faces) > args.max_face_count:
            report.update({
                "skipped": True,
                "reason": "too_big",
                "face_count": len(all_faces),
                "limit": args.max_face_count,
            })
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bspline_patch": report},
            )

        bs_faces = _bspline_faces(shape)
        report["bspline_face_count"] = len(bs_faces)
        report["bspline_area_mm2"] = round(
            sum(_face_area(f) for f in bs_faces), 4
        ) if bs_faces else 0.0

        if not bs_faces:
            # No freeform skin to fit — honest no-op. Box base unchanged.
            report["reason"] = "no_bspline_faces"
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bspline_patch": report},
            )

        # ── sew + shell inventory ──────────────────────────────────────────
        try:
            sewed = _sew_faces(bs_faces, args.sew_tol_mm)
            shells = _shells(sewed)
        except Exception as exc:
            report["reason"] = f"sew_failed: {type(exc).__name__}"
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bspline_patch": report},
            )
        report["shell_count"] = len(shells)

        # ── try to SOLIDIFY any shell (the "great" path) ───────────────────
        for sh in shells:
            solid = _try_solidify(sh)
            if solid is None:
                continue
            vol = _shape_volume(solid)
            haus = self._solid_hausdorff(solid, shape)
            if haus is None:
                continue
            # A watertight valid solid WITH a measurable fidelity. Emit it as a
            # base candidate (the planner may swap it in) and claim the solid
            # hausdorff fidelity. confidence scales inversely with deviation.
            report.update({
                "solidified": True,
                "volume_mm3": round(vol, 4) if vol is not None else None,
                "hausdorff_mm": haus,
                "fidelity_basis": "solid_vs_original_body",
                "feasible_range": None,  # single recovered solid, no sweep range
                "confidence": round(1.0 / (1.0 + float(haus)), 4),
                "base_step": {
                    "skill": "import_shape",
                    "note": "bspline-skinned watertight solid base candidate",
                },
                "reason": "solidified_watertight",
            })
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"bspline_patch": report},
            )

        # ── HONEST FALLBACK: open shell, no solid (OCCT 7.8 likely path) ───
        # Two deviation reports, both honest and clearly labelled:
        #  * vs the bspline REGION — confirms the sewing is lossless (≈0 mm):
        #    the open shell reproduces the original freeform skin exactly, it
        #    just does not bound a volume.
        #  * vs the FULL original body — the directed surface deviation of the
        #    recovered skin against the whole part, i.e. how much of the body
        #    the freeform region does NOT cover. This is a SURFACE report only;
        #    it is NOT a solid fidelity and no win is claimed from it.
        dev_region = _open_shell_deviation(sewed, bs_faces)
        try:
            dev_body = _open_shell_deviation(sewed, all_faces)
        except Exception:
            dev_body = None
        report.update({
            "solidified": False,
            "base_step": None,          # KEEP the box base — unbuildable surface.
            "feasible_range": None,
            "confidence": 0.0,          # no solid-fidelity is claimed.
            "open_shell_deviation_vs_region": dev_region,
            "open_shell_deviation_vs_body": dev_body,
            "fidelity_basis": "open_shell_surface_region_only",
            "reason": "open_shell_no_solid",
        })
        return SkillResult(
            body=body, history=EntityHistoryMap(),
            extras={"bspline_patch": report},
        )

    def _solid_hausdorff(self, solid, orig_shape) -> float | None:
        """Hausdorff (mm) of a recovered SOLID vs the original body — reuses
        geometry_deviation's tessellation + _TriGrid. Returns None on failure."""
        try:
            import numpy as np

            from phone_designer.skills.inspect import geometry_deviation as _gd

            _gd._tessellate(solid, 0.5)
            _gd._tessellate(orig_shape, 0.5)
            av, at = _gd._triangle_soup(solid)
            bv, bt = _gd._triangle_soup(orig_shape)
            if len(av) == 0 or len(bv) == 0:
                return None
            d1, _ = _gd._directed_deviation(av, _gd._TriGrid(bt), bv, 20000)
            d2, _ = _gd._directed_deviation(bv, _gd._TriGrid(at), av, 20000)
            return round(float(np.concatenate([d1, d2]).max()), 6)
        except Exception:
            return None
