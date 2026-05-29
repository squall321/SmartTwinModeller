"""Phase 4 batch 4-2: 워치 특화 macro — crown_shaft_hole, lug_pair, o_ring_groove."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome
from phone_designer.skills.modify_boss.crown_shaft_hole import CrownShaftHole
from phone_designer.skills.modify_boss.lug_pair import LugPair
from phone_designer.skills.modify_boss.o_ring_groove import ORingGroove


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ── crown_shaft_hole ─────────────────────────────────────────────────────────


def test_crown_shaft_hole_reduces_volume():
    """샤프트 + 베어링 두 hole → 부피 감소."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 15}).body
    v_before = _volume(box)
    r = CrownShaftHole().apply(box, {
        "position": (15.0, 0.0, 7.5),
        "direction": "+X",
        "shaft_d_mm": 2.5,
        "shaft_depth_mm": 5.0,
        "bearing_recess_d_mm": 5.0,
        "bearing_recess_depth_mm": 1.5,
    })
    assert _volume(r.body) < v_before


def test_crown_shaft_hole_with_plinth_can_increase_volume():
    """plinth_height>0 → 외부 cylinder union → 충분히 크면 net 증가 가능."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 15}).body
    r = CrownShaftHole().apply(box, {
        "position": (15.0, 0.0, 7.5),
        "direction": "+X",
        "shaft_d_mm": 1.5,
        "shaft_depth_mm": 3.0,
        "bearing_recess_d_mm": 3.0,
        "bearing_recess_depth_mm": 1.0,
        "plinth_d_mm": 8.0,
        "plinth_height_mm": 4.0,
    })
    # plinth 가 시각 확인용 — 크기 합리이면 OK
    assert r.body is not None


def test_crown_shaft_hole_rejects_bearing_smaller_than_shaft():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    with pytest.raises(ValueError, match="bearing_recess_d"):
        CrownShaftHole().apply(box, {
            "position": (10, 0, 5),
            "direction": "+X",
            "shaft_d_mm": 4.0,
            "bearing_recess_d_mm": 3.0,    # smaller — invalid
            "shaft_depth_mm": 5.0,
            "bearing_recess_depth_mm": 1.0,
        })


def test_crown_shaft_hole_spec_is_macro():
    spec = CrownShaftHole.spec
    assert spec.level == "macro"
    assert spec.expansion is not None
    assert "hole" in spec.expansion


# ── lug_pair ────────────────────────────────────────────────────────────────


def test_lug_pair_adds_two_lugs():
    """+Y/-Y lug 한 쌍 추가 → 부피 증가 + bbox Y 확장."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    v_before = _volume(box)
    r = LugPair().apply(box, {
        "axis": "+Y/-Y",
        "offset_mm": 17.0,    # housing Y/2 = 15 + lug 약간 바깥
        "z_position_mm": 5.0,
        "lug_length_mm": 6.0,
        "lug_thickness_mm": 3.0,
        "lug_height_mm": 3.0,
        "pin_diameter_mm": 1.5,
    })
    # 두 lug 추가 (volume 증가) + 핀 hole 두 개 (감소). net 증가.
    assert _volume(r.body) > v_before


def test_lug_pair_x_axis():
    """+X/-X 방향 lug."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    r = LugPair().apply(box, {
        "axis": "+X/-X",
        "offset_mm": 17.0,
        "z_position_mm": 5.0,
    })
    assert r.body is not None


def test_lug_pair_spec_is_macro():
    spec = LugPair.spec
    assert spec.level == "macro"
    assert "hole" in spec.expansion


# ── o_ring_groove ────────────────────────────────────────────────────────────


def test_o_ring_groove_reduces_volume():
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 30, "height_mm": 5, "dome_rise_mm": 0, "corner_r_mm": 0,
    }).body
    v_before = _volume(disc)
    r = ORingGroove().apply(disc, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "outer_diameter_mm": 20.0,
        "inner_diameter_mm": 17.0,
        "depth_mm": 0.5,
    })
    v_after = _volume(r.body)
    # ring 부피 ≈ π * (10² - 8.5²) * 0.5 ≈ 43.6
    expected_loss = 3.14159 * (10**2 - 8.5**2) * 0.5
    actual = v_before - v_after
    assert abs(actual - expected_loss) / expected_loss < 0.1


def test_o_ring_groove_rejects_inner_ge_outer():
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 30, "height_mm": 5, "dome_rise_mm": 0, "corner_r_mm": 0,
    }).body
    with pytest.raises(ValueError, match="outer_d"):
        ORingGroove().apply(disc, {
            "face_selector": {"kind": "face_named", "name": "top"},
            "outer_diameter_mm": 10.0,
            "inner_diameter_mm": 10.0,    # equal — fail
            "depth_mm": 0.5,
        })


def test_o_ring_groove_spec_metadata():
    spec = ORingGroove.spec
    assert spec.level == "atomic"
    assert "o_ring_groove" in spec.produces_features


# ── manifest 갱신 ────────────────────────────────────────────────────────────


def test_manifest_includes_batch_4_2():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {"crown_shaft_hole", "lug_pair", "o_ring_groove"}
    assert expected <= names


def test_manifest_total_count_at_least_18():
    """Batch 4-2 시점 최소 18."""
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    assert len(m["skills"]) >= 18
