"""create_open_surface — the surface-first gateway (OPEN face/shell, NOT a solid).

Pins the capability that ``create_open_surface`` emits an OPEN surface spanning
≥2 sections along +Z: an un-capped skin you can later thicken/trim/blend/sew.

The load-bearing analytic proof is the OPEN-vs-CLOSED contrast. For a 20×20→10×10
frustum over height 10, each of the 4 side faces is a trapezoid of parallel edges
20 and 10 and slant height √(5² + 10²) = √125:
    side area = 4 · (20 + 10)/2 · √125 = 4 · 15 · 11.180340 = 670.820 mm².
That is EXACTLY the open skin's surface area (ends NOT capped). The sibling
``sketch_loft`` (ThruSections isSolid=TRUE) yields the SAME side wall PLUS the two
end caps (20² + 10² = 500 mm²) = 1170.820 mm² — so closed − open == 500.0, proving
the ONLY difference is the absent caps, and that this skill leaves the surface open.

``is_solid`` is honestly False (an open SHELL has zero TopAbs_SOLID sub-shapes —
VolumeProperties_s would LIE here, returning a nonzero divergence-theorem
pseudo-volume for the un-capped shell). The wire-cast fix is implicitly pinned:
a bare TopoDS_Shape fed to ThruSections.AddWire / BRepFill.Shell_s HANGS, so a
passing (non-timeout) build proves the TopoDS.Wire_s cast is in place.
"""
from __future__ import annotations

import pytest

from phone_designer.skills.create.create_open_surface import CreateOpenSurface

_SQ20 = {"kind": "rectangle", "length_mm": 20, "width_mm": 20}
_SQ10 = {"kind": "rectangle", "length_mm": 10, "width_mm": 10}
# analytic side-wall area of the 20→10 frustum over height 10 (no caps).
_OPEN_AREA = 4.0 * (20.0 + 10.0) / 2.0 * (5.0 ** 2 + 10.0 ** 2) ** 0.5  # 670.820


def _os(**kw):
    return CreateOpenSurface().apply(None, kw).extras["open_surface"]


# ── builds: an OPEN surface with the analytic side-wall area ──────────────────

def test_lofted_open_skin_area_matches_uncapped_walls():
    r = _os(sections=[_SQ20, _SQ10], heights_mm=[0, 10], mode="lofted", ruled=True)
    assert r["surface_area_mm2"] == pytest.approx(670.820, rel=1e-4)
    assert r["surface_area_mm2"] == pytest.approx(_OPEN_AREA, rel=1e-6)
    assert r["bbox_mm"] == pytest.approx([20.0, 20.0, 10.0])
    assert r["is_solid"] is False          # honestly an open skin, NOT a solid
    assert r["mode"] == "lofted" and r["kind"] == "open_surface"


def test_ruled_mode_band_matches_lofted():
    # mode='ruled' = BRepFill.Shell between the two wires — same un-capped band.
    r = _os(sections=[_SQ20, _SQ10], heights_mm=[0, 10], mode="ruled")
    assert r["surface_area_mm2"] == pytest.approx(670.820, rel=1e-4)
    assert r["is_solid"] is False and r["mode"] == "ruled"


def test_open_vs_closed_differs_by_exactly_the_end_caps():
    # THE crux: create_open_surface (isSolid=FALSE) vs sketch_loft (isSolid=TRUE)
    # differ by EXACTLY the two end caps 20²+10²=500 mm², and by is_solid.
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from phone_designer.skills.create.sketch_loft import SketchLoft

    open_area = _os(sections=[_SQ20, _SQ10], heights_mm=[0, 10],
                    mode="lofted", ruled=True)["surface_area_mm2"]
    closed = SketchLoft().apply(
        None, {"sections": [_SQ20, _SQ10], "heights_mm": [0, 10], "ruled": True})
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(closed.body.wrapped, g)
    closed_area = abs(float(g.Mass()))
    assert closed_area - open_area == pytest.approx(500.0, rel=1e-4)  # 20²+10²
    # and the closed loft IS a solid whereas ours is not.
    assert closed.extras["sketch_solid"]["is_solid"] is True


