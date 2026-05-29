"""Inspection v2 — measure / cross_section / silhouette / find_features /
selector_preview / symmetry_axes 의 단위 테스트.

Phase 4 v1 LLM-facing helper skills. 모두 read-only, body 를 변경하지 않는다.
"""
from __future__ import annotations

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_boss.boss_with_hole import BossWithHole
from phone_designer.skills.modify_pocket.hole import Hole
from phone_designer.skills.inspect.cross_section import CrossSection
from phone_designer.skills.inspect.find_features import FindFeatures
from phone_designer.skills.inspect.measure import Measure
from phone_designer.skills.inspect.selector_preview import SelectorPreview
from phone_designer.skills.inspect.silhouette import Silhouette
from phone_designer.skills.inspect.symmetry_axes import SymmetryAxes


# ──────────────────────────────────────────────────────────────────────────────
# measure


def test_measure_distance_between_face_centers():
    """box top center (z=10) → bottom center (z=0) 사이의 거리 = 10 mm."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = Measure().apply(box, {
        "entity_a": {"kind": "face_center",
                      "selector": {"kind": "face_named", "name": "top"}},
        "entity_b": {"kind": "face_center",
                      "selector": {"kind": "face_named", "name": "bottom"}},
        "measure_kind": "distance",
    })
    assert r.extras["kind"] == "distance"
    assert abs(r.extras["value_mm"] - 10.0) < 0.01
    # body 보존
    assert r.body is box


def test_measure_angle_between_perpendicular_faces():
    """box top face normal (+Z) vs side face normal (+X) → 90°."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = Measure().apply(box, {
        "entity_a": {"kind": "face_center",
                      "selector": {"kind": "face_named", "name": "top"}},
        "entity_b": {"kind": "face_center",
                      "selector": {"kind": "faces_by_normal",
                                    "direction": (1.0, 0.0, 0.0),
                                    "tol_deg": 5.0}},
        "measure_kind": "angle",
    })
    assert r.extras["kind"] == "angle_deg"
    assert abs(r.extras["value_deg"] - 90.0) < 1.0


def test_measure_distance_to_explicit_point():
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    # top face center = (0,0,10) → origin = (0,0,0) 거리 = 10
    r = Measure().apply(box, {
        "entity_a": {"kind": "face_center",
                      "selector": {"kind": "face_named", "name": "top"}},
        "entity_b": {"kind": "point", "point": (0.0, 0.0, 0.0)},
        "measure_kind": "distance",
    })
    assert abs(r.extras["value_mm"] - 10.0) < 0.01


# ──────────────────────────────────────────────────────────────────────────────
# cross_section


