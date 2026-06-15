"""recover_freeform_solid — atomic, read-only.

FREEFORM RE-SOLIDIFY (2026-06-15). Sew the body's COMPLETE boundary face set
into a watertight freeform SOLID via the proven OCCT 7.8 recipe, and report the
solid's geometry fidelity vs the original body.

HONEST FRAMING — READ THIS FIRST
--------------------------------
This skill RE-SOLIDIFIES the imported boundary faces. It recovers the body's
TRUE outer geometry near-losslessly (hausdorff ~0, volume == original) because
it REUSES THE BODY'S OWN FACES — it sews the exact B-rep boundary the importer
already carries into a closed solid. It is therefore the freeform analogue of a
base-stock recovery: distinct from, and complementary to, the primitive
(box / cylinder / sphere / torus) base and the silhouette-extrude
(``extrude_profile_world``) base.

It is NOT, and does NOT claim to be:
  * a parametric rebuild-from-catalog (it fits no primitive, recovers no
    feature tree, and exposes no editable parameters);
  * a generative / approximating surface fit (no B-spline is re-fit, no
    deviation is introduced — the faces are the originals);
  * a feature-removing operation (every pocket / hole / boss already present in
    the boundary stays present, because the boundary is reused verbatim).

The win is a HAUSDORFF claim: the re-solidified boundary reproduces the part's
real shape to ~0 mm where a bbox box base is off by tens of mm. It is the right
base ONLY when the goal is "recover the as-imported outer solid", not when the
goal is an editable parametric model.

WHY THE COMPLETE FACE SET (not the B-spline subset)
---------------------------------------------------
A prior analysis WRONGLY concluded "B-spline freeform → watertight solid is a
fundamental limit of OCCT 7.8". That was over-claimed. OCCT 7.8 sews and
solidifies B-spline boundaries fine. The earlier failure (see
``fit_bspline_surface_patch``) sewed only the 62% B-spline FACE SUBSET of the
boundary — an incomplete, genuinely-open boundary that can never close. Sewing
the COMPLETE face set closes perfectly (Ventilator: 305 faces → 1 shell →
IsValid, volume EXACTLY == original, hausdorff 0.0 mm vs 20.1 mm for the box
base). ``fit_bspline_surface_patch`` measures the B-spline SURFACE region only;
this skill recovers the whole SOLID.

PROVEN RECIPE
-------------
  1. Inventory every boundary face (``_all_faces``).
  2. ``BRepBuilderAPI_Sewing(tol_mm)`` (default 0.1 mm) — add each face, Perform.
  3. Pick the LARGEST-area ``TopAbs_SHELL`` of the sewn shape.
  4. ``ShapeFix_Shell.Perform()`` → fixed shell.
  5. ``BRepBuilderAPI_MakeSolid(shell)`` → ``ShapeFix_Solid.Perform()``.
  6. Gate on ``BRepCheck_Analyzer(solid).IsValid()``.

HONEST OUTCOMES — and ONLY these:
  * SOLIDIFIED — a watertight VALID solid forms. ``solidified=True`` with the
    recovered solid attached in ``extras['freeform_solid']['part']`` (a
    build123d ``Part``) so the planner can consume it as a base. The hausdorff
    vs the original body is reported (typically ~0).
  * NOT SOLIDIFIED — the boundary is GENUINELY open after ``ShapeFix`` (or
    ``BRepCheck`` rejects the solid). ``solidified=False`` + the honest
    open-shell deviation of the sewn shell vs the original body. NO fake solid
    is fabricated.

extras schema:
    {"freeform_solid": {
        "solidified": bool,
        "hausdorff_vs_original_mm": float | None,  # solid (or open shell) vs orig
        "volume_mm3": float | None,                # recovered solid volume
        "original_volume_mm3": float | None,
        "volume_ratio": float | None,              # recovered / original
        "n_faces_sewn": int,
        "n_shells": int,
        "is_valid": bool,
        "sew_tol_mm": float,
        # solidified=False only — honest open-shell surface deviation:
        "open_shell_deviation_vs_body": {hausdorff_mm, rms_mm, p95_mm, n} | None,
        "part": build123d.Part | None,             # the recovered solid (extras)
        "fidelity_basis": str,
     }}

body 는 변경하지 않는다 (post ``body_present``).
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


def _shell_area(shell) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shell, p)
    return float(p.Mass())


def _shape_volume(shape) -> float | None:
    """Signed-magnitude volume (mm³) or None on failure / ~0."""
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        p = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, p)
        v = abs(float(p.Mass()))
        return v if v > 1e-12 else None
    except Exception:
        return None


def _sew_faces(faces, tol_mm: float):
    """Sew ``faces`` into a single (possibly multi-shell) sewn shape — the
    proven recipe step 2 (BRepBuilderAPI_Sewing of the COMPLETE face set)."""
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


def recover_solid_from_boundary(shape, sew_tol_mm: float = 0.1):
    """Re-solidify ``shape``'s COMPLETE boundary via the proven recipe.

    Returns ``(solid_or_None, sewn_shape, shells, fixed_shell_or_None)``:
      * ``solid`` is a watertight ``BRepCheck``-VALID ``TopoDS_Solid`` or
        ``None`` when the boundary stays genuinely open after ``ShapeFix``;
      * ``sewn_shape`` / ``shells`` / ``fixed_shell`` are surfaced so the caller
        can report the open-shell deviation honestly when ``solid is None``.

    Shared with the planner's freeform-shell base candidate so the metric is
    computed identically in the skill and the A/B revert-guard.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_Shell, ShapeFix_Solid

    sewed = _sew_faces_from_shape(shape, sew_tol_mm)
    shells = _shells(sewed)
    if not shells:
        return (None, sewed, [], None)

    # Proven recipe step 3 — pick the LARGEST-area shell (the outer boundary;
    # any tiny stray shell from a non-manifold seam is ignored).
    shells_sorted = sorted(shells, key=_shell_area, reverse=True)
    shell = shells_sorted[0]

    # Step 4 — ShapeFix_Shell on the chosen shell.
    fixed_shell = shell
    try:
        sfs = ShapeFix_Shell(shell)
        sfs.Perform()
        fixed = sfs.Shell()
        if fixed is not None and not fixed.IsNull():
            fixed_shell = fixed
    except Exception:
        pass

    # Step 5 — MakeSolid + ShapeFix_Solid.
    try:
        ms = BRepBuilderAPI_MakeSolid(fixed_shell)
        ms.Build()
        if not ms.IsDone():
            return (None, sewed, shells_sorted, fixed_shell)
        solid = ms.Solid()
        try:
            sfsol = ShapeFix_Solid(solid)
            sfsol.Perform()
            fixed_solid = sfsol.Solid()
            if fixed_solid is not None and not fixed_solid.IsNull():
                solid = fixed_solid
        except Exception:
            pass
    except Exception:
        return (None, sewed, shells_sorted, fixed_shell)

    # Step 6 — BRepCheck.IsValid gate. Anything short of a watertight VALID
    # solid is an honest failure — NO fake solid is returned.
    try:
        if not BRepCheck_Analyzer(solid).IsValid():
            return (None, sewed, shells_sorted, fixed_shell)
    except Exception:
        return (None, sewed, shells_sorted, fixed_shell)

    return (solid, sewed, shells_sorted, fixed_shell)


