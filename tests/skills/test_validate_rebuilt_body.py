"""validate_rebuilt_body — post-variation rebuild validation (plan P3).

Synthetic cases: a 50 × 50 × 10 slab with one Ø6 hole. A hand-authored
plan dict pointing at the REAL hole must census it as realized; the same
plan pointing where NO hole was cut must report the cut as lost — that is
the exact failure mode this skill exists for (today a rebuild that
silently zero-delta-SKIPped every cut reports PASS).

Corpus case (requires_oem + slow): linkrods 1.5× box-mode rebuild — with
the P1 whitelist fix committed, every world-placed ``hole`` step must
survive into the rebuilt body (features_lost has no hole entries).

The module is imported DIRECTLY (not via export_manifest) so these tests
pass before the orchestrator registers the skill.
"""
from __future__ import annotations

import pathlib

import pytest

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.validate_rebuilt_body import ValidateRebuiltBody
from phone_designer.skills.modify_pocket.hole import Hole

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINKRODS = _REPO_ROOT / "corpus" / "oem" / "complex" / "occt__linkrods.step"

# Hole drilled into the synthetic slab — entry on the top face (z=10).
_HOLE_POS = [10.0, 5.0, 10.0]
_HOLE_D = 6.0
_HOLE_DEPTH = 6.0


def _slab_with_hole():
    """50 × 50 × 10 slab (XY-centred, z ∈ [0, 10]) + Ø6 × 6 blind hole."""
    body = Box().apply(None, {
        "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0,
    }).body
    body = Hole().apply(body, {
        "position": tuple(_HOLE_POS),
        "diameter_mm": _HOLE_D,
        "depth_mm": _HOLE_DEPTH,
        "direction": "-Z",
    }).body
    return body


