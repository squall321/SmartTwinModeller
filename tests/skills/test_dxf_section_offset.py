"""dxf_export + section_to_sketch + offset_sketch — Tier-2 interop / curve-derivation.

Pins the three "expose an existing engine" ops from the gap roadmap:
  * offset_sketch — 2D parallel offset of a profile (exposes _offset_wire), round-
    tripped through sketch_extrude.
  * section_to_sketch — section a solid → executable SketchSpec (exposes
    recover_section_loop), round-tripped: section→sketch→extrude matches area·height.
  * dxf_export — write the computed 2D loops to a real DXF file (ezdxf).
"""
from __future__ import annotations

import os

import pytest
from build123d import Align, Box

from phone_designer.skills.create.offset_sketch import OffsetSketch
from phone_designer.skills.create.sketch_extrude import SketchExtrude
from phone_designer.skills.inspect.section_to_sketch import SectionToSketch
from phone_designer.skills.io.dxf_export import DxfExport


def _cbox(l, w, h):
    return Box(l, w, h, align=(Align.CENTER, Align.CENTER, Align.CENTER))


def _ext(sketch, h):
    return SketchExtrude().apply(None, {"sketch": sketch, "height_mm": h}).extras["sketch_solid"]


# ── offset_sketch ────────────────────────────────────────────────────────────

def test_offset_sketch_inward_shrinks_profile():
    # a 20×20 square offset inward by 2 → a 16×16 loop; extrude ×5 = 1280 exactly
    # (a straight-sided inward offset has no corner arcs).
    o = OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": -2}).extras
    assert o["sketch"]["kind"] == "polygon"
    assert _ext(o["sketch"], 5)["volume_mm3"] == pytest.approx(1280.0, rel=1e-3)


def test_offset_sketch_outward_grows_profile():
    # offset outward by 2 → bbox 24×24 (corners filleted, so volume a touch under
    # 24·24·5 = 2880 — assert the bbox is exact and volume is in the right band).
    o = OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": 2}).extras
    e = _ext(o["sketch"], 5)
    assert e["bbox_mm"][0] == pytest.approx(24.0, abs=0.1)
    assert e["bbox_mm"][1] == pytest.approx(24.0, abs=0.1)
    assert 2700 < e["volume_mm3"] <= 2880


def test_offset_sketch_zero_distance_refused():
    with pytest.raises(ValueError, match="offset_failed"):
        OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": 0})


# ── section_to_sketch ────────────────────────────────────────────────────────

def test_section_to_sketch_round_trips_area():
    # section a 30×20×10 box at z=0 → a 30×20 loop (area 600); re-extrude ×10 = 6000.
    s = SectionToSketch().apply(_cbox(30, 20, 10),
                                {"plane_origin": [0, 0, 0], "plane_normal": [0, 0, 1]}).extras
    assert s["area_mm2"] == pytest.approx(600.0, rel=1e-3)
    assert _ext(s["sketch"], 10)["volume_mm3"] == pytest.approx(6000.0, rel=1e-3)


def test_section_to_sketch_plane_missing_body_refused():
    with pytest.raises(ValueError, match="empty_section"):
        SectionToSketch().apply(_cbox(10, 10, 10),
                                {"plane_origin": [0, 0, 100], "plane_normal": [0, 0, 1]})


# ── dxf_export ───────────────────────────────────────────────────────────────

def test_dxf_export_writes_section_file(tmp_path):
    out = os.path.join(str(tmp_path), "sec.dxf")
    r = DxfExport().apply(_cbox(30, 20, 10),
                          {"path": out, "source": "section", "plane_normal": [0, 0, 1]}).extras
    assert os.path.exists(out) and os.path.getsize(out) > 0
    assert r["n_polylines"] == 4  # the four edges of the rectangular section


def test_dxf_export_no_curves_refused(tmp_path):
    out = os.path.join(str(tmp_path), "empty.dxf")
    with pytest.raises(ValueError, match="no_curves"):
        DxfExport().apply(_cbox(10, 10, 10),
                          {"path": out, "source": "section", "plane_origin": [0, 0, 100],
                           "plane_normal": [0, 0, 1]})


# ── discoverability ──────────────────────────────────────────────────────────

def test_three_skills_in_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"dxf_export", "section_to_sketch", "offset_sketch"} <= names
