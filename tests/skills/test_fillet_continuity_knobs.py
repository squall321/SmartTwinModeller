"""fillet continuity / cross-section knobs — the roadmap's pure arg-add item.

Pins the Class-A fillet controls added to fillet_edges_by_predicate: continuity
('g1' default | 'g2' curvature-continuous via maker.SetContinuity) and fillet_shape
('rational' default | 'quasi_angular' | 'polynomial' via maker.SetFilletShape).
The DEFAULTS must preserve the historical G1-rational behaviour exactly (byte-stable
plans); the non-default combinations must actually build.
"""
from __future__ import annotations

import pytest
from build123d import Align, Box

from phone_designer.skills.modify_curvature.fillet_predicate import (
    FilletEdgesByPredicate,
)

# 4 Z-edges of a 20³ box filleted r=3: vol = 8000 − 4·(9 − 9π/4)·20 = 7845.5
_EXPECT = 7845.5


def _vol(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return abs(g.Mass())


def _fil(**kw):
    b = Box(20, 20, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    r = FilletEdgesByPredicate().apply(b, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "radius_mm": 3.0, **kw})
    return _vol(r.body.wrapped)


def test_default_is_historical_g1_rational():
    assert _fil() == pytest.approx(_EXPECT, rel=1e-3)


@pytest.mark.parametrize("kw", [
    {"continuity": "g2"},
    {"continuity": "g2", "fillet_shape": "quasi_angular"},
    {"fillet_shape": "polynomial"},
    {"continuity": "g2", "fillet_shape": "polynomial"},
])
def test_nondefault_continuity_and_shapes_build(kw):
    # the knobs change the surface LAW, not the gross volume of a simple box
    # fillet — assert each combination builds to the same analytic volume.
    assert _fil(**kw) == pytest.approx(_EXPECT, rel=1e-2)


def test_knobs_are_discoverable_in_the_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    sk = next(s for s in m["skills"] if s["name"] == "fillet_edges_by_predicate")
    props = (sk["args_schema"] or {}).get("properties", {})
    assert "continuity" in props and "fillet_shape" in props
