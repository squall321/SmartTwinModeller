"""measure_thread_pitch + (diameter, pitch) standard matching — plan item A6.

Covers:
    (a) synthetic helical thread (helical_thread_internal on a bored
        cylinder) — measured pitch within 5% of authored, handedness right;
    (b) plain cylinder bore — is_thread False;
    (c) ground truth: corpus/oem/revolved/freecad__screw_ISO4762_M3x10.step —
        the STEP carries 0 B_SPLINE_SURFACE / 0 SURFACE_OF_REVOLUTION
        entities (verified 2026-06-10 by grep), i.e. FreeCAD fastener-library
        COSMETIC threads. The test skips honestly at runtime when no helical
        geometry is measurable, and asserts pitch ≈ 0.5 ±10% if a future
        corpus refresh ships real threads;
    (d) _best_standard_match with a measured pitch — pitch breaks diameter
        ties (Ø2.5 = M2.5 outer_d AND M3 tap_drill); without a pitch the
        diameter-only outputs are pinned (pass-25 regression pin).
"""
from __future__ import annotations

import math
import pathlib

import pytest

from phone_designer.skills.inspect.classify_holes import (
    ClassifyHoles,
    _best_standard_match,
)
from phone_designer.skills.inspect.measure_thread import MeasureThreadPitch
from phone_designer.skills.modify_pocket.helical_thread_internal import (
    HelicalThreadInternal,
)
from phone_designer.skills.modify_pocket.hole import Hole

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCREW = (
    _REPO_ROOT / "corpus" / "oem" / "revolved"
    / "freecad__screw_ISO4762_M3x10.step"
)

_AUTHORED_PITCH = 1.0


def _make_cylinder(radius_mm: float, height_mm: float):
    from build123d import Align, Cylinder
    return Cylinder(
        radius=radius_mm,
        height=height_mm,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )


def _bored_cylinder():
    """Ø12 × 10 cylinder with a Ø6 through-bore on the z-axis."""
    body = _make_cylinder(radius_mm=6.0, height_mm=10.0)
    return Hole().apply(body, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 6.0,
        "depth_mm": 10.0,
        "direction": "-Z",
        "through": True,
    }).body


@pytest.fixture(scope="module")
def threaded_bore():
    """Bored cylinder with a real swept internal thread: pitch 1.0, 8 turns,
    right-handed (helical_thread_internal builds +Z right-hand helices)."""
    body = _bored_cylinder()
    return HelicalThreadInternal().apply(body, {
        "axis_origin": (0.0, 0.0, 0.0),
        "axis_direction": (0.0, 0.0, 1.0),
        "outer_radius_mm": 3.0,
        "pitch_mm": _AUTHORED_PITCH,
        "turns": 8.0,
        "thread_height_mm": 0.5,
        "thread_angle_deg": 60.0,
    }).body


# ──────────────────────────────────────────────────────────────────────────────
# (a) synthetic helical thread


def test_synthetic_thread_pitch_within_5pct(threaded_bore):
    r = MeasureThreadPitch().apply(threaded_bore, {
        "axis_origin": [0.0, 0.0, 0.0],
        "axis_dir": [0.0, 0.0, 1.0],
        "search_radius_mm": 5.0,
    })
    m = r.extras["thread_measurement"]
    assert m["is_thread"] is True, f"thread not detected: {m}"
    assert m["pitch_mm"] == pytest.approx(_AUTHORED_PITCH, rel=0.05), (
        f"pitch {m['pitch_mm']} not within 5% of {_AUTHORED_PITCH}: {m}"
    )
    assert m["handedness"] == "right", f"wrong handedness: {m}"
    assert m["n_edges_used"] >= 2
    assert 0.0 < m["confidence"] <= 1.0
    # read-only — body unchanged
    assert r.body is threaded_bore