def _sew_faces_from_shape(shape, sew_tol_mm: float):
    from phone_designer.skills._resolvers import _all_faces
    faces = _all_faces(shape)
    return _sew_faces(faces, sew_tol_mm)


def _directed_hausdorff(a_shape, b_shape) -> dict | None:
    """Bidirectional tessellated deviation (hausdorff / rms / p95 mm, n) between
    two OCCT shapes — reuses geometry_deviation's machinery so the metric is
    identical to the standalone skill. Returns ``None`` on failure."""
    try:
        import numpy as np

        from phone_designer.skills.inspect import geometry_deviation as _gd

        _gd._tessellate(a_shape, 0.5)
        _gd._tessellate(b_shape, 0.5)
        av, at = _gd._triangle_soup(a_shape)
        bv, bt = _gd._triangle_soup(b_shape)
        if len(av) == 0 or len(bv) == 0:
            return None
        d1, _ = _gd._directed_deviation(av, _gd._TriGrid(bt), bv, 20000)
        d2, _ = _gd._directed_deviation(bv, _gd._TriGrid(at), av, 20000)
        alld = np.concatenate([d1, d2])
        return {
            "hausdorff_mm": round(float(alld.max()), 6),
            "rms_mm": round(float(np.sqrt((alld * alld).mean())), 6),
            "p95_mm": round(float(np.percentile(alld, 95.0)), 6),
            "n": int(len(alld)),
        }
    except Exception:
        return None


