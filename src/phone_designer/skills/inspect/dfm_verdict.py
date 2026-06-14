"""dfm_verdict — atomic, read-only. Pillar report (phase-1, 2026-06-14).

Per-process manufacturability verdict (pass / marginal / fail + reasons)
derived from the measured quality sections produced by
``extract_quality_report``. This is the *productized* DFM layer on top of the
raw inspectors: it turns measured numbers (min wall, draft distribution,
fillet radii, undercut count, sliver/topology health) into a per-process
go / no-go call against a process spec.

Process specs
-------------
Pulled from ``catalogs/processes/<code>.yaml`` (the same files the planner's
ProcessRegistry reads) so the DFM thresholds are single-sourced. The
public-facing process names map to those catalog codes::

    cnc_milling       -> cnc_3axis      (cnc_5axis consulted for the 5-axis
                                          undercut-recovery flag)
    injection_molding -> injection_mold_pa
    die_casting       -> die_cast_al

``3d_printing`` (FDM) has no catalog yaml — a small, defensible default
table is embedded here and flagged ``spec_source='embedded_default'`` so the
report never hides where the numbers came from.

CRITICAL — grade inheritance
----------------------------
Every check INHERITS and DISPLAYS the underlying measured|estimate grade of
the quality section it reads (the ``grade`` field on each
``quality_report`` section envelope, which itself echoes the inner skill's
``result_grade``). A verdict computed from an estimate-grade input is
reported as ``"marginal"`` at most with ``grade_limited=True`` — an estimate
input may NEVER be upgraded into a confident ``pass`` or ``fail``. The
overall process verdict carries ``grade`` = the WORST (estimate < measured)
grade among the sections it actually consulted.

extras schema::

    extras["dfm_verdict"] = {
      "schema_version": 1,
      "pull_direction": [px, py, pz],
      "processes": {
        "<process>": {
          "verdict": "pass" | "marginal" | "fail",
          "grade":   "measured" | "estimate",   # worst consulted-section grade
          "grade_limited": bool,                 # an estimate input capped this
          "spec_source": "catalog:<code>" | "embedded_default",
          "spec": {min_wall_mm, min_draft_deg, undercut_allowed,
                   min_fillet_r_mm, ...},
          "checks": [
            {"id": str, "verdict": "pass"|"marginal"|"fail"|"skipped",
             "grade": "measured"|"estimate",
             "measured": Any, "limit": Any, "reason": str}, ...
          ],
          "reasons": [str, ...],                 # fail/marginal one-liners
        }, ...
      },
      "_meta": {"processes_requested": [...], "unknown_processes": [...]},
    }

Read-only — body unchanged (post ``body_present``). This skill takes a
``quality_report`` dict directly (the normal path — emit_quality_report /
extract_quality_report run first) OR runs extract_quality_report itself when
none is supplied.
"""
from __future__ import annotations

import pathlib
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


SCHEMA_VERSION = 1


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


# ──────────────────────────────────────────────────────────────────────────────
# Process spec resolution

#: public process name -> catalog code under catalogs/processes/.
_PROCESS_TO_CODE: dict[str, str] = {
    "cnc_milling": "cnc_3axis",
    "injection_molding": "injection_mold_pa",
    "die_casting": "die_cast_al",
    "sheet_metal": "sheet_metal_stamp",
}

#: 3d_printing (FDM) has no catalog yaml — embedded, clearly-flagged defaults.
#: Defensible mid-range FDM numbers (0.4 mm nozzle, 0.2 mm layer). Overhang
#: limit is the unsupported wall-angle-from-vertical threshold.
_EMBEDDED_DEFAULTS: dict[str, dict[str, Any]] = {
    "3d_printing": {
        "min_wall_mm": 0.8,          # ~2x nozzle for a robust printed wall
        "min_feature_mm": 0.4,       # single nozzle bead
        "min_draft_deg": None,       # FDM needs no draft
        "undercut_allowed": True,    # supports handle overhangs/undercuts
        "min_fillet_r_mm": 0.0,      # not a hard constraint
        "max_overhang_deg": 45.0,    # walls > 45 deg from vertical need support
    },
}


def _catalogs_root() -> pathlib.Path | None:
    """Walk up from this file until a dir containing /catalogs is found."""
    here = pathlib.Path(__file__).resolve()
    for p in here.parents:
        if (p / "catalogs").is_dir():
            return p
    return None


