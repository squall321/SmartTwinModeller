"""Phase 4 batch 4-1: import_step, subtract, union, extrude_through, hole_array, final_fillet."""
from __future__ import annotations

from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures"


def _volume(body) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, props)
    return props.Mass()


def _face_count(body) -> int:
    from phone_designer.skills._resolvers import _all_faces
    return len(_all_faces(body.wrapped))


# ── import_step ─────────────────────────────────────────────────────────────


def test_import_step_loads_housing_only():
    p = FIXTURE_DIR / "simple_watch_housing_only.step"
    if not p.exists():
        pytest.skip("fixture 없음")
    from phone_designer.skills.create.import_step import ImportStep
    result = ImportStep().apply(None, {"path": str(p)})
    assert _face_count(result.body) >= 5


def test_import_step_file_not_found():
    from phone_designer.skills.create.import_step import ImportStep
    with pytest.raises(FileNotFoundError):
        ImportStep().apply(None, {"path": "nonexistent.step"})


def test_import_step_scale_doubles_volume():
    p = FIXTURE_DIR / "simple_watch_components" / "battery.step"
    if not p.exists():
        pytest.skip("battery.step 없음")
    from phone_designer.skills.create.import_step import ImportStep
    v1 = _volume(ImportStep().apply(None, {"path": str(p), "scale": 1.0}).body)
    v2 = _volume(ImportStep().apply(None, {"path": str(p), "scale": 2.0}).body)
    # scale 2.0 → 부피 8배 (scale³)
    assert abs(v2 / v1 - 8.0) < 0.1


# ── subtract / union ────────────────────────────────────────────────────────


def test_subtract_with_primitive_box_reduces_volume():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.subtract import Subtract

    big = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(big)

    r = Subtract().apply(big, {
        "other": {
            "kind": "primitive_box",
            "length_mm": 5, "width_mm": 5, "height_mm": 20,
            "position": (0, 0, 0),
        },
    })
    v_after = _volume(r.body)
    # 5x5x10 만큼 제거 (box overlap)
    assert abs((v_before - v_after) - 250.0) / 250.0 < 0.05


def test_subtract_with_primitive_cylinder():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.subtract import Subtract

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = Subtract().apply(box, {
        "other": {
            "kind": "primitive_cylinder",
            "diameter_mm": 4.0, "height_mm": 15,
            "position": (0, 0, 0),
        },
    })
    # cylinder 가 box 안에서 4mm 두께 hole — 부피 감소
    assert _volume(r.body) < _volume(box)


def test_union_with_primitive_box_increases_volume():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.union import Union

    base = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    v_before = _volume(base)

    r = Union().apply(base, {
        "other": {
            "kind": "primitive_box",
            "length_mm": 10, "width_mm": 10, "height_mm": 10,
            "position": (15, 0, 5),    # 떨어진 위치
        },
    })
    # 두 box 따로 → 부피 합 = 2 * 1000
    assert abs(_volume(r.body) - 2000) / 2000 < 0.05


def test_subtract_with_step_path():
    """STEP 파일을 other 로."""
    p = FIXTURE_DIR / "simple_watch_components" / "battery.step"
    if not p.exists():
        pytest.skip("battery.step 없음")
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.subtract import Subtract

    box = Box().apply(None, {"length_mm": 50, "width_mm": 50, "height_mm": 30}).body
    r = Subtract().apply(box, {"other": {"kind": "step_path", "path": str(p)}})
    # battery 가 box 안 어딘가 — 부피 감소
    assert _volume(r.body) < _volume(box)


def test_subtract_with_fixture_part():
    """XDE assembly 의 특정 부품 로드."""
    p = FIXTURE_DIR / "simple_watch.step"
    if not p.exists():
        pytest.skip("simple_watch.step 없음")
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.subtract import Subtract

    box = Box().apply(None, {"length_mm": 80, "width_mm": 80, "height_mm": 30}).body
    r = Subtract().apply(box, {
        "other": {"kind": "fixture_part",
                   "assembly_path": str(p), "part_name": "display"},
    })
    assert _volume(r.body) < _volume(box)


