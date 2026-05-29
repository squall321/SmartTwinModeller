"""Phase 6: Component model + loader + collision + arrangement."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.components import (
    BoundingBox,
    ClearanceSpec,
    Component,
    ComponentArrangement,
    ComponentSource,
    Pose,
    collision_report,
    has_collision,
    load_catalog,
)


REPO_ROOT = Path(__file__).parent.parent.parent
CATALOG_DIR = REPO_ROOT / "catalogs" / "components"


# ── model ────────────────────────────────────────────────────────────────────


def test_component_model_required_fields():
    c = Component(
        name="test",
        bbox=BoundingBox(length=10, width=10, thickness=5),
    )
    assert c.category == "unknown"
    assert c.source == ComponentSource.CATALOG
    assert c.pose.x_mm == 0


def test_bbox_circular_diameter():
    bb = BoundingBox(length=20, width=20, thickness=2, is_circular=True)
    assert bb.diameter == 20


# ── catalog loader ──────────────────────────────────────────────────────────


def test_load_catalog_returns_5_components():
    if not CATALOG_DIR.is_dir():
        pytest.skip("catalogs/components/ 없음")
    comps = load_catalog(CATALOG_DIR)
    assert len(comps) >= 5
    names = {c.name for c in comps}
    assert "Galaxy Watch 44 AMOLED" in names
    assert "Watch Li-Po 350mAh" in names


def test_load_catalog_categories():
    if not CATALOG_DIR.is_dir():
        pytest.skip("catalogs 없음")
    comps = load_catalog(CATALOG_DIR)
    cats = {c.category for c in comps}
    assert {"display", "battery", "crown", "sensor", "wireless_coil"} <= cats


# ── collision ──────────────────────────────────────────────────────────────


def _make(name, x, y, z, l, w, t):
    return Component(
        name=name,
        bbox=BoundingBox(length=l, width=w, thickness=t),
        pose=Pose(x_mm=x, y_mm=y, z_mm=z),
    )


def test_no_collision_when_far_apart():
    a = _make("a", 0, 0, 0, 10, 10, 5)
    b = _make("b", 50, 50, 50, 10, 10, 5)
    assert not has_collision(a, b)


def test_collision_when_overlapping():
    a = _make("a", 0, 0, 0, 10, 10, 5)
    b = _make("b", 3, 3, 0, 10, 10, 5)
    assert has_collision(a, b)


def test_collision_report_finds_pairs():
    components = [
        _make("a", 0, 0, 0, 10, 10, 5),
        _make("b", 3, 3, 0, 10, 10, 5),
        _make("c", 100, 0, 0, 5, 5, 5),
    ]
    report = collision_report(components)
    assert report.has_any
    assert ("a", "b") in report.pairs
    assert not any("c" in pair for pair in report.pairs)


def test_clearance_violation_detected():
    a = Component(
        name="a",
        bbox=BoundingBox(length=10, width=10, thickness=5),
        pose=Pose(x_mm=0, y_mm=0, z_mm=0),
        clearance=ClearanceSpec(side_mm=2.0),
    )
    b = Component(
        name="b",
        bbox=BoundingBox(length=10, width=10, thickness=5),
        pose=Pose(x_mm=11, y_mm=0, z_mm=0),    # gap 1mm < clearance 2mm → 위반
        clearance=ClearanceSpec(side_mm=2.0),
    )
    report = collision_report([a, b])
    assert not report.has_any   # bbox 안 겹침
    assert len(report.clearance_violations) == 1


# ── ComponentArrangement ────────────────────────────────────────────────────


def test_arrangement_estimate_inner_bbox():
    arr = ComponentArrangement()
    arr.add(_make("a", 0, 0, 0, 10, 10, 5))
    arr.add(_make("b", 20, 0, 0, 10, 10, 5))
    xmin, ymin, zmin, xmax, ymax, zmax = arr.estimate_inner_volume_bbox()
    assert abs(xmin - (-5)) < 0.5     # a 의 -X + clearance
    assert xmax > 24                    # b 의 +X 끝


def test_arrangement_housing_bbox():
    arr = ComponentArrangement()
    arr.add(_make("a", 0, 0, 0, 10, 10, 5))
    L, W, T = arr.estimate_housing_bbox(outer_skin_mm=2.0)
    # bbox + 2 * (clearance + skin) — clearance default 0.3, 0.5 등
    assert L >= 10
    assert T >= 5


def test_arrangement_by_category():
    arr = ComponentArrangement()
    arr.add(Component(name="d1", category="display",
                       bbox=BoundingBox(length=20, width=20, thickness=2)))
    arr.add(Component(name="b1", category="battery",
                       bbox=BoundingBox(length=15, width=15, thickness=3)))
    arr.add(Component(name="d2", category="display",
                       bbox=BoundingBox(length=10, width=10, thickness=1)))
    displays = arr.by_category("display")
    assert len(displays) == 2


# ── housing_synth_rule ──────────────────────────────────────────────────────


def test_synthesize_basic_arrangement():
    from phone_designer.components.model import (
        AdhesivePerimeterMount, Port,
    )
    from phone_designer.planner.housing_synth_rule import HousingSynthRule

    arr = ComponentArrangement()
    arr.add(Component(
        name="d", category="display",
        bbox=BoundingBox(length=33, width=33, thickness=2.7, is_circular=True),
        mount_interface=AdhesivePerimeterMount(width_mm=1.5),
        ports=[Port(name="glass", requires_housing_window=True,
                     window_shape={"kind": "circle", "diameter": 34.0})],
    ))
    arr.add(Component(
        name="b", category="battery",
        bbox=BoundingBox(length=28, width=28, thickness=4, is_circular=True),
    ))

    plan = HousingSynthRule().synthesize(arr)
    # 외피 + (display: pad + window) + final_fillet
    assert len(plan.steps) >= 3
    # 첫 step 은 외피 base
    assert plan.steps[0].skill in ("disc_with_dome", "rounded_slab")
    # 마지막 step 은 final_fillet
    assert plan.steps[-1].skill == "final_fillet_all_sharp_edges"
    # display (adhesive_perimeter) 의 window → pocket (관통 X) + mounting_pad
    skills = [s.skill for s in plan.steps]
    assert "extrude_pocket" in skills
    assert "mounting_pad" in skills


def test_synthesize_executes_with_plan_executor():
    """합성된 plan 이 실제로 실행되는지."""
    from phone_designer.components.model import AdhesivePerimeterMount
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.planner.housing_synth_rule import HousingSynthRule

    arr = ComponentArrangement()
    arr.add(Component(
        name="d", category="display",
        bbox=BoundingBox(length=33, width=33, thickness=2.7, is_circular=True),
    ))

    plan = HousingSynthRule().synthesize(arr)
    result = PlanExecutor(plan).run()
    # 일부 step fail 해도 plan 자체는 시도 — outcome 은 status 따라
    # 적어도 첫 step (외피) 은 PASS
    from phone_designer.plan.model import StepStatus
    assert plan.steps[0].status == StepStatus.PASS
