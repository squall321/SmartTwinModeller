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


def test_offset_sketch_tiny_corner_arcs_dedup_to_valid_polygon():
    # +0.005 outward: the r=0.005 corner arcs sample at ~0.33µm spacing — below
    # PolygonSketch's EPS_CLOSE (1µm). These used to ESCAPE as a raw pydantic
    # ValidationError ("polygon vertex 24 and 25 are coincident"); the EPS_CLOSE
    # dedup in _sample_wire_2d now collapses them into a valid polygon.
    o = OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": 0.005}).extras
    assert o["sketch"]["kind"] == "polygon" and o["n_points"] >= 4
    xs = [p[0] for p in o["sketch"]["vertices"]]
    ys = [p[1] for p in o["sketch"]["vertices"]]
    assert max(xs) - min(xs) == pytest.approx(20.01, abs=1e-3)
    assert max(ys) - min(ys) == pytest.approx(20.01, abs=1e-3)


def test_offset_sketch_near_inradius_remnant_succeeds():
    # -9.99 leaves a geometrically valid 0.02×0.02 remnant (still inside the
    # inradius of 10) — the docstring promises success, but sub-EPS_CLOSE sample
    # spacing used to die as a raw ValidationError. Must now succeed.
    o = OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": -9.99}).extras
    assert o["sketch"]["kind"] == "polygon" and o["n_points"] >= 4
    xs = [p[0] for p in o["sketch"]["vertices"]]
    assert max(xs) - min(xs) == pytest.approx(0.02, abs=1e-3)


def test_offset_sketch_collapsed_loop_structured_refusal():
    # -9.9999 collapses the remnant (0.0002×0.0002) below EPS_CLOSE everywhere:
    # the EPS_CLOSE dedup leaves <3 points → the STRUCTURED fm.offset_collapsed,
    # never a raw pydantic ValidationError. (pydantic's ValidationError IS a
    # ValueError subclass, so the match string is what pins the structure.)
    with pytest.raises(ValueError, match="offset_collapsed"):
        OffsetSketch().apply(None, {"sketch": {"kind": "rectangle", "length_mm": 20, "width_mm": 20},
                                    "distance_mm": -9.9999})


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


def test_section_to_sketch_face_guard_reports_too_many_faces(monkeypatch):
    # A face-count-guard SKIP is not an empty section — it must surface as
    # fm.too_many_faces (with the counts + override hint), never as
    # fm.empty_section's misleading "plane may miss the body".
    import phone_designer.skills.inspect._section_curve as sc

    def _skipped(shape, origin, normal, **kw):
        return {"sketch": None, "area_mm2": 0.0, "n_points": 0,
                "closed": False, "n_loops": 0, "loop_kind": None,
                "skipped": True, "reason": "too_big",
                "face_count": 6, "limit": 1}

    monkeypatch.setattr(sc, "recover_section_loop", _skipped)
    with pytest.raises(ValueError, match=r"fm\.too_many_faces") as ei:
        SectionToSketch().apply(_cbox(10, 10, 10),
                                {"plane_origin": [0, 0, 0], "plane_normal": [0, 0, 1]})
    msg = str(ei.value)
    assert "empty_section" not in msg
    assert "6 faces" in msg and "limit 1" in msg
    assert "PHONE_DESIGNER_MAX_FACE_COUNT" in msg


def test_section_to_sketch_declares_too_many_faces_failure_mode():
    assert "fm.too_many_faces" in SectionToSketch.spec.failure_modes


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


def test_dxf_export_blocked_path_fm_dxf_write_failed(tmp_path):
    # A path whose parent component is an existing FILE must surface the
    # declared fm.dxf_write_failed (used to escape as a raw OSError from the
    # unguarded mkdir before the saveas try/except).
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory")
    out = os.path.join(str(blocker), "out.dxf")
    with pytest.raises(ValueError, match=r"fm\.dxf_write_failed"):
        DxfExport().apply(_cbox(10, 10, 2),
                          {"path": out, "source": "section", "plane_normal": [0, 0, 1]})


def test_dxf_silhouette_dedupes_and_drops_degenerate_loops(tmp_path):
    # Brute all-edges projection of a box along Z emits 12 edge polylines:
    # 4 view-parallel verticals collapse to points (degenerate) and the top/
    # bottom rims duplicate each other pairwise — the DXF must contain only
    # the 4 unique segments, not 12 entities (4 of them zero-extent).
    out = os.path.join(str(tmp_path), "sil.dxf")
    r = DxfExport().apply(_cbox(60, 40, 10),
                          {"path": out, "source": "silhouette",
                           "plane_normal": [0, 0, 1]}).extras
    assert r["n_polylines"] == 4
    import ezdxf
    doc = ezdxf.readfile(out)
    polys = list(doc.modelspace().query("LWPOLYLINE"))
    assert len(polys) == 4
    for pl in polys:
        pts = [(p[0], p[1]) for p in pl.get_points()]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # every written polyline has a real 2D extent (no point-collapsed loops)
        assert max(max(xs) - min(xs), max(ys) - min(ys)) > 1.0


def test_dxf_silhouette_summary_is_honest():
    # The skill metadata must not sell the brute projection as a cut-ready
    # outer outline (hidden edges are included — no visibility computation).
    summary = DxfExport.spec.summary
    assert "NOT a cut-ready outline" in summary
    assert "hidden edges included" in summary
    assert "outer outline along a view dir" not in summary


# ── discoverability ──────────────────────────────────────────────────────────

def test_three_skills_in_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"dxf_export", "section_to_sketch", "offset_sketch"} <= names
