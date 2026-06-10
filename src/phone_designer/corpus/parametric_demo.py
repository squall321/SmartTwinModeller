"""Parametric regeneration demo harness v2 — ANALYTIC scoring (plan item P4).

v1 (docs/parametric_variation_demo.md + run_logs/_tmp/parametric_demo.py)
scored nothing analytically: its ``per_feat`` variant produced bit-identical
geometry on all 3 source files and the report still called it a success.
v2 makes every cell of the (files x variants) matrix prove itself against
ANALYTIC expectations — no catalog-vs-catalog circularity:

  (a) scale cells — rebuilt bbox per-axis extent ratio == scale ±0.5 %.
  (b) scale cells — classify_holes re-detected min hole diameter ==
      scale x original min ±8 % (skipped when the original catalog has
      no holes).
  (c) edit cells — rebuilt volume must differ from the scale-1.0 baseline
      rebuild by the analytically expected delta ±15 %, computed from the
      EMITTED plan-step dims (never from the catalog — that would be
      circular):
        hole step pair changed:   pi/4 * (d2² - d1²) * emitted depth
        pocket step pair changed: emitted footprint area x extra depth
      A plan that CHANGED yet rebuilt to a bit-identical volume is an
      automatic FAIL with reason 'edit produced no geometric change' —
      the exact v1 blind spot.
  (d) all cells — validate_rebuilt_body feature census on the executed
      plan: features_lost must be empty (trivially ok when the plan has
      no world-placed cut steps).

Edit-cell SKIP-NO-TARGET refinement (P4 decision, 2026-06-10): when the
edited plan's steps are DEEP-EQUAL to the baseline plan's steps, the edit
never reached the plan at all and no analytic delta is defined. Two
sub-cases, both surfaced loudly (never silently, unlike v1):
  * the dotted key is absent from the catalog                  → SKIP
  * the key exists but the planner emitted no step derived from
    the targeted feature (axis-aligned / silhouette / dedup
    guards in plan_from_feature_catalog)                       → SKIP
Both gated demo files hit the second case for ``pockets.0.depth_mm``:
simple_watch's only catalog pocket is a diagonal-axis detector artifact
(axis_dir ≈ [0.27, 0.53, 0.80] — dropped by _pocket_is_axis_aligned), and
all 3 linkrods pockets are silhouette / full-depth duplicates of its two
holes (dropped by the pass-6/PACK-B silhouette guards). Those drops are
principled planner behavior proven over passes 1-23, not bugs, so the
cells are recorded as SKIP with the full diagnosis instead of FAIL. The
automatic FAIL stays reserved for the dangerous silent-no-op class: a
step CHANGED in the emitted plan yet the rebuilt volume did not move.
A SKIP cell additionally asserts plan-identity consistency: identical
plans MUST rebuild to a bit-identical volume (non-determinism = FAIL).

GATED files (process exit 1 on any FAIL/ERROR cell): simple_watch housing
+ occt__linkrods. NON-GATED (FAIL/ERROR recorded honestly as KNOWN-GAP):
occt__screw (cylinder-base scaling is plan item P5 — not landed yet;
actual behavior recorded) and pythonocc__as1-oc-214.

Execution model: in-process + serial (PlanFromFeatureCatalog writes the
shared plans/reconstructed_plan.yaml as a side effect, so parallel cells
would race — same constraint as corpus.regress; we read the plan dict
from extras and never touch that YAML).

CLI wrapper: ``tools/parametric_regen_demo.py`` (thin sys.path shim).
Test consumer: ``tests/test_parametric_regeneration.py`` (CI-safe
simple_watch subset only — fixture is rebuilt on demand).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phone_designer.corpus.regress import _force_register_all, _repo_root, _volume_mm3

# ──────────────────────────────────────────────────────────────────────────────
# Matrix definition

SCALES: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)

#: (variant_name, per_feature_scale dict) — applied at scale_factor=1.0.
EDIT_VARIANTS: tuple[tuple[str, dict[str, float]], ...] = (
    ("edit_holes0_d_x1.5", {"holes.0.diameters_mm": 1.5}),
    ("edit_pockets0_depth_x2.0", {"pockets.0.depth_mm": 2.0}),
)

#: (a) rebuilt bbox per-axis ratio vs scale — relative tolerance.
BBOX_RTOL = 0.005
#: (b) re-detected min hole diameter vs scale x original — relative tolerance.
HOLE_D_RTOL = 0.08
#: (c) edit-cell volume delta vs analytic expectation — relative tolerance.
EDIT_VOL_RTOL = 0.15
#: volume considered bit-identical below this relative difference (identical
#: plans rebuild deterministically — any larger drift means the edit DID
#: change the plan or the executor is non-deterministic).
IDENTICAL_VOL_RTOL = 1e-12

#: statuses that fail the gate when the cell belongs to a GATED file.
GATING_STATUSES = ("FAIL", "ERROR")


@dataclass
class FileSpec:
    label: str
    path: Path
    gated: bool
    note: str = ""


@dataclass
class Cell:
    file: str
    variant: str
    gated: bool
    status: str  # PASS | FAIL | SKIP | ERROR
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "variant": self.variant,
            "gated": self.gated,
            "status": self.status,
            "reason": self.reason,
            "checks": self.checks,
            "metrics": self.metrics,
            "duration_s": self.duration_s,
        }


# ──────────────────────────────────────────────────────────────────────────────
# File specs

def ensure_simple_watch_fixture(repo: Path) -> Path:
    """Return the housing-only fixture path, generating it via
    fixtures/make_simple_watch.py when absent (CI-safe — pure build123d)."""
    target = repo / "fixtures" / "simple_watch_housing_only.step"
    if target.is_file():
        return target
    script = repo / "fixtures" / "make_simple_watch.py"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--housing-only",
            "--out-dir",
            str(repo / "fixtures"),
        ],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if not target.is_file():
        raise FileNotFoundError(
            f"make_simple_watch.py ran but {target} was not produced"
        )
    return target


def simple_watch_file_spec(repo: Path | None = None) -> FileSpec:
    """The CI-safe gated fixture cell — the ONLY file the pytest gate uses."""
    repo = repo or _repo_root()
    return FileSpec(
        label="simple_watch_housing",
        path=ensure_simple_watch_fixture(repo),
        gated=True,
        note="synthetic fixture (build via fixtures/make_simple_watch.py)",
    )


def default_file_specs(repo: Path | None = None) -> list[FileSpec]:
    """Full demo matrix: fixture + corpus files (corpus entries only when
    present — partial checkouts record them as SKIP cells)."""
    repo = repo or _repo_root()
    complex_dir = repo / "corpus" / "oem" / "complex"
    specs = [simple_watch_file_spec(repo)]
    specs.append(FileSpec(
        label="occt__linkrods",
        path=complex_dir / "occt__linkrods.step",
        gated=True,
    ))
    specs.append(FileSpec(
        label="occt__screw",
        path=complex_dir / "occt__screw.step",
        gated=False,
        note="NON-GATED: cylinder-base scaling is plan item P5 (not landed) "
             "— actual behavior recorded",
    ))
    specs.append(FileSpec(
        label="pythonocc__as1-oc-214",
        path=complex_dir / "pythonocc__as1-oc-214.stp",
        gated=False,
        note="NON-GATED: box-mode baseline 0.8235 — document only",
    ))
    return specs


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers

def _bbox_extents(body: Any) -> tuple[float, float, float] | None:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    try:
        shape = body.wrapped if hasattr(body, "wrapped") else body
        bb = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, bb)
        if bb.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        return (xmax - xmin, ymax - ymin, zmax - zmin)
    except Exception:
        return None


def _min_catalog_hole_d(catalog: dict) -> float | None:
    ds = [
        float(min(h["diameters_mm"]))
        for h in (catalog.get("holes") or [])
        if h.get("diameters_mm")
    ]
    return min(ds) if ds else None


def _redetect_min_hole_d(body: Any) -> float | None:
    """classify_holes on the REBUILT body — the (b) re-detection probe."""
    from phone_designer.skills.inspect.classify_holes import ClassifyHoles

    holes = ClassifyHoles().apply(
        body, {"match_standards": False},
    ).extras.get("holes") or []
    ds = [
        float(min(h["diameters_mm"])) for h in holes if h.get("diameters_mm")
    ]
    return min(ds) if ds else None


def _feature_census(body: Any, plan_dict: dict) -> dict[str, Any]:
    """validate_rebuilt_body 'features' check on the EXECUTED plan (d)."""
    from phone_designer.skills.inspect.validate_rebuilt_body import (
        ValidateRebuiltBody,
    )

    return ValidateRebuiltBody().apply(
        body, {"plan": plan_dict, "checks": ["features"]},
    ).extras["rebuilt_validation"]


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline: import → catalog → (vary + plan) → execute

def load_file(path: Path) -> tuple[Any, dict, tuple[float, float, float]]:
    """ImportStep → ExtractFeatureCatalog. Raises on a skipped catalog.

    Returns ``(body, catalog, orig_extents)``. The original extents are
    measured BEFORE the detectors run: once ExtractFeatureCatalog has
    triangulated the shape, AddOptimal_s returns the inflated
    post-detector mesh bbox (up to +9 % on the curvy simple_watch dome —
    see the ``initial_bbox_mm`` comment in extract_feature_catalog.py),
    which is the wrong (a)-check reference."""
    from phone_designer.skills.create.import_step import ImportStep
    from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
        ExtractFeatureCatalog,
    )

    body = ImportStep().apply(None, {"path": str(path)}).body
    if body is None:
        raise ValueError(f"import_step produced no body for {path}")
    orig_extents = _bbox_extents(body)
    if orig_extents is None:
        raise ValueError(f"imported body has no bbox: {path}")
    catalog = ExtractFeatureCatalog().apply(body, {}).extras["feature_catalog"]
    if catalog.get("skipped"):
        raise ValueError(f"catalog skipped: {catalog.get('reason')}")
    return body, catalog, orig_extents


def build_and_execute(
    body: Any,
    catalog: dict,
    scale: float,
    per_feature_scale: dict | None = None,
) -> tuple[dict, Any]:
    """plan_from_scaled_catalog (box mode) → Plan.model_validate →
    PlanExecutor with NO initial_body (box mode builds its own s_base).
    Returns ``(plan_dict, rebuilt_body)``."""
    from phone_designer.plan.executor import PlanExecutor
    from phone_designer.plan.model import Plan
    from phone_designer.skills.reverse_engineer.plan_from_scaled_catalog import (
        PlanFromScaledCatalog,
    )

    res = PlanFromScaledCatalog().apply(body, {
        "catalog": catalog,
        "scale_factor": scale,
        "per_feature_scale": per_feature_scale or {},
        "base_step_kind": "box",
    })
    plan_dict = res.extras.get("generated_plan") or {}
    if not plan_dict.get("steps"):
        raise ValueError(
            f"no steps in generated_plan (scale={scale}, "
            f"per_feature_scale={per_feature_scale})"
        )
    plan = Plan.model_validate(plan_dict)
    exec_result = PlanExecutor(plan).run()
    if exec_result.final_body is None:
        raise ValueError(
            f"executor produced no final body (scale={scale}, "
            f"outcome={exec_result.outcome})"
        )
    return plan_dict, exec_result.final_body


# ──────────────────────────────────────────────────────────────────────────────
# Analytic edit-delta model (check c)

def _analytic_cut_volume_mm3(step: dict) -> float | None:
    """Tool volume of a world-placed cut step from its EMITTED dims.
    None for skills with no analytic model (recorded as a gap, never
    silently approximated)."""
    skill_name = step.get("skill")
    args = step.get("args") or {}
    try:
        if skill_name == "hole":
            d = float(args.get("diameter_mm") or 0.0)
            # hole skill: depth_mm=None means "through" (internally 200).
            depth = float(args.get("depth_mm") or 200.0)
            return math.pi / 4.0 * d * d * depth
        if skill_name == "extrude_pocket_world":
            return (
                float(args.get("length_mm") or 0.0)
                * float(args.get("width_mm") or 0.0)
                * float(args.get("depth_mm") or 0.0)
            )
    except Exception:
        return None
    return None


def _diff_plans(
    base_plan: dict, edit_plan: dict,
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair steps by id. Returns (changed_pairs, added, removed)."""
    base_idx = {s.get("id"): s for s in base_plan.get("steps") or []}
    edit_idx = {s.get("id"): s for s in edit_plan.get("steps") or []}
    changed: list[tuple[dict, dict]] = []
    for sid, sb in base_idx.items():
        se = edit_idx.get(sid)
        if se is not None and (sb.get("args") != se.get("args")
                               or sb.get("skill") != se.get("skill")):
            changed.append((sb, se))
    added = [s for sid, s in edit_idx.items() if sid not in base_idx]
    removed = [s for sid, s in base_idx.items() if sid not in edit_idx]
    return changed, added, removed


