"""delete_face_defeature + fill_surface_patch — Tier-2 direct-edit / surfacing ops.

Pins the two general-CAD primitives the repair/surfacing categories lacked:
  * delete_face_defeature — true topological defeaturing (delete faces + heal),
    proven by removing every fillet face from a filleted box and recovering the
    exact sharp-box volume.
  * fill_surface_patch — an N-sided fill surface spanning a caller-selected boundary,
    proven by filling one face's 4-edge loop into a patch of the right area.
"""
from __future__ import annotations

import pytest
from build123d import Align, Box, fillet

from phone_designer.skills.modify_curvature.fill_surface_patch import FillSurfacePatch
from phone_designer.skills.repair.delete_face_defeature import DeleteFaceDefeature


def _vol(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(g.Mass())


def _area(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return g.Mass()


def _cbox(s=20):
    return Box(s, s, s, align=(Align.CENTER, Align.CENTER, Align.CENTER))


# ── delete_face_defeature ────────────────────────────────────────────────────

def test_defeature_removes_fillets_and_heals_to_sharp_box():
    # a 20³ box with a 3mm fillet on every edge has vol < 8000; deleting the fillet
    # faces (the small non-planar ones, area < 80) and healing recovers exactly 8000.
    bf = fillet(_cbox(20).edges(), radius=3)
    assert _vol(bf.wrapped) < 8000
    r = DeleteFaceDefeature().apply(bf, {"face_selector": {"kind": "faces_by_area", "max": 80.0}})
    assert _vol(r.body.wrapped) == pytest.approx(8000.0, rel=1e-4)
    assert r.extras["defeature"]["n_faces_removed"] > 0


def test_defeature_no_faces_selected_refused():
    # a selector matching nothing (area window that excludes all faces) is refused.
    with pytest.raises(ValueError, match="no_faces_selected"):
        DeleteFaceDefeature().apply(_cbox(20),
                                    {"face_selector": {"kind": "faces_by_area", "min": 1e9}})


def test_defeature_volume_change_guard_fires():
    # the max_volume_change_frac guard: removing the fillets recovers 8000 from
    # ~7573 (a ~5.6% change), so a strict 1% cap must REJECT it as too large a heal.
    bf = fillet(_cbox(20).edges(), radius=3)
    with pytest.raises(ValueError, match="defeature_volume_collapse"):
        DeleteFaceDefeature().apply(bf, {
            "face_selector": {"kind": "faces_by_area", "max": 80.0},
            "max_volume_change_frac": 0.01})


# ── fill_surface_patch ───────────────────────────────────────────────────────

def test_fill_patch_spans_a_face_boundary():
    # fill the top (+Z) face's 4-edge loop → a patch surface of area ~400 (20×20).
    fr = FillSurfacePatch().apply(_cbox(20), {
        "boundary_selector": {"kind": "faces_by_normal", "direction": [0, 0, 1], "tol_deg": 5},
        "continuity": "c0"})
    assert fr.extras["fill_surface"]["is_solid"] is False
    assert fr.extras["fill_surface"]["n_boundary_edges"] == 4
    assert _area(fr.body.wrapped) == pytest.approx(400.0, rel=1e-2)


def test_fill_patch_no_boundary_refused():
    # a selector matching no face and no edge is a structured refusal.
    with pytest.raises((ValueError, RuntimeError)):
        FillSurfacePatch().apply(_cbox(20), {
            "boundary_selector": {"kind": "faces_by_area", "min": 1e9},
            "continuity": "g1"})


# ── discoverability ──────────────────────────────────────────────────────────

def test_both_in_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"delete_face_defeature", "fill_surface_patch"} <= names
    df = next(s for s in m["skills"] if s["name"] == "delete_face_defeature")
    assert df["category"] == "repair"
    fp = next(s for s in m["skills"] if s["name"] == "fill_surface_patch")
    assert fp["category"] == "modify/curvature"
