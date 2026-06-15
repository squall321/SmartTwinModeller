"""Contract tests for snap_to_standards (Pillar VARIANTS, phase-4 2026-06-15).

snap_to_standards is the conservative finishing pass after a parametric variant
edit: it snaps DRIVEN dimensions to the nearest catalog standard *only when the
standard is within tolerance*, never forcing a custom value, never touching a
dimension the caller did not drive. The load-bearing guarantees pinned here:

  1. A bore driven to 8.4 mm snaps to the M8 close clearance (8.4 mm exactly,
     delta 0) — reusing classify_holes._best_standard_match + threads_metric.
  2. A bore a hair off (8.55 → M10 tap 8.5) snaps within tolerance, and is left
     ALONE when the tolerance is tighter than the delta (conservative).
  3. A wall snaps to the nearest stock thickness; a pitch to a thread pitch.
  4. snap=False is a pure no-op report (everything in 'unchanged').
  5. Envelope / pocket dims are never snapped (no standard ladder).
  6. The input catalog is never mutated; the absolute_overrides round-trips.

Pure resolution layer is OCCT-free (synthetic catalogs / driver lists).
"""
from __future__ import annotations

import copy

import pytest

from phone_designer.skills.reverse_engineer.snap_to_standards import (
    SnapToStandards,
    snap_to_standards,
)


def _dr(name, dotted, role, to_value):
    return {"name": name, "dotted_key": dotted, "role": role,
            "to_value": to_value}


# ──────────────────────────────────────────────────────────────────────────
# pure function — bore snapping (the headline pillar case)
# ──────────────────────────────────────────────────────────────────────────
def test_bore_8p4_snaps_to_m8_close_clearance():
    """The pillar headline: a bore driven to 8.4 mm snaps to the M8-family
    standard (close clearance = 8.4 mm exactly, zero delta)."""
    drivers = [_dr("primary_bore_diameter", "holes.0.diameters_mm.0",
                   "primary_bore", 8.4)]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.25)
    assert len(res["snapped"]) == 1
    s = res["snapped"][0]
    assert s["to_value"] == pytest.approx(8.4)
    assert s["delta_mm"] == pytest.approx(0.0)
    assert s["standard"].startswith("M8")
    assert res["absolute_overrides"]["holes.0.diameters_mm.0"] == pytest.approx(8.4)


def test_bore_off_standard_snaps_within_tolerance_and_records_delta():
    """8.55 mm snaps to the nearest catalog drill / clearance (M10 tap 8.5),
    a -0.05 mm delta, within the default tolerance."""
    drivers = [_dr("primary_bore_diameter", "holes.0.diameters_mm.0",
                   "primary_bore", 8.55)]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.25)
    assert len(res["snapped"]) == 1
    s = res["snapped"][0]
    assert s["to_value"] == pytest.approx(8.5)
    assert s["delta_mm"] == pytest.approx(-0.05)


def test_tight_tolerance_leaves_off_standard_bore_alone():
    """Conservative: a tolerance tighter than the nearest-standard delta must
    NOT snap — the custom value is preserved, with a reason."""
    drivers = [_dr("primary_bore_diameter", "holes.0.diameters_mm.0",
                   "primary_bore", 8.55)]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.01)
    assert res["snapped"] == []
    assert res["absolute_overrides"] == {}
    assert len(res["unchanged"]) == 1
    assert "tolerance" in res["unchanged"][0]["reason"]


# ──────────────────────────────────────────────────────────────────────────
# wall + pitch roles
# ──────────────────────────────────────────────────────────────────────────
def test_wall_snaps_to_nearest_stock_thickness():
    drivers = [_dr("wall_thickness", "base_thickness_mm", "wall", 1.47)]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.25)
    assert len(res["snapped"]) == 1
    s = res["snapped"][0]
    assert s["to_value"] == pytest.approx(1.5)
    assert "stock" in s["standard"]


def test_pitch_snaps_to_thread_pitch():
    """A hole-pattern pitch driven to 1.24 mm snaps to the M8 thread pitch
    (1.25 mm) — within tolerance."""
    drivers = [_dr("hole_pattern_pitch", "patterns.0.spacing_mm", "pitch",
                   1.24)]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.1)
    assert len(res["snapped"]) == 1
    assert res["snapped"][0]["to_value"] == pytest.approx(1.25)