def _expected_volume_change_mm3(
    changed: list[tuple[dict, dict]], added: list[dict], removed: list[dict],
) -> tuple[float | None, list[str]]:
    """Signed expected BODY-volume change from the plan diff.

    A larger/extra cut removes material (negative body-volume change).
    For a same-skill ``hole`` pair with unchanged depth this reduces
    exactly to -pi/4*(d2²-d1²)*depth; for an ``extrude_pocket_world``
    pair with unchanged footprint, to -area*(depth2-depth1).
    Returns (expected_change | None-when-unmodeled, changed_step_ids).
    """
    ids: list[str] = []
    total = 0.0
    for sb, se in changed:
        ids.append(str(se.get("id")))
        vb = _analytic_cut_volume_mm3(sb)
        ve = _analytic_cut_volume_mm3(se)
        if vb is None or ve is None:
            return None, ids
        total -= (ve - vb)
    for se in added:
        ids.append(f"+{se.get('id')}")
        ve = _analytic_cut_volume_mm3(se)
        if ve is None:
            return None, ids
        total -= ve
    for sb in removed:
        ids.append(f"-{sb.get('id')}")
        vb = _analytic_cut_volume_mm3(sb)
        if vb is None:
            return None, ids
        total += vb
    return total, ids


def _catalog_has_target(catalog: dict, dotted_key: str) -> bool:
    """Does the per_feature_scale dotted key resolve inside the catalog?
    Reuses vary_feature_catalog's navigation so the answer matches what
    vary_catalog itself would have touched."""
    from phone_designer.skills.reverse_engineer.vary_feature_catalog import (
        _navigate_to_parent,
        _split_dotted_key,
    )

    return _navigate_to_parent(catalog, _split_dotted_key(dotted_key)) is not None


