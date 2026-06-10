"""body_orientation_check — synthetic-body tests for the inverted-shell probe.

Actual logic (read from the source):

    signed = BRepGProp.VolumeProperties Mass()   (SIGNED)
    - signed is None                      → advice "...failed...", inverted False
    - |signed| < min_magnitude_mm3        → "empty or sheet-like", inverted False
    - signed < 0                          → inverted True + INVERTED SHELL advice
    - otherwise                           → healthy: inverted False, advice None

A solid whose orientation is flipped (TopoDS_Shape.Reversed()) integrates
to a NEGATIVE signed volume — that is exactly the pythonocc__11752 import
pathology this skill exists to surface.
"""
from __future__ import annotations

import pytest
from build123d import Box as B3dBox, Part

from phone_designer.skills.inspect.body_orientation_check import BodyOrientationCheck


def _report(body, args=None):
    return BodyOrientationCheck().apply(body, args or {}).extras["body_orientation"]


# ──────────────────────────────────────────────────────────────────────────────
# Healthy solid


def test_healthy_box_is_not_inverted():
    rep = _report(B3dBox(10, 20, 5))
    assert rep["inverted"] is False
    assert rep["advice"] is None
    assert rep["signed_volume_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert rep["magnitude_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert rep["bbox_volume_mm3"] == pytest.approx(1000.0, rel=1e-3)
    assert rep["solidity_ratio"] == pytest.approx(1.0, abs=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# Inverted shell (negative signed volume, correct magnitude)


def test_reversed_solid_is_flagged_inverted():
    box = B3dBox(10, 20, 5)
    flipped = Part(box.wrapped.Reversed())
    rep = _report(flipped)
    assert rep["signed_volume_mm3"] == pytest.approx(-1000.0, rel=1e-6)
    assert rep["magnitude_mm3"] == pytest.approx(1000.0, rel=1e-6)
    assert rep["inverted"] is True
    assert "INVERTED SHELL" in rep["advice"]
    # solidity is computed on the MAGNITUDE, so it stays sane.
    assert rep["solidity_ratio"] == pytest.approx(1.0, abs=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# Empty / sheet-like floor


def test_magnitude_below_floor_is_empty_not_inverted():
    # 1 mm^3 body against a 10 mm^3 floor → "empty or sheet-like" wins,
    # even though the signed volume is positive and well-formed.
    rep = _report(B3dBox(1, 1, 1), {"min_magnitude_mm3": 10.0})
    assert rep["inverted"] is False
    assert rep["advice"] is not None
    assert "empty or sheet-like" in rep["advice"]


def test_floor_applies_before_the_inversion_check():
    # A reversed TINY solid: |Mass| under the floor → flagged empty, NOT
    # inverted (the floor branch precedes the signed<0 branch).
    flipped = Part(B3dBox(1, 1, 1).wrapped.Reversed())
    rep = _report(flipped, {"min_magnitude_mm3": 10.0})
    assert rep["inverted"] is False
    assert "empty or sheet-like" in rep["advice"]


# ──────────────────────────────────────────────────────────────────────────────
# No body


def test_no_body_reports_gracefully():
    rep = _report(None)
    assert rep["inverted"] is False
    assert rep["advice"] == "no body to check"
    assert rep["signed_volume_mm3"] is None
    assert rep["magnitude_mm3"] is None
