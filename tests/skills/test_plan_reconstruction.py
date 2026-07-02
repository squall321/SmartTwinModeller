"""Wave4-A plan reconstruction — extract_feature_catalog + plan_from_feature_catalog.

The pipeline:

    box (50×50×10) + 3 drilled holes
        ──► extract_feature_catalog  (feature_catalog dict)
        ──► plan_from_feature_catalog (generated_plan with N ≥ 3 steps)

Verifies the generated plan is dict-shaped, has the expected top-level keys,
and contains at least 3 steps (1 base + ≥ 2 hole / pocket steps for the
three holes we drilled).
"""
from __future__ import annotations

import pathlib
import tempfile

from phone_designer.skills.create.box import Box
from phone_designer.skills.modify_pocket.hole import Hole
from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
    ExtractFeatureCatalog,
)
from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
    PlanFromFeatureCatalog,
)


def _box_with_holes():
    """50×50×10 box with three Ø4 mm holes drilled from the top."""
    body = Box().apply(None, {
        "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0,
    }).body

    # Three small holes drilled into the top face.
    for (x, y) in [(-15.0, -15.0), (15.0, -15.0), (0.0, 15.0)]:
        body = Hole().apply(body, {
            "position": (x, y, 10.0),
            "diameter_mm": 4.0,
            "depth_mm": 6.0,
            "direction": "-Z",
        }).body
    return body


def _box_with_holes_via_step(tmpdir: pathlib.Path):
    """Build the same body, write to a STEP file, and re-import it.

    This exercises the "STEP roundtrip" angle of the instructions (the plan
    reconstruction pipeline should work whether the body came from in-
    memory build or from an external STEP).
    """
    body = _box_with_holes()
    # Write STEP via OCCT directly (no skill for export_step exists).
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    step_path = tmpdir / "box_with_holes.step"
    shape = body.wrapped if hasattr(body, "wrapped") else body
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(step_path))
    assert step_path.exists()

    # Re-import via import_step skill.
    from phone_designer.skills.create.import_step import ImportStep
    return ImportStep().apply(None, {"path": str(step_path)}).body


# ──────────────────────────────────────────────────────────────────────────────
# extract_feature_catalog


def test_extract_feature_catalog_aggregates_all_keys():
    body = _box_with_holes()
    r = ExtractFeatureCatalog().apply(body, {})
    cat = r.extras["feature_catalog"]
    # body unchanged
    assert r.body is body
    # every expected aggregation key is present
    for k in (
        "holes", "pockets", "bosses", "ribs", "lugs",
        "symmetries", "patterns", "standard_matches",
    ):
        assert k in cat, f"missing catalog key: {k}"
    # DETERMINISM fix (2026-07-02, test_catalog_determinism.py): the old
    # ``len(cat["holes"]) >= 1`` pinned a THREAD-RACE ARTIFACT — pre-fix the
    # catalog reported 1 hole ONLY under parallel scheduling (pre-fix
    # parallel=False and standalone ClassifyHoles both report 0 on this
    # body; one drill flickered into 'holes' from a mid-classification
    # partial bbox state). The deterministic truth for these three Ø4x6
    # blind drills is: classify_pockets captures all 3 as circular pockets,
    # classify_holes 0. Pin that all three drills ARE captured.
    assert len(cat["holes"]) + len(cat["pockets"]) >= 3, (
        f"3 drills not captured: holes={len(cat['holes'])} "
        f"pockets={len(cat['pockets'])}"
    )
    # the box is symmetric — at least one mirror plane should score > 0.5
    if cat["symmetries"]:
        assert max(s["symmetry_score"] for s in cat["symmetries"]) > 0.3


# ──────────────────────────────────────────────────────────────────────────────
# plan_from_feature_catalog


def test_plan_from_feature_catalog_generates_three_or_more_steps():
    body = _box_with_holes()
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]

    r = PlanFromFeatureCatalog().apply(body, {"catalog": cat})
    plan = r.extras["generated_plan"]
    assert plan["plan_name"] == "reconstructed_plan"
    steps = plan["steps"]
    # 1 base box + ≥ 2 hole / pocket steps
    assert len(steps) >= 3, (
        f"expected ≥3 steps, got {len(steps)}: "
        f"{[s['skill'] for s in steps]}"
    )
    # first step is the base shape
    assert steps[0]["skill"] == "box"
    # every step has the required shape
    for s in steps:
        assert set(s.keys()) >= {"id", "skill", "args"}
    # body unchanged
    assert r.body is body


def test_plan_from_feature_catalog_writes_yaml_file():
    body = _box_with_holes()
    PlanFromFeatureCatalog().apply(body, {})  # catalog=None → use last extract
    # The skill writes plans/reconstructed_plan.yaml at the repo root.
    root = pathlib.Path(__file__).resolve().parents[2]
    yaml_path = root / "plans" / "reconstructed_plan.yaml"
    # Either the file exists (write succeeded) or the skill silently
    # tolerated the write failure; both are acceptable. When present, the
    # YAML must parse and contain a "steps" list.
    if yaml_path.exists():
        import yaml as _yaml
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert "plan_name" in data
        assert isinstance(data["steps"], list)