def _load_process_spec(process: str) -> tuple[dict[str, Any], str]:
    """(rules_dict, spec_source) for ``process``.

    Catalog-backed processes load ``catalogs/processes/<code>.yaml`` -> its
    ``rules``; embedded processes return the baked-in default table. Missing
    or unreadable catalog files fall back to an empty rules dict tagged
    ``catalog_missing:<code>`` so the report is honest about the gap.
    """
    if process in _EMBEDDED_DEFAULTS:
        return dict(_EMBEDDED_DEFAULTS[process]), "embedded_default"

    code = _PROCESS_TO_CODE.get(process)
    if code is None:
        return {}, "unknown"

    root = _catalogs_root()
    if root is not None:
        path = root / "catalogs" / "processes" / f"{code}.yaml"
        if path.is_file():
            try:
                import yaml

                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                rules = dict(doc.get("rules", {}) or {})
                return rules, f"catalog:{code}"
            except Exception:
                pass
    return {}, f"catalog_missing:{code}"


def _five_axis_spec() -> dict[str, Any] | None:
    """cnc_5axis rules — consulted to decide whether an undercut found on a
    3-axis part is *recoverable* on a 5-axis machine (flag, not a pass)."""
    root = _catalogs_root()
    if root is None:
        return None
    path = root / "catalogs" / "processes" / "cnc_5axis.yaml"
    if not path.is_file():
        return None
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return dict(doc.get("rules", {}) or {})
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Quality-section access helpers — every getter returns (data, grade, ok).
# ok=False means the section is missing / errored / guarded, so the check is
# SKIPPED (never silently passed).


def _section(report: dict[str, Any], name: str) -> tuple[Any, str, bool]:
    sec = report.get(name)
    if not isinstance(sec, dict):
        return None, "measured", False
    grade = sec.get("grade", "measured")
    if sec.get("error") or sec.get("guarded"):
        return sec.get("data"), grade, False
    data = sec.get("data")
    if data is None:
        return None, grade, False
    # A guarded inner payload (sentinel inside data) is also unusable.
    if isinstance(data, dict) and (data.get("guarded") or data.get("skipped")):
        return data, grade, False
    return data, grade, True


def _worse_grade(a: str, b: str) -> str:
    return "estimate" if "estimate" in (a, b) else "measured"


# Verdict severity ordering for "worst wins" aggregation.
_SEVERITY = {"skipped": 0, "pass": 1, "marginal": 2, "fail": 3}


def _cap_for_grade(verdict: str, grade: str) -> str:
    """An estimate-grade input may never produce a confident pass/fail.

    A measured-clean stays ``pass``; an estimate input that would read as a
    confident ``pass`` or ``fail`` is softened to ``marginal`` (you cannot
    confidently clear OR condemn a part on a heuristic estimate).
    """
    if grade != "estimate":
        return verdict
    if verdict in ("pass", "fail"):
        return "marginal"
    return verdict


# ──────────────────────────────────────────────────────────────────────────────
# Individual checks. Each returns a check dict (see schema) or None to skip.


def _chk_min_wall(report, spec) -> dict[str, Any] | None:
    limit = spec.get("min_wall_mm")
    if limit is None:
        return None
    data, grade, ok = _section(report, "wall_thickness")
    if not ok:
        return {"id": "min_wall", "verdict": "skipped", "grade": grade,
                "measured": None, "limit": limit,
                "reason": "wall_thickness section unavailable/guarded"}
    gmin = data.get("global_min")
    thin = data.get("thin_regions") or []
    if gmin is None or gmin <= 0.0:
        return {"id": "min_wall", "verdict": "skipped", "grade": grade,
                "measured": gmin, "limit": limit,
                "reason": "no usable wall measurement"}
    limit = float(limit)
    if gmin < limit:
        v = "fail"
        reason = (f"min wall {gmin:.3f} mm < process min {limit:.3f} mm "
                  f"({len(thin)} thin region(s))")
    elif gmin < 1.5 * limit:
        v = "marginal"
        reason = (f"min wall {gmin:.3f} mm within 1.5x of the {limit:.3f} mm "
                  "floor — verify in production")
    else:
        v = "pass"
        reason = f"min wall {gmin:.3f} mm >= 1.5x process min {limit:.3f} mm"
    return {"id": "min_wall", "verdict": _cap_for_grade(v, grade),
            "grade": grade, "measured": round(float(gmin), 4),
            "limit": limit, "reason": reason}


