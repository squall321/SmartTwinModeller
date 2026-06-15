"""PILLAR FREEFORM phase-4 tests (2026-06-15) — the three frontier items.

ITEM 1 — hausdorff-keyed base revert-guard (``_accept_alt_base``): the
silhouette profile base for ``rounded_plate`` is KEPT because its hausdorff is
strictly tighter than the box's, EVEN THOUGH match_ratio drops. The old guard
reverted it on the match-count metric — fake-accuracy in reverse.

ITEM 2 — true sweep path + profile recovery
(``extract_feature_catalog._recover_sweep_path_and_profile``): on a real
``Geom_SurfaceOfLinearExtrusion`` prism the recovered path is the
extrusion-direction-projected bbox span (NOT the fabricated 3-point bbox
diagonal) and a real perpendicular section is recovered as the profile, gated by
``BRepCheck.IsValid``.

ITEM 3 — ``fit_bspline_surface_patch`` honest fallback: the discarded B-spline
faces of a real freeform body (Ventilator) sew into VALID but OPEN shells that
do NOT solidify in OCCT 7.8, so the skill emits an open-shell deviation report,
keeps the box base (``base_step is None``), and claims NO solid fidelity.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT = Path(__file__).resolve().parents[2]
_FIXTURES = _PROJECT / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))


# ──────────────────────────────────────────────────────────────────────────────
# ITEM 1 — hausdorff-keyed revert-guard keeps the geometrically-tighter base.


def test_accept_alt_base_keeps_strictly_better_hausdorff(monkeypatch):
    """``_accept_alt_base`` keeps the candidate when its hausdorff is strictly
    better — regardless of match_ratio (ITEM 1)."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    box_plan = {"steps": [{"id": "s_base", "skill": "box", "args": {}}]}

    # box: match 0.444, haus 2.523 — candidate: match DROPS to 0.333 but
    # haus improves to 0.272 (the rounded_plate situation). The old guard
    # (match >= box AND haus <) would REJECT; ITEM 1 must KEEP.
    scores = {"box": (0.444, 2.523), "extrude_profile_world": (0.333, 0.272)}

    def fake_score(plan_dict, body, bbox):
        sid = plan_dict["steps"][0]["skill"]
        return scores[sid]

    monkeypatch.setattr(P, "_score_reconstruction", fake_score)
    kept = P._accept_alt_base(
        box_plan, "extrude_profile_world", {"foo": 1}, body=object(), bbox=(0,) * 6
    )
    assert kept is not None, "tighter-hausdorff candidate must be KEPT"
    assert kept["steps"][0]["skill"] == "extrude_profile_world"


def test_accept_alt_base_reverts_worse_hausdorff(monkeypatch):
    """Revert-safety: a candidate whose hausdorff is NOT better keeps the box —
    even if its match_ratio is higher (prismatic corpus protection)."""
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )

    box_plan = {"steps": [{"id": "s_base", "skill": "box", "args": {}}]}
    # candidate has BETTER match but WORSE (equal) hausdorff → must revert.
    scores = {"box": (0.50, 1.00), "extrude_profile_world": (0.90, 1.00)}
    monkeypatch.setattr(
        P, "_score_reconstruction",
        lambda pd, b, bb: scores[pd["steps"][0]["skill"]],
    )
    kept = P._accept_alt_base(
        box_plan, "extrude_profile_world", {}, body=object(), bbox=(0,) * 6
    )
    assert kept is None, "equal/worse-hausdorff candidate must revert to box"


@pytest.mark.slow
def test_rounded_plate_auto_keeps_profile_base():
    """End-to-end: the shipping auto path keeps the profile base for
    rounded_plate (hausdorff strictly better than box)."""
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer import (
        plan_from_feature_catalog as P,
    )
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    step = _FIXTURES / "freeform" / "rounded_plate.step"
    if not step.is_file():
        pytest.skip("rounded_plate.step missing — build_freeform_fixtures first")
    body = ImportStep().apply(None, {"path": str(step)}).body
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    bb = cat["initial_bbox_mm"]
    bbox = tuple(float(c) for c in bb[:6])
    box_plan = P._build_plan(
        cat, body=body, base_step_kind="box", base_profile_mode="off"
    )
    base_l, base_w, base_h = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
    kept = P.accept_freeform_base(
        box_plan, body, bbox, base_l=base_l, base_w=base_w, base_h=base_h,
    )
    assert kept is not None, "rounded_plate must keep a non-box base now"
    base_step = next(
        s for s in kept["steps"] if str(s["id"]).startswith("s_base")
    )
    assert base_step["skill"] == "extrude_profile_world"


