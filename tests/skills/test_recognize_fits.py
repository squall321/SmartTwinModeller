"""recognize_fits — ISO 286 tolerance bands + standard-fit recommendation.

Pins the EXACT ISO 286 reference values (so a transcription slip is caught) and
the honest split: tolerance magnitudes are standard, the fit-class choice is a
graded recommendation. Direct-diameter mode is deterministic (no CAD); one
geometry test confirms real hole detection flows through.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.recognize_fits import RecognizeFits
from phone_designer.skills.modify_pocket.hole import Hole


def _fa(**kw):
    return RecognizeFits().apply(kw.pop("body", None), kw).extras["fit_analysis"]


def test_iso_clearance_fit_exact_values():
    # H7/g6 at Ø12 (band 10-18) = +0.006 .. +0.035 clearance (ISO 286 table)
    f = _fa(diameter_mm=12.0, role="clearance")["features"][0]
    rf = f["recommended_fit"]
    assert rf["designation"] == "H7/g6"
    assert rf["fit_type"] == "clearance"
    assert rf["clearance_mm"] == {"min": 0.006, "max": 0.035}
    # the bore's own H7 band = 0 .. +0.018
    assert f["tolerance"]["upper_mm"] == 0.018
    assert f["tolerance"]["lower_mm"] == 0.0


def test_iso_interference_and_transition_signs():
    press = _fa(diameter_mm=12.0, role="press")["features"][0]["recommended_fit"]
    assert press["designation"] == "H7/p6"
    assert press["fit_type"] == "interference"
    assert press["clearance_mm"] == {"min": -0.029, "max": 0.0}  # both ≤ 0 → press
    trans = _fa(diameter_mm=12.0, role="transition")["features"][0]["recommended_fit"]
    assert trans["fit_type"] == "transition"  # straddles zero
    assert trans["clearance_mm"]["min"] < 0 < trans["clearance_mm"]["max"]


def test_iso_band_changes_with_size():
    # same fit, larger nominal -> different ISO band -> wider clearance
    small = _fa(diameter_mm=12.0, role="clearance")["features"][0]["recommended_fit"]
    big = _fa(diameter_mm=20.0, role="clearance")["features"][0]["recommended_fit"]
    assert big["clearance_mm"] == {"min": 0.007, "max": 0.041}  # band 18-30
    assert big["clearance_mm"]["max"] > small["clearance_mm"]["max"]


def test_out_of_table_diameter_is_skipped_honestly():
    r = _fa(diameter_mm=400.0)
    assert r["features"] == []
    assert any("outside ISO table" in a for a in r["assumptions"])


def test_grade_is_estimate_and_recommendation_is_disclaimed():
    r = _fa(diameter_mm=12.0)
    assert r["grade"] == "estimate"
    assert r["standard"] == "ISO 286"
    # the honest split must be stated
    assert any("heuristic" in a.lower() for a in r["assumptions"])
    assert any("ISO 286" in a for a in r["assumptions"])


def test_measured_fit_recognises_actual_clearance_and_nearest_standard():
    # a Ø12 bore with a Ø11.98 shaft -> +0.020mm real gap -> nearest H7/g6
    m = _fa(diameter_mm=12.0, mating_diameter_mm=11.98)["features"][0]["measured_fit"]
    assert m["actual_clearance_mm"] == 0.02
    assert m["fit_type"] == "clearance"  # sign of the real gap, not a heuristic
    assert m["nearest_standard_fit"]["designation"] == "H7/g6"
    # an oversize shaft is recognised as interference
    press = _fa(diameter_mm=12.0, mating_diameter_mm=12.03)["features"][0]["measured_fit"]
    assert press["actual_clearance_mm"] == -0.03
    assert press["fit_type"] == "interference"
    # exact nominal -> transition
    exact = _fa(diameter_mm=12.0, mating_diameter_mm=12.0)["features"][0]["measured_fit"]
    assert exact["fit_type"] == "transition"


def test_geometry_mode_detects_hole_bores():
    b = Box().apply(None, {"length_mm": 80.0, "width_mm": 50.0, "height_mm": 20.0}).body
    for x in (-25.0, 0.0, 25.0):
        b = Hole().apply(b, {"position": (x, 0.0, 20.0), "diameter_mm": 12.0,
                             "depth_mm": 20.0, "direction": "-Z"}).body
    r = RecognizeFits().apply(b, {"role": "press"}).extras["fit_analysis"]
    assert r["n_holes"] == 3
    assert all(f["kind"] == "hole" for f in r["features"])
    f0 = r["features"][0]
    assert f0["nominal_mm"] == 12.0
    assert f0["recommended_fit"]["designation"] == "H7/p6"
    assert "position" in f0 and len(f0["position"]) == 3