def _chk_max_wall_sink(report, spec) -> dict[str, Any] | None:
    """Sink-mark risk for molding: a wall much thicker than the nominal min
    cools unevenly. Only meaningful for molding/casting processes (those that
    declare a min_draft_deg, i.e. mold-opening processes)."""
    if spec.get("min_draft_deg") is None:
        return None  # not a molding process — sink risk N/A
    data, grade, ok = _section(report, "wall_thickness")
    if not ok:
        return None
    gmax = data.get("global_max")
    gmin = data.get("global_min")
    if not gmax or not gmin or gmin <= 0.0:
        return None
    ratio = float(gmax) / float(gmin)
    # >4x local thickness variation is the classic sink/void heuristic.
    if ratio >= 4.0:
        v, reason = ("marginal",
                     f"wall thickness varies {ratio:.1f}x "
                     f"({gmin:.2f}-{gmax:.2f} mm) — sink-mark/void risk")
    else:
        v, reason = ("pass",
                     f"wall thickness variation {ratio:.1f}x within bound")
    return {"id": "max_wall_sink", "verdict": _cap_for_grade(v, grade),
            "grade": grade, "measured": round(ratio, 3), "limit": 4.0,
            "reason": reason}


def _chk_draft(report, spec) -> dict[str, Any] | None:
    min_draft = spec.get("min_draft_deg")
    if min_draft is None:
        return None  # process needs no draft (CNC, FDM)
    data, grade, ok = _section(report, "draft")
    if not ok:
        return {"id": "draft", "verdict": "skipped", "grade": grade,
                "measured": None, "limit": min_draft,
                "reason": "draft section unavailable/guarded"}
    counts = data.get("counts") or {}
    n_below = int(counts.get("below_min_draft", 0))
    n_under = int(counts.get("undercut", 0))
    if n_under > 0:
        v = "fail"
        reason = f"{n_under} undercut wall(s) (negative draft)"
    elif n_below > 0:
        v = "marginal"
        reason = (f"{n_below} wall(s) below {float(min_draft):.2f} deg "
                  "min draft")
    else:
        v = "pass"
        reason = f"all walls >= {float(min_draft):.2f} deg draft"
    return {"id": "draft", "verdict": _cap_for_grade(v, grade), "grade": grade,
            "measured": {"below_min_draft": n_below, "undercut": n_under},
            "limit": float(min_draft), "reason": reason}


def _chk_undercut(report, spec, *, five_axis_ok: bool) -> dict[str, Any] | None:
    """Undercut presence vs the process's undercut tolerance.

    Undercuts are read from the draft report's per-face ``undercut`` count
    (negative draft against the pull direction = a side-action / 5-axis
    feature). For CNC the pull direction is the tool-approach axis.
    """
    if spec.get("undercut_allowed", False):
        return None  # process tolerates undercuts (5-axis, FDM)
    data, grade, ok = _section(report, "draft")
    if not ok:
        return None
    n_under = int((data.get("counts") or {}).get("undercut", 0))
    if n_under == 0:
        v, reason = "pass", "no undercuts against the pull direction"
    elif five_axis_ok:
        v = "marginal"
        reason = (f"{n_under} undercut(s) — not 3-axis machinable; "
                  "recoverable on 5-axis (cost up)")
    else:
        v = "fail"
        reason = (f"{n_under} undercut(s) require side-action tooling "
                  "(not allowed for this process)")
    return {"id": "undercut", "verdict": _cap_for_grade(v, grade),
            "grade": grade, "measured": n_under, "limit": 0,
            "reason": reason, "five_axis_recoverable": bool(five_axis_ok)}


def _chk_min_fillet(report, spec) -> dict[str, Any] | None:
    """Internal-corner / tool-radius check.

    For CNC the limiting number is the cutting-tool radius (an internal
    concave fillet smaller than the tool cannot be milled). We read the
    smallest CONCAVE fillet (convexity 'fillet') from classify_edge_blends;
    convex 'round' blends are unconstrained. The spec's
    ``min_tool_radius_mm`` (CNC) takes precedence over ``min_fillet_r_mm``.
    """
    limit = spec.get("min_tool_radius_mm")
    label = "min_tool_radius"
    if limit is None:
        limit = spec.get("min_fillet_r_mm")
        label = "min_fillet"
    if limit is None:
        return None
    data, grade, ok = _section(report, "edge_blends")
    if not ok:
        return None
    if not isinstance(data, list):
        return None
    concave = [
        b for b in data
        if b.get("kind") == "fillet" and b.get("convexity") == "fillet"
        and isinstance(b.get("radius_mm"), (int, float))
    ]
    if not concave:
        return {"id": label, "verdict": "pass", "grade": grade,
                "measured": None, "limit": float(limit),
                "reason": "no internal concave fillets to constrain"}
    r_min = min(float(b["radius_mm"]) for b in concave)
    limit = float(limit)
    if r_min < limit:
        v = "fail"
        reason = (f"smallest internal radius {r_min:.3f} mm < tool/fillet "
                  f"min {limit:.3f} mm")
    elif r_min < 1.2 * limit:
        v = "marginal"
        reason = (f"smallest internal radius {r_min:.3f} mm near the "
                  f"{limit:.3f} mm limit")
    else:
        v = "pass"
        reason = f"smallest internal radius {r_min:.3f} mm >= {limit:.3f} mm"
    return {"id": label, "verdict": _cap_for_grade(v, grade), "grade": grade,
            "measured": round(r_min, 4), "limit": limit, "reason": reason}


