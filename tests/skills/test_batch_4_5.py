"""Phase 4 batch 4-5: antenna_slit + polymer_inlay."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_antenna.antenna_slit import AntennaSlit
from phone_designer.skills.modify_antenna.polymer_inlay import PolymerInlay


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def test_antenna_slit_removes_volume():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    v_before = _volume(box)
    r = AntennaSlit().apply(box, {
        "start": (-12.0, 0.0, 5.0),
        "end":   (12.0, 0.0, 5.0),
        "width_mm": 0.8,
        "depth_mm": 12.0,
    })
    # slit: 24 × 0.8 × 10 (box thickness) = 192 (depth 12 이지만 body thickness 10 만 cut)
    expected = 24 * 0.8 * 10
    actual = v_before - _volume(r.body)
    assert abs(actual - expected) / expected < 0.05


def test_antenna_slit_rejects_zero_length():
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    with pytest.raises(ValueError, match="zero length"):
        AntennaSlit().apply(box, {
            "start": (0, 0, 5), "end": (0, 0, 5),
            "width_mm": 0.8, "depth_mm": 10,
        })


def test_polymer_inlay_adds_volume_outside_body():
    """body 외부에 inlay → 부피 증가."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)
    r = PolymerInlay().apply(box, {
        "start": (-8.0, 15.0, 5.0),    # box (Y=±10) 밖
        "end":   (8.0, 15.0, 5.0),
        "width_mm": 0.6,
        "depth_mm": 10.0,
    })
    # 16 × 0.6 × 10 = 96 추가
    diff = _volume(r.body) - v_before
    assert diff > 50


def test_antenna_slit_spec_metadata():
    spec = AntennaSlit.spec
    assert spec.level == "atomic"
    assert "antenna_slit" in spec.produces_features


def test_polymer_inlay_spec_metadata():
    spec = PolymerInlay.spec
    assert spec.level == "atomic"
    assert "polymer_inlay" in spec.produces_features


# ── manifest ────────────────────────────────────────────────────────────────


def test_manifest_includes_batch_4_5():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {"antenna_slit", "polymer_inlay"}
    assert expected <= names


def test_manifest_total_count_at_least_29():
    """Phase 4 의 5 batch 완료 시점 — atomic 22 + macro 7 = 29 (polynomial_pocket 제외)."""
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    assert len(m["skills"]) >= 29