@skill(
    name="recover_freeform_solid",
    category="inspect",
    level="atomic",
    summary="RE-SOLIDIFY the body's COMPLETE boundary face set into a "
            "watertight freeform SOLID via the proven OCCT 7.8 sew recipe "
            "(largest shell → ShapeFix_Shell → MakeSolid → ShapeFix_Solid → "
            "BRepCheck.IsValid). Recovers the TRUE outer geometry "
            "near-losslessly (hausdorff ~0, volume == original) by REUSING the "
            "body's own faces — the freeform analogue of a base-stock recovery, "
            "NOT a parametric rebuild. If the boundary is genuinely open it "
            "reports solidified=False + an honest open-shell deviation; no fake "
            "solid. Body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["freeform_solid"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class RecoverFreeformSolid(SkillBase):
    class Args(BaseModel):
        sew_tol_mm: float = Field(
            default=0.1, gt=0.0, le=5.0,
            description="BRepBuilderAPI_Sewing tolerance (mm). Boundary faces "
                        "whose shared edges fall within this band are merged "
                        "into one shell. The proven 0.1 mm default closes "
                        "Ventilator exactly; larger values close wider gaps "
                        "but risk fusing distinct seams.",
        )
        max_face_count: int | None = Field(
            default=DEFAULT_MAX_FACE_COUNT,
            description="Skip when the body has more than this many faces "
                        "(returns freeform_solid={'skipped': True, ...}). None "
                        "disables the guard — a multi-thousand-face mesh shell "
                        "must not hang the sewing.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills._resolvers import _all_faces

        shape = _occt_shape(body)

        report: dict[str, Any] = {
            "solidified": False,
            "hausdorff_vs_original_mm": None,
            "volume_mm3": None,
            "original_volume_mm3": None,
            "volume_ratio": None,
            "n_faces_sewn": 0,
            "n_shells": 0,
            "is_valid": False,
            "sew_tol_mm": float(args.sew_tol_mm),
            "open_shell_deviation_vs_body": None,
            "part": None,
            "fidelity_basis": None,
        }

        try:
            all_faces = _all_faces(shape)
        except Exception:
            all_faces = []
        report["n_faces_sewn"] = len(all_faces)
        report["original_volume_mm3"] = (
            round(v, 4) if (v := _shape_volume(shape)) is not None else None
        )

        if args.max_face_count is not None and len(all_faces) > args.max_face_count:
            report.update({
                "skipped": True,
                "reason": "too_big",
                "face_count": len(all_faces),
                "limit": args.max_face_count,
            })
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"freeform_solid": report},
            )

        if not all_faces:
            report["reason"] = "no_faces"
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"freeform_solid": report},
            )

        # ── proven recipe: sew COMPLETE face set → largest shell → solid ────
        try:
            solid, _sewed, shells, fixed_shell = recover_solid_from_boundary(
                shape, args.sew_tol_mm,
            )
        except Exception as exc:
            report["reason"] = f"sew_failed: {type(exc).__name__}"
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"freeform_solid": report},
            )
        report["n_shells"] = len(shells)

        if solid is not None:
            # SOLIDIFIED — watertight VALID solid. Report the solid fidelity
            # (hausdorff vs the ORIGINAL body — ~0 because the faces are the
            # originals) and attach the recovered Part for the planner.
            from build123d import Part

            report["is_valid"] = True
            vol = _shape_volume(solid)
            report["volume_mm3"] = round(vol, 4) if vol is not None else None
            orig_v = _shape_volume(shape)
            if vol is not None and orig_v not in (None, 0.0):
                report["volume_ratio"] = round(vol / orig_v, 6)
            dev = _directed_hausdorff(solid, shape)
            report["hausdorff_vs_original_mm"] = (
                dev["hausdorff_mm"] if dev else None
            )
            report["solidified"] = True
            report["fidelity_basis"] = "resolidified_boundary_vs_original_body"
            # Attach the recovered solid (build123d Part) in extras so the
            # planner can consume it as a base. The skill body itself is
            # returned UNCHANGED (read-only contract).
            report["part"] = Part(solid)
            report["shape"] = solid
            return SkillResult(
                body=body, history=EntityHistoryMap(),
                extras={"freeform_solid": report},
            )

        # ── HONEST FALLBACK: boundary genuinely open after ShapeFix ─────────
        # NO fake solid. Report the open-shell surface deviation of the sewn
        # boundary vs the original body so the caller can SEE how close the
        # open shell is, while claiming NO solid fidelity.
        report["solidified"] = False
        report["is_valid"] = False
        report["fidelity_basis"] = "open_shell_surface_only"
        report["reason"] = "open_shell_no_solid"
        if fixed_shell is not None:
            dev = _directed_hausdorff(fixed_shell, shape)
            report["open_shell_deviation_vs_body"] = dev
            report["hausdorff_vs_original_mm"] = (
                dev["hausdorff_mm"] if dev else None
            )
        return SkillResult(
            body=body, history=EntityHistoryMap(),
            extras={"freeform_solid": report},
        )