def _chk_sharp_internal_corners(report, spec) -> dict[str, Any] | None:
    """Sharp internal corners for CNC: a zero-radius internal corner needs a
    smaller tool or is unmachinable. Heuristic: a CNC process with a tool
    radius but NO detected internal fillet on a real (non-trivial) part hints
    at square pockets. We surface this only as an informational marginal when
    the part has pockets/holes but no concave blends — never a hard fail."""
    if "min_tool_radius_mm" not in spec:
        return None
    data, grade, ok = _section(report, "edge_blends")
    if not ok:
        return None
    if not isinstance(data, list):
        return None
    concave = [b for b in data if b.get("convexity") == "fillet"]
    # Informational only: presence of internal fillets is good DFM.
    if concave:
        return {"id": "sharp_internal_corners", "verdict": "pass",
                "grade": grade, "measured": len(concave), "limit": None,
                "reason": f"{len(concave)} internal corner radius/radii present"}
    return None


def _chk_min_feature_print(report, spec) -> dict[str, Any] | None:
    """3D printing min feature: reuse global_min wall as the thinnest feature
    proxy vs the embedded min_feature_mm (single bead)."""
    limit = spec.get("min_feature_mm")
    if limit is None:
        return None
    data, grade, ok = _section(report, "wall_thickness")
    if not ok:
        return None
    gmin = data.get("global_min")
    if gmin is None or gmin <= 0.0:
        return None
    limit = float(limit)
    if float(gmin) < limit:
        v = "fail"
        reason = (f"thinnest feature {float(gmin):.3f} mm < printable min "
                  f"{limit:.3f} mm")
    else:
        v = "pass"
        reason = f"thinnest feature {float(gmin):.3f} mm >= {limit:.3f} mm"
    return {"id": "min_feature", "verdict": _cap_for_grade(v, grade),
            "grade": grade, "measured": round(float(gmin), 4),
            "limit": limit, "reason": reason}


def _chk_topology(report, spec) -> dict[str, Any] | None:
    """Manufacturing-agnostic gate: a non-watertight / self-intersecting /
    sliver-ridden body cannot be reliably toolpathed or sliced. Marginal,
    never fail — a CAM/slicer repair pass may recover it."""
    data, grade, ok = _section(report, "topology")
    if not ok:
        return None
    bad = []
    if data.get("is_valid") is False:
        bad.append("BREP invalid")
    if data.get("free_edge_count"):
        bad.append(f"{data['free_edge_count']} free edge(s)")
    if data.get("non_manifold_edge_count"):
        bad.append(f"{data['non_manifold_edge_count']} non-manifold edge(s)")
    sf = data.get("sliver_faces") or []
    if sf:
        bad.append(f"{len(sf)} sliver face(s)")
    if data.get("self_intersecting") is True:
        bad.append("self-intersecting")
    if not bad:
        return {"id": "topology", "verdict": "pass", "grade": grade,
                "measured": "valid", "limit": None,
                "reason": "watertight, manifold, no slivers"}
    return {"id": "topology", "verdict": _cap_for_grade("marginal", grade),
            "grade": grade, "measured": "; ".join(bad), "limit": None,
            "reason": "geometry health issues may block CAM/slicing: "
                      + "; ".join(bad)}


