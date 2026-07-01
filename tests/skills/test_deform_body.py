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