def test_cross_section_box_horizontal_plane():
    """z=5 평면이 20x20 box 의 중간을 자르면 정사각형 외곽선 (4 edge)."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = CrossSection().apply(box, {
        "plane_origin": (0.0, 0.0, 5.0),
        "plane_normal": (0.0, 0.0, 1.0),
    })
    pls = r.extras["polylines"]
    assert r.extras["edge_count"] == 4
    assert len(pls) == 4
    # total perimeter ≈ 4 * 20 = 80 mm
    assert abs(r.extras["total_length_mm"] - 80.0) < 0.5
    # body 보존
    assert r.body is box


def test_cross_section_off_body_returns_empty():
    """body 와 만나지 않는 plane 은 polylines 가 비어 있어야 한다."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = CrossSection().apply(box, {
        "plane_origin": (0.0, 0.0, 100.0),  # box 위 한참
        "plane_normal": (0.0, 0.0, 1.0),
    })
    assert r.extras["polylines"] == []
    assert r.extras["edge_count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# silhouette


def test_silhouette_box_z_view():
    """top-down view (view_direction=-Z) 에서 20x20x10 box → 사각형 outline.

    Brute projection 이라 12 edge 전부 투영되지만 UV extent 는 20x20 이어야 한다.
    """
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = Silhouette().apply(box, {"view_direction": (0.0, 0.0, -1.0)})
    pls = r.extras["polylines_2d"]
    # 12 edge → 12 polyline
    assert len(pls) == 12
    extent = r.extras["extent_uv"]
    u_min, v_min, u_max, v_max = extent
    # 사각형 outline — extent 한 축은 20, 다른 축도 20
    width_u = u_max - u_min
    width_v = v_max - v_min
    assert abs(width_u - 20.0) < 0.1
    assert abs(width_v - 20.0) < 0.1
    assert r.body is box


# ──────────────────────────────────────────────────────────────────────────────
# find_features


def test_find_features_box_with_hole():
    """box 에 hole drill → find_features 가 hole 1 개 찾는다."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 30, "height_mm": 10}).body
    holed = Hole().apply(box, {
        "position": (0.0, 0.0, 10.0),
        "diameter_mm": 4.0,
        "depth_mm": 5.0,
        "direction": "-Z",
    }).body
    r = FindFeatures().apply(holed, {"kinds": ["hole"]})
    holes = [f for f in r.extras["features"] if f["kind"] == "hole"]
    assert len(holes) == 1
    h = holes[0]
    assert abs(h["radius_mm"] - 2.0) < 0.01
    # axis 가 ±Z 와 평행
    ax = h["axis"]
    assert abs(abs(ax[2]) - 1.0) < 0.01
    # boss / rib skip
    r2 = FindFeatures().apply(holed, {"kinds": ["boss", "rib"]})
    assert r2.extras["features"] == []
    assert any("boss" in n for n in r2.extras["notes"])


def test_find_features_clean_box_no_hole():
    """plain box → hole 0 개."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = FindFeatures().apply(box, {"kinds": ["hole", "fillet"]})
    holes = [f for f in r.extras["features"] if f["kind"] == "hole"]
    fillets = [f for f in r.extras["features"] if f["kind"] == "fillet"]
    assert len(holes) == 0
    assert len(fillets) == 0


# ──────────────────────────────────────────────────────────────────────────────
# selector_preview


def test_selector_preview_face_named_top():
    """face_named=top 은 box 에서 1 개 매칭."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = SelectorPreview().apply(box, {
        "selector": {"kind": "face_named", "name": "top"},
        "kind": "faces",
    })
    assert r.extras["error"] is None
    assert r.extras["match_count"] == 1
    # 1 row, plane 면, area ≈ 100
    rows = r.extras["matched_entities"]
    assert len(rows) == 1
    assert rows[0]["surface_type"] == "plane"
    assert abs(rows[0]["area_mm2"] - 100.0) < 0.01
    # serialized selector — kind 만 확인 (dict 형태)
    assert r.extras["selector"]["kind"] == "face_named"


def test_selector_preview_zero_match_does_not_crash():
    """impossible selector (예: bbox 가 body 밖) → match_count=0, error=None."""
    box = Box().apply(None, {"length_mm": 10, "width_mm": 10, "height_mm": 10}).body
    r = SelectorPreview().apply(box, {
        "selector": {
            "kind": "edges_by_position",
            "bbox": {"min": (1000.0, 1000.0, 1000.0),
                      "max": (1100.0, 1100.0, 1100.0)},
        },
        "kind": "edges",
    })
    assert r.extras["match_count"] == 0
    assert r.extras["matched_entities"] == []
    assert r.extras["error"] is None


def test_selector_preview_axis_aligned_edges():
    """axis_aligned_edges Z on box → 4 edge."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SelectorPreview().apply(box, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "kind": "edges",
    })
    assert r.extras["match_count"] == 4
    for row in r.extras["matched_entities"]:
        assert abs(row["length_mm"] - 10.0) < 0.01
        assert row["curve_type"] == "line"


def test_selector_preview_truncation_flag():
    """max_entities=2 면 4 match 중 2 만 반환 + truncated=True."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SelectorPreview().apply(box, {
        "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
        "kind": "edges",
        "max_entities": 2,
    })
    assert r.extras["match_count"] == 4   # 실제 매칭 수는 그대로
    assert len(r.extras["matched_entities"]) == 2
    assert r.extras["truncated"] is True


# ──────────────────────────────────────────────────────────────────────────────
# symmetry_axes


def test_symmetry_axes_box_all_three():
    """center-aligned box 는 X, Y, Z 모두 대칭."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    r = SymmetryAxes().apply(box, {"axis_candidates": ["X", "Y", "Z"]})
    sym = set(r.extras["symmetric_axes"])
    assert "X" in sym
    assert "Y" in sym
    assert "Z" in sym
    for ax in ("X", "Y", "Z"):
        assert r.extras["scores"][ax] >= 0.9
    assert r.body is box


def test_symmetry_axes_asymmetric_body():
    """box + offset boss → boss 가 한쪽에만 있으므로 일부 축은 비대칭."""
    box = Box().apply(None, {"length_mm": 40, "width_mm": 40, "height_mm": 10}).body
    bossed = BossWithHole().apply(box, {
        "position": (15.0, 15.0, 10.0),  # X+, Y+ 쪽 (off-center)
        "direction": "+Z",
        "boss_diameter_mm": 6.0,
        "boss_height_mm": 4.0,
        "hole_diameter_mm": 2.0,
        "hole_depth_mm": 3.0,
    }).body
    r = SymmetryAxes().apply(bossed, {
        "axis_candidates": ["X", "Y", "Z"],
        "tolerance_mm": 0.5,
    })
    sym = set(r.extras["symmetric_axes"])
    # X 와 Y 축 대칭은 깨졌어야 한다 (boss 가 한 코너에만)
    assert "X" not in sym or "Y" not in sym
    # 점수 sanity
    assert "scores" in r.extras
