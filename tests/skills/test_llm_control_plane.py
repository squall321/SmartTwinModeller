"""Wave3-B LLM control plane — unit tests.

Skills under test:
    suggest_selector_from_phrase
    predict_post_conditions
    explain_skill_failure
    find_similar_skills
    plan_diff

All five are read-only registry / metadata helpers. They require a placeholder
body to satisfy the `body_present` post_condition. We reuse a small Box for
that purpose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Side-effect import — registers every skill in the package so that
# predict_post_conditions / find_similar_skills can look up "hole",
# "extrude_pocket", "extrude_through", etc.
import phone_designer.skills.export_manifest  # noqa: F401

from phone_designer.skills.create.box import Box
from phone_designer.skills.inspect.explain_skill_failure import (
    ExplainSkillFailure,
)
from phone_designer.skills.inspect.find_similar_skills import FindSimilarSkills
from phone_designer.skills.inspect.plan_diff import PlanDiff
from phone_designer.skills.inspect.predict_post_conditions import (
    PredictPostConditions,
)
from phone_designer.skills.inspect.suggest_selector_from_phrase import (
    SuggestSelectorFromPhrase,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def placeholder_body():
    return Box().apply(
        None, {"length_mm": 10, "width_mm": 10, "height_mm": 10},
    ).body


# ===========================================================================
# 1) suggest_selector_from_phrase
# ===========================================================================


def test_suggest_metadata():
    spec = SuggestSelectorFromPhrase.spec
    assert spec.name == "suggest_selector_from_phrase"
    assert spec.category == "inspect"
    assert spec.level == "atomic"


def test_suggest_top_face(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "the top face", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    assert isinstance(sugg, list)
    assert len(sugg) >= 1
    top = sugg[0]
    assert top["selector"]["kind"] == "face_named"
    assert top["selector"]["name"] == "top"
    assert 0.0 <= top["confidence"] <= 1.0
    assert "reasoning" in top
    assert top["confidence"] >= 0.5


def test_suggest_bottom_face(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "remove the bottom face", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    kinds_names = [(s["selector"]["kind"], s["selector"].get("name")) for s in sugg]
    assert ("face_named", "bottom") in kinds_names


def test_suggest_tagged_uppercase(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "select the CAMERA_RING tag", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    tags = [s["selector"] for s in sugg if s["selector"]["kind"] == "tagged"]
    assert any(t["tag"] == "CAMERA_RING" for t in tags)


def test_suggest_vertical_edges(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "all vertical edges", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    aae = [s for s in sugg if s["selector"]["kind"] == "axis_aligned_edges"]
    assert len(aae) >= 1
    assert aae[0]["selector"]["axis"] == "Z"


def test_suggest_cylindrical_holes_no_native_selector(placeholder_body):
    """The schema lacks a faces_by_type selector. Skill must still return
    *some* suggestion and explain in `reasoning`."""
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "all cylindrical holes", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    assert len(sugg) >= 1
    joined = " ".join(s["reasoning"] for s in sugg).lower()
    assert "faces_by_type" in joined or "tagged" in joined or "cylindrical" in joined


def test_suggest_no_match_falls_back(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "blah blah", "top_k": 3},
    )
    sugg = r.extras["selector_suggestions"]
    assert len(sugg) >= 1   # always at least the fallback


def test_suggest_top_k_respected(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "the top face and bottom face and CAMERA tag", "top_k": 2},
    )
    sugg = r.extras["selector_suggestions"]
    assert len(sugg) <= 2


def test_suggest_body_unchanged(placeholder_body):
    r = SuggestSelectorFromPhrase().apply(
        placeholder_body, {"phrase": "top face"},
    )
    assert r.body is placeholder_body


# ===========================================================================
# 2) predict_post_conditions
# ===========================================================================


def test_predict_metadata():
    spec = PredictPostConditions.spec
    assert spec.name == "predict_post_conditions"
    assert spec.category == "inspect"


def test_predict_hole_negative_delta(placeholder_body):
    r = PredictPostConditions().apply(
        placeholder_body,
        {
            "skill_name": "hole",
            "current_body_volume_mm3": 6000.0,
            "target_args": {
                "position": [0.0, 0.0, 5.0],
                "diameter_mm": 4.0,
                "depth_mm": 5.0,
                "direction": "-Z",
            },
        },
    )
    pred = r.extras["prediction"]
    assert pred["skill_name"] == "hole"
    assert pred["error"] is None
    # Approx: π * 2² * 5 = 62.83 mm³ removed
    import math
    expected = -math.pi * 4.0 * 5.0
    assert pred["volume_delta_estimate_mm3"] == pytest.approx(expected, rel=1e-3)
    assert pred["volume_delta_sign"] == "negative"
    pc_kinds = {p["kind"] for p in pred["post_conditions_expected"]}
    assert "volume_decreased" in pc_kinds


def test_predict_pocket_circle(placeholder_body):
    r = PredictPostConditions().apply(
        placeholder_body,
        {
            "skill_name": "extrude_pocket",
            "current_body_volume_mm3": 16000.0,
            "target_args": {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": {"kind": "circle", "diameter_mm": 10.0},
                "depth_mm": 3.0,
            },
        },
    )
    pred = r.extras["prediction"]
    import math
    expected = -math.pi * 25.0 * 3.0  # -π · 5² · 3
    assert pred["volume_delta_estimate_mm3"] == pytest.approx(expected, rel=1e-3)
    assert pred["volume_delta_sign"] == "negative"


def test_predict_unknown_skill_no_raise(placeholder_body):
    r = PredictPostConditions().apply(
        placeholder_body,
        {
            "skill_name": "absolutely_not_a_real_skill_xyz",
            "current_body_volume_mm3": 100.0,
            "target_args": {},
        },
    )
    pred = r.extras["prediction"]
    assert pred["error"] is not None
    assert "absolutely_not_a_real_skill_xyz" in pred["error"]
    assert pred["volume_delta_estimate_mm3"] is None


def test_predict_no_estimator_sign_only(placeholder_body):
    """Skills without a closed-form estimator still get sign from post_conditions."""
    r = PredictPostConditions().apply(
        placeholder_body,
        {
            "skill_name": "inspect_geometry",
            "current_body_volume_mm3": 100.0,
            "target_args": {},
        },
    )
    pred = r.extras["prediction"]
    assert pred["error"] is None
    # inspect_geometry declares body_present → unchanged
    assert pred["volume_delta_sign"] in ("unchanged", "unknown")


def test_predict_body_unchanged(placeholder_body):
    r = PredictPostConditions().apply(
        placeholder_body,
        {
            "skill_name": "hole",
            "current_body_volume_mm3": 1.0,
            "target_args": {"position": [0, 0, 0], "diameter_mm": 1.0, "depth_mm": 1.0},
        },
    )
    assert r.body is placeholder_body


# ===========================================================================
# 3) explain_skill_failure
# ===========================================================================


def test_explain_metadata():
    spec = ExplainSkillFailure.spec
    assert spec.name == "explain_skill_failure"
    assert spec.category == "inspect"


def test_explain_zero_match(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "s3",
            "failure_message": "face_selector matched 0 faces: {kind=face_named}",
        },
    )
    diag = r.extras["diagnosis"]
    assert "matched 0" in diag["likely_cause"].lower() or "0" in diag["likely_cause"]
    assert "dry_run" in diag["related_skills_to_run"]
    assert diag["failed_step_id"] == "s3"


def test_explain_depth_exceeds_body(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "s4",
            "failure_message": "fm.hole_exits_body: depth exceeds body thickness",
        },
    )
    diag = r.extras["diagnosis"]
    cause = diag["likely_cause"].lower()
    assert "depth" in cause or "exit" in cause


def test_explain_post_condition_volume_decreased(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "s2",
            "failure_message": (
                "extrude_pocket: post_condition 'volume_decreased' failed — "
                "pre=16000 mm³, post=16000 mm³"
            ),
        },
    )
    diag = r.extras["diagnosis"]
    assert "no material" in diag["likely_cause"].lower() or \
        "volume_decreased" in diag["failure_message"]
    assert "inspect_geometry" in diag["related_skills_to_run"]


def test_explain_unknown_skill(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "sX",
            "failure_message": "unknown skill: weird_skill_xyz",
        },
    )
    diag = r.extras["diagnosis"]
    assert "find_skill_by_intent" in diag["related_skills_to_run"]


def test_explain_with_plan_yaml(placeholder_body, tmp_path):
    plan_path = tmp_path / "p.yaml"
    plan_path.write_text(
        "schema_version: 1\n"
        "plan_name: test\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: extrude_pocket\n"
        "    args:\n"
        "      face_selector:\n"
        "        kind: face_named\n"
        "        name: top\n"
        "      sketch: {kind: circle, diameter_mm: 5}\n"
        "      depth_mm: 1\n",
        encoding="utf-8",
    )
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "s1",
            "failure_message": "face_selector matched 0 faces",
            "plan_path": str(plan_path),
        },
    )
    diag = r.extras["diagnosis"]
    assert diag["plan_step_found"] is True
    assert diag["skill_name"] == "extrude_pocket"


def test_explain_missing_plan_path_safe(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {
            "failed_step_id": "s1",
            "failure_message": "something failed",
            "plan_path": "/nonexistent/path/plan.yaml",
        },
    )
    diag = r.extras["diagnosis"]
    assert diag["plan_step_found"] is False
    assert diag["error"] is not None


def test_explain_body_unchanged(placeholder_body):
    r = ExplainSkillFailure().apply(
        placeholder_body,
        {"failed_step_id": "x", "failure_message": "anything"},
    )
    assert r.body is placeholder_body


# ===========================================================================
# 4) find_similar_skills
# ===========================================================================


def test_similar_metadata():
    spec = FindSimilarSkills.spec
    assert spec.name == "find_similar_skills"
    assert spec.category == "inspect"


def test_similar_hole_returns_other_holes(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "hole", "top_k": 10},
    )
    similar = r.extras["similar"]
    assert isinstance(similar, list)
    assert len(similar) > 0
    names = [s["skill_name"] for s in similar]
    # At least one other hole-like skill should appear.
    assert any("hole" in n for n in names)
    # All entries have similarity in [0, 1]
    for s in similar:
        assert 0.0 <= s["similarity"] <= 1.0
        assert "summary" in s
        assert "shared_tokens" in s


def test_similar_extrude_pocket_includes_extrude_through(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "extrude_pocket", "top_k": 20},
    )
    similar = r.extras["similar"]
    names = [s["skill_name"] for s in similar]
    # Both are modify/pocket, share 'extrude' token.
    assert "extrude_through" in names


def test_similar_unknown_skill_no_raise(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "absolutely_not_a_skill_xyz", "top_k": 5},
    )
    assert r.extras["similar"] == []
    assert r.extras["error"] is not None


def test_similar_top_k_respected(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "hole", "top_k": 3},
    )
    assert len(r.extras["similar"]) <= 3


def test_similar_sorted_desc(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "hole", "top_k": 10},
    )
    sims = [s["similarity"] for s in r.extras["similar"]]
    assert sims == sorted(sims, reverse=True)


def test_similar_body_unchanged(placeholder_body):
    r = FindSimilarSkills().apply(
        placeholder_body, {"skill_name": "hole"},
    )
    assert r.body is placeholder_body


# ===========================================================================
# 5) plan_diff
# ===========================================================================


def test_plan_diff_metadata():
    spec = PlanDiff.spec
    assert spec.name == "plan_diff"
    assert spec.category == "inspect"


def _write_plan(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_plan_diff_added_step(placeholder_body, tmp_path):
    a = _write_plan(tmp_path, "a.yaml",
        "schema_version: 1\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: hole\n"
        "    args: {position: [0,0,0], diameter_mm: 2, depth_mm: 5}\n"
    )
    b = _write_plan(tmp_path, "b.yaml",
        "schema_version: 1\n"
        "steps:\n"
        "  - id: s1\n"
        "    skill: hole\n"
        "    args: {position: [0,0,0], diameter_mm: 2, depth_mm: 5}\n"
        "  - id: s2\n"
        "    skill: hole\n"
        "    args: {position: [5,5,0], diameter_mm: 1, depth_mm: 2}\n"
    )
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    diff = r.extras["diff"]
    by_id = {e["step_id"]: e for e in diff}
    assert by_id["s1"]["change"] == "unchanged"
    assert by_id["s2"]["change"] == "added"
    assert r.extras["summary_counts"]["added"] == 1
    assert r.extras["summary_counts"]["unchanged"] == 1


def test_plan_diff_removed_step(placeholder_body, tmp_path):
    a = _write_plan(tmp_path, "a.yaml",
        "steps:\n"
        "  - id: s1\n    skill: hole\n    args: {diameter_mm: 2}\n"
        "  - id: s2\n    skill: hole\n    args: {diameter_mm: 3}\n"
    )
    b = _write_plan(tmp_path, "b.yaml",
        "steps:\n"
        "  - id: s1\n    skill: hole\n    args: {diameter_mm: 2}\n"
    )
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    by_id = {e["step_id"]: e for e in r.extras["diff"]}
    assert by_id["s2"]["change"] == "removed"
    assert r.extras["summary_counts"]["removed"] == 1


def test_plan_diff_skill_changed(placeholder_body, tmp_path):
    a = _write_plan(tmp_path, "a.yaml",
        "steps:\n"
        "  - id: s1\n    skill: hole\n    args: {diameter_mm: 2}\n"
    )
    b = _write_plan(tmp_path, "b.yaml",
        "steps:\n"
        "  - id: s1\n    skill: extrude_pocket\n    args: {depth_mm: 3}\n"
    )
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    e = r.extras["diff"][0]
    assert e["change"] == "skill_changed"
    assert e["skill_a"] == "hole"
    assert e["skill_b"] == "extrude_pocket"


def test_plan_diff_args_changed(placeholder_body, tmp_path):
    a = _write_plan(tmp_path, "a.yaml",
        "steps:\n"
        "  - id: s1\n    skill: hole\n    args: {diameter_mm: 2, depth_mm: 5}\n"
    )
    b = _write_plan(tmp_path, "b.yaml",
        "steps:\n"
        "  - id: s1\n    skill: hole\n    args: {diameter_mm: 3, depth_mm: 5}\n"
    )
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    e = r.extras["diff"][0]
    assert e["change"] == "args_changed"
    field_diffs = e["arg_field_diffs"]
    assert any(
        d["field"] == "diameter_mm" and d["before"] == 2 and d["after"] == 3
        for d in field_diffs
    )


def test_plan_diff_missing_file(placeholder_body, tmp_path):
    b = _write_plan(tmp_path, "b.yaml", "steps:\n  - id: s1\n    skill: hole\n    args: {}\n")
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": "/no/such/file.yaml", "plan_path_b": str(b)},
    )
    assert r.extras["error_a"] is not None
    # b still parsed → s1 should show as "added"
    by_id = {e["step_id"]: e for e in r.extras["diff"]}
    assert by_id["s1"]["change"] == "added"


def test_plan_diff_unchanged(placeholder_body, tmp_path):
    body = "steps:\n  - id: s1\n    skill: hole\n    args: {diameter_mm: 2}\n"
    a = _write_plan(tmp_path, "a.yaml", body)
    b = _write_plan(tmp_path, "b.yaml", body)
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    assert r.extras["summary_counts"]["unchanged"] == 1
    assert r.extras["summary_counts"]["added"] == 0
    assert r.extras["summary_counts"]["removed"] == 0


def test_plan_diff_body_unchanged(placeholder_body, tmp_path):
    a = _write_plan(tmp_path, "a.yaml", "steps: []\n")
    b = _write_plan(tmp_path, "b.yaml", "steps: []\n")
    r = PlanDiff().apply(
        placeholder_body,
        {"plan_path_a": str(a), "plan_path_b": str(b)},
    )
    assert r.body is placeholder_body