def test_subtract_no_intersection_keeps_body_volume_close():
    """겹치지 않으면 부피 거의 변화 없음."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.compose.subtract import Subtract

    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    v_before = _volume(box)

    r = Subtract().apply(box, {
        "other": {
            "kind": "primitive_box",
            "length_mm": 5, "width_mm": 5, "height_mm": 5,
            "position": (100, 100, 100),   # 멀리
        },
    })
    # 영향 없음
    assert abs(_volume(r.body) - v_before) / v_before < 0.01


# ── extrude_through ─────────────────────────────────────────────────────────


def test_extrude_through_removes_volume():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.extrude_through import ExtrudeThrough

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)

    r = ExtrudeThrough().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "rectangle", "length_mm": 4, "width_mm": 3},
    })
    v_after = _volume(r.body)
    # 4×3×10 = 120 만큼 제거
    expected_loss = 4 * 3 * 10
    actual_loss = v_before - v_after
    assert abs(actual_loss - expected_loss) / expected_loss < 0.1


def test_extrude_through_circular_sketch():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.extrude_through import ExtrudeThrough

    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    v_before = _volume(box)
    r = ExtrudeThrough().apply(box, {
        "face_selector": {"kind": "face_named", "name": "top"},
        "sketch": {"kind": "circle", "diameter_mm": 5},
    })
    # π × 2.5² × 10 ≈ 196.3
    expected = 3.14159 * 6.25 * 10
    actual = v_before - _volume(r.body)
    assert abs(actual - expected) / expected < 0.1


# ── hole_array ──────────────────────────────────────────────────────────────


def test_hole_array_drills_n_holes():
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_pocket.hole_array import HoleArray

    box = Box().apply(None, {"length_mm": 40, "width_mm": 10, "height_mm": 5}).body
    v_before = _volume(box)

    r = HoleArray().apply(box, {
        "points": [
            (-12, 0, 5), (-4, 0, 5), (4, 0, 5), (12, 0, 5),
        ],
        "diameter_mm": 2.0,
        "depth_mm": None,    # through
        "direction": "-Z",
    })
    # 4 hole × π × 1² × 5 = 62.83
    expected_loss = 4 * 3.14159 * 1.0 * 5.0
    actual = v_before - _volume(r.body)
    assert abs(actual - expected_loss) / expected_loss < 0.1


def test_hole_array_macro_metadata():
    from phone_designer.skills.modify_pocket.hole_array import HoleArray
    spec = HoleArray.spec
    assert spec.level == "macro"
    assert spec.expansion is not None
    assert "hole" in spec.expansion


# ── final_fillet_all_sharp_edges ─────────────────────────────────────────────


def test_final_fillet_increases_face_count():
    """fillet 후 face 늘어남 (각 edge 마다 새 face)."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_finish.final_fillet import FinalFilletAllSharpEdges

    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    fc_before = _face_count(box)

    r = FinalFilletAllSharpEdges().apply(box, {"radius_mm": 0.3})
    fc_after = _face_count(r.body)
    assert fc_after > fc_before


def test_final_fillet_rejects_radius_out_of_range():
    """R > 2.0 는 pydantic Field(le=2.0) 위반."""
    from phone_designer.skills.modify_finish.final_fillet import FinalFilletAllSharpEdges
    with pytest.raises(Exception):    # pydantic ValidationError
        FinalFilletAllSharpEdges().apply(None, {"radius_mm": 5.0})


def test_final_fillet_with_small_radius_succeeds():
    """R=0.3 의 일괄 마감 → 모든 edge fillet 성공."""
    from phone_designer.skills.create.box import Box
    from phone_designer.skills.modify_finish.final_fillet import FinalFilletAllSharpEdges
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = FinalFilletAllSharpEdges().apply(box, {"radius_mm": 0.3})
    # 모든 edge fillet → 6 face → ~26 face (6 plane + 12 cylinder + 8 sphere)
    assert _face_count(r.body) > 6


# ── manifest 갱신 검증 ──────────────────────────────────────────────────────


def test_manifest_includes_new_6_skills():
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    names = {s["name"] for s in m["skills"]}
    new6 = {"import_step", "subtract", "union", "extrude_through",
            "hole_array", "final_fillet_all_sharp_edges"}
    missing = new6 - names
    assert not missing, f"manifest 누락: {missing}"


def test_manifest_total_count_at_least_15():
    """Batch 4-1+1.5 시점에 최소 15 — 후속 batch 추가되면 늘어남."""
    from phone_designer.skills.export_manifest import build_manifest
    m = build_manifest()
    assert len(m["skills"]) >= 15