def test_smooth_square_to_circle_open_surface_builds():
    # mismatched vertex counts: a smooth (non-ruled) open skin still builds.
    r = _os(sections=[{"kind": "rectangle", "length_mm": 10, "width_mm": 10},
                      {"kind": "circle", "diameter_mm": 4}],
            heights_mm=[0, 10], mode="lofted", ruled=False)
    assert r["surface_area_mm2"] > 0 and r["is_solid"] is False
    assert r["section_kinds"] == ["rectangle", "circle"]


def test_three_section_open_skin_stacks():
    r = _os(sections=[{"kind": "rectangle", "length_mm": 8, "width_mm": 8},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4},
                      {"kind": "rectangle", "length_mm": 6, "width_mm": 6}],
            heights_mm=[0, 5, 10], mode="lofted", ruled=True)
    assert r["n_sections"] == 3 and r["surface_area_mm2"] > 0
    assert r["bbox_mm"][2] == pytest.approx(10.0) and r["is_solid"] is False


def test_result_body_is_a_bare_face_not_a_solid():
    # the surface-first contract: the returned body is a Face/Shell, not a solid,
    # so downstream surface skills (thicken/trim/sew) can consume it.
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    res = CreateOpenSurface().apply(
        None, {"sections": [_SQ20, _SQ10], "heights_mm": [0, 10],
               "mode": "lofted", "ruled": True})
    shape = res.body.wrapped
    assert not TopExp_Explorer(shape, TopAbs_SOLID).More()  # ZERO solids


# ── honest refusals ───────────────────────────────────────────────────────────

def test_height_count_mismatch_refused():
    with pytest.raises(ValueError, match="surface_height_count_mismatch"):
        _os(sections=[_SQ20, _SQ10], heights_mm=[0, 5, 10])


def test_non_monotonic_heights_refused():
    with pytest.raises(ValueError, match="surface_heights_not_monotonic"):
        _os(sections=[_SQ20, _SQ10], heights_mm=[10, 5])


def test_ruled_mode_needs_exactly_two_sections():
    with pytest.raises(ValueError, match="surface_ruled_needs_two_sections"):
        _os(sections=[{"kind": "rectangle", "length_mm": 8, "width_mm": 8},
                      {"kind": "rectangle", "length_mm": 4, "width_mm": 4},
                      {"kind": "rectangle", "length_mm": 6, "width_mm": 6}],
            heights_mm=[0, 5, 10], mode="ruled")


# ── discoverability + from-scratch via generate_from_spec (MCP) ───────────────

def test_open_surface_is_discoverable_in_the_manifest():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    s = next((x for x in m["skills"] if x["name"] == "create_open_surface"), None)
    assert s is not None
    assert s["category"] == "create" and s["level"] == "atomic"


def test_open_surface_from_scratch_via_generate_from_spec():
    # generate_from_spec's is_solid is now HONEST for an open shell (volume>0 AND
    # a TopAbs_SOLID present — VolumeProperties_s alone returns a nonzero
    # divergence-theorem pseudo-volume and used to lie True). So:
    #   * with the default validate_solid=True, an open-surface spec reports
    #     is_solid=False and ok=False — the honest refusal;
    #   * a surface-first workflow passes validate_solid=False → ok=True.
    from phone_designer.skills.create.generate_from_spec import GenerateFromSpec
    spec = [{"op": "create_open_surface", "args": {
        "sections": [_SQ20, _SQ10], "heights_mm": [0, 10],
        "mode": "lofted", "ruled": True}}]

    strict = GenerateFromSpec().apply(None, {"spec": spec}).extras["generated"]
    assert strict["is_solid"] is False and strict["ok"] is False

    surf = GenerateFromSpec().apply(None, {
        "spec": spec, "validate_solid": False}).extras["generated"]
    assert surf["ok"] and not surf["spec_errors"]
    assert surf["is_solid"] is False  # still honestly reported
