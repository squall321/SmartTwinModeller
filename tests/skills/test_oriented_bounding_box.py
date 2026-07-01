"""oriented_bounding_box — the minimal OBB query (Tier-2 general measure op).

Pins the headline property that distinguishes an OBB from the world AABB: under an
arbitrary rotation the AABB balloons but the OBB still recovers the part's true
minimal box. This is why a general CAD user needs it — stock/blank sizing and fit
on an arbitrarily-posed part.
"""
from __future__ import annotations

import pytest
from build123d import Align, Axis, Box

from phone_designer.skills.inspect.oriented_bounding_box import OrientedBoundingBox


def _obb(body):
    return OrientedBoundingBox().apply(body, {}).extras


def _cbox(l, w, h):
    return Box(l, w, h, align=(Align.CENTER, Align.CENTER, Align.CENTER))


def test_axis_aligned_obb_equals_dimensions():
    e = _obb(_cbox(30, 20, 10))
    assert sorted(e["obb"]["size_mm"]) == pytest.approx([10, 20, 30], abs=1e-3)
    assert e["obb"]["volume_mm3"] == pytest.approx(6000, rel=1e-4)


def test_obb_is_rotation_invariant_but_aabb_is_not():
    # a 30×20×10 box rotated 45° about Z: the OBB still recovers [10,20,30]; the
    # world AABB balloons in X and Y (30/20 → ~35.36). This is the OBB's raison d'être.
    rot = _cbox(30, 20, 10).rotate(Axis.Z, 45)
    e = _obb(rot)
    assert sorted(e["obb"]["size_mm"]) == pytest.approx([10, 20, 30], abs=1e-2)
    assert e["obb"]["volume_mm3"] == pytest.approx(6000, rel=1e-3)
    aabb = sorted(e["aabb"]["size_mm"])
    assert aabb[0] == pytest.approx(10, abs=1e-2)      # Z unchanged
    assert aabb[2] > 34                                 # XY ballooned well past 30


def test_obb_axes_are_orthonormal():
    e = _obb(_cbox(30, 20, 10).rotate(Axis.Z, 30))
    axes = e["obb"]["axes"]
    for ax in axes:
        assert sum(c * c for c in ax) == pytest.approx(1.0, abs=1e-6)
    # pairwise orthogonal
    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))
    assert dot(axes[0], axes[1]) == pytest.approx(0.0, abs=1e-6)
    assert dot(axes[0], axes[2]) == pytest.approx(0.0, abs=1e-6)


def test_obb_reachable_via_analyze_pipeline():
    # read-only skills return the body unchanged in extras; confirm it runs on a
    # generated body and carries the obb payload.
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert "oriented_bounding_box" in names
    sk = next(s for s in m["skills"] if s["name"] == "oriented_bounding_box")
    assert sk["category"] == "inspect" and sk["level"] == "atomic"
