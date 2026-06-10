"""extract_quality_report — atomic, read-only. A9 (2026-06-11).

Analyst-facing quality aggregator. Runs the verification / DFM inspectors in
sequence on the current body and merges their outputs into a single
``extras["quality_report"]`` dict::

    {
      "topology":        {...},   # topology_health
      "wall_thickness":  {...},   # inspect_wall_thickness_v2
      "draft":           {...},   # inspect_draft_angles (pull_direction arg)
      "edge_blends":     {...},   # classify_edge_blends (ALWAYS on here —
                                  # this is an analyst report, not the RE
                                  # catalog where A3 keeps blends opt-in)
      "mass_properties": {...},   # mass_properties
      "principal_axes":  {...},   # principal_axes
      "mesh_quality":    {...},   # mesh_quality
      "gdt_summary":     {...},   # pmi_inspect_summary (cheap tag reads)
      "_meta":           {face_count, max_face_count, sections_requested,
                          unknown_sections}
    }

Each section is the same envelope::

    {"data": Any,            # the inner skill's extras payload (None on error)
     "grade": str,           # 'measured' | 'estimate' — from spec.result_grade
     "duration_s": float,
     "error": str | None,    # per-section isolation (extract_feature_catalog
                             # _safe/_timed pattern): one broken inspector
                             # cannot poison the whole report
     "guarded": True}        # only present when the face-count guard fired

Face-count guard: sections whose inner skill has its own guard
(topology_health / inspect_draft_angles / classify_edge_blends) get
``max_face_count`` passed through; the un-guarded heavy sections
(inspect_wall_thickness_v2 ray-march, mesh_quality tessellation) are guarded
HERE at the aggregator level — over-budget bodies record ``guarded=True``
instead of hanging.

Body unchanged (post ``body_present``).
"""
from __future__ import annotations

import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


#: Section execution order — also the default ``sections`` arg value.
DEFAULT_SECTIONS: tuple[str, ...] = (
    "topology",
    "wall_thickness",
    "draft",
    "edge_blends",
    "mass_properties",
    "principal_axes",
    "mesh_quality",
    "gdt_summary",
)


def _grade_of(skill_cls: type) -> str:
    """'measured' | 'estimate' from the inner skill's spec (A9 metadata)."""
    spec = getattr(skill_cls, "spec", None)
    return getattr(spec, "result_grade", "measured") if spec is not None else "measured"


def _run_section(fn: Callable[[], Any], grade: str) -> dict[str, Any]:
    """Per-section isolation — extract_feature_catalog's _safe/_timed pattern.

    Times the call, swallows the exception into ``error`` so one broken
    inspector cannot poison the rest of the report.
    """
    t0 = time.perf_counter()
    data: Any = None
    error: str | None = None
    try:
        data = fn()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    section: dict[str, Any] = {
        "data": data,
        "grade": grade,
        "duration_s": round(time.perf_counter() - t0, 4),
        "error": error,
    }
    # Surface the inner skill's own guard sentinel (topology_health's
    # guarded=True / classify_edge_blends' skipped=True) at section level so
    # callers can filter guarded sections without digging into data.
    if isinstance(data, dict) and (data.get("guarded") or data.get("skipped")):
        section["guarded"] = True
    return section


def _guarded_section(grade: str, face_count: int, limit: int) -> dict[str, Any]:
    """Aggregator-level too_big sentinel for inner skills without own guards."""
    return {
        "data": {
            "guarded": True,
            "face_count": face_count,
            "limit": limit,
            "reason": "too_big",
            "advice": "decimate the input mesh (mesh_decimate skill) or "
                      "simplify_to_canonical the BREP before calling "
                      "extract_quality_report.",
        },
        "grade": grade,
        "duration_s": 0.0,
        "error": None,
        "guarded": True,
    }