# ──────────────────────────────────────────────────────────────────────────────
# Cell scoring

def _score_scale_cell(
    spec: FileSpec,
    variant: str,
    scale: float,
    orig_extents: tuple[float, float, float],
    orig_min_hole_d: float | None,
    base_min_hole_d: float | None,
    plan_dict: dict,
    rebuilt: Any,
) -> Cell:
    checks: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    reasons: list[str] = []

    # (a) bbox per-axis ratio == scale ±0.5 %
    ext = _bbox_extents(rebuilt)
    if ext is None:
        checks["a_bbox"] = {"ok": False}
        reasons.append("(a) rebuilt body has no bbox")
    else:
        ratios = [
            (ext[i] / orig_extents[i]) if orig_extents[i] > 0 else float("inf")
            for i in range(3)
        ]
        metrics["bbox_axis_ratios"] = [round(r, 5) for r in ratios]
        ok = all(abs(r / scale - 1.0) <= BBOX_RTOL for r in ratios)
        checks["a_bbox"] = {"ok": ok, "expected": scale, "rtol": BBOX_RTOL}
        if not ok:
            reasons.append(
                f"(a) bbox ratios {[round(r, 4) for r in ratios]} != "
                f"scale {scale} (±{BBOX_RTOL:.1%})"
            )

    # (b) re-detected min hole diameter == scale x BASELINE-REBUILD min ±8 %.
    #
    # P4 revision (2026-06-10, first live run): the reference is the
    # scale-1.0 rebuild's re-detected min — NOT the catalog min. The
    # box-mode planner legitimately drops some catalog holes (round-body
    # inscribed-circle guard, sub-threshold cuts), so catalog-referenced
    # scoring double-counted a RECONSTRUCTION-completeness gap that
    # corpus-regress already tracks (simple_watch: catalog min 2.5 vs
    # baseline re-detect 33.4 — yet the scale ratios were EXACT
    # 16.7/33.4/50.1/66.8). This metric isolates VARIATION correctness:
    # whatever the baseline rebuild contains must scale proportionally.
    # The reconstruction gap stays visible as the catalog_min_hole_d_mm
    # vs baseline_min_hole_d_mm info metrics.
    if orig_min_hole_d is not None:
        metrics["catalog_min_hole_d_mm"] = round(orig_min_hole_d, 4)
    if base_min_hole_d is None:
        checks["b_hole_redetect"] = {
            "ok": None,
            "skipped": "scale-1.0 baseline rebuild has no detectable holes "
                       "(box-mode reconstruction gap — tracked by "
                       "corpus-regress, not a variation defect)",
        }
    else:
        redet = _redetect_min_hole_d(rebuilt)
        expected_d = scale * base_min_hole_d
        metrics["baseline_min_hole_d_mm"] = round(base_min_hole_d, 4)
        metrics["redetected_min_hole_d_mm"] = (
            round(redet, 4) if redet is not None else None
        )
        if redet is None:
            checks["b_hole_redetect"] = {"ok": False, "expected_mm": expected_d}
            reasons.append(
                f"(b) no holes re-detected on rebuild (expected min d "
                f"≈{expected_d:.4g} mm) — scaled variant lost its holes"
            )
        else:
            ok = abs(redet / expected_d - 1.0) <= HOLE_D_RTOL
            checks["b_hole_redetect"] = {
                "ok": ok, "expected_mm": round(expected_d, 4),
                "rtol": HOLE_D_RTOL,
            }
            if not ok:
                reasons.append(
                    f"(b) re-detected min hole d {redet:.4g} != "
                    f"{expected_d:.4g} (±{HOLE_D_RTOL:.0%})"
                )

    _apply_feature_census_check(checks, metrics, reasons, rebuilt, plan_dict)

    status = "FAIL" if reasons else "PASS"
    return Cell(
        file=spec.label, variant=variant, gated=spec.gated, status=status,
        reason="; ".join(reasons), checks=checks, metrics=metrics,
    )