def test_synthetic_thread_axis_flip_same_chirality(threaded_bore):
    """Handedness is intrinsic — measuring along -Z must agree with +Z."""
    m = MeasureThreadPitch().apply(threaded_bore, {
        "axis_origin": [0.0, 0.0, 10.0],
        "axis_dir": [0.0, 0.0, -1.0],
        "search_radius_mm": 5.0,
    }).extras["thread_measurement"]
    assert m["is_thread"] is True
    assert m["handedness"] == "right"
    assert m["pitch_mm"] == pytest.approx(_AUTHORED_PITCH, rel=0.05)


# ──────────────────────────────────────────────────────────────────────────────
# (b) plain bore negative


def test_plain_cylinder_bore_is_not_thread():
    body = _bored_cylinder()
    m = MeasureThreadPitch().apply(body, {
        "axis_origin": [0.0, 0.0, 0.0],
        "axis_dir": [0.0, 0.0, 1.0],
        "search_radius_mm": 5.0,
    }).extras["thread_measurement"]
    assert m["is_thread"] is False
    assert m["handedness"] is None
    assert m["n_edges_used"] == 0  # no bspline/revolution faces at all


# ──────────────────────────────────────────────────────────────────────────────
# (c) ground truth — freecad ISO4762 M3x10


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bb)
    return bb.Get()


@pytest.mark.skipif(not _SCREW.exists(), reason=f"corpus file missing: {_SCREW}")
def test_ground_truth_freecad_iso4762_m3x10():
    from phone_designer.skills.create.import_step import ImportStep

    body = ImportStep().apply(None, {"path": str(_SCREW)}).body
    assert body is not None

    # Screw axis = longest bbox extent through the bbox center (the model is
    # a solid of revolution; its axis passes through the bbox center).
    xmin, ymin, zmin, xmax, ymax, zmax = _bbox(
        body.wrapped if hasattr(body, "wrapped") else body
    )
    ext = (xmax - xmin, ymax - ymin, zmax - zmin)
    k = max(range(3), key=lambda i: ext[i])
    axis_dir = [0.0, 0.0, 0.0]
    axis_dir[k] = 1.0
    center = [
        0.5 * (xmin + xmax), 0.5 * (ymin + ymax), 0.5 * (zmin + zmax),
    ]
    lateral = [e for i, e in enumerate(ext) if i != k]
    search_radius = 0.5 * math.hypot(*lateral) + 1.0

    m = MeasureThreadPitch().apply(body, {
        "axis_origin": center,
        "axis_dir": axis_dir,
        "search_radius_mm": search_radius,
    }).extras["thread_measurement"]

    if not m["is_thread"]:
        # HONEST ground-truth outcome (verified 2026-06-10): the FreeCAD
        # fastener-library export contains only CYLINDRICAL_SURFACE (×2),
        # planes and tori — the M3×0.5 thread is COSMETIC (unmodeled), so
        # there is no helical geometry to measure. Not a detector failure.
        pytest.skip(
            "freecad__screw_ISO4762_M3x10.step carries cosmetic (unmodeled) "
            f"threads — no helical faces near the axis (measurement: {m})"
        )

    # If a future corpus refresh ships real modeled threads: M3 coarse.
    assert m["pitch_mm"] == pytest.approx(0.5, rel=0.10)


# ──────────────────────────────────────────────────────────────────────────────
# (d) (diameter, pitch) standard matching


def test_pitch_breaks_diameter_tie():
    """Ø2.5 is BOTH M2.5 outer_d (2.5) and M3 tap_drill (2.5) — a perfect
    diameter tie the pre-pass-25 matcher resolves by catalog iteration order
    (M2.5). A measured pitch picks the right spec either way."""
    m3 = _best_standard_match(2.5, None, None, measured_pitch_mm=0.5)
    assert m3 is not None and m3["thread_spec"] == "M3", m3
    assert m3["fit"] == "tap"

    m25 = _best_standard_match(2.5, None, None, measured_pitch_mm=0.45)
    assert m25 is not None and m25["thread_spec"] == "M2.5", m25