# ──────────────────────────────────────────────────────────────────────────────
# ITEM 2 — real sweep path + profile from a true surface-of-extrusion solid.


def _spline_extrusion_prism():
    """A closed spline profile extruded along +Z → real Geom_SurfaceOfLinear-
    Extrusion side wall (OCCT keeps it because the boundary is curved)."""
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TColgp import TColgp_HArray1OfPnt

    pts = [(0, 0, 0), (10, 0, 0), (12, 4, 0), (10, 8, 0), (0, 8, 0), (-2, 4, 0)]
    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, (x, y, z) in enumerate(pts, start=1):
        arr.SetValue(i, gp_Pnt(x, y, z))
    interp = GeomAPI_Interpolate(arr, True, 1e-6)  # closed/periodic
    interp.Perform()
    edge = BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    face = BRepBuilderAPI_MakeFace(wire).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 15)).Shape()


def test_sweep_recovery_real_path_and_profile():
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceOfExtrusion

    from phone_designer.skills._resolvers import _all_faces
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        _extrusion_direction,
        _face_bbox_local,
        _recover_sweep_path_and_profile,
    )

    prism = _spline_extrusion_prism()
    faces = _all_faces(prism)
    ext = [
        (fi, f) for fi, f in enumerate(faces)
        if int(BRepAdaptor_Surface(f).GetType()) == int(GeomAbs_SurfaceOfExtrusion)
    ]
    assert ext, "synthetic prism must carry a surface-of-extrusion wall"

    direction = _extrusion_direction(ext)
    assert direction is not None
    # extrusion is along +Z.
    assert abs(direction[2]) > 0.99

    all_bb = None
    for _fi, f in ext:
        bb = _face_bbox_local(f)
        if bb is None:
            continue
        if all_bb is None:
            all_bb = list(bb)
        else:
            for k in range(3):
                all_bb[k] = min(all_bb[k], bb[k])
            for k in range(3, 6):
                all_bb[k] = max(all_bb[k], bb[k])

    rec = _recover_sweep_path_and_profile(prism, ext, all_bb)
    assert rec is not None
    path_points, profile_sketch, profile_d = rec
    # REAL straight 2-point path (not the fabricated 3-point bbox diagonal).
    assert len(path_points) == 2
    # span of the path equals the prism height (15 mm) along Z.
    dz = abs(path_points[1][2] - path_points[0][2])
    assert dz == pytest.approx(15.0, abs=0.5)
    # a real perpendicular section was recovered and passed the IsValid gate.
    assert profile_sketch is not None
    assert profile_sketch.get("kind") in ("polygon", "composite", "bspline_closed")
    assert profile_d and profile_d > 0.0


# ──────────────────────────────────────────────────────────────────────────────
# ITEM 3 — fit_bspline_surface_patch honest open-shell fallback on Ventilator.


@pytest.mark.slow
def test_fit_bspline_patch_open_shell_fallback():
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.inspect.fit_bspline_surface_patch import (
        FitBSplineSurfacePatch,
    )

    vent = _PROJECT / "corpus" / "oem" / "pythonocc__Ventilator.step"
    if not vent.is_file():
        pytest.skip("Ventilator corpus file missing")
    body = ImportStep().apply(None, {"path": str(vent)}).body
    res = FitBSplineSurfacePatch().apply(body, {})
    rep = res.extras["bspline_patch"]

    # The body has real freeform B-spline skin.
    assert rep["bspline_face_count"] > 0
    assert rep["bspline_area_mm2"] > 0.0
    # Honest OCCT 7.8 outcome: the patches do NOT solidify.
    assert rep["solidified"] is False
    # No fake win: box base kept, no solid fidelity / feasible range claimed.
    assert rep["base_step"] is None
    assert rep["feasible_range"] is None
    assert rep["confidence"] == 0.0
    # An honest open-shell deviation report IS produced.
    assert "open_shell_deviation_vs_region" in rep
    # Body returned unchanged (read-only skill).
    assert res.body is body


def test_fit_bspline_patch_no_bspline_is_honest_noop():
    """A plain box body (no B-spline faces) → honest no-op, box base kept."""
    from build123d import Box

    from phone_designer.skills.inspect.fit_bspline_surface_patch import (
        FitBSplineSurfacePatch,
    )

    body = Box(10, 10, 10)
    res = FitBSplineSurfacePatch().apply(body, {})
    rep = res.extras["bspline_patch"]
    assert rep["bspline_face_count"] == 0
    assert rep["solidified"] is False
    assert rep["base_step"] is None
    assert rep.get("reason") == "no_bspline_faces"
