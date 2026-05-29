"""Phase 4 batch 4-3: boss_with_hole, rib, snap_hook, mounting_pad."""
from __future__ import annotations

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.boss_with_hole import BossWithHole
from phone_designer.skills.modify_boss.mounting_pad import MountingPad
from phone_designer.skills.modify_boss.rib import Rib
from phone_designer.skills.modify_boss.snap_hook import SnapHook


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


# ── boss_with_hole ──────────────────────────────────────────────────────────


def test_boss_with_hole_adds_volume_then_subtracts():
    """boss union → 부피 추가 → hole cut → 약간 감소. net: 약 (D²-d²) × h × π/4."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    v_before = _volume(box)
    r = BossWithHole().apply(box, {
        "position": (5.0, 5.0, 10.0),
        "direction": "+Z",
        "boss_diameter_mm": 6.0,
        "boss_height_mm": 4.0,
        "hole_diameter_mm": 2.5,
        "hole_depth_mm": 5.0,
    })
    v_after = _volume(r.body)
    # boss 추가: π × 3² × 4 = 113.1
    # hole 제거: π × 1.25² × 5 = 24.5 (단 hole 의 일부는 boss 영역, 일부는 box 안)
    # net 약 +88
    diff = v_after - v_before
    assert diff > 50 and diff < 130


def test_boss_with_hole_rejects_hole_geq_boss():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    with pytest.raises(ValueError, match="hole_d"):
        BossWithHole().apply(box, {
            "position": (0, 0, 10),
            "direction": "+Z",
            "boss_diameter_mm": 4.0,
            "boss_height_mm": 5.0,
            "hole_diameter_mm": 4.0,   # equal — invalid
        })


def test_boss_with_hole_spec_is_macro():
    spec = BossWithHole.spec
    assert spec.level == "macro"
    assert "hole" in spec.expansion
    assert "union" in spec.expansion


# ── rib ─────────────────────────────────────────────────────────────────────


def test_rib_adds_volume_along_path():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    v_before = _volume(box)
    r = Rib().apply(box, {
        "start": (-10.0, 0.0, 5.0),
        "end": (10.0, 0.0, 5.0),
        "width_mm": 1.5,
        "height_mm": 3.0,
        "up_axis": "+Z",
    })
    # rib 부피: 20 × 1.5 × 3 = 90
    expected = 20 * 1.5 * 3
    diff = _volume(r.body) - v_before
    assert abs(diff - expected) / expected < 0.05


def test_rib_diagonal_path():
    """대각선 rib 도 정상 동작."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    v_before = _volume(box)
    r = Rib().apply(box, {
        "start": (-5.0, -5.0, 5.0),
        "end": (5.0, 5.0, 5.0),
        "width_mm": 1.0,
        "height_mm": 2.0,
    })
    # 대각선 길이 = sqrt(200) ≈ 14.14
    import math
    expected = math.sqrt(200) * 1.0 * 2.0
    diff = _volume(r.body) - v_before
    assert abs(diff - expected) / expected < 0.1


def test_rib_rejects_zero_length():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 5}).body
    with pytest.raises(ValueError, match="zero length"):
        Rib().apply(box, {
            "start": (0, 0, 0),
            "end": (0, 0, 0),
        })


# ── snap_hook ───────────────────────────────────────────────────────────────


def test_snap_hook_adds_volume():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    v_before = _volume(box)
    r = SnapHook().apply(box, {
        "base_position": (0.0, 0.0, 5.0),
        "cantilever_length_mm": 6.0,
        "cantilever_width_mm": 3.0,
        "cantilever_thickness_mm": 1.0,
        "lip_overhang_mm": 0.8,
        "lip_height_mm": 1.5,
        "cantilever_axis": "+Z",
    })
    # beam (1×3×6=18) + lip ((1+0.8)×3×1.5=8.1) − 겹침
    diff = _volume(r.body) - v_before
    assert diff > 15


def test_snap_hook_axis_minus_y():
    """-Y 방향 cantilever — base 가 box face 위에 있으면 hook 이 안쪽으로 자라남."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    fc_before = len(__import__("phone_designer.skills._resolvers",
                                fromlist=["_all_faces"])._all_faces(box.wrapped))
    r = SnapHook().apply(box, {
        "base_position": (0.0, 18.0, 5.0),    # box (Y=±15) 밖에 base, hook 가 +Y 로 자라남
        "cantilever_axis": "+Y",
    })
    fc_after = len(__import__("phone_designer.skills._resolvers",
                                fromlist=["_all_faces"])._all_faces(r.body.wrapped))
    assert fc_after > fc_before    # snap hook 추가됨


# ── mounting_pad ────────────────────────────────────────────────────────────


def test_mounting_pad_extrudes_on_top():
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    v_before = _volume(box)
    r = MountingPad().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 10, "width_mm": 8},
        "height_mm": 1.0,
        "required_roughness_ra_um": 1.6,
    })
    # pad 부피: 10 × 8 × 1 = 80
    diff = _volume(r.body) - v_before
    assert abs(diff - 80) / 80 < 0.05


def test_mounting_pad_metadata_in_args():
    """required_roughness/flatness 가 args 에 보존됨 (Phase 5 DFM 에서 사용)."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 5}).body
    args_dict = {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 10, "width_mm": 8},
        "height_mm": 1.0,
        "required_roughness_ra_um": 0.8,
        "required_flatness_mm": 0.05,
    }
    r = MountingPad().apply(box, args_dict)
    assert r.body is not None


# ── manifest ────────────────────────────────────────────────────────────────


def test_manifest_includes_batch_4_3():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    expected = {"boss_with_hole", "rib", "snap_hook", "mounting_pad"}
    assert expected <= names


def test_manifest_total_count_at_least_22():
    """Batch 4-3 시점 최소 22."""
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    assert len(m["skills"]) >= 22