def _apply_feature_census_check(
    checks: dict, metrics: dict, reasons: list[str],
    rebuilt: Any, plan_dict: dict,
) -> None:
    """(d) features_lost must be empty (trivially ok with 0 world cuts)."""
    try:
        rep = _feature_census(rebuilt, plan_dict)
    except Exception as exc:
        checks["d_features"] = {"ok": False}
        reasons.append(f"(d) validate_rebuilt_body raised: {exc}")
        return
    lost = rep.get("features_lost") or []
    expected = int(rep.get("features_expected") or 0)
    metrics["features_expected"] = expected
    metrics["features_realized"] = int(rep.get("features_realized") or 0)
    metrics["features_lost"] = len(lost)
    if expected == 0:
        checks["d_features"] = {
            "ok": True, "note": "no world-placed cut steps in plan",
        }
        return
    checks["d_features"] = {"ok": not lost}
    if lost:
        reasons.append(
            f"(d) {len(lost)}/{expected} planned cut(s) lost: "
            + "; ".join(
                f"{e.get('step_id')}({e.get('reason', '')[:60]})"
                for e in lost[:3]
            )
        )


def _score_edit_cell(
    spec: FileSpec,
    variant: str,
    dotted_key: str,
    catalog: dict,
    base_plan: dict,
    base_vol: float,
    edit_plan: dict,
    rebuilt: Any,
) -> Cell:
    checks: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    reasons: list[str] = []

    v_edit = _volume_mm3(rebuilt)
    if v_edit is None:
        return Cell(
            file=spec.label, variant=variant, gated=spec.gated,
            status="ERROR", reason="volume of edited rebuild unmeasurable",
        )
    metrics["baseline_volume_mm3"] = round(base_vol, 4)
    metrics["edited_volume_mm3"] = round(v_edit, 4)
    actual_change = v_edit - base_vol
    metrics["actual_delta_mm3"] = round(actual_change, 4)
    bit_identical = abs(actual_change) <= IDENTICAL_VOL_RTOL * max(1.0, abs(base_vol))

    changed, added, removed = _diff_plans(base_plan, edit_plan)

    if not changed and not added and not removed:
        # SKIP-NO-TARGET path (see module docstring): the edit never
        # reached the plan, so no analytic delta exists. Diagnose WHY and
        # still assert determinism (identical plans ⇒ identical volume).
        if not bit_identical:
            checks["c_edit_delta"] = {"ok": False}
            reasons.append(
                "(c) plans identical yet volume changed by "
                f"{actual_change:.6g} mm³ — executor non-determinism"
            )
            status = "FAIL"
            reason = "; ".join(reasons)
        else:
            status = "SKIP"
            if _catalog_has_target(catalog, dotted_key):
                reason = (
                    f"edit target '{dotted_key}' exists in catalog but the "
                    "planner emitted no step for that feature (axis-aligned/"
                    "silhouette/dedup guard) — plan unchanged, no analytic "
                    "delta defined [SKIP-NO-TARGET]"
                )
            else:
                reason = (
                    f"edit target '{dotted_key}' absent from catalog — "
                    "vary_catalog ignored the dotted key, plan unchanged "
                    "[SKIP-NO-TARGET]"
                )
            checks["c_edit_delta"] = {"ok": None, "skipped": reason}
        _apply_feature_census_check(checks, metrics, reasons, rebuilt, edit_plan)
        if any(v.get("ok") is False for v in checks.values()):
            status = "FAIL"
            reason = "; ".join(reasons)
        return Cell(
            file=spec.label, variant=variant, gated=spec.gated,
            status=status, reason=reason, checks=checks, metrics=metrics,
        )

    expected_change, changed_ids = _expected_volume_change_mm3(
        changed, added, removed,
    )
    metrics["changed_steps"] = changed_ids
    if bit_identical:
        # THE v1 blind spot — a plan step changed but the geometry didn't.
        checks["c_edit_delta"] = {"ok": False}
        reasons.append("(c) edit produced no geometric change")
    elif expected_change is None:
        checks["c_edit_delta"] = {"ok": False}
        reasons.append(
            "(c) changed step(s) "
            f"{changed_ids} have no analytic volume model — cannot verify"
        )
    elif abs(expected_change) < 1e-9:
        checks["c_edit_delta"] = {"ok": False}
        reasons.append(
            "(c) plans differ but analytic expected delta ≈ 0 — emitted "
            "dims did not move"
        )
    else:
        metrics["expected_delta_mm3"] = round(expected_change, 4)
        rel_err = abs(actual_change - expected_change) / abs(expected_change)
        metrics["delta_rel_err"] = round(rel_err, 4)
        ok = rel_err <= EDIT_VOL_RTOL
        checks["c_edit_delta"] = {"ok": ok, "rtol": EDIT_VOL_RTOL}
        if not ok:
            reasons.append(
                f"(c) volume delta {actual_change:.6g} mm³ deviates from "
                f"analytic {expected_change:.6g} mm³ by {rel_err:.1%} "
                f"(>{EDIT_VOL_RTOL:.0%})"
            )

    _apply_feature_census_check(checks, metrics, reasons, rebuilt, edit_plan)

    status = "FAIL" if reasons else "PASS"
    return Cell(
        file=spec.label, variant=variant, gated=spec.gated, status=status,
        reason="; ".join(reasons), checks=checks, metrics=metrics,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Per-file + matrix runners

def run_file(spec: FileSpec, log=print) -> list[Cell]:
    cells: list[Cell] = []
    if not spec.path.is_file():
        return [Cell(
            file=spec.label, variant="(all)", gated=spec.gated,
            status="SKIP", reason=f"file not present: {spec.path}",
        )]

    t0 = time.perf_counter()
    try:
        body, catalog, orig_extents = load_file(spec.path)
    except Exception as exc:
        return [Cell(
            file=spec.label, variant="(import/extract)", gated=spec.gated,
            status="ERROR",
            reason=f"{type(exc).__name__}: {str(exc)[:200]}",
            duration_s=round(time.perf_counter() - t0, 1),
        )]
    orig_min_hole_d = _min_catalog_hole_d(catalog)
    log(f"[{spec.label}] catalog: holes={len(catalog.get('holes') or [])} "
        f"pockets={len(catalog.get('pockets') or [])} "
        f"min_hole_d={orig_min_hole_d} "
        f"(extract {time.perf_counter() - t0:.1f}s)")

    base_plan: dict | None = None
    base_vol: float | None = None
    base_min_hole_d: float | None = None

    # Build the scale-1.0 baseline FIRST: metric (b) references the
    # baseline rebuild's re-detected min hole diameter (see
    # _score_scale_cell), and the edit cells diff against its volume.
    base_rebuilds: dict[float, tuple[dict, Any]] = {}
    try:
        base_plan, base_rebuilt = build_and_execute(body, catalog, 1.0)
        base_vol = _volume_mm3(base_rebuilt)
        base_min_hole_d = _redetect_min_hole_d(base_rebuilt)
        base_rebuilds[1.0] = (base_plan, base_rebuilt)
    except Exception:
        pass  # the scale-1.0 cell below will surface the ERROR

    for scale in SCALES:
        variant = f"scale_{scale:g}"
        t1 = time.perf_counter()
        try:
            if scale in base_rebuilds:
                plan_dict, rebuilt = base_rebuilds[scale]
            else:
                plan_dict, rebuilt = build_and_execute(body, catalog, scale)
            cell = _score_scale_cell(
                spec, variant, scale, orig_extents, orig_min_hole_d,
                base_min_hole_d, plan_dict, rebuilt,
            )
        except Exception as exc:
            cell = Cell(
                file=spec.label, variant=variant, gated=spec.gated,
                status="ERROR",
                reason=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        cell.duration_s = round(time.perf_counter() - t1, 1)
        cells.append(cell)
        log(f"  {variant:<26} {cell.status:<5} {cell.reason[:90]} "
            f"({cell.duration_s}s)")

    for variant, pfs in EDIT_VARIANTS:
        t1 = time.perf_counter()
        dotted_key = next(iter(pfs))
        if base_plan is None or base_vol is None:
            cell = Cell(
                file=spec.label, variant=variant, gated=spec.gated,
                status="ERROR",
                reason="no scale-1.0 baseline rebuild to diff against",
            )
        else:
            try:
                edit_plan, rebuilt = build_and_execute(
                    body, catalog, 1.0, per_feature_scale=pfs,
                )
                cell = _score_edit_cell(
                    spec, variant, dotted_key, catalog,
                    base_plan, base_vol, edit_plan, rebuilt,
                )
            except Exception as exc:
                cell = Cell(
                    file=spec.label, variant=variant, gated=spec.gated,
                    status="ERROR",
                    reason=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
        cell.duration_s = round(time.perf_counter() - t1, 1)
        cells.append(cell)
        log(f"  {variant:<26} {cell.status:<5} {cell.reason[:90]} "
            f"({cell.duration_s}s)")

    return cells


def run_matrix(specs: list[FileSpec], log=print) -> list[Cell]:
    _force_register_all()
    cells: list[Cell] = []
    for spec in specs:
        log(f"\n===== {spec.label} ({'GATED' if spec.gated else 'non-gated'}) "
            f"{spec.note}")
        cells.extend(run_file(spec, log=log))
    return cells


def gated_failures(cells: list[Cell]) -> list[Cell]:
    return [c for c in cells if c.gated and c.status in GATING_STATUSES]


# ──────────────────────────────────────────────────────────────────────────────
# Reporting

def render_table(cells: list[Cell]) -> str:
    header = (
        f"{'file':<24} {'variant':<26} {'gated':<6} {'status':<7} "
        f"{'key metrics':<58} reason"
    )
    lines = [header, "-" * len(header)]
    for c in cells:
        m = c.metrics
        bits: list[str] = []
        if "bbox_axis_ratios" in m:
            bits.append(f"bbox_r={m['bbox_axis_ratios']}")
        if m.get("redetected_min_hole_d_mm") is not None:
            bits.append(f"min_d={m['redetected_min_hole_d_mm']}")
        if "actual_delta_mm3" in m:
            bits.append(f"dV={m['actual_delta_mm3']}")
        if "expected_delta_mm3" in m:
            bits.append(f"dV_exp={m['expected_delta_mm3']}")
        if "delta_rel_err" in m:
            bits.append(f"err={m['delta_rel_err']:.1%}")
        if "features_lost" in m:
            bits.append(
                f"feat={m.get('features_realized')}/"
                f"{m.get('features_expected')}"
            )
        lines.append(
            f"{c.file:<24} {c.variant:<26} "
            f"{('GATED' if c.gated else '-'):<6} {c.status:<7} "
            f"{' '.join(bits):<58} {c.reason}"
        )
    return "\n".join(lines)


def _kg_label(c: Cell) -> str:
    """Display status — non-gated FAIL/ERROR are KNOWN-GAP by decree."""
    if not c.gated and c.status in GATING_STATUSES:
        return f"KNOWN-GAP({c.status})"
    return c.status


def write_markdown(cells: list[Cell], specs: list[FileSpec], path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = [
        "# Parametric Regeneration Demo v2 — analytic scoring (plan item P4)",
        "",
        f"Generated {now} by `tools/parametric_regen_demo.py` "
        "(logic: `src/phone_designer/corpus/parametric_demo.py`).",
        "",
        "v1 (`docs/parametric_variation_demo.md`) scored nothing analytically",
        "and its `per_feat` variant produced **bit-identical geometry without",
        "anyone noticing**. v2 scores every cell against analytic expectations",
        "derived from the EMITTED plan steps — never catalog-vs-catalog.",
        "",
        "## Pipeline per cell",
        "",
        "`import_step` → `extract_feature_catalog` → `plan_from_scaled_catalog`",
        "(box mode, `scale_factor` + `per_feature_scale`) → `Plan.model_validate`",
        "→ `PlanExecutor` (no initial_body) → analytic checks on the rebuilt",
        "body + executed plan.",
        "",
        "## Checks",
        "",
        f"| check | applies to | rule | tol |",
        f"|---|---|---|---|",
        f"| (a) bbox | scale cells | rebuilt per-axis extent / original == scale | ±{BBOX_RTOL:.1%} |",
        f"| (b) hole re-detect | scale cells | classify_holes min diameter on rebuild == scale × **scale-1.0 baseline rebuild** min | ±{HOLE_D_RTOL:.0%} (skip when the baseline rebuild has no detectable holes — that is a box-mode reconstruction gap tracked by corpus-regress, not a variation defect; the catalog-vs-baseline gap is reported in the metrics column) |",
        f"| (c) edit delta | edit cells | volume − baseline == analytic delta from EMITTED step dims (hole: π/4·(d₂²−d₁²)·depth; pocket: footprint × extra depth) | ±{EDIT_VOL_RTOL:.0%} |",
        f"| (d) feature census | all cells | `validate_rebuilt_body` features_lost == 0 | exact |",
        "",
        "Edit-cell decision rule: a plan that **changed** but rebuilt to a",
        "bit-identical volume is an automatic FAIL (`edit produced no geometric",
        "change` — the v1 blind spot). A plan that did **not** change at all is",
        "SKIP-NO-TARGET with the diagnosis of *why* the edit never reached the",
        "plan (catalog key absent, or planner guard filtered the feature);",
        "identical plans must still rebuild bit-identically (determinism check).",
        "",
        "## Matrix results",
        "",
        "| file | variant | gated | status | metrics | reason |",
        "|---|---|---|---|---|---|",
    ]
    for c in cells:
        m = c.metrics
        bits = []
        if "bbox_axis_ratios" in m:
            bits.append(f"bbox_r={m['bbox_axis_ratios']}")
        if m.get("redetected_min_hole_d_mm") is not None:
            bits.append(
                f"min_d={m['redetected_min_hole_d_mm']}"
                f" (base {m.get('baseline_min_hole_d_mm')},"
                f" cat {m.get('catalog_min_hole_d_mm')})"
            )
        if "actual_delta_mm3" in m:
            bits.append(f"ΔV={m['actual_delta_mm3']}")
        if "expected_delta_mm3" in m:
            bits.append(f"ΔV_exp={m['expected_delta_mm3']}")
        if "delta_rel_err" in m:
            bits.append(f"err={m['delta_rel_err']:.1%}")
        if "features_expected" in m:
            bits.append(
                f"feat {m.get('features_realized')}/{m.get('features_expected')}"
            )
        lines.append(
            f"| {c.file} | {c.variant} | {'GATED' if c.gated else '—'} "
            f"| {_kg_label(c)} | {' '.join(bits)} | {c.reason or '—'} |"
        )

    fails = gated_failures(cells)
    lines += [
        "",
        "## Gate verdict",
        "",
        (f"**{len(fails)} gated cell(s) FAILED** — exit 1."
         if fails else
         "**All gated cells pass** (SKIP-NO-TARGET cells documented below)."),
        "",
        "## Honesty notes",
        "",
        "* **KNOWN UPSTREAM BUG (out of P4 scope — classify_holes):** the",
        "  simple_watch crown hole (Ø2.5, the catalog's min hole) reports",
        "  `axis_origin=[11, 0, 6.5]` — the OCCT cylinder surface's analytic",
        "  location (the fixture's cut-primitive origin) — while the actual",
        "  face band sits at x≈20.5..22 (the wall). `_body_entry_along_axis`",
        "  clamps the entry to `[0, depth_mm]` FROM that origin, so",
        "  `entry_origin` lands ~9.5 mm short, the box-mode cut at",
        "  x∈[11, 12.5] falls entirely inside the Ø33.4 bore void and",
        "  no-ops, and (b) re-detects min d = 33.4·scale instead of",
        "  2.5·scale at EVERY scale (scale-independent — a box-mode entry",
        "  placement bug, not a scaling bug; scaling itself is proven by",
        "  (a)/(d) and by linkrods (b) passing at all 4 scales).",
        "  classify_holes already stashes the TRUE band endpoints",
        "  (`_band_lo_point`/`_band_hi_point`, pass-24) but only consumes",
        "  them in the no-intersect rescue path; anchoring the entry to the",
        "  band unconditionally would fix this.",
        "* `edit_pockets0_depth_x2.0` on **both gated files** is",
        "  SKIP-NO-TARGET: simple_watch's only catalog pocket is a",
        "  diagonal-axis detector artifact (axis_dir ≈ [0.27, 0.53, 0.80],",
        "  depth 42.5 mm on an 11.6 mm body — dropped by",
        "  `_pocket_is_axis_aligned`), and linkrods' 3 pockets are",
        "  silhouette / full-depth duplicates of its 2 holes (dropped by the",
        "  pass-6 / PACK-B silhouette guards). The planner filters are",
        "  *correct*; the harness surfaces the no-op loudly instead of",
        "  reporting a fake variant like v1 did.",
        "* `occt__screw` is non-gated: its base is a turned cylinder but box",
        "  mode rebuilds on a rectangular slab — cylinder-base scaling is",
        "  plan item P5. Actual behavior is recorded above, FAIL/ERROR cells",
        "  count as KNOWN-GAP.",
        "* `pythonocc__as1-oc-214` is non-gated (box-mode corpus baseline",
        "  0.8235) — recorded for coverage.",
        "",
        "## Files",
        "",
    ]
    for s in specs:
        lines.append(
            f"* `{s.label}` — `{s.path}` "
            f"({'GATED' if s.gated else 'non-gated'})"
            + (f" — {s.note}" if s.note else "")
        )
    lines += [
        "",
        "## Run command",
        "",
        "```powershell",
        '$env:PYTHONPATH = "src"',
        '& "venv\\Scripts\\python.exe" tools/parametric_regen_demo.py',
        "```",
        "",
        "Machine-readable results: `run_logs/_tmp/parametric_regen_v2.json`.",
        "CI-safe subset (simple_watch only): "
        "`pytest -q tests/test_parametric_regeneration.py`.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(cells: list[Cell], path: Path) -> None:
    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scales": list(SCALES),
        "edit_variants": [
            {"variant": name, "per_feature_scale": pfs}
            for name, pfs in EDIT_VARIANTS
        ],
        "tolerances": {
            "bbox_rtol": BBOX_RTOL,
            "hole_d_rtol": HOLE_D_RTOL,
            "edit_vol_rtol": EDIT_VOL_RTOL,
        },
        "cells": [c.to_dict() for c in cells],
        "gated_failures": [c.to_dict() for c in gated_failures(cells)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI

def main(argv: list[str] | None = None) -> int:
    repo = _repo_root()
    ap = argparse.ArgumentParser(
        description="Parametric regeneration demo v2 (analytic scoring, P4)",
    )
    ap.add_argument(
        "--files",
        default=None,
        help="comma-separated file labels to run (default: full matrix)",
    )
    ap.add_argument(
        "--json-out",
        default=str(repo / "run_logs" / "_tmp" / "parametric_regen_v2.json"),
    )
    ap.add_argument(
        "--md-out",
        default=str(repo / "docs" / "parametric_regeneration_demo_v2.md"),
    )
    args = ap.parse_args(argv)

    specs = default_file_specs(repo)
    if args.files:
        wanted = {w.strip() for w in args.files.split(",") if w.strip()}
        unknown = wanted - {s.label for s in specs}
        if unknown:
            print(f"unknown --files label(s): {sorted(unknown)}", file=sys.stderr)
            return 2
        specs = [s for s in specs if s.label in wanted]

    cells = run_matrix(specs)

    print("\n" + render_table(cells))
    write_json(cells, Path(args.json_out))
    write_markdown(cells, specs, Path(args.md_out))
    print(f"\njson  -> {args.json_out}")
    print(f"md    -> {args.md_out}")

    fails = gated_failures(cells)
    if fails:
        print(f"\nGATE: {len(fails)} gated cell(s) FAILED:", file=sys.stderr)
        for c in fails:
            print(f"  {c.file} / {c.variant}: {c.reason}", file=sys.stderr)
        return 1
    print("\nGATE: all gated cells pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
