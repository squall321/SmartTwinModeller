"""deform_body — free-form twist / taper (the roadmap's only 'hard' deferred gap).

Pins the space-warp deform (SolidWorks Flex / Fusion Deform): a genuine kernel op
(NurbsConvert → refine poles → displace → re-sew), NOT a rigid motion or a uniform
scale. Two modes:
  * twist — rotate each cross-section about Z with height. Ideally volume-preserving;
    the B-spline approximation converges to that as `refine` grows.
  * taper — ramp the XY scale with height (frustum-like). Volume matches the exact
    analytic integral of the varying cross-section area.
"""
from __future__ import annotations

import pytest
from build123d import Align, Box

from phone_designer.skills.transform.deform_body import DeformBody


def _dfm(body, **kw):
    return DeformBody().apply(body, kw).extras["deform"]


def _tall_box():
    # 20×20×40, base at z=0, XY-centred on the Z axis (so twist/taper are about it).
    return Box(20, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _signed_volume(shape) -> float:
    # SIGNED volume — negative means the solid is inside-out.
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return float(g.Mass())


def _classify(shape, x, y, z):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    clf = BRepClass3d_SolidClassifier(shape)
    clf.Perform(gp_Pnt(x, y, z), 1e-6)
    return clf.State()


def test_twist_is_approximately_volume_preserving():
    # an 80° twist over the height ideally preserves the 16000mm³ volume; at
    # refine=12 the B-spline approximation is within ~1%.
    d = _dfm(_tall_box(), mode="twist", twist_deg=80, refine=12)
    assert d["n_faces"] == 6
    assert d["volume_after_mm3"] == pytest.approx(16000.0, rel=0.02)


def test_twist_accuracy_improves_with_refine():
    # the honest convergence claim: more refinement → volume closer to 16000.
    raw = _dfm(_tall_box(), mode="twist", twist_deg=80, refine=0)["volume_after_mm3"]
    fine = _dfm(_tall_box(), mode="twist", twist_deg=80, refine=16)["volume_after_mm3"]
    assert abs(fine - 16000) < abs(raw - 16000)          # refinement helps
    assert fine == pytest.approx(16000.0, rel=0.01)      # and gets close


def test_taper_matches_analytic_frustum_integral():
    # XY scale ramps 1.0→0.5 over the height. Volume = 400·∫₀¹(1−0.5t)²·40 dt
    # = 16000·(1 − 0.5 + 0.25/3) = 16000·0.58333 = 9333.33.
    d = _dfm(_tall_box(), mode="taper", taper_ratio=0.5, refine=12)
    assert d["volume_after_mm3"] == pytest.approx(9333.33, rel=1e-3)


def test_taper_flare_grows_the_top():
    # taper_ratio > 1 flares the top → volume larger than the straight prism.
    d = _dfm(_tall_box(), mode="taper", taper_ratio=1.5, refine=12)
    assert d["volume_after_mm3"] > 16000


def test_twist_solid_is_not_inside_out():
    # regression: sewing used to hand back an inward-oriented solid (signed
    # volume −15935): interior points classified OUT and downstream booleans got
    # the COMPLEMENT of the body. Pin the corrected orientation.
    from OCP.TopAbs import TopAbs_State
    res = DeformBody().apply(_tall_box(), {"mode": "twist", "twist_deg": 80, "refine": 12})
    shape = res.body.wrapped
    assert _signed_volume(shape) > 0                      # not inside-out
    assert _classify(shape, 0, 0, 20) == TopAbs_State.TopAbs_IN     # true interior
    assert _classify(shape, 500, 500, 500) == TopAbs_State.TopAbs_OUT  # far exterior


def test_taper_solid_is_not_inside_out():
    # taper mode had the same inversion (signed volume −9333.33).
    res = DeformBody().apply(_tall_box(), {"mode": "taper", "taper_ratio": 0.5, "refine": 12})
    assert _signed_volume(res.body.wrapped) > 0


def test_deforming_a_deformed_body_does_not_mutate_it():
    # regression: when the input's faces are already B-splines, NurbsConvert
    # reuses the LIVE Geom handles, and the pole edits used to write straight
    # into the input body — deforming a deformed body corrupted the first result.
    r1 = DeformBody().apply(_tall_box(), {"mode": "twist", "twist_deg": 30, "refine": 4})
    v1 = _signed_volume(r1.body.wrapped)
    DeformBody().apply(r1.body, {"mode": "twist", "twist_deg": 30, "refine": 4})
    assert _signed_volume(r1.body.wrapped) == pytest.approx(v1, rel=1e-9)


def test_refuses_body_with_through_hole():
    # regression: MakeFace(surface-only) drops inner wires, so a through-hole
    # used to be silently FILLED (12858mm³ in → 15999mm³ out). Now an honest
    # structured refusal fires before any warping.
    from build123d import Cylinder
    holed = _tall_box() - Cylinder(5, 100)
    with pytest.raises(ValueError, match=r"fm\.deform_failed.*wires"):
        _dfm(holed, mode="twist", twist_deg=10, refine=4)


def test_twist_needs_nonzero_angle():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="deform_needs_param"):
        _dfm(_tall_box(), mode="twist", twist_deg=0)


def test_taper_needs_nonunit_ratio():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="deform_needs_param"):
        _dfm(_tall_box(), mode="taper", taper_ratio=1.0)


def test_deform_in_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    assert "deform_body" in names
    sk = next(s for s in m["skills"] if s["name"] == "deform_body")
    assert sk["category"] == "transform" and sk["level"] == "atomic"
