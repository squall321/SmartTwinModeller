"""Manifest export 검증."""
from __future__ import annotations

import json

from phone_designer.skills.export_manifest import build_manifest


def test_manifest_has_registered_skills():
    m = build_manifest()
    names = [s["name"] for s in m["skills"]]
    # Phase 1 의 8 skill (3 macro + 5 atomic)
    expected = {
        "box",
        "rounded_slab",
        "disc_with_dome",
        "fillet_edges_by_predicate",
        "chamfer_edges_by_predicate",
        "extrude_pocket",
        "extrude_plateau",
        "hole",
    }
    missing = expected - set(names)
    assert not missing, f"manifest 에서 누락된 skill: {missing}"


def test_manifest_atomic_macro_counts():
    m = build_manifest()
    atomic = [s for s in m["skills"] if s["level"] == "atomic"]
    macro = [s for s in m["skills"] if s["level"] == "macro"]
    # Phase 1 끝 시점: atomic 5 (box, fillet, chamfer, hole, extrude_pocket, extrude_plateau)
    # macro 3 (rounded_slab, disc_with_dome ... wait 2 only)
    assert len(atomic) >= 5
    assert len(macro) >= 2


# Dynamic front-door / orchestrator macros build their SkillResult IN ``_apply``
# (conditional, failure-isolated stages — NOT a fixed atomic sequence), so a
# static ``expansion`` list is meaningless for them and ``expansion=None`` is
# correct (SkillSpec allows ``list[str] | None``). Every OTHER (static composite)
# macro must still declare its expansion, so this allowlist keeps that check
# while exempting the legitimate orchestrators.
_DYNAMIC_ORCHESTRATOR_MACROS = {
    "compare_parts", "analyze_part", "generate_variant_family",
    "solve_driver_range", "assembly_reverse_engineer",
    "emit_parametric_script", "estimate_cost", "recognize_fits",
    "detect_sheet_metal", "measure_assembly_fit", "recommend_process",
    "generate_from_spec", "repair_dfm", "cost_min_variant_search",
}


def test_macro_skills_have_expansion():
    m = build_manifest()
    for s in m["skills"]:
        if s["level"] == "macro" and s["name"] not in _DYNAMIC_ORCHESTRATOR_MACROS:
            assert s["expansion"] is not None, (
                f"static macro {s['name']} 의 expansion 누락 (동적 orchestrator 면 "
                f"_DYNAMIC_ORCHESTRATOR_MACROS 에 추가)"
            )
            assert len(s["expansion"]) >= 1, f"macro {s['name']} 의 expansion 비어있음"


def test_manifest_json_serializable():
    m = build_manifest()
    s = json.dumps(m, ensure_ascii=False, indent=2)
    assert "box" in s
    assert "schema_version" in s


def test_manifest_has_selector_schemas():
    m = build_manifest()
    assert "tagged" in m["selectors"]
    assert "axis_aligned_edges" in m["selectors"]
    assert "and" in m["selectors"]


def test_skill_args_schema_valid():
    m = build_manifest()
    for skill in m["skills"]:
        assert "args_schema" in skill
        assert isinstance(skill["args_schema"], dict)


def test_skill_level_atomic_or_macro():
    m = build_manifest()
    for skill in m["skills"]:
        assert skill["level"] in {"atomic", "macro"}


def test_skill_history_rules_use_enum_values():
    m = build_manifest()
    valid = {"modified_inherit", "split_branch", "consumed", "generated_new"}
    for skill in m["skills"]:
        for role, rule in skill.get("history_rules", {}).items():
            assert rule in valid, f"{skill['name']}.{role}: bad rule {rule}"
