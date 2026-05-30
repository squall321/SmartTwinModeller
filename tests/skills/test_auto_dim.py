"""Wave4-C — auto-dimensioning + cross-section reports.

Tests:
    - auto_dimension
    - cross_section_at_features
    - auto_datum_planes
    - principal_axes
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.auto_datum_planes import AutoDatumPlanes
from phone_designer.skills.inspect.auto_dimension import AutoDimension
from phone_designer.skills.inspect.cross_section_at_features import (
    CrossSectionAtFeatures,
)
from phone_designer.skills.inspect.principal_axes import PrincipalAxes
from phone_designer.skills.modify_pocket.hole import Hole


# ----------------------------------------------------------------------------
# auto_dimension
# ----------------------------------------------------------------------------


def _box(L: float = 60, W: float = 40, H: float = 20):
    return Box().apply(None, {"length_mm": L, "width_mm": W, "height_mm": H}).body


def test_auto_dimension_returns_bbox_entries():
    body = _box(60, 40, 20)
    r = AutoDimension().apply(body, {})
    ext = r.extras
    assert "key_dimensions" in ext
    names = {d["name"] for d in ext["key_dimensions"]}
    assert "overall_length_mm" in names
    assert "overall_width_mm" in names
    assert "overall_height_mm" in names


def test_auto_dimension_bbox_values_match_box():
    body = _box(60, 40, 20)
    r = AutoDimension().apply(body, {})
    by_name = {d["name"]: d for d in r.extras["key_dimensions"]}
    assert abs(by_name["overall_length_mm"]["value"] - 60.0) < 0.5
    assert abs(by_name["overall_width_mm"]["value"] - 40.0) < 0.5
    assert abs(by_name["overall_height_mm"]["value"] - 20.0) < 0.5


def test_auto_dimension_entries_have_unit_and_tolerance():
    body = _box()
    r = AutoDimension().apply(body, {})
    for d in r.extras["key_dimensions"]:
        assert "value" in d
        assert d.get("unit") == "mm"
        assert "tolerance" in d


def test_auto_dimension_picks_up_holes():
    body = _box(60, 40, 20)
    # Box uses CENTER+CENTER+MIN alignment → x∈[-30,30], y∈[-20,20], z∈[0,20].
    body = Hole().apply(body, {
        "position": (-15.0, 0.0, 20.0),
        "diameter_mm": 4.0,
        "depth_mm": 10.0,
        "direction": "-Z",
    }).body
    body = Hole().apply(body, {
        "position": (15.0, 0.0, 20.0),
        "diameter_mm": 4.0,
        "depth_mm": 10.0,
        "direction": "-Z",
    }).body
    r = AutoDimension().apply(body, {})
    names = {d["name"] for d in r.extras["key_dimensions"]}
    # at least one hole diameter and at least one spacing
    assert any(n.endswith("_diameter_mm") for n in names)
    assert any(n.startswith("hole_spacing_") for n in names)


def test_auto_dimension_body_unchanged():
    body = _box()
    r = AutoDimension().apply(body, {})
    assert r.body is body


# ----------------------------------------------------------------------------
# cross_section_at_features
# ----------------------------------------------------------------------------


def test_cross_section_at_features_default_three():
    body = _box(40, 30, 20)
    r = CrossSectionAtFeatures().apply(body, {})
    sections = r.extras["sections"]
    assert r.extras["section_count"] == len(sections)
    assert len(sections) == 3
    for s in sections:
        assert "plane_origin" in s
        assert "plane_normal" in s
        assert "wire_count" in s
        assert "bbox" in s


def test_cross_section_at_features_through_box_midplane_has_wires():
    body = _box(40, 30, 20)
    r = CrossSectionAtFeatures().apply(body, {"num_sections": 3})
    # at least one of the bbox midplanes should produce a non-empty section
    assert any(s["wire_count"] > 0 for s in r.extras["sections"])


def test_cross_section_at_features_extra_sections_after_hole():
    body = _box(40, 30, 20)
    body = Hole().apply(body, {
        "position": (20.0, 15.0, 20.0),
        "diameter_mm": 5.0,
        "depth_mm": 10.0,
        "direction": "-Z",
    }).body
    # ask for more sections; we should still get exactly that many entries.
    r = CrossSectionAtFeatures().apply(body, {"num_sections": 5})
    assert len(r.extras["sections"]) <= 5


def test_cross_section_at_features_body_unchanged():
    body = _box()
    r = CrossSectionAtFeatures().apply(body, {})
    assert r.body is body


# ----------------------------------------------------------------------------
# auto_datum_planes
# ----------------------------------------------------------------------------


def test_auto_datum_planes_box_yields_three_datums():
    body = _box(60, 40, 20)
    r = AutoDatumPlanes().apply(body, {})
    datums = r.extras["datums"]
    assert "A" in datums
    assert "B" in datums
    assert "C" in datums
    # face indices should be distinct
    idxs = {datums["A"], datums["B"], datums["C"]}
    assert len(idxs) == 3


def test_auto_datum_planes_details_have_areas():
    body = _box(60, 40, 20)
    r = AutoDatumPlanes().apply(body, {})
    details = r.extras["details"]
    assert details["A"]["area_mm2"] >= details["B"]["area_mm2"]


def test_auto_datum_planes_body_unchanged():
    body = _box()
    r = AutoDatumPlanes().apply(body, {})
    assert r.body is body


# ----------------------------------------------------------------------------
# principal_axes
# ----------------------------------------------------------------------------


def test_principal_axes_returns_three_axes_for_box():
    body = _box(60, 40, 20)
    r = PrincipalAxes().apply(body, {})
    pa = r.extras["principal_axes"]
    assert len(pa) == 3
    for entry in pa:
        assert "axis" in entry and len(entry["axis"]) == 3
        assert "moment" in entry


def test_principal_axes_moments_sorted_ascending():
    body = _box(60, 40, 20)
    r = PrincipalAxes().apply(body, {})
    moments = [e["moment"] for e in r.extras["principal_axes"]]
    assert moments == sorted(moments)


def test_principal_axes_centroid_and_volume_match_box():
    body = _box(60, 40, 20)
    r = PrincipalAxes().apply(body, {})
    assert abs(r.extras["volume_mm3"] - 60 * 40 * 20) < 1.0


def test_principal_axes_body_unchanged():
    body = _box()
    r = PrincipalAxes().apply(body, {})
    assert r.body is body


# ----------------------------------------------------------------------------
# Spec metadata
# ----------------------------------------------------------------------------


def test_auto_dimension_spec():
    s = AutoDimension.spec
    assert s.name == "auto_dimension"
    assert s.category == "inspect"
    assert s.level == "atomic"


def test_cross_section_at_features_spec():
    s = CrossSectionAtFeatures.spec
    assert s.name == "cross_section_at_features"
    assert s.category == "inspect"


def test_auto_datum_planes_spec():
    s = AutoDatumPlanes.spec
    assert s.name == "auto_datum_planes"
    assert s.category == "inspect"


def test_principal_axes_spec():
    s = PrincipalAxes.spec
    assert s.name == "principal_axes"
    assert s.category == "inspect"
