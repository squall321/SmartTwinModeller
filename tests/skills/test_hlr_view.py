"""hlr_view — exact hidden-line-removal projection (HLRBRep_Algo) + layered DXF.

Track 2-1 pins (plans/NEXT_ROADMAP.md §2-1):
  * the retired-risk probe, REPRODUCED through the skill: box-with-hole front
    view → visible 4 / hidden 9 / outline 1 on the pinned OCP 7.8;
  * an OCCLUSION case — edges of a boss that are VISIBLE when the boss stands
    alone become HIDDEN when a plate blocks the view;
  * the honest fallback: over the face budget → brute silhouette with
    label='non_cut_ready' and NO hidden/outline claims;
  * dxf_export source='hlr' → VISIBLE and HIDDEN entities on separate DXF
    layers (dashed hidden linetype).

All pins are analytic (box/cylinder edge counts + exact extents).
"""
from __future__ import annotations

import json

import pytest
from build123d import Align, Box, Cylinder, Part, Pos

from phone_designer.skills.inspect.hlr_view import HlrView, sheet_basis
from phone_designer.skills.io.dxf_export import DxfExport

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


@pytest.fixture(scope="module")
def box_with_hole():
    """80×60×30 box, ⌀16 through-hole along Z — the roadmap probe geometry."""
    return Part() + Box(80, 60, 30, align=_C) - Cylinder(8, 40, align=_C)


@pytest.fixture(scope="module")
def front_view(box_with_hole):
    """Front view (camera at -Y, view_direction +Y), computed once."""
    return HlrView().apply(box_with_hole,
                           {"view_direction": [0, 1, 0]}).extras


# ── the probe pin ────────────────────────────────────────────────────────────

def test_probe_pin_visible4_hidden9_outline1(front_view):
    # plans/NEXT_ROADMAP.md §2-1: the HLR-works-on-OCP-7.8 risk was retired by
    # this exact probe — visible 4 (front rect), hidden 9 (back rect ×4 +
    # hole circles split ×4 + 1 silhouette-adjacent), outline 1 (the hole
    # cylinder's hidden outline).
    assert front_view["mode"] == "hlr"
    assert front_view["label"] == "hlr"
    assert front_view["n_visible"] == 4
    assert front_view["n_hidden"] == 9
    assert front_view["n_outline"] == 1
    # outline bucket split: the hole outline is HIDDEN (inside the box).
    assert len(front_view["outline"]["visible_polylines_2d"]) == 0
    assert len(front_view["outline"]["hidden_polylines_2d"]) == 1


def test_front_view_frame_and_extent_analytic(front_view):
    # sheet frame: u = world +X (±40), v = world +Z (±15) — exact box halves.
    assert front_view["extent_uv"] == [-40.0, -15.0, 40.0, 15.0]


def test_right_view_frame_analytic(box_with_hole):
    # camera at +X (view_direction -X), up +Z: u = world +Y (±30), v = +Z (±15).
    ex = HlrView().apply(box_with_hole, {
        "view_direction": [-1, 0, 0], "up_hint": [0, 0, 1]}).extras
    assert ex["extent_uv"] == [-30.0, -15.0, 30.0, 15.0]
    u, v, z = sheet_basis((-1, 0, 0), (0, 0, 1))
    assert u == pytest.approx((0.0, 1.0, 0.0))
    assert v == pytest.approx((0.0, 0.0, 1.0))
    assert z == pytest.approx((1.0, 0.0, 0.0))


def test_extras_strict_json_safe(front_view):
    json.dumps(front_view)  # no inf/nan/tuple-key leakage


# ── occlusion: hidden edges appear when a boss blocks the view ──────────────

def _in_boss_band(poly, band=5.001):
    return all(abs(u) <= band and abs(v) <= band for (u, v) in poly)