def test_pitch_with_nominal_diameter_picks_coarse_spec():
    """d=3.0 + measured pitch 0.5 → M3 (the M3×0.5 coarse row). NOTE: the
    threads_metric.yaml catalog carries no fine-pitch series (no M3×0.35
    entry), so the coarse-vs-fine discrimination is exercised through the
    M2.5/M3 diameter tie above instead."""
    m = _best_standard_match(3.0, None, None, measured_pitch_mm=0.5)
    assert m is not None and m["thread_spec"] == "M3", m
    assert m["confidence"] > 0.8

    # A wrong pitch must cost confidence relative to the right pitch on the
    # same diameter.
    wrong = _best_standard_match(3.0, None, None, measured_pitch_mm=0.8)
    assert wrong is not None
    assert wrong["confidence"] < m["confidence"]


# Pinned outputs of the diameter-only matcher captured on HEAD BEFORE the
# pass-25 change (2026-06-10) — the no-pitch path must stay bit-identical.
_DIAMETER_ONLY_PINS = {
    1.9:  {"thread_spec": "M2",   "fit": "outer",  "confidence": 0.909},
    2.5:  {"thread_spec": "M2.5", "fit": "outer",  "confidence": 1.0},
    3.0:  {"thread_spec": "M3",   "fit": "outer",  "confidence": 1.0},
    3.4:  {"thread_spec": "M3",   "fit": "medium", "confidence": 1.0},
    4.5:  {"thread_spec": "M4",   "fit": "medium", "confidence": 1.0},
    5.0:  {"thread_spec": "M5",   "fit": "outer",  "confidence": 1.0},
    6.6:  {"thread_spec": "M6",   "fit": "medium", "confidence": 1.0},
    8.5:  {"thread_spec": "M10",  "fit": "tap",    "confidence": 1.0},
    11.0: {"thread_spec": "M10",  "fit": "medium", "confidence": 1.0},
}


def test_no_pitch_behavior_identical_regression_pin():
    for d, expected in _DIAMETER_ONLY_PINS.items():
        got = _best_standard_match(d, None, None)
        assert got == expected, f"d={d}: {got} != pinned {expected}"
        # explicit None must take the exact same code path
        got_none = _best_standard_match(d, None, None, measured_pitch_mm=None)
        assert got_none == expected, f"d={d} (None): {got_none} != {expected}"
    # cb / cs secondary terms unchanged too
    assert _best_standard_match(4.5, 8.0, None) == {
        "thread_spec": "M4", "fit": "medium", "confidence": 1.0,
    }
    assert _best_standard_match(3.4, None, 6.0) == {
        "thread_spec": "M3", "fit": "medium", "confidence": 1.0,
    }


def test_classify_holes_measured_pitch_arg_inert_by_default():
    """ClassifyHoles with measured_pitch_mm omitted vs explicit None must
    emit identical hole inventories (the corpus-safety requirement)."""
    from phone_designer.skills.create.box import Box

    body = Box().apply(None, {
        "length_mm": 30.0, "width_mm": 30.0, "height_mm": 8.0,
    }).body
    body = Hole().apply(body, {
        "position": (0.0, 0.0, 8.0),
        "diameter_mm": 3.4,
        "depth_mm": 8.0,
        "direction": "-Z",
        "through": True,
    }).body

    default_holes = ClassifyHoles().apply(body, {}).extras["holes"]
    none_holes = ClassifyHoles().apply(
        body, {"measured_pitch_mm": None}
    ).extras["holes"]
    assert default_holes == none_holes

    # And the pitch actually flows through when supplied: Ø3.4 + pitch 0.5
    # still resolves to M3 (clearance medium) with standards on.
    pitched = ClassifyHoles().apply(
        body, {"measured_pitch_mm": 0.5}
    ).extras["holes"]
    assert pitched, "no holes detected"
    sm = pitched[0]["standard_match"]
    assert sm is not None and sm["thread_spec"] == "M3", sm
