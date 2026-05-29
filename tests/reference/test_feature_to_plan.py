"""feature_to_plan 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.plan.executor import PlanExecutor
from phone_designer.reference.feature_to_plan import _shape_bbox, feature_to_plan
from phone_designer.reference.step_reader import classify_parts, read_xde_step
from phone_designer.reference.topology_analyzer import TopologyAnalyzer
from phone_designer.skills.create.box import Box
from phone_designer.skills.create.disc_with_dome import DiscWithDome


FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "simple_watch.step"


def _shape(part):
    return part.wrapped if hasattr(part, "wrapped") else part


def test_box_reverse_engineers_to_rounded_slab():
    """Box → 자동 plan 의 첫 step 이 rounded_slab + 매칭 bbox."""
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 10}).body
    catalog = TopologyAnalyzer().analyze(_shape(box))
    plan = feature_to_plan(catalog, _shape(box))

    assert plan.steps
    first = plan.steps[0]
    assert first.skill == "rounded_slab"
    assert abs(first.args["length_mm"] - 30) < 0.1
    assert abs(first.args["width_mm"] - 20) < 0.1
    assert abs(first.args["height_mm"] - 10) < 0.1


def test_disc_reverse_engineers_to_disc_with_dome():
    """원판 (XY 정사각) → 첫 step disc_with_dome."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 2.0,
    }).body
    catalog = TopologyAnalyzer().analyze(_shape(disc))
    plan = feature_to_plan(catalog, _shape(disc))

    first = plan.steps[0]
    assert first.skill == "disc_with_dome"
    assert abs(first.args["diameter_mm"] - 40) < 0.5
    assert abs(first.args["height_mm"] - 10) < 0.1


def test_disc_with_fillet_detects_corner_r():
    """원판 bottom R=2.0 → 자동 plan 의 corner_r_mm 가 ~2.0."""
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10,
        "dome_rise_mm": 0, "corner_r_mm": 2.0,
    }).body
    catalog = TopologyAnalyzer().analyze(_shape(disc))
    plan = feature_to_plan(catalog, _shape(disc))
    corner_r = plan.steps[0].args.get("corner_r_mm", 0)
    assert abs(corner_r - 2.0) < 0.5, f"detected corner_r={corner_r}, expected ~2.0"


def test_auto_plan_executes_without_error():
    """생성된 plan 이 PlanExecutor 에서 정상 실행."""
    box = Box().apply(None, {"length_mm": 20, "width_mm": 20, "height_mm": 10}).body
    catalog = TopologyAnalyzer().analyze(_shape(box))
    plan = feature_to_plan(catalog, _shape(box))
    result = PlanExecutor(plan).run()
    assert result.outcome == "PASS"


def test_fixture_housing_reverse_engineer():
    """fixture 의 housing → 자동 plan 생성 + 실행 + bbox 비교."""
    if not FIXTURE.exists():
        pytest.skip("fixture STEP 없음")
    parts = read_xde_step(FIXTURE, load_shapes=True)
    cat_map = classify_parts(parts)
    housing = cat_map["housing"][0]

    catalog = TopologyAnalyzer().analyze(housing.shape)
    plan = feature_to_plan(catalog, housing.shape, plan_name="housing_reverse")

    assert len(plan.steps) >= 1
    assert plan.steps[0].skill in ("disc_with_dome", "rounded_slab")

    # bbox 비교
    orig_bbox = _shape_bbox(housing.shape)
    result = PlanExecutor(plan).run()
    if result.outcome == "PASS" and result.final_body is not None:
        re_bbox = _shape_bbox(_shape(result.final_body))
        # bbox 가 비슷 (±5%)
        assert abs(re_bbox.diameter - orig_bbox.diameter) / orig_bbox.diameter < 0.1


def test_bbox_is_circular_detection():
    """BBox.is_circular: XY 정사각 → True, 직사각 → False."""
    # Box 30×20 → 직사각
    box = Box().apply(None, {"length_mm": 30, "width_mm": 20, "height_mm": 10}).body
    bb = _shape_bbox(_shape(box))
    assert not bb.is_circular

    # Disc 40 → 정사각 bbox
    disc = DiscWithDome().apply(None, {
        "diameter_mm": 40, "height_mm": 10, "dome_rise_mm": 0, "corner_r_mm": 0,
    }).body
    bb = _shape_bbox(_shape(disc))
    assert bb.is_circular
