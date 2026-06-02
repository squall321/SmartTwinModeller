"""Tests for the W1-A PMI / GD&T export skill pack.

One test per skill on a simple box. The OCCT AP242 PMI write path is
notoriously brittle across pythonOCC bindings; the skills are designed to
*always* succeed by also writing a JSON sidecar, so the tests check the
high-level contract (tags attached, file written, summary populated)
rather than the OCCT AP242 byte layout.
"""
from __future__ import annotations

import json
from pathlib import Path

from phone_designer.skills.create.box import Box
from phone_designer.skills.pmi.export_step_ap242_pmi import ExportStepAp242Pmi
from phone_designer.skills.pmi.pmi_dimension_callout import PmiDimensionCallout
from phone_designer.skills.pmi.pmi_inspect_summary import PmiInspectSummary
from phone_designer.skills.pmi.pmi_surface_texture import PmiSurfaceTexture
from phone_designer.skills.pmi.pmi_weld_symbol import PmiWeldSymbol


def _box():
    return Box().apply(None, {"length_mm": 40, "width_mm": 30, "height_mm": 10}).body


def test_pmi_dimension_callout_appends_entry():
    body = _box()
    r = PmiDimensionCallout().apply(body, {
        "entity_a_selector": {"kind": "face_named", "name": "top"},
        "entity_b_selector": {"kind": "face_named", "name": "bottom"},
        "kind": "linear",
        "nominal_value": 10.0,
        "upper_tol": 0.05,
        "lower_tol": -0.05,
        "datum_refs": ["A", "B"],
    })
    entry = r.extras["pmi_dimension"]
    assert entry["kind"] == "linear"
    assert entry["nominal"] == 10.0
    assert entry["upper_tol"] == 0.05
    assert entry["lower_tol"] == -0.05
    assert entry["datum_refs"] == ["A", "B"]
    assert entry["unit"] == "mm"
    # Tag attached and total count returned.
    assert r.extras["pmi_dimensions_total"] == 1
    assert len(getattr(r.body, "_pd_pmi_dimensions", [])) == 1
    # Anchor points resolved.
    assert entry["anchor_a"] is not None
    assert entry["anchor_b"] is not None


def test_pmi_surface_texture_tags_face():
    body = _box()
    r = PmiSurfaceTexture().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "ra_um": 1.6,
        "profile": "Ra",
        "direction": "parallel",
        "lay_angle_deg": 30.0,
    })
    rec = r.extras["pmi_surface_texture"]
    assert rec["ra_um"] == 1.6
    assert rec["profile"] == "Ra"
    assert rec["direction"] == "parallel"
    assert rec["lay_angle_deg"] == 30.0
    assert rec["face_count"] >= 1
    assert len(rec["face_anchors"]) >= 1
    assert r.extras["pmi_surface_textures_total"] == 1
    assert len(getattr(r.body, "_pd_surface_texture", [])) == 1


def test_pmi_weld_symbol_tags_edge():
    body = _box()
    # X-axis aligned edges → 4 of them on a box.
    r = PmiWeldSymbol().apply(body, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "weld_kind": "fillet",
        "leg_length_mm": 3.0,
        "length_mm": 40.0,
        "all_around": False,
    })
    rec = r.extras["pmi_weld"]
    assert rec["weld_kind"] == "fillet"
    assert rec["leg_length_mm"] == 3.0
    assert rec["length_mm"] == 40.0
    assert rec["all_around"] is False
    assert rec["edge_count"] >= 1
    assert len(rec["edge_anchors"]) >= 1
    assert r.extras["pmi_welds_total"] == 1
    assert len(getattr(r.body, "_pd_welds", [])) == 1


def test_export_step_ap242_pmi_writes_file_and_sidecar(tmp_path: Path):
    body = _box()
    body = PmiDimensionCallout().apply(body, {
        "entity_a_selector": {"kind": "face_named", "name": "top"},
        "entity_b_selector": {"kind": "face_named", "name": "bottom"},
        "kind": "linear",
        "nominal_value": 10.0,
        "upper_tol": 0.05,
        "lower_tol": -0.05,
    }).body
    body = PmiSurfaceTexture().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "ra_um": 0.8,
    }).body
    body = PmiWeldSymbol().apply(body, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "X"},
        "weld_kind": "fillet",
        "leg_length_mm": 2.0,
        "length_mm": 40.0,
    }).body

    out = tmp_path / "box_pmi.step"
    r = ExportStepAp242Pmi().apply(body, {
        "path": str(out),
        "include_dimensions": True,
        "include_textures": True,
        "include_datums": True,
    })

    # File written.
    assert out.exists(), f"STEP file not written: {out}"
    assert out.stat().st_size > 0
    # Sidecar written next to the STEP file.
    sidecar = Path(r.extras["sidecar_path"])
    assert sidecar.exists(), f"PMI sidecar not written: {sidecar}"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["schema"] == "AP242"
    assert payload["summary"]["dimensions"] == 1
    assert payload["summary"]["surface_textures"] == 1
    assert payload["summary"]["welds"] == 1
    # extras reflect what we wrote.
    assert r.extras["schema"] == "AP242"
    assert r.extras["summary"]["dimensions"] == 1
    assert r.extras["summary"]["surface_textures"] == 1
    assert r.extras["summary"]["welds"] == 1
    # body is unchanged.
    assert r.body is body


def test_pmi_inspect_summary_counts_all_tags():
    body = _box()
    body = PmiDimensionCallout().apply(body, {
        "entity_a_selector": {"kind": "face_named", "name": "top"},
        "kind": "diameter",
        "nominal_value": 4.0,
        "upper_tol": 0.02,
        "lower_tol": -0.02,
    }).body
    body = PmiSurfaceTexture().apply(body, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "ra_um": 0.4,
    }).body
    body = PmiWeldSymbol().apply(body, {
        "edge_selector": {"kind": "axis_aligned_edges", "axis": "Y"},
        "weld_kind": "groove",
        "leg_length_mm": 2.5,
        "length_mm": 30.0,
        "all_around": True,
    }).body

    r = PmiInspectSummary().apply(body, {})
    s = r.extras["pmi_summary"]
    assert s["dimensions"] == 1
    assert s["textures"] == 1
    assert s["welds"] == 1
    assert s["fcfs"] == 0
    assert s["datums"] == []
    assert r.body is body
