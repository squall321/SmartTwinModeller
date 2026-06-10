"""assembly_reverse_engineer — 2-solid compound smoke test.

The macro splits the compound into shells, runs the full per-component RE
loop (ExtractFeatureCatalog → PlanFromFeatureCatalog → PlanExecutor →
FeatureFidelityDiff) and aggregates a volume-weighted match. Two plain
disjoint boxes are the minimal honest input: each is a closed shell above
the 100 mm³ volume floor, each produces a per-component plan, and the
loop must complete without per-component errors.

NOTE: PlanFromFeatureCatalog writes plans/reconstructed_plan.yaml at the
repo root as a side effect — same contract the in-proc runner uses.
"""
from __future__ import annotations

import pytest
from build123d import Box as B3dBox, Part, Pos
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

from phone_designer.skills.reverse_engineer.assembly_reverse_engineer import (
    AssemblyReverseEngineer,
)


def _two_box_compound():
    """Two disjoint 20x20x10 boxes (4000 mm³ each) in one compound."""
    a = B3dBox(20, 20, 10)
    b = Pos(50, 0, 0) * B3dBox(20, 20, 10)
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    builder.Add(comp, a.wrapped)
    builder.Add(comp, b.wrapped)
    return Part(comp)


def test_args_default_to_preserve_brep():
    # Assembly components are real BREP shells — the documented default.
    args = AssemblyReverseEngineer.Args()
    assert args.base_step_kind == "preserve_brep"
    assert args.top_n == 10
    assert args.min_volume_mm3 == 100.0


def test_two_solid_compound_returns_per_component_plans_without_error():
    body = _two_box_compound()
    res = AssemblyReverseEngineer().apply(body, {"top_n": 2})
    extras = res.extras

    assert extras["components_total"] == 2
    assert extras["components_processed"] == 2
    results = extras["assembly_re_results"]
    assert len(results) == 2

    for entry in results:
        assert entry["skipped"] is False, entry
        assert entry["error"] is None, entry
        assert entry["volume_mm3"] == pytest.approx(4000.0, rel=1e-3)
        # A plan was produced for the component (preserve_brep plans on a
        # featureless box may legitimately have zero steps — only None
        # would mean "never planned").
        assert entry["plan_steps"] is not None
        assert entry["plan_steps"] >= 0
        assert entry["match"] is not None
        assert 0.0 <= entry["match"] <= 1.0

    assert 0.0 <= extras["aggregate_match_ratio"] <= 1.0
    # Read-only macro — the input body comes back unchanged.
    assert res.body is body


def test_volume_floor_skips_debris_components():
    # Raise the floor above both boxes → nothing survives the split.
    body = _two_box_compound()
    res = AssemblyReverseEngineer().apply(
        body, {"min_volume_mm3": 10000.0},
    )
    assert res.extras["components_total"] == 0
    assert res.extras["components_processed"] == 0
    assert res.extras["aggregate_match_ratio"] == 0.0