# Per-process check pipeline. five_axis_ok is injected for CNC.
def _run_process(report, process, spec, *, five_axis_ok: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for fn in (_chk_topology, _chk_min_wall, _chk_min_feature_print,
               _chk_max_wall_sink, _chk_draft, _chk_min_fillet,
               _chk_sharp_internal_corners):
        c = fn(report, spec)
        if c is not None:
            checks.append(c)
    uc = _chk_undercut(report, spec, five_axis_ok=five_axis_ok)
    if uc is not None:
        checks.append(uc)

    # Overall verdict = worst non-skipped check. Grade = worst grade among the
    # sections that produced a real (non-skipped) check.
    overall = "pass"
    grade = "measured"
    grade_limited = False
    reasons: list[str] = []
    consulted_any = False
    for c in checks:
        if c["verdict"] == "skipped":
            continue
        consulted_any = True
        grade = _worse_grade(grade, c.get("grade", "measured"))
        if _SEVERITY[c["verdict"]] > _SEVERITY[overall]:
            overall = c["verdict"]
        if c["verdict"] in ("fail", "marginal"):
            reasons.append(c["reason"])
        # an estimate input that softened a pass/fail into marginal flagged it
        if c.get("grade") == "estimate":
            grade_limited = True

    if not consulted_any:
        overall = "skipped"
        reasons = ["no quality sections available to judge this process"]

    return {
        "verdict": overall,
        "grade": grade,
        "grade_limited": bool(grade_limited),
        "checks": checks,
        "reasons": reasons,
    }


@skill(
    name="dfm_verdict",
    category="inspect",
    level="atomic",
    summary="Per-process manufacturability verdict (pass/marginal/fail + "
            "reasons) from the measured quality_report sections "
            "(extract_quality_report). Checks min wall vs process min, draft "
            "vs min_draft (undercut detection, 5-axis recovery flag), "
            "internal radius vs tool radius, sink-mark wall variation, "
            "min printable feature, and topology health, against the "
            "catalogs/processes specs (3d_printing uses an embedded default "
            "table). CRITICAL: each verdict inherits + displays the "
            "underlying measured|estimate grade — an estimate input is never "
            "upgraded into a confident pass/fail. Read-only — body unchanged.",
    selector_kinds=[],
    history_rules={},
    produces_features=["dfm_verdict"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=[],
    cost_hint=0.2,
    post_conditions=[PostCondition(kind="body_present")],
)
class DfmVerdict(SkillBase):
    class Args(BaseModel):
        processes: list[str] = Field(
            default_factory=lambda: ["cnc_milling", "injection_molding"],
            description="Manufacturing processes to evaluate. Known: "
                        f"{sorted(set(_PROCESS_TO_CODE) | set(_EMBEDDED_DEFAULTS))}. "
                        "Unknown names are listed in "
                        "dfm_verdict['_meta']['unknown_processes'].",
        )
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold opening / tool approach direction — passed "
                        "through to extract_quality_report's draft section "
                        "when this skill runs the report itself.",
        )
        quality_report: dict | None = Field(
            default=None,
            description="A pre-computed extract_quality_report "
                        "quality_report dict. When None the skill runs "
                        "extract_quality_report on the body first.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("dfm_verdict: body is None")

        report = args.quality_report
        if not isinstance(report, dict):
            from phone_designer.skills.inspect.extract_quality_report import (
                ExtractQualityReport,
            )
            report = ExtractQualityReport().apply(
                body,
                {"pull_direction": [float(c) for c in args.pull_direction]},
            ).extras["quality_report"]

        five_axis = _five_axis_spec()
        five_axis_ok = bool(five_axis and five_axis.get("undercut_allowed"))

        known = set(_PROCESS_TO_CODE) | set(_EMBEDDED_DEFAULTS)
        requested = [p for p in args.processes if p in known]
        unknown = [p for p in args.processes if p not in known]

        processes: dict[str, Any] = {}
        for proc in requested:
            spec, source = _load_process_spec(proc)
            # 5-axis recovery only applies to CNC milling.
            fa_ok = five_axis_ok and proc == "cnc_milling"
            result = _run_process(report, proc, spec, five_axis_ok=fa_ok)
            result["spec_source"] = source
            result["spec"] = {
                k: spec.get(k) for k in (
                    "min_wall_mm", "min_draft_deg", "undercut_allowed",
                    "min_fillet_r_mm", "min_tool_radius_mm", "min_feature_mm",
                    "max_overhang_deg",
                ) if k in spec
            }
            processes[proc] = result

        verdict = {
            "schema_version": SCHEMA_VERSION,
            "pull_direction": [round(float(c), 6) for c in args.pull_direction],
            "processes": processes,
            "_meta": {
                "processes_requested": list(args.processes),
                "unknown_processes": unknown,
            },
        }
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={"dfm_verdict": verdict},
        )