def test_occlusion_boss_edges_hidden_behind_plate():
    plate = Box(40, 2, 40, align=_C)                # y ∈ [-1, 1]
    boss = Pos(0, 6, 0) * Box(10, 10, 10, align=_C)  # y ∈ [1, 11], behind
    boss_alone = HlrView().apply(Part() + boss,
                                 {"view_direction": [0, 1, 0]}).extras
    fused = HlrView().apply(Part() + plate + boss,
                            {"view_direction": [0, 1, 0]}).extras

    # alone: the boss's projected rim (|u|,|v| ≤ 5) is VISIBLE (4 edges).
    assert sum(1 for p in boss_alone["visible_polylines_2d"]
               if _in_boss_band(p)) == 4
    # blocked by the plate: ONLY the plate outline stays visible…
    assert fused["n_visible"] == 4
    assert fused["extent_uv"] == [-20.0, -20.0, 20.0, 20.0]
    assert sum(1 for p in fused["visible_polylines_2d"]
               if _in_boss_band(p)) == 0
    # …and the boss edges re-appear as HIDDEN lines (8 in the boss band).
    assert sum(1 for p in fused["hidden_polylines_2d"]
               if _in_boss_band(p)) == 8


# ── guard: face budget → honest silhouette fallback ─────────────────────────

def test_face_budget_falls_back_to_non_cut_ready(box_with_hole):
    ex = HlrView().apply(box_with_hole, {
        "view_direction": [0, 1, 0], "max_face_count": 1}).extras
    assert ex["mode"] == "silhouette_fallback"
    assert ex["label"] == "non_cut_ready"
    # brute projection: ALL 15 edges (12 box + 2 circles + 1 seam) land in
    # 'visible'; NO hidden/outline claims are made.
    assert ex["n_visible"] == 15
    assert ex["n_hidden"] == 0
    assert ex["n_outline"] == 0
    assert "max_face_count" in ex["note"]
    # fallback stays frame-consistent with the HLR path (same u/v basis).
    assert ex["extent_uv"] == [-40.0, -15.0, 40.0, 15.0]


# ── structured refusals ──────────────────────────────────────────────────────

def test_zero_view_direction_refused(box_with_hole):
    with pytest.raises(ValueError, match="fm.bad_view_direction"):
        HlrView().apply(box_with_hole, {"view_direction": [0, 0, 0]})


def test_none_body_refused():
    with pytest.raises(ValueError, match="fm.empty_view"):
        HlrView().apply(None, {"view_direction": [0, 1, 0]})


def test_empty_compound_refused():
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp = TopoDS_Compound()
    BRep_Builder().MakeCompound(comp)
    with pytest.raises(ValueError, match="fm.empty_view"):
        HlrView().apply(comp, {"view_direction": [0, 1, 0]})


# ── dxf_export source='hlr': VISIBLE / HIDDEN on separate layers ─────────────

def test_dxf_hlr_two_layers(box_with_hole, tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "front.dxf"
    ex = DxfExport().apply(box_with_hole, {
        "path": str(path), "source": "hlr",
        "plane_normal": [0, 1, 0]}).extras
    # extras pin: 4 visible + (9 hidden + 1 hidden outline) = 10 on HIDDEN.
    assert ex["layers"] == {"VISIBLE": 4, "HIDDEN": 10}
    assert ex["n_polylines"] == 14
    assert ex["hlr_mode"] == "hlr" and ex["label"] == "hlr"

    doc = ezdxf.readfile(str(path))
    names = {layer.dxf.name for layer in doc.layers}
    assert {"VISIBLE", "HIDDEN"} <= names
    by_layer: dict[str, int] = {}
    for e in doc.modelspace():
        by_layer[e.dxf.layer] = by_layer.get(e.dxf.layer, 0) + 1
    assert by_layer == {"VISIBLE": 4, "HIDDEN": 10}
    # hidden lines render dashed (ezdxf setup has no 'HIDDEN' linetype —
    # the writer falls back to DASHED, never silently Continuous).
    assert doc.layers.get("HIDDEN").dxf.linetype == "DASHED"


def test_dxf_hlr_fallback_label_passthrough(box_with_hole, tmp_path):
    """When hlr_view falls back, the DXF extras carry non_cut_ready and the
    HIDDEN layer is honestly EMPTY (still present in the file)."""
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "fallback.dxf"
    ex = DxfExport().apply(box_with_hole, {
        "path": str(path), "source": "hlr",
        "plane_normal": [0, 1, 0], "hlr_max_face_count": 1}).extras
    assert ex["hlr_mode"] == "silhouette_fallback"
    assert ex["label"] == "non_cut_ready"
    assert ex["layers"]["HIDDEN"] == 0
    doc = ezdxf.readfile(str(path))
    assert "HIDDEN" in {layer.dxf.name for layer in doc.layers}