@skill(
    name="extract_quality_report",
    category="inspect",
    level="atomic",
    summary="Aggregate the verification inspectors (topology_health / "
            "inspect_wall_thickness_v2 / inspect_draft_angles / "
            "classify_edge_blends / mass_properties / principal_axes / "
            "mesh_quality / pmi_inspect_summary) into a single "
            "quality_report dict on result.extras. Each section carries "
            "{data, grade, duration_s, error}; grade comes from the inner "
            "skill's result_grade ('measured'|'estimate'). Read-only — "
            "body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["quality_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.6,
    post_conditions=[PostCondition(kind="body_present")],
)
class ExtractQualityReport(SkillBase):
    class Args(BaseModel):
        sections: list[str] = Field(
            default_factory=lambda: list(DEFAULT_SECTIONS),
            description="Which report sections to compute, in this skill's "
                        f"fixed order. Known names: {list(DEFAULT_SECTIONS)}. "
                        "Unknown names are ignored and listed in "
                        "quality_report['_meta']['unknown_sections'].",
        )
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold opening direction — passed through to "
                        "inspect_draft_angles' pull_direction.",
        )
        max_face_count: int | None = Field(
            default_factory=lambda: __import__(
                "phone_designer.skills._face_count_guard",
                fromlist=["DEFAULT_MAX_FACE_COUNT"],
            ).DEFAULT_MAX_FACE_COUNT,
            description="Face-count budget. Passed through to the inner "
                        "skills that have their own guard (topology / draft / "
                        "edge_blends) and enforced at this level for the "
                        "un-guarded heavy sections (wall_thickness, "
                        "mesh_quality) — over-budget sections return "
                        "guarded=True instead of hanging. None disables. "
                        "Default from _face_count_guard "
                        "(PHONE_DESIGNER_MAX_FACE_COUNT).",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        # Local imports — same convention as extract_feature_catalog so the
        # skill modules can be loaded in any order.
        from phone_designer.skills._resolvers import _all_faces
        from phone_designer.skills.inspect.classify_edge_blends import (
            ClassifyEdgeBlends,
        )
        from phone_designer.skills.inspect.inspect_draft_angles import (
            InspectDraftAngles,
        )
        from phone_designer.skills.inspect.inspect_wall_thickness_v2 import (
            InspectWallThicknessV2,
        )
        from phone_designer.skills.inspect.mass_properties import MassProperties
        from phone_designer.skills.inspect.mesh_quality import MeshQuality
        from phone_designer.skills.inspect.principal_axes import PrincipalAxes
        from phone_designer.skills.inspect.topology_health import TopologyHealth
        from phone_designer.skills.pmi.pmi_inspect_summary import PmiInspectSummary

        if body is None:
            raise ValueError("extract_quality_report: body is None")

        # Face count once — shared by the aggregator-level guards below.
        try:
            face_count = len(_all_faces(_occt_shape(body)))
        except Exception:
            face_count = -1
        over_budget = (
            args.max_face_count is not None and face_count > args.max_face_count
        )

        # Section name → (inner skill class, callable returning its payload).
        # Each callable returns the inner skill's extras payload directly so
        # report consumers see the documented per-skill schemas unchanged.
        runners: dict[str, tuple[type, Callable[[], Any]]] = {
            "topology": (TopologyHealth, lambda: TopologyHealth().apply(
                body, {"max_face_count": args.max_face_count},
            ).extras["topology_health"]),
            "wall_thickness": (InspectWallThicknessV2,
                               lambda: InspectWallThicknessV2().apply(
                                   body, {},  # skill defaults: 30 samples/face, 20 mm march
                               ).extras["wall_thickness"]),
            "draft": (InspectDraftAngles, lambda: InspectDraftAngles().apply(
                body, {
                    "pull_direction": [float(c) for c in args.pull_direction],
                    "max_face_count": args.max_face_count,
                },
            ).extras["draft_report"]),
            # Always-on here (analyst report) — unlike extract_feature_catalog
            # where A3 keeps edge blends opt-in for round-trip stability.
            "edge_blends": (ClassifyEdgeBlends, lambda: ClassifyEdgeBlends().apply(
                body, {"max_face_count": args.max_face_count},
            ).extras["edge_blends"]),
            "mass_properties": (MassProperties, lambda: MassProperties().apply(
                body, {},
            ).extras),
            "principal_axes": (PrincipalAxes, lambda: PrincipalAxes().apply(
                body, {},
            ).extras),
            "mesh_quality": (MeshQuality, lambda: MeshQuality().apply(
                body, {},
            ).extras["mesh_quality_report"]),
            # pmi_inspect_summary is pure tag reads — always cheap, so it is
            # never guarded/skipped (the plan's "if cheap" condition holds).
            "gdt_summary": (PmiInspectSummary, lambda: PmiInspectSummary().apply(
                body, {},
            ).extras["pmi_summary"]),
        }

        # Sections WITHOUT an internal face-count guard whose cost scales
        # hard with face count — guarded at this level when over budget.
        aggregator_guarded = {"wall_thickness", "mesh_quality"}

        requested = [s for s in args.sections if s in runners]
        unknown = [s for s in args.sections if s not in runners]

        report: dict[str, Any] = {}
        for name in DEFAULT_SECTIONS:           # fixed, deterministic order
            if name not in requested:
                continue
            skill_cls, fn = runners[name]
            grade = _grade_of(skill_cls)
            if over_budget and name in aggregator_guarded:
                report[name] = _guarded_section(
                    grade, face_count, int(args.max_face_count),  # type: ignore[arg-type]
                )
            else:
                report[name] = _run_section(fn, grade)

        report["_meta"] = {
            "face_count": face_count,
            "max_face_count": args.max_face_count,
            "sections_requested": list(args.sections),
            "unknown_sections": unknown,
        }

        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"quality_report": report},
        )
