"""Phase 1 확장 검증 — history catalog 생성.

각 atomic skill 을 표준 입력으로 한 번 실행한 뒤 history map 의 propagation
성공 여부 catalog 생성. [[lat.md/persistent-naming#확장-검증]] 의 70% 목표 측정.

출력: docs/reports/history_rule_catalog.md (markdown)
실행:
    python -m phone_designer.skills.audit
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Side-effect: 모든 skill 등록
from phone_designer.skills import export_manifest  # noqa: F401
from phone_designer.skills._registry import registry
from phone_designer.skills._spec import SkillBase


def _make_standard_body():
    """모든 modify skill 의 입력으로 쓸 standard body — 20×20×10 box."""
    from phone_designer.skills.create.box import Box
    return Box().apply(None, {"length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0}).body


def _default_args_for(skill_name: str) -> dict[str, Any]:
    """skill 별 audit 용 기본 인자."""
    return {
        "box":
            {"length_mm": 10.0, "width_mm": 10.0, "height_mm": 10.0},
        "rounded_slab":
            {"length_mm": 20.0, "width_mm": 20.0, "height_mm": 10.0, "corner_r_mm": 2.0},
        "disc_with_dome":
            {"diameter_mm": 40.0, "height_mm": 10.0, "dome_rise_mm": 1.5, "corner_r_mm": 2.0},
        "fillet_edges_by_predicate":
            {"selector": {"kind": "axis_aligned_edges", "axis": "Z"}, "radius_mm": 1.5},
        "chamfer_edges_by_predicate":
            {"selector": {"kind": "axis_aligned_edges", "axis": "Z"}, "width_mm": 0.5},
        "extrude_pocket":
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": {"kind": "circle", "diameter_mm": 6.0},
                "depth_mm": 1.0,
            },
        "extrude_plateau":
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": {"kind": "circle", "diameter_mm": 5.0},
                "height_mm": 2.0,
            },
        "hole":
            {
                "position": (0.0, 0.0, 10.0),
                "diameter_mm": 3.0,
                "depth_mm": 5.0,
                "direction": "-Z",
            },
        # Phase 4 batch 4-1
        "import_step":
            {"path": "fixtures/simple_watch_components/battery.step"},
        "subtract":
            {
                "other": {
                    "kind": "primitive_box",
                    "length_mm": 4, "width_mm": 4, "height_mm": 12,
                    "position": (0, 0, 0),
                },
            },
        "union":
            {
                "other": {
                    "kind": "primitive_box",
                    "length_mm": 5, "width_mm": 5, "height_mm": 5,
                    "position": (15, 0, 5),
                },
            },
        "extrude_through":
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": {"kind": "circle", "diameter_mm": 3.0},
            },
        "hole_array":
            {
                "points": [(-5, 0, 10), (0, 0, 10), (5, 0, 10)],
                "diameter_mm": 1.5,
                "depth_mm": 5.0,
                "direction": "-Z",
            },
        "final_fillet_all_sharp_edges":
            {"radius_mm": 0.2},
        "tag_face":
            {
                "selector": {"kind": "face_named", "name": "top"},
                "tag": "audit_top_tag",
            },
        # Phase 4 batch 4-2 (워치 특화 macro)
        "crown_shaft_hole":
            {
                "position": (10.0, 0.0, 5.0),
                "direction": "+X",
                "shaft_d_mm": 2.0,
                "shaft_depth_mm": 5.0,
                "bearing_recess_d_mm": 4.0,
                "bearing_recess_depth_mm": 1.0,
                "plinth_d_mm": 0.0,
                "plinth_height_mm": 0.0,
            },
        "lug_pair":
            {
                "axis": "+Y/-Y",
                "offset_mm": 8.0,
                "z_position_mm": 5.0,
                "lug_length_mm": 4.0,
                "lug_thickness_mm": 2.0,
                "lug_height_mm": 2.0,
                "pin_diameter_mm": 1.0,
            },
        "o_ring_groove":
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "outer_diameter_mm": 16.0,
                "inner_diameter_mm": 13.0,
                "depth_mm": 0.5,
            },
        # Phase 4 batch 4-3 (boss / rib / snap / mounting_pad)
        "boss_with_hole":
            {
                "position": (0.0, 0.0, 0.0),
                "direction": "+Z",
                "boss_diameter_mm": 4.0,
                "boss_height_mm": 5.0,
                "hole_diameter_mm": 2.0,
                "hole_depth_mm": 4.0,
            },
        "rib":
            {
                "start": (-5.0, 0.0, 0.0),
                "end": (5.0, 0.0, 0.0),
                "width_mm": 1.0,
                "height_mm": 2.0,
                "draft_deg": 0.0,
                "up_axis": "+Z",
            },
        "snap_hook":
            {
                "base_position": (0.0, 0.0, 10.0),
                "cantilever_length_mm": 3.0,
                "cantilever_width_mm": 2.0,
                "cantilever_thickness_mm": 0.8,
                "lip_overhang_mm": 0.5,
                "lip_height_mm": 0.8,
                "cantilever_axis": "+Z",
            },
        "mounting_pad":
            {
                "face_selector": {"kind": "face_named", "name": "top"},
                "sketch": {"kind": "rectangle", "length_mm": 5.0, "width_mm": 3.0},
                "height_mm": 0.5,
            },
        # Phase 4 batch 4-4 (고급 곡률)
        "variable_radius_fillet":
            {
                "selector": {"kind": "axis_aligned_edges", "axis": "Z"},
                "radii_mm": [0.5, 1.0, 0.5, 1.0],
            },
        "loft_side_profile":
            {
                "bottom": {"kind": "circle", "diameter_mm": 10.0, "z_mm": 0.0},
                "top":    {"kind": "circle", "diameter_mm": 6.0,  "z_mm": 8.0},
            },
        "swept_relief":
            {
                "start": (-5.0, 0.0, 5.0),
                "end":   (5.0, 0.0, 5.0),
                "width_mm": 1.0,
                "depth_mm": 1.5,
            },
        "surface_offset":
            {"offset_mm": 0.2},
        "grille_pattern":
            {
                "center": (0.0, 0.0, 10.0),
                "pattern": "hex",
                "window_length_mm": 6.0,
                "window_width_mm": 4.0,
                "hole_diameter_mm": 0.6,
                "spacing_mm": 1.2,
                "depth_mm": 5.0,
                "direction": "-Z",
            },
        # Phase 4 batch 4-5 (안테나)
        "antenna_slit":
            {
                "start": (-8.0, 0.0, 5.0),
                "end":   (8.0, 0.0, 5.0),
                "width_mm": 0.6,
                "depth_mm": 8.0,
            },
        "polymer_inlay":
            {
                "start": (-8.0, 5.0, 5.0),
                "end":   (8.0, 5.0, 5.0),
                "width_mm": 0.6,
                "depth_mm": 8.0,
            },
    }.get(skill_name, {})


def audit_skill(name: str, spec, body_for_create_or_modify) -> dict[str, Any]:
    """1 skill 의 audit — apply 시도 + history rule 매핑 + 실행 시간."""
    cls = spec.implementation_class
    if cls is None:
        return {"name": name, "status": "no_impl"}

    args = _default_args_for(name)
    inst: SkillBase = cls()
    body_input = None if spec.category.startswith("create") else body_for_create_or_modify

    started = time.time()
    try:
        result = inst.apply(body_input, args)
        duration = time.time() - started

        # history 정합성 — declared rules vs actually present in result.history.rules
        declared = set(spec.history_rules.keys())
        actual = set(result.history.rules.keys())
        match = declared <= actual

        return {
            "name": name,
            "level": spec.level,
            "category": spec.category,
            "status": "PASS",
            "duration_ms": round(duration * 1000, 1),
            "history_rules_declared": list(declared),
            "history_rules_present_in_result": list(actual),
            "history_match": match,
            "freeze_present": result.selector_freeze is not None,
            "new_entities": len(result.history.new_entities),
        }
    except Exception as e:
        duration = time.time() - started
        return {
            "name": name,
            "level": spec.level,
            "category": spec.category,
            "status": "FAIL",
            "duration_ms": round(duration * 1000, 1),
            "error": str(e),
            "error_type": type(e).__name__,
        }


def write_markdown_report(audits: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pass_count = sum(1 for a in audits if a["status"] == "PASS")
    fail_count = sum(1 for a in audits if a["status"] == "FAIL")
    total = len(audits)
    propagate_count = sum(1 for a in audits if a.get("history_match", False))

    lines = [
        f"<!-- generated_at: {datetime.now(timezone.utc).isoformat()} -->",
        "# Phase 1 — History Rule Catalog",
        "",
        f"**Total skills audited**: {total}",
        f"**Apply PASS**: {pass_count}",
        f"**Apply FAIL**: {fail_count}",
        f"**History propagate match**: {propagate_count}/{total}  "
        f"({propagate_count / total * 100:.0f}%)",
        "",
        "Target: ≥ 70% propagate match. See [[lat.md/persistent-naming]].",
        "",
        "## Per-skill audit",
        "",
        "| Skill | Level | Status | History match | Freeze | Duration |",
        "|---|---|---|---|---|---|",
    ]
    for a in audits:
        status = a["status"]
        match = "✓" if a.get("history_match", False) else (
            "✗" if status == "PASS" else "—"
        )
        freeze = "✓" if a.get("freeze_present", False) else "—"
        dur = a.get("duration_ms", "—")
        lines.append(
            f"| `{a['name']}` | {a.get('level', '?')} | {status} | {match} | {freeze} | {dur} ms |"
        )

    lines.append("")
    lines.append("## Failures (if any)")
    lines.append("")
    failures = [a for a in audits if a["status"] == "FAIL"]
    if not failures:
        lines.append("None.")
    else:
        for a in failures:
            lines.append(f"### `{a['name']}`")
            lines.append(f"- error_type: `{a.get('error_type')}`")
            lines.append(f"- error: {a.get('error')}")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=Path,
        default=Path("docs/reports/history_rule_catalog.md"),
    )
    ap.add_argument(
        "--skip", nargs="*", default=[], metavar="SKILL_NAME",
        help="skill 이름들을 audit 에서 제외 (예: launch_ui_panel — headless "
             "CI runner 에서 Qt event loop 진입 / VTK OpenGL crash)",
    )
    args = ap.parse_args()

    print(">>> Phase 1 audit: history rule catalog")
    body = _make_standard_body()
    audits = []
    for spec in registry.all():
        if spec.name in args.skip:
            print(f"    {spec.name:35s} ... SKIP (--skip)")
            continue
        print(f"    {spec.name:35s} ... ", end="", flush=True)
        a = audit_skill(spec.name, spec, body)
        audits.append(a)
        print(a["status"])

    write_markdown_report(audits, args.out)
    print(f">>> Report: {args.out}")

    fail = [a for a in audits if a["status"] == "FAIL"]
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
