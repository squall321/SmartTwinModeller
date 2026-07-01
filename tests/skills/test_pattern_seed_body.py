"""pattern_seed_body unit tests — linear/circular pattern-and-fuse of the seed body.

Analytic anchors (box 10×10×10, seed volume 1000 mm³):
  * linear count=3 spacing=20 (disjoint)     -> total vol 3000
  * linear count=3 spacing=5  (overlapping)   -> UNION vol 2000 (20×10×10 bar)
  * circular count=4 about Z through an off-body centre -> 4×1000 = 4000
    (disjoint copies) — proves the off-origin T·R·T-inverse composition.
"""
from __future__ import annotations

import math

from phone_designer.skills.create.box import Box
from phone_designer.skills.transform.pattern_seed_body import PatternSeedBody


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, g)
    return abs(float(g.Mass()))


def _bbox(body):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def _seed():
    # Box origin corner at (0,0,0), spanning 0..10 on each axis, vol 1000.
    return Box().apply(None, {
        "length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0,
    }).body


def test_linear_disjoint_sums_volumes():
    r = PatternSeedBody().apply(_seed(), {
        "mode": "linear", "count": 3,
        "direction": (1.0, 0.0, 0.0), "spacing_mm": 20.0,
    })
    v = _volume(r.body)
    assert abs(v - 3000.0) < 1.0, f"disjoint x3 vol {v:.3f}, expected 3000"
    assert r.extras["transform"]["fused"] is True


def test_linear_overlap_unions_volume():
    # boxes at x=0,5,10 each span 10 in x -> union spans 0..20 -> 20×10×10 = 2000.
    r = PatternSeedBody().apply(_seed(), {
        "mode": "linear", "count": 3,
        "direction": (1.0, 0.0, 0.0), "spacing_mm": 5.0,
    })
    v = _volume(r.body)
    assert abs(v - 2000.0) < 1.0, f"overlap union vol {v:.3f}, expected 2000"
    dx, dy, dz = _bbox(r.body)
    assert abs(dx - 20.0) < 1e-3 and abs(dy - 10.0) < 1e-3 and abs(dz - 10.0) < 1e-3


def test_count_one_returns_seed():
    r = PatternSeedBody().apply(_seed(), {
        "mode": "linear", "count": 1, "spacing_mm": 20.0,
    })
    v = _volume(r.body)
    assert abs(v - 1000.0) < 1.0, f"count=1 vol {v:.3f}, expected 1000"


def test_circular_off_center_full_ring():
    # 4 copies, 90° apart, about Z through an off-body centre far away so the
    # copies are disjoint -> total vol 4×1000 = 4000. Proves the off-origin
    # T(c)·R·T(-c) rotation composition.
    r = PatternSeedBody().apply(_seed(), {
        "mode": "circular", "count": 4,
        "axis": (0.0, 0.0, 1.0), "center_mm": (100.0, 0.0, 0.0),
        "total_angle_deg": 360.0,
    })
    v = _volume(r.body)
    assert abs(v - 4000.0) < 2.0, f"circular ring vol {v:.3f}, expected ~4000"
    assert r.extras["transform"]["count"] == 4


def test_zero_direction_refused():
    import pytest
    with pytest.raises(Exception) as exc:
        PatternSeedBody().apply(_seed(), {
            "mode": "linear", "count": 3,
            "direction": (0.0, 0.0, 0.0), "spacing_mm": 20.0,
        })
    assert "zero_direction" in str(exc.value)


def test_post_condition_and_registration():
    spec = PatternSeedBody.spec
    assert any(pc.kind == "body_present" for pc in spec.post_conditions)
    assert spec.category == "transform"
    assert spec.name == "pattern_seed_body"
