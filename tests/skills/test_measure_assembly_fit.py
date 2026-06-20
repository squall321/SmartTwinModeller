"""measure_assembly_fit — measure a real assembled fit from two solids.

Builds a 2-solid assembly (a pin in a housing bore) as a Compound and pins that
the clearance is MEASURED from geometry (not an argument), the fit_type follows
the real gap sign, and the nearest standard ISO fit is named. result_grade is
'measured' because the gap is real.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.inspect.measure_assembly_fit import MeasureAssemblyFit


def _assembly(shaft_d, bore_d=10.0):
    from build123d import Box, Compound, Cylinder
    housing = Box(30, 30, 20) - Cylinder(bore_d / 2.0, 21)
    shaft = Cylinder(shaft_d / 2.0, 20)
    return Compound(children=[housing, shaft])


def _fit(shaft_d, **kw):
    asm = _assembly(shaft_d)
    return MeasureAssemblyFit().apply(asm, kw).extras["assembly_fit"]


def test_clearance_fit_measured_from_two_solids():
    r = _fit(9.98)
    assert r["n_solids"] == 2 and r["n_fits"] == 1
    assert r["grade"] == "measured"
    f = r["fits"][0]
    assert f["hole_mm"] == pytest.approx(10.0, abs=1e-3)
    assert f["shaft_mm"] == pytest.approx(9.98, abs=1e-3)
    assert f["actual_clearance_mm"] == pytest.approx(0.02, abs=1e-3)
    assert f["fit_type"] == "clearance"
    assert f["nearest_standard_fit"]["designation"] == "H7/g6"
    assert f["hole_solid"] != f["shaft_solid"]


def test_interference_fit_is_recognised():
    f = _fit(10.03)["fits"][0]
    assert f["actual_clearance_mm"] == pytest.approx(-0.03, abs=1e-3)
    assert f["fit_type"] == "interference"


def test_transition_at_exact_nominal():
    f = _fit(10.0)["fits"][0]
    assert f["fit_type"] == "transition"


def test_unrelated_cylinder_not_matched_as_fit():
    # a shaft far too small for the bore is not a mating fit (radius_tol)
    r = _fit(6.0)  # Ø6 pin, Ø10 bore -> |bore_r-shaft_r| = 2.0 > 0.6 tol
    assert r["n_fits"] == 0


def test_single_solid_yields_no_fit():
    from build123d import Box, Cylinder
    one = Box(30, 30, 20) - Cylinder(5.0, 21)  # a single solid, not an assembly
    r = MeasureAssemblyFit().apply(one, {}).extras["assembly_fit"]
    assert r["n_solids"] == 1 and r["n_fits"] == 0


def _keyway(key_w, slot_w=10.0):
    from build123d import Box, Compound, Pos
    housing = Box(40, 40, 20) - Pos(0, 0, 6) * Box(slot_w, 50, 12)
    key = Box(key_w, 40, 8)
    return Compound(children=[housing, key])


def test_prismatic_keyway_clearance_measured():
    r = MeasureAssemblyFit().apply(_keyway(9.98), {}).extras["assembly_fit"]
    prism = [f for f in r["fits"] if f["geometry"] == "prismatic"]
    assert len(prism) == 1
    f = prism[0]
    assert f["width_mm"] == pytest.approx(10.0, abs=1e-3)
    assert f["key_width_mm"] == pytest.approx(9.98, abs=1e-3)
    assert f["actual_clearance_mm"] == pytest.approx(0.02, abs=1e-3)
    assert f["fit_type"] == "clearance"
    assert f["nearest_standard_fit"]["designation"] == "H7/g6"
    assert f["slot_solid"] != f["key_solid"]


def test_prismatic_interference_keyway():
    f = [x for x in MeasureAssemblyFit().apply(_keyway(10.04), {})
         .extras["assembly_fit"]["fits"] if x["geometry"] == "prismatic"][0]
    assert f["actual_clearance_mm"] == pytest.approx(-0.04, abs=1e-3)
    assert f["fit_type"] == "interference"


def test_cylindrical_fit_does_not_spawn_fake_prismatic():
    # a round bore + round shaft must yield ONE cylindrical fit, no prismatic
    # (a bore through the block centre once faked a slot via a midplane probe)
    r = MeasureAssemblyFit().apply(_assembly(9.98), {}).extras["assembly_fit"]
    geoms = sorted(f["geometry"] for f in r["fits"])
    assert geoms == ["cylindrical"]
