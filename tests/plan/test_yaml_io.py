"""Plan YAML io 검증."""
from __future__ import annotations

from pathlib import Path

import pytest

from phone_designer.plan.model import Plan, Step
from phone_designer.plan.yaml_io import load_plan, save_plan


def test_load_simple_watch_outer():
    p = Path(__file__).parent.parent.parent / "plans" / "simple_watch_outer.yaml"
    plan = load_plan(p)
    assert plan.schema_version == 1
    assert plan.plan_name == "simple_watch_outer"
    assert len(plan.steps) >= 4
    assert plan.steps[0].skill == "disc_with_dome"


def test_roundtrip(tmp_path):
    plan = Plan(
        schema_version=1,
        plan_name="test_plan",
        steps=[
            Step(id="s1", skill="box",
                 args={"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0}),
            Step(id="s2", skill="fillet_edges_by_predicate",
                 args={
                     "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                     "radius_mm": 1.0,
                 }),
        ],
    )
    out = tmp_path / "plan.yaml"
    save_plan(plan, out)
    loaded = load_plan(out)
    assert loaded.plan_name == "test_plan"
    assert len(loaded.steps) == 2
    assert loaded.steps[0].args["length_mm"] == 10.0


def test_unsupported_schema_version_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 99\nplan_name: x\nsteps: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version=99"):
        load_plan(bad)


def test_missing_top_level_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="top-level"):
        load_plan(bad)