# ──────────────────────────────────────────────────────────────────────────
# conservatism gates
# ──────────────────────────────────────────────────────────────────────────
def test_snap_false_is_pure_no_op_report():
    drivers = [_dr("primary_bore_diameter", "holes.0.diameters_mm.0",
                   "primary_bore", 8.4)]
    res = snap_to_standards({}, drivers, snap=False)
    assert res["snapped"] == []
    assert res["absolute_overrides"] == {}
    assert res["unchanged"][0]["reason"] == "snap disabled"


def test_envelope_and_pocket_dims_are_never_snapped():
    drivers = [
        _dr("housing_length", "initial_bbox_mm.3", "envelope", 8.4),
        _dr("pocket_footprint_diameter", "pockets.0.top_d_mm", "pocket", 8.4),
    ]
    res = snap_to_standards({}, drivers, snap=True, tolerance=1.0)
    assert res["snapped"] == []
    reasons = {u["dotted_key"]: u["reason"] for u in res["unchanged"]}
    assert "not snappable" in reasons["initial_bbox_mm.3"]
    assert "not snappable" in reasons["pockets.0.top_d_mm"]


def test_roles_subset_limits_what_snaps():
    drivers = [
        _dr("primary_bore_diameter", "holes.0.diameters_mm.0",
            "primary_bore", 8.4),
        _dr("wall_thickness", "base_thickness_mm", "wall", 1.47),
    ]
    res = snap_to_standards({}, drivers, snap=True, tolerance=0.25,
                            roles=["primary_bore"])
    snapped_keys = {s["dotted_key"] for s in res["snapped"]}
    assert snapped_keys == {"holes.0.diameters_mm.0"}
    # wall is excluded by the roles subset
    assert any(u["dotted_key"] == "base_thickness_mm"
               and "not snappable" in u["reason"] for u in res["unchanged"])


# ──────────────────────────────────────────────────────────────────────────
# skill wrapper — resolves named drivers itself
# ──────────────────────────────────────────────────────────────────────────
def _bore_catalog() -> dict:
    """Envelope 40x20x10 with one big bore so primary_bore_diameter resolves
    onto holes.0.diameters_mm.0."""
    return {
        "initial_bbox_mm": [0.0, 0.0, 0.0, 40.0, 20.0, 10.0],
        "base_thickness_mm": 3.0,
        "holes": [
            {"id": 0, "type": "simple", "axis_origin": [20.0, 10.0, 10.0],
             "axis_dir": [0.0, 0.0, -1.0], "diameters_mm": [8.0],
             "depth_mm": 8.0},
        ],
        "pockets": [], "patterns": [], "symmetries": [],
        "bosses": [], "ribs": [], "lugs": [],
    }


def test_skill_wrapper_resolves_drivers_and_snaps():
    cat = _bore_catalog()
    snap = copy.deepcopy(cat)
    res = SnapToStandards().apply(None, {
        "catalog": cat,
        "drivers": {"primary_bore_diameter": 8.4},
        "tolerance": 0.25,
    })
    sr = res.extras["snap_result"]
    assert len(sr["snapped"]) == 1
    s = sr["snapped"][0]
    assert s["role"] == "primary_bore"
    assert s["to_value"] == pytest.approx(8.4)
    assert s["standard"].startswith("M8")
    # read-only: input catalog untouched
    assert cat == snap


def test_skill_wrapper_unknown_arg_key_raises():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SnapToStandards().apply(None, {
            "catalog": _bore_catalog(),
            "driverz": {"primary_bore_diameter": 8.4},  # typo
        })


def test_pre_resolved_drivers_resolved_is_used_directly():
    drivers_resolved = [_dr("primary_bore_diameter", "holes.0.diameters_mm.0",
                            "primary_bore", 8.4)]
    res = SnapToStandards().apply(None, {
        "catalog": {},
        "drivers_resolved": drivers_resolved,
        "tolerance": 0.25,
    })
    sr = res.extras["snap_result"]
    assert len(sr["snapped"]) == 1
    assert sr["snapped"][0]["standard"].startswith("M8")
