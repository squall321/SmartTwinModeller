"""sketch_extrude + sketch_revolve — from-scratch profile→solid create skills.

Pins the headline capability: build an ARBITRARY 2D profile with ARCS (a composite
of line/arc/spline) and extrude OR revolve it into a solid, RE-free, via the same
flow a client runs (cad_generate from scratch). Volumes are exact analytic values.
Also pins the validator-caught traps: a non-Z revolve axis and a profile crossing
the axis are structured refusals (not silent garbage / crashes); is_solid is gated
on volume>0; all extrude axes (X/Y/Z) build (the parallel-axis-reference fix).
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.sketch_extrude import SketchExtrude
from phone_designer.skills.create.sketch_revolve import SketchRevolve


def _ext(**kw):
    return SketchExtrude().apply(None, kw).extras["sketch_solid"]


def _rev(**kw):
    return SketchRevolve().apply(None, kw).extras["sketch_solid"]


_DSHAPE = {"kind": "composite", "segments": [
    {"kind": "line", "start": [-10, 0], "end": [10, 0]},
    {"kind": "arc", "start": [10, 0], "end": [-10, 0], "radius": 10, "ccw": True}]}
# a bushing profile (rect on the +x half-plane) → annulus solid of revolution.
_RECT_X8 = {"kind": "rectangle", "length_mm": 2, "width_mm": 4, "center_x_mm": 8}


# ── extrude ──────────────────────────────────────────────────────────────────

def test_dshape_line_arc_extrudes_to_right_volume():
    e = _ext(sketch=_DSHAPE, height_mm=5)
    # (rectangle 20×10 + semicircle r=10) × 5 = (200 + π·50)·5 = 785.398
    assert e["volume_mm3"] == pytest.approx(785.398, rel=1e-4)
    assert e["bbox_mm"] == pytest.approx([20.0, 10.0, 5.0])
    assert e["is_solid"] is True and e["kind"] == "extrude"
    assert e["profile_kind"] == "composite"


def test_l_profile_with_arc_fillet_extrudes():
    # an L whose inner corner is a fillet ARC — confirms a line+arc composite
    # builds a valid solid (not just a polygon).
    L = {"kind": "composite", "segments": [
        {"kind": "line", "start": [0, 0], "end": [20, 0]},
        {"kind": "line", "start": [20, 0], "end": [20, 6]},
        {"kind": "line", "start": [20, 6], "end": [10, 6]},
        {"kind": "arc", "start": [10, 6], "end": [6, 10], "radius": 4, "ccw": True},
        {"kind": "line", "start": [6, 10], "end": [6, 20]},
        {"kind": "line", "start": [6, 20], "end": [0, 20]},
        {"kind": "line", "start": [0, 20], "end": [0, 0]}]}
    e = _ext(sketch=L, height_mm=3)
    assert e["is_solid"] and e["volume_mm3"] > 0 and e["profile_kind"] == "composite"


def test_extrude_all_three_axes_build():
    # the parallel-axis-reference fix: axis="X" with a naive +X xref would crash.
    for ax in ("X", "Y", "Z"):
        e = _ext(sketch=_DSHAPE, height_mm=5, axis=ax)
        assert e["is_solid"] and e["volume_mm3"] == pytest.approx(785.398, rel=1e-4)


def test_extrude_degenerate_collinear_profile_refused():
    # a closed collinear 2-line composite has zero area; BRepCheck.IsValid() would
    # call it valid, so the guard MUST be volume>0 → structured refusal, not a
    # zero-volume "solid".
    flat = {"kind": "composite", "segments": [
        {"kind": "line", "start": [0, 0], "end": [10, 0]},
        {"kind": "line", "start": [10, 0], "end": [0, 0]}]}
    with pytest.raises(ValueError, match="degenerate_profile"):
        _ext(sketch=flat, height_mm=5)


# ── revolve ──────────────────────────────────────────────────────────────────

def test_rectangle_revolves_360_to_bushing():
    r = _rev(sketch=_RECT_X8, angle_deg=360)
    # annulus: π·(9² − 7²)·4 = π·32·4 = 402.124
    assert r["volume_mm3"] == pytest.approx(402.124, rel=1e-4)
    assert r["bbox_mm"] == pytest.approx([18.0, 18.0, 4.0])
    assert r["is_solid"] is True and r["kind"] == "revolve"


def test_composite_arc_profile_revolves():
    # a chamfered bushing (line + arc corner) on the +x half-plane.
    prof = {"kind": "composite", "segments": [
        {"kind": "line", "start": [6, 0], "end": [10, 0]},
        {"kind": "line", "start": [10, 0], "end": [10, 8]},
        {"kind": "arc", "start": [10, 8], "end": [6, 8], "radius": 4, "ccw": False},
        {"kind": "line", "start": [6, 8], "end": [6, 0]}]}
    r = _rev(sketch=prof, angle_deg=360)
    assert r["is_solid"] and r["volume_mm3"] > 0 and r["profile_kind"] == "composite"


def test_partial_90deg_revolve_is_quarter_volume():
    r = _rev(sketch=_RECT_X8, angle_deg=90)
    assert r["volume_mm3"] == pytest.approx(100.531, rel=1e-4)  # 402.124 / 4


def test_revolve_profile_crossing_axis_refused():
    # a rect centred on the axis (local x in [-1, 1]) would self-intersect on
    # revolve → structured refusal, not a bare MakeRevol RuntimeError.
    crossing = {"kind": "rectangle", "length_mm": 2, "width_mm": 4, "center_x_mm": 0}
    with pytest.raises(ValueError, match="profile_crosses_axis"):
        _rev(sketch=crossing, angle_deg=360)


def test_revolve_non_z_axis_refused():
    # a non-Z axis silently produces a zero-volume garbage solid (or crashes) in
    # the revolve core — refuse it up front instead.
    with pytest.raises(ValueError, match="unsupported_revolve_axis"):
        _rev(sketch=_RECT_X8, axis_direction=(0, 1, 0), angle_deg=360)
    with pytest.raises(ValueError, match="unsupported_revolve_axis"):
        _rev(sketch=_RECT_X8, axis_origin=(5, 0, 0), angle_deg=360)


# ── from-scratch via cad_generate (MCP) + discoverability ────────────────────

def test_both_build_from_scratch_via_generate_from_spec():
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    ge = GenerateFromSpec().apply(None, {"spec": [
        {"op": "sketch_extrude", "args": {"sketch": _DSHAPE, "height_mm": 5}}]}).extras["generated"]
    assert ge["ok"] and ge["is_solid"] and ge["volume_mm3"] == pytest.approx(785.398, rel=1e-4)
    gr = GenerateFromSpec().apply(None, {"spec": [
        {"op": "sketch_revolve", "args": {"sketch": _RECT_X8, "angle_deg": 360}}]}).extras["generated"]
    assert gr["ok"] and gr["is_solid"] and gr["volume_mm3"] == pytest.approx(402.124, rel=1e-4)


def test_arc_segment_is_discoverable_in_the_manifest():
    # a client learns the arc-segment shape from the published schema alone.
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert {"sketch_extrude", "sketch_revolve"} <= names
    se = next(s for s in m["skills"] if s["name"] == "sketch_extrude")
    assert se["category"] == "create" and se["level"] == "atomic"
    defs = (se["args_schema"] or {}).get("$defs", {})
    assert "ArcSegment" in defs, "the arc segment must be published for discovery"
    assert "radius" in (defs["ArcSegment"].get("properties", {}))