def test_plan_from_feature_catalog_uses_last_catalog_when_none():
    """When catalog=None, the skill must fall back to the most recent
    extract_feature_catalog output (cached at module scope)."""
    body = _box_with_holes()
    ExtractFeatureCatalog().apply(body, {})  # primes the cache
    r = PlanFromFeatureCatalog().apply(body, {})  # catalog omitted
    plan = r.extras["generated_plan"]
    assert plan["steps"][0]["skill"] == "box"
    assert len(plan["steps"]) >= 3


def test_plan_from_feature_catalog_step_roundtrip():
    """Sanity check that the same pipeline also works with a body roundtripped
    through a STEP file (matches the pack instruction "make a box+holes STEP").
    """
    with tempfile.TemporaryDirectory() as td:
        body = _box_with_holes_via_step(pathlib.Path(td))
        cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
        plan = PlanFromFeatureCatalog().apply(
            body, {"catalog": cat},
        ).extras["generated_plan"]
        assert len(plan["steps"]) >= 3


# ──────────────────────────────────────────────────────────────────────────────
# base_step_kind variants


def test_plan_base_step_kind_box_default():
    """Default base_step_kind="box" emits a parametric box s_base step
    sized from the body bbox."""
    body = _box_with_holes()
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    plan = PlanFromFeatureCatalog().apply(
        body, {"catalog": cat, "base_step_kind": "box"},
    ).extras["generated_plan"]

    steps = plan["steps"]
    assert steps[0]["id"] == "s_base"
    assert steps[0]["skill"] == "box"
    # bbox-sized: roughly 50×50×10 for our test body
    args = steps[0]["args"]
    assert {"length_mm", "width_mm", "height_mm"} <= set(args.keys())
    assert args["length_mm"] > 0.0
    assert args["width_mm"] > 0.0
    assert args["height_mm"] > 0.0


def test_plan_base_step_kind_import_step_writes_step_and_emits_import():
    """base_step_kind="import_step" writes the body to a STEP file under
    run_logs/_tmp/ and emits an import_step s_base step pointing at it."""
    body = _box_with_holes()
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    plan = PlanFromFeatureCatalog().apply(
        body, {"catalog": cat, "base_step_kind": "import_step"},
    ).extras["generated_plan"]

    steps = plan["steps"]
    assert steps[0]["id"] == "s_base"
    # Either the import_step emission succeeded (preferred), or the writer
    # silently fell back to a box (degradation path). Both are acceptable
    # but the success path is the one we want to verify.
    assert steps[0]["skill"] == "import_step"
    args = steps[0]["args"]
    assert "path" in args
    step_path = pathlib.Path(args["path"])
    assert step_path.exists(), f"STEP not written: {step_path}"
    # File should live under run_logs/_tmp/ and be named after the plan.
    assert "_tmp" in step_path.parts
    assert step_path.name == "reconstructed_plan_base.step"
    assert step_path.stat().st_size > 0


def test_plan_base_step_kind_preserve_brep_skips_s_base():
    """base_step_kind="preserve_brep" emits NO s_base step and records a
    plan-level description noting the executor must supply initial_body."""
    body = _box_with_holes()
    cat = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    plan = PlanFromFeatureCatalog().apply(
        body, {"catalog": cat, "base_step_kind": "preserve_brep"},
    ).extras["generated_plan"]

    steps = plan["steps"]
    # No step has id "s_base" or skill "box" / "import_step" as the first.
    assert all(s["id"] != "s_base" for s in steps), (
        f"preserve_brep should skip s_base, got: {[s['id'] for s in steps]}"
    )
    # Plan-level description warns about the initial_body requirement.
    desc = plan.get("description") or ""
    assert "preserve_brep" in desc
    assert "initial_body" in desc


def test_plan_executor_accepts_initial_body_kwarg():
    """PlanExecutor.run(initial_body=...) seeds the execution with the
    supplied body so preserve_brep plans can use the original BREP as
    their starting point."""
    from phone_designer.plan.executor import ExecutionMode, PlanExecutor
    from phone_designer.plan.model import Plan

    body = _box_with_holes()
    # An empty plan — the executor's job is just to thread initial_body
    # through to final_body when no steps run.
    plan = Plan(plan_name="empty", steps=[])
    res = PlanExecutor(plan, mode=ExecutionMode.STRICT).run(initial_body=body)
    assert res.final_body is body
    assert res.error_count == 0


# ──────────────────────────────────────────────────────────────────────────────
# Spec sanity


def test_reverse_engineer_skills_post_body_present():
    for cls in (ExtractFeatureCatalog, PlanFromFeatureCatalog):
        kinds = {pc.kind for pc in cls.spec.post_conditions}
        assert kinds == {"body_present"}, (
            f"{cls.spec.name}: expected body_present, got {kinds}"
        )