def _plan_with_hole_at(position):
    """Hand-authored executed-plan dict carrying one world-placed cut."""
    return {
        "steps": [
            {"id": "s_base", "skill": "box", "args": {
                "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0,
            }},
            {"id": "s_hole_0", "skill": "hole", "args": {
                "position": list(position),
                "diameter_mm": _HOLE_D,
                "depth_mm": _HOLE_DEPTH,
                "direction": "-Z",
            }},
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic: realized / lost / brep / bbox


def test_realized_hole_passes_census():
    body = _slab_with_hole()
    res = ValidateRebuiltBody().apply(body, {
        "plan": _plan_with_hole_at(_HOLE_POS),
        "checks": ["features", "brep"],
    })
    rv = res.extras["rebuilt_validation"]
    assert rv["features_expected"] == 1
    assert rv["features_realized"] == 1
    assert rv["features_lost"] == []
    assert rv["valid"] is True
    # read-only contract — same body object back.
    assert res.body is body


def test_lost_hole_is_reported_and_fails():
    """Plan says a hole exists at (-15, -15) but the body was never cut
    there — the census must read MATERIAL inside the expected void."""
    body = _slab_with_hole()
    res = ValidateRebuiltBody().apply(body, {
        "plan": _plan_with_hole_at([-15.0, -15.0, 10.0]),
        "checks": ["features"],
    })
    rv = res.extras["rebuilt_validation"]
    assert rv["features_expected"] == 1
    assert rv["features_realized"] == 0
    assert len(rv["features_lost"]) == 1
    lost = rv["features_lost"][0]
    assert lost["step_id"] == "s_hole_0"
    assert lost["skill"] == "hole"
    assert "material" in lost["reason"]
    assert rv["valid"] is False


def test_brep_check_on_healthy_slab():
    body = _slab_with_hole()
    rv = ValidateRebuiltBody().apply(body, {
        "checks": ["brep"],
    }).extras["rebuilt_validation"]
    assert rv["brep_valid"] is True
    assert rv["valid"] is True


def test_bbox_check_within_half_percent():
    body = _slab_with_hole()
    skill = ValidateRebuiltBody()

    ok = skill.apply(body, {
        "checks": ["bbox"],
        "expected_bbox_mm": [-25.0, -25.0, 0.0, 25.0, 25.0, 10.0],
    }).extras["rebuilt_validation"]
    assert ok["bbox_axis_ratios"] is not None
    assert all(abs(r - 1.0) <= 0.005 for r in ok["bbox_axis_ratios"])
    assert ok["valid"] is True

    # 2% too large in X — outside the 0.5% gate.
    bad = skill.apply(body, {
        "checks": ["bbox"],
        "expected_bbox_mm": [-25.5, -25.0, 0.0, 25.5, 25.0, 10.0],
    }).extras["rebuilt_validation"]
    assert bad["valid"] is False


def test_face_anchored_steps_are_skipped_not_failed():
    """preserve_brep style steps (face_selector + position_xy) carry no
    world anchor — they must be counted in skipped_steps, never lost."""
    body = _slab_with_hole()
    plan = _plan_with_hole_at(_HOLE_POS)
    plan["steps"].append({
        "id": "s_hole_1", "skill": "clearance_hole", "args": {
            "face_selector": {"kind": "face_named", "name": "top"},
            "position_xy": [-15.0, -15.0],
            "thread_spec": "M3",
            "fit": "medium",
            "depth_mm": 5.0,
        },
    })
    rv = ValidateRebuiltBody().apply(body, {
        "plan": plan,
        "checks": ["features"],
    }).extras["rebuilt_validation"]
    assert rv["features_expected"] == 1
    assert rv["features_realized"] == 1
    assert rv["skipped_steps"] == 1
    assert rv["valid"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Corpus: linkrods 1.5× box-mode rebuild (pattern from test_vary_then_execute)


@pytest.mark.requires_oem
@pytest.mark.slow
@pytest.mark.skipif(
    not _LINKRODS.exists(), reason=f"corpus STEP not found: {_LINKRODS}",
)
def test_linkrods_scaled_rebuild_loses_no_hole_steps():
    """With the committed P1 whitelist fix, the 1.5× box rebuild realizes
    every world-placed hole cut — features_lost contains no hole entries."""
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan
    from phone_designer.skills import export_manifest  # noqa: F401
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )
    from phone_designer.skills.reverse_engineer.plan_from_scaled_catalog import (
        PlanFromScaledCatalog,
    )
    try:
        import phone_designer.skills.modify_pocket.extrude_pocket_world  # noqa: F401
    except Exception:
        pass

    body = ImportStep().apply(None, {"path": str(_LINKRODS)}).body
    assert body is not None
    catalog = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    assert not catalog.get("skipped"), f"catalog skipped: {catalog}"

    plan_dict = PlanFromScaledCatalog().apply(body, {
        "catalog": catalog,
        "scale_factor": 1.5,
        "base_step_kind": "box",
    }).extras.get("generated_plan") or {}
    assert plan_dict.get("steps"), "no steps in generated_plan (scale=1.5)"
    plan = Plan.model_validate(plan_dict)
    # Box mode constructs its own s_base placeholder — NO initial_body.
    exec_result = PlanExecutor(plan).run()
    assert exec_result.final_body is not None, (
        f"executor produced no final body (outcome={exec_result.outcome})"
    )

    rv = ValidateRebuiltBody().apply(exec_result.final_body, {
        "plan": plan_dict,
        "checks": ["features", "brep"],
    }).extras["rebuilt_validation"]

    assert rv["features_expected"] >= 2, (
        f"census found only {rv['features_expected']} world-placed cut(s) — "
        "planner emission shape changed?"
    )
    lost_holes = [f for f in rv["features_lost"] if f["skill"] == "hole"]
    assert lost_holes == [], (
        f"{len(lost_holes)} hole cut(s) lost in the 1.5x rebuild "
        f"(pass-23 whitelist regression?): {lost_holes}"
    )
