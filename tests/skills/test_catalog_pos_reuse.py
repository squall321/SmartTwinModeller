"""phase-0 (2026-06-14): byte-identity guard for the extract_feature_catalog
detector-output reuse optimization.

The catalog used to re-run ClassifyHoles / ClassifyPockets / DetectBosses
inside detect_linear_array / detect_circular_array once per (feature_kind,
linear|circular) pair — ~6 redundant re-detections per catalog on top of the
ones already done in _build_catalog_for. The optimization feeds the
already-computed hole/pocket/boss positions to the array detectors via the
``_precomputed_positions`` instance attribute.

This test proves the optimization is a pure speedup: for several synthetic
bodies (incl. a pattern-heavy 3×3 hole grid and a circular bolt pattern) the
catalog dict produced WITH the optimization is byte-identical (json.dumps
sort_keys) to the catalog produced with the optimization force-disabled via the
private ``PD_DISABLE_POS_REUSE`` env flag — which routes every array detector
back through its original re-extracting path.
"""
from __future__ import annotations

import json
import math
import os
import time

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole
from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
    ExtractFeatureCatalog,
)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic bodies


def _plain_box():
    """No patterns at all — exercises the empty-positions edge case."""
    return Box().apply(None, {
        "length_mm": 40.0, "width_mm": 30.0, "height_mm": 10.0,
    }).body


def _linear_hole_row():
    """5 Ø3 holes along +X, 8 mm spacing — one linear array."""
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 20.0, "height_mm": 10.0,
    }).body
    for x in (-16.0, -8.0, 0.0, 8.0, 16.0):
        body = Hole().apply(body, {
            "position": (x, 0.0, 10.0),
            "diameter_mm": 3.0,
            "depth_mm": 5.0,
            "direction": "-Z",
        }).body
    return body


def _circular_bolt_pattern():
    """6 Ø3 holes on a circle of radius 15 mm — one circular array."""
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 60.0, "height_mm": 10.0,
    }).body
    R = 15.0
    for k in range(6):
        theta = 2.0 * math.pi * k / 6.0
        body = Hole().apply(body, {
            "position": (R * math.cos(theta), R * math.sin(theta), 10.0),
            "diameter_mm": 3.0,
            "depth_mm": 5.0,
            "direction": "-Z",
        }).body
    return body


def _grid_3x3():
    """Pattern-heavy: a 3×3 grid of 9 Ø3 holes (multiple linear arrays in
    both X and Y, plus diagonal collinear runs). The densest array-detector
    exercise in the suite."""
    body = Box().apply(None, {
        "length_mm": 60.0, "width_mm": 60.0, "height_mm": 10.0,
    }).body
    for ix in (-16.0, 0.0, 16.0):
        for iy in (-16.0, 0.0, 16.0):
            body = Hole().apply(body, {
                "position": (ix, iy, 10.0),
                "diameter_mm": 3.0,
                "depth_mm": 5.0,
                "direction": "-Z",
            }).body
    return body


_BODIES = {
    "plain_box": _plain_box,
    "linear_hole_row": _linear_hole_row,
    "circular_bolt_pattern": _circular_bolt_pattern,
    "grid_3x3": _grid_3x3,
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _catalog(body, *, disable_reuse: bool) -> dict:
    """Run extract_feature_catalog, optionally forcing the old re-extracting
    path via PD_DISABLE_POS_REUSE. parallel=False for deterministic timing."""
    prev = os.environ.get("PD_DISABLE_POS_REUSE")
    if disable_reuse:
        os.environ["PD_DISABLE_POS_REUSE"] = "1"
    else:
        os.environ.pop("PD_DISABLE_POS_REUSE", None)
    try:
        t0 = time.perf_counter()
        res = ExtractFeatureCatalog().apply(body, {"parallel": False})
        elapsed = time.perf_counter() - t0
    finally:
        if prev is None:
            os.environ.pop("PD_DISABLE_POS_REUSE", None)
        else:
            os.environ["PD_DISABLE_POS_REUSE"] = prev
    cat = res.extras["feature_catalog"]
    return cat, elapsed


def _canonical(cat: dict) -> str:
    """Stable JSON of the catalog with the wall-clock timings stripped —
    _timings_sec is the only non-deterministic key (per-detector seconds)."""
    c = dict(cat)
    c.pop("_timings_sec", None)
    return json.dumps(c, sort_keys=True, default=str)


# ──────────────────────────────────────────────────────────────────────────────
# Byte-identity tests


# NOTE: every comparison rebuilds the body FRESH for each run. The detector
# pipeline mutates OCCT's optimal-bbox cache (BRepBndLib.AddOptimal_s widens by
# ~1e-7 mm after the first full face traversal), so running two catalogs on the
# *same* body object would make `initial_bbox_mm` drift between run 1 and run 2
# — a documented OCCT cache artefact unrelated to the position-reuse path. A
# fresh body per run isolates the only thing under test: detector reuse.


@pytest.mark.parametrize("name", list(_BODIES))
def test_catalog_byte_identical_with_pos_reuse(name):
    mk = _BODIES[name]
    ref_cat, _ = _catalog(mk(), disable_reuse=True)    # old re-extracting path
    opt_cat, _ = _catalog(mk(), disable_reuse=False)   # new reuse path
    assert _canonical(opt_cat) == _canonical(ref_cat), (
        f"catalog diverged for body '{name}' between the reuse path and the "
        f"re-extracting reference path"
    )


def test_pattern_heavy_body_detects_arrays_and_stays_identical():
    """The grid body must actually exercise the array detectors (non-empty
    patterns) AND remain byte-identical — otherwise the identity check above
    would be vacuously true on an empty patterns list."""
    ref_cat, _ = _catalog(_grid_3x3(), disable_reuse=True)
    opt_cat, _ = _catalog(_grid_3x3(), disable_reuse=False)
    assert ref_cat["patterns"], "expected the 3x3 grid to produce patterns"
    assert _canonical(opt_cat) == _canonical(ref_cat)
    # Holes themselves must be byte-identical too (positions fed from here).
    assert json.dumps(opt_cat["holes"], sort_keys=True) == \
        json.dumps(ref_cat["holes"], sort_keys=True)


def test_pos_reuse_does_not_change_circular_arrays():
    ref_cat, _ = _catalog(_circular_bolt_pattern(), disable_reuse=True)
    opt_cat, _ = _catalog(_circular_bolt_pattern(), disable_reuse=False)
    ref_circ = [p for p in ref_cat["patterns"] if p["pattern_kind"] == "circular"]
    opt_circ = [p for p in opt_cat["patterns"] if p["pattern_kind"] == "circular"]
    assert ref_circ, "expected a circular array on the bolt pattern"
    assert json.dumps(opt_circ, sort_keys=True) == \
        json.dumps(ref_circ, sort_keys=True)
