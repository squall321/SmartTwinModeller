"""poles_spline_closed — the Class-A "by control vertices" curve (Tier-3).

Pins the spline-by-poles SketchSpec kind: a closed B-spline / rational NURBS defined
by its CONTROL POLYGON (poles + optional weights), not by interpolation through
points. Low-oscillation, pole-driven — the missing half of curve creation (all prior
2D splines route through GeomAPI_Interpolate / through-points only).

The defining property vs a polygon: a B-spline is a convex combination of its poles,
so the curve lies strictly INSIDE the control polygon — its bbox is smaller than the
poles' span. That's how we know it's a real pole-driven spline, not an interpolation.
"""
from __future__ import annotations

import math

import pytest

from phone_designer.skills.create.sketch_extrude import SketchExtrude


def _ext(sketch, h=5):
    return SketchExtrude().apply(None, {"sketch": sketch, "height_mm": h}).extras["sketch_solid"]


def test_poles_spline_builds_inside_its_control_polygon():
    # control polygon = 20×20 square (poles at ±10). A degree-3 periodic B-spline is
    # pulled INSIDE the polygon, so bbox < 20 — proof it's pole-driven, not interp.
    sk = {"kind": "poles_spline_closed",
          "poles": [[10, 10], [-10, 10], [-10, -10], [10, -10]], "degree": 3}
    e = _ext(sk)
    assert e["is_solid"] is True
    assert e["bbox_mm"][0] < 20 and e["bbox_mm"][0] > 15   # inside, but not collapsed
    assert e["bbox_mm"][2] == pytest.approx(5.0)


def test_poles_spline_circle_approximation():
    # 8 poles on a radius-10 circle → a smooth near-circular loop; area is positive
    # and below the exact circle (π·100 = 314) since the poles pull the curve in.
    poles = [[10 * math.cos(a), 10 * math.sin(a)]
             for a in [i * math.pi / 4 for i in range(8)]]
    e = _ext({"kind": "poles_spline_closed", "poles": poles, "degree": 3})
    assert e["is_solid"]
    area = e["volume_mm3"] / 5.0
    assert 200 < area < 314


def test_poles_spline_rational_weights_build():
    # a weighted (rational / NURBS) poles spline builds a valid solid.
    poles = [[10 * math.cos(a), 10 * math.sin(a)]
             for a in [i * math.pi / 4 for i in range(8)]]
    e = _ext({"kind": "poles_spline_closed", "poles": poles,
              "weights": [1, 0.9, 1, 0.9, 1, 0.9, 1, 0.9], "degree": 3})
    assert e["is_solid"] and e["volume_mm3"] > 0


def test_poles_spline_bad_weights_refused():
    from pydantic import ValidationError
    # weights of the wrong length or non-positive are refused at build.
    poles = [[10, 10], [-10, 10], [-10, -10], [10, -10]]
    with pytest.raises((ValueError, RuntimeError, ValidationError)):
        _ext({"kind": "poles_spline_closed", "poles": poles,
              "weights": [1, 1], "degree": 3})  # len mismatch


def test_poles_spline_nonfinite_weights_refused():
    # NaN slips past a bare `w <= 0` guard and OCCT silently DROPS all weights,
    # returning the UNWEIGHTED solid as success — must be a pydantic-layer
    # refusal instead (finite AND positive required). ValidationError is a
    # ValueError subclass, so pytest.raises(ValueError) covers both layers.
    poles = [[0, 0], [30, 0], [30, 20], [0, 20]]
    for bad in ([1.0, float("nan"), 1.0, 1.0],
                [1.0, float("inf"), 1.0, 1.0],
                [1.0, 0.0, 1.0, 1.0],
                [1.0, -2.0, 1.0, 1.0]):
        with pytest.raises(ValueError, match="finite and > 0"):
            _ext({"kind": "poles_spline_closed", "poles": poles,
                  "weights": bad, "degree": 3})


def test_poles_spline_degree_must_be_below_pole_count():
    # documented contract: degree < len(poles). Was silently clamped to
    # len(poles)-1 (a different curve than requested) — now a refusal.
    poles = [[10, 10], [-10, 10], [-10, -10], [10, -10]]
    with pytest.raises(ValueError, match="degree must be < len\\(poles\\)"):
        _ext({"kind": "poles_spline_closed", "poles": poles, "degree": 8})
    with pytest.raises(ValueError, match="degree must be < len\\(poles\\)"):
        _ext({"kind": "poles_spline_closed", "poles": poles, "degree": 4})
    # boundary: degree == len(poles)-1 is legal and builds.
    e = _ext({"kind": "poles_spline_closed", "poles": poles, "degree": 3})
    assert e["is_solid"] and e["volume_mm3"] > 0


def test_poles_spline_crossed_polygon_refused():
    # a CROSSED pole order makes the periodic curve self-intersect; the prism
    # passes the volume>0 gate while BRepCheck says invalid (probe-proven), so
    # the wire builder must refuse with a structured failure mode.
    with pytest.raises(ValueError, match="self_intersecting_profile"):
        _ext({"kind": "poles_spline_closed",
              "poles": [[0, 0], [40, 0], [10, 30], [40, 30]], "degree": 3})
    # the symmetric figure-eight (near-zero net area) is refused too — the old
    # volume gate even let this one through (0.392 mm³ > eps).
    with pytest.raises(ValueError, match="self_intersecting_profile"):
        _ext({"kind": "poles_spline_closed",
              "poles": [[0, 0], [40, 0], [0, 30], [40, 30]], "degree": 3})
    # control: same four poles correctly ordered CCW build a valid solid.
    e = _ext({"kind": "poles_spline_closed",
              "poles": [[0, 0], [40, 0], [40, 30], [0, 30]], "degree": 3})
    assert e["is_solid"] and e["volume_mm3"] > 0


def test_poles_spline_reachable_and_discoverable():
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    g = GenerateFromSpec().apply(None, {"spec": [
        {"op": "sketch_extrude", "args": {
            "sketch": {"kind": "poles_spline_closed",
                       "poles": [[10, 10], [-10, 10], [-10, -10], [10, -10]], "degree": 3},
            "height_mm": 5}}]}).extras["generated"]
    assert g["ok"] and g["is_solid"]

    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    se = next(s for s in m["skills"] if s["name"] == "sketch_extrude")
    defs = (se["args_schema"] or {}).get("$defs", {})
    assert "PolesSplineSketch" in defs
    props = defs["PolesSplineSketch"].get("properties", {})
    assert "poles" in props and "weights" in props and "degree" in props
