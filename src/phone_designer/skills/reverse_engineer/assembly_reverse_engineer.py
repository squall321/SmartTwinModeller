"""assembly_reverse_engineer — first-class macro skill for multi-shell parts.

COMPLEX-CAD pass-6 (2026-06-09): the 0.913-on-RC_Buggy pipeline lived
only in run_logs/_tmp/run_assembly_re.py — invisible to the @skill
registry and unreachable from the planner. This macro skill promotes the
flow so future ARBITRARY assemblies route through the registry instead
of needing a hand-written script.

Flow:
    body
      ├─ split_into_components(min_volume_mm3) → components
      ├─ group components by geometric_signature  → signature CLASSES
      │    (pillar-perf 2026-06-14: an assembly of 50 identical bolts
      │     detects the bolt ONCE; the result is reused for every instance)
      ├─ for each signature class (in a process pool):
      │    ├─ ExtractFeatureCatalog
      │    ├─ PlanFromFeatureCatalog(base_step_kind=preserve_brep,
      │    │                          plan_out_path=<per-class file>)
      │    ├─ PlanExecutor.run(initial_body=component_body)
      │    └─ FeatureFidelityDiff
      └─ aggregate match (volume-weighted) across ALL instances

extras schema::

    extras["assembly_re_results"] = [
        {"index": int, "volume_mm3": float, "match": float | None,
         "outcome": "PASS"|"FAIL"|"COARSE", "skipped": bool, "error": str | None,
         "plan_steps": int | None, "signature_class": int,
         "instance_count": int, "representative": bool,
         "lod": "full"|"coarse"|None,           # phase-4 reduced-fidelity flag
         "result_grade": "measured"|"estimate"|None,
         "bbox_fill_ratio": float | None},      # coarse-only: vol / bbox_vol
        ...
    ]
    extras["aggregate_match_ratio"]   = float in [0, 1]
    extras["components_total"]        = int
    extras["components_processed"]    = int
    extras["signature_classes"]       = int   # distinct components RE'd
    extras["re_runs"]                 = int   # how many times RE actually ran
    extras["components_full"]         = int   # phase-4: full-fidelity catalog
    extras["components_coarse"]       = int   # phase-4: LOD coarse (estimate)
    extras["components_skipped"]      = int   # phase-4: KNOWN-GAP / timeout
    extras["split_mode"]              = "solid"|"shell"
    extras["cache_stats"]             = {"enabled","dir","hits","misses","writes"}

post_conditions = [body_present]. Body returned unchanged (this is a
read-only diagnostic macro).

pillar-perf (2026-06-14): two performance levers attack the KR600 /
RC_Buggy 900 s timeouts WITHOUT changing the small-compound default
results:

  1. INSTANCE DEDUP (dedup_instances=True, default): components that
     hash EQUAL under ``_component_signature.geometric_signature`` are
     grouped into a signature CLASS; the full RE pipeline runs ONCE per
     class and the result is fanned back out to every instance. Volume
     and feature aggregation still cover the WHOLE assembly. Set
     ``dedup_instances=False`` for the historic per-component behaviour
     (byte-identity escape).
  2. PROCESS POOL: each representative class is RE'd in its own worker
     process (``concurrent.futures.ProcessPool``), serialising the shell
     to a ``.brep`` file (OCCT shapes do not pickle) and writing its own
     plan file via the phase-0 ``plan_out_path`` Arg — no shared-state
     race. Workers are capped at ``min(cpu-2, n_classes)`` and results
     are re-sorted deterministically so output is worker-count
     independent. ``max_workers=1`` forces the serial in-process path
     (used by the default-result regression tests).
  3. TIME-BOX GUARD: a representative whose face count exceeds
     ``DEFAULT_MAX_FACE_COUNT`` is recorded as a per-component
     KNOWN-GAP (skipped with reason) instead of hanging the whole
     assembly — a partial catalog beats a 900 s timeout.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult


def _occt_shape(body):
    return body.wrapped if hasattr(body, "wrapped") else body


def _write_brep(shape, path: str) -> None:
    """Serialise an OCCT shape to a native .brep file (lossless)."""
    from OCP.BRepTools import BRepTools

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ok = BRepTools.Write_s(shape, str(path))
    if not ok or not Path(path).exists():
        raise RuntimeError(f"assembly_re: brep write failed → {path}")


def _read_brep(path: str):
    """Load a native .brep file back into an OCCT shape → build123d Part."""
    from build123d import Part
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    ok = BRepTools.Read_s(shape, str(path), builder)
    if not ok or shape.IsNull():
        raise RuntimeError(f"assembly_re: brep read failed → {path}")
    return Part(shape)


def _face_count(body) -> int:
    """Unique face count — used by the time-box guard. -1 on failure."""
    try:
        from phone_designer.skills._resolvers import _all_faces

        return len(_all_faces(_occt_shape(body)))
    except Exception:
        return -1


# ──────────────────────────────────────────────────────────────────────────────
# LOD / face-budget guard (pillar-perf phase-4, 2026-06-15).
#
# Empirically the per-face detector pipeline in ExtractFeatureCatalog is NOT
# the cheap O(N) we assumed for assembly leaves: a single 493-face RC_Buggy
# solid takes ~220 s in the catalog ALONE — yet it is nowhere near the 16 000
# DEFAULT_MAX_FACE_COUNT cap, so the existing too_many_faces guard never fires
# and the whole 211-solid assembly times out at 700 s.
#
# DEFAULT_LOD_MAX_FACES is the *reduced-fidelity* budget: a component above it
# is reconstructed COARSE — a bbox primitive base only, skipping every
# expensive per-face detector — and flagged lod='coarse',
# result_grade='estimate'. The assembly catalog then covers ALL components,
# with the heavy ones marked coarse. Partial-but-complete beats a hang.


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


# SPOT-CHECK-tuned (phase-4, 2026-06-15; raised 2026-06-16 after the #2 perf
# fix). RC_Buggy's worst leaves cost ~220 s each in the full per-face catalog
# (a 493-face solid), so anything face-heavy MUST go coarse — but the
# 2026-06-16 match_standard_hole fix (killing the per-hole whole-body
# re-measurement) cut that 493-face catalog 182 s → 105 s (~42%), so the
# budget can rise: MORE components reconstruct full-fidelity in the same
# wall-clock. Measured on RC_Buggy with the speedup (per_class_timeout=None):
#   * lod=60  → 0 full / 10 coarse in 81 s   (the old conservative default)
#   * lod=150 → 2 full / 8 coarse in 100 s   (the new default — +full, +19 s)
# So 150 is measured-safe (100 s, well under any per-file timeout, 0 skipped).
# Phase-4 lod=60 baseline (pre-speedup, no global timeout): RC_Buggy 144/144
# coverage in 277 s, KR600 61/61 in 79 s. Cost driver is geometric complexity,
# not face count alone — the budget bounds the WORST offenders. Override with
# PHONE_DESIGNER_LOD_MAX_FACES; set the Arg to None to disable (full fidelity,
# the historic hang-prone path); raise further to trade wall-clock for fidelity
# on hosts with more time budget.
DEFAULT_LOD_MAX_FACES: int = _env_int("PHONE_DESIGNER_LOD_MAX_FACES", 150)


def _bbox_of(shape):
    """(xmin,ymin,zmin,xmax,ymax,zmax) or None. Plain Add_s (deterministic)."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        bb = Bnd_Box()
        BRepBndLib.Add_s(shape, bb)
        if bb.IsVoid():
            return None
        return tuple(float(c) for c in bb.Get())
    except Exception:
        return None


def _volume_of(shape) -> float:
    try:
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return abs(float(props.Mass()))
    except Exception:
        return 0.0


def _coarse_reconstruct(body_ref, face_count: int, lod_max_faces: int) -> dict:
    """COARSE (reduced-fidelity) reconstruction for an over-budget component.

    Builds ONLY a bbox primitive base — no per-face detectors run — so an
    over-budget leaf costs milliseconds instead of the ~220 s full catalog.
    The honest fidelity proxy reported is ``bbox_fill_ratio`` =
    component_volume / bbox_volume (how box-like the part is); it is NOT a
    feature match_ratio and ``match`` stays None (no fake-accuracy: a coarse
    body cannot honestly claim a feature-fidelity win).

    Returns the same dict shape as ``_run_class_re`` with ``lod='coarse'`` and
    ``result_grade='estimate'``.
    """
    shape = _occt_shape(body_ref)
    bbox = _bbox_of(shape)
    vol = _volume_of(shape)
    bbox_vol = None
    fill = None
    if bbox is not None:
        dx = max(0.0, bbox[3] - bbox[0])
        dy = max(0.0, bbox[4] - bbox[1])
        dz = max(0.0, bbox[5] - bbox[2])
        bbox_vol = dx * dy * dz
        if bbox_vol > 0:
            fill = round(min(1.0, vol / bbox_vol), 4)
    return {
        "match": None,           # honest: a coarse bbox base is NOT a match
        "outcome": "COARSE",
        "plan_steps": 1,         # the single bbox-base step
        "error": None,
        "face_count": face_count,
        "skipped": False,
        "skip_reason": None,
        "lod": "coarse",
        "result_grade": "estimate",
        "bbox": [round(c, 4) for c in bbox] if bbox is not None else None,
        "bbox_fill_ratio": fill,
        "coarse_reason": (
            f"face_count {face_count} > lod_max_faces ({lod_max_faces}) — "
            f"reconstructed COARSE (bbox primitive base, detectors skipped) "
            f"and flagged result_grade='estimate' so the assembly catalog is "
            f"partial-but-complete instead of hanging on the full per-face "
            f"detector pass (~220 s for a 493-face solid)"
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Per-class RE pipeline — pure function so it can run in a worker process.
#
# Takes only picklable inputs (paths + scalars). The OCCT shell is loaded from
# the .brep file written by the parent. The plan is written to ``plan_path``
# (a per-class file, so workers never race) and re-loaded for execution.


def _run_class_re(
    brep_path: str,
    plan_path: str,
    base_step_kind: str,
    max_face_count: int | None,
    lod_max_faces: int | None = None,
) -> dict:
    """Run the full RE round-trip on ONE representative component.

    Returns a picklable dict with keys: match, outcome, plan_steps, error,
    face_count, skipped, skip_reason, lod, result_grade. Never raises —
    failures land in ``error``. This is a module-level function (not a closure
    / method) so ``ProcessPoolExecutor`` can pickle + ship it to a worker.

    LOD guard (phase-4): a component whose face count exceeds ``lod_max_faces``
    is reconstructed COARSE (bbox base only, detectors skipped) and flagged
    lod='coarse' / result_grade='estimate' rather than paying the ~220 s full
    catalog. ``lod_max_faces=None`` disables the coarse path (full fidelity for
    every component). This is checked AFTER the hard ``max_face_count``
    KNOWN-GAP cap, so an enormous mesh shell is still skipped outright while a
    merely-heavy solid degrades to coarse instead of being dropped.
    """
    out: dict = {
        "match": None,
        "outcome": None,
        "plan_steps": None,
        "error": None,
        "face_count": None,
        "skipped": False,
        "skip_reason": None,
        "lod": "full",
        "result_grade": "measured",
    }
    try:
        from phone_designer.plan.executor import PlanExecutor
        from phone_designer.plan.yaml_io import load_plan
        from phone_designer.skills.reverse_engineer.extract_feature_catalog import (
            ExtractFeatureCatalog,
        )
        from phone_designer.skills.reverse_engineer.feature_fidelity_diff import (
            FeatureFidelityDiff,
        )
        from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
            PlanFromFeatureCatalog,
        )

        body_ref = _read_brep(brep_path)

        # ── time-box guard (pillar-perf 2026-06-14) ────────────────────────
        # A single 4000+-face component that the catalog cannot finish must
        # be recorded as a per-component KNOWN-GAP rather than hanging the
        # whole assembly. We check the face count BEFORE the (O(N)) catalog
        # so an oversize shell is skipped cheaply with a reason.
        fc = _face_count(body_ref)
        out["face_count"] = fc
        if max_face_count is not None and fc > max_face_count:
            out["skipped"] = True
            out["skip_reason"] = (
                f"too_many_faces: {fc} > DEFAULT_MAX_FACE_COUNT "
                f"({max_face_count}) — recorded as KNOWN-GAP to keep the "
                f"assembly catalog partial-but-complete instead of hanging"
            )
            return out

        # ── LOD / face-budget guard (phase-4 2026-06-15) ───────────────────
        # A merely-heavy solid (above the coarse budget but below the hard
        # KNOWN-GAP cap) is reconstructed at REDUCED fidelity — a bbox
        # primitive base only — so the whole assembly never stalls on the
        # ~220 s full per-face detector pass for one 493-face leaf.
        if lod_max_faces is not None and fc > lod_max_faces:
            return _coarse_reconstruct(body_ref, fc, lod_max_faces)

        cat = ExtractFeatureCatalog().apply(body_ref, {}).extras["feature_catalog"]
        if cat.get("skipped"):
            out["skipped"] = True
            out["skip_reason"] = f"catalog skipped: {cat.get('reason')}"
            return out

        PlanFromFeatureCatalog().apply(
            body_ref,
            {
                "catalog": cat,
                "base_step_kind": base_step_kind,
                "plan_out_path": plan_path,
            },
        )
        plan = load_plan(plan_path)
        out["plan_steps"] = len(plan.steps)

        exec_result = PlanExecutor(plan).run(initial_body=body_ref)
        regen = exec_result.final_body
        out["outcome"] = exec_result.outcome
        if regen is None:
            out["match"] = 0.0
            return out

        regen_cat = ExtractFeatureCatalog().apply(regen, {}).extras[
            "feature_catalog"
        ]
        fid = FeatureFidelityDiff().apply(
            regen, {"catalog_a": cat, "catalog_b": regen_cat}
        ).extras["feature_fidelity"]
        out["match"] = float(fid.get("overall_match_ratio") or 0.0)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
    return out


@skill(
    name="assembly_reverse_engineer",
    category="reverse_engineer",
    level="macro",
    summary="Reverse-engineer a multi-shell assembly: split_into_components, "
            "dedup identical instances by geometric_signature, run "
            "ExtractFeatureCatalog + PlanFromFeatureCatalog + "
            "FeatureFidelityDiff ONCE per signature class (in a process pool), "
            "aggregate match as a volume-weighted average across all "
            "instances. Returns the per-component log + aggregate ratio in "
            "extras.",
    selector_kinds=[],
    history_rules={},
    produces_features=["assembly_re_report"],
    preserves=["body_topology"],
    manufacturing={},
    failure_modes=["fm.too_many_shells", "fm.empty_assembly"],
    cost_hint=0.9,
    post_conditions=[PostCondition(kind="body_present")],
)
class AssemblyReverseEngineer(SkillBase):
    class Args(BaseModel):
        top_n: int = Field(
            default=10, ge=1, le=1000,
            description="Number of components to process, sorted by volume "
                        "(biggest first — biggest components carry the most "
                        "feature topology).",
        )
        min_volume_mm3: float = Field(
            default=100.0, ge=0.0,
            description="Components below this volume are treated as mesh "
                        "artefacts (bolts / washers / debris) and skipped at "
                        "split time. Default 100 mm³ catches anything "
                        "screw-head-sized or larger.",
        )
        base_step_kind: str = Field(
            default="preserve_brep",
            description="Passed to PlanFromFeatureCatalog for each component. "
                        "preserve_brep is the right choice for assembly "
                        "components (they are real BREP shells, not bbox "
                        "placeholders); 'box' is supported but rarely useful.",
        )
        # phase-0 (2026-06-13): injectable plan output directory. When None
        # (default) each component's plan is written to the historic shared
        # default plans/reconstructed_plan.yaml and loaded from there —
        # byte-identical to pre-phase-0 behaviour. When set, each component
        # writes to a DISTINCT file inside this directory.
        plan_out_dir: str | None = Field(
            default=None,
            description="Directory for per-component plan YAMLs. None ⇒ a "
                        "process-local temp directory (each signature class "
                        "still gets its own file so pool workers never "
                        "clobber each other). When set, plans are written to "
                        "<dir>/class_<k>_plan.yaml so they survive the run.",
        )
        # pillar-perf (2026-06-14): instance dedup. Default True groups
        # components that hash EQUAL under geometric_signature into a
        # signature CLASS and runs the RE pipeline ONCE per class. Set False
        # for the historic per-component behaviour (the byte-identity escape:
        # every component is its own class, RE runs once per component).
        dedup_instances: bool = Field(
            default=True,
            description="Group identical component instances by "
                        "geometric_signature and run the RE pipeline ONCE per "
                        "signature class, reusing the result for every "
                        "instance (an assembly of 50 identical bolts detects "
                        "the bolt once). Volume + feature aggregation still "
                        "cover the whole assembly. False ⇒ historic "
                        "per-component behaviour (one RE run per component).",
        )
        # pillar-perf (2026-06-14): process-pool worker cap. None ⇒
        # auto = min(cpu-2, n_classes). 1 ⇒ serial in-process path (used by
        # the default-result regression tests so they need no subprocess).
        # The result is re-sorted deterministically so output is independent
        # of this value.
        max_workers: int | None = Field(
            default=None, ge=1,
            description="Process-pool worker cap for per-class RE. None ⇒ "
                        "auto = min(cpu-2, n_classes). 1 ⇒ serial in-process "
                        "(no subprocess). Output is worker-count-independent "
                        "(results re-sorted deterministically).",
        )
        # pillar-perf (2026-06-14): per-class wall-clock cap (pool path only).
        # A class whose worker exceeds this is recorded as a KNOWN-GAP
        # (timeout) instead of hanging the whole assembly — a partial catalog
        # beats a 900 s stall. None ⇒ no per-class cap (the serial path never
        # applies one — it has no second process to kill).
        per_class_timeout_s: float | None = Field(
            default=None, ge=1.0,
            description="Per-class wall-clock cap in seconds for the process "
                        "pool. A class exceeding it is recorded as a "
                        "KNOWN-GAP (timeout) so one pathological component "
                        "cannot stall the whole assembly. None ⇒ no cap.",
        )
        # pillar-perf (phase-4, 2026-06-15): SOLID-aware split. Default True
        # explodes the assembly compound into its constituent SOLIDS (one body
        # per physical part) — a solid with an internal cavity owns TWO shells,
        # so the historic shell split over-counts (RC_Buggy 211 solids → 293
        # shells) and slices one body into two bogus components. Falls back to
        # the shell split when the body has no solids (a pure open-shell mesh).
        # Set False for the historic per-shell behaviour.
        split_solids: bool = Field(
            default=True,
            description="Split the assembly into its constituent SOLIDS (one "
                        "body per physical part) instead of shells. Right for "
                        "STEP assemblies. Falls back to the shell split when "
                        "the body has no solids. False ⇒ historic shell split.",
        )
        # pillar-perf (phase-4, 2026-06-15): LOD / face-budget guard. A
        # component whose face count exceeds this is reconstructed COARSE
        # (bbox primitive base, detectors skipped) and flagged lod='coarse' /
        # result_grade='estimate' instead of paying the ~220 s full per-face
        # catalog (a 493-face solid alone costs that). None ⇒ full fidelity for
        # every component (the historic, hang-prone path). Default reads
        # DEFAULT_LOD_MAX_FACES (220, overridable via env).
        lod_max_faces: int | None = Field(
            default=DEFAULT_LOD_MAX_FACES, ge=1,
            description="Components above this face count are reconstructed "
                        "COARSE (bbox base, detectors skipped) and flagged "
                        "result_grade='estimate' so the assembly catalog stays "
                        "partial-but-complete instead of hanging on the full "
                        "detector pass. None ⇒ full fidelity for every "
                        "component (historic hang-prone path).",
        )
        # pillar-perf (phase-4, 2026-06-15): opt-in content-addressed cache.
        # Default False ⇒ no disk I/O, behaviour byte-identical to pre-cache.
        # True caches each per-class RE result keyed by geometric_signature, so
        # a re-run of the same assembly (or a different assembly sharing a
        # component) skips re-detection. Invalidation is by signature band.
        cache: bool = Field(
            default=False,
            description="Opt-in content-addressed cache of per-component RE "
                        "results keyed by geometric_signature. False (default) "
                        "⇒ no disk I/O, behaviour unchanged. True ⇒ a re-run "
                        "(or a different assembly sharing a component) skips "
                        "re-detection on a cache hit.",
        )
        cache_dir: str | None = Field(
            default=None,
            description="Cache root directory when cache=True. None ⇒ "
                        "PHONE_DESIGNER_RE_CACHE_DIR env or a per-user temp "
                        "dir, so re-runs across processes share entries.",
        )

    def _apply(self, body: Any, args: Args) -> SkillResult:
        from phone_designer.skills.repair.split_into_components import (
            SplitIntoComponents,
        )

        if body is None:
            return SkillResult(
                body=body,
                history=EntityHistoryMap(),
                extras={
                    "assembly_re_results": [],
                    "aggregate_match_ratio": 0.0,
                    "components_total": 0,
                    "components_processed": 0,
                    "signature_classes": 0,
                    "re_runs": 0,
                    "error": "no body",
                },
            )

        # max_face_count comes from the shared guard so it stays in sync with
        # the catalog skip threshold (imported here, not at module top, so the
        # env override is read at apply-time).
        from phone_designer.skills._face_count_guard import (
            DEFAULT_MAX_FACE_COUNT as _MAX_FACE_COUNT,
        )

        # 1. Split into components (SOLID-aware by default), sort biggest-first.
        split_extras = SplitIntoComponents().apply(
            body,
            {
                "min_volume_mm3": args.min_volume_mm3,
                "split_solids": args.split_solids,
            },
        ).extras
        comps: list = list(split_extras.get("components") or [])
        comps.sort(key=lambda c: -float(c.get("volume_mm3") or 0))
        top_comps = comps[: args.top_n]

        # 2. Build per-component entry stubs (one per instance — the output
        #    must reflect the WHOLE assembly, not just representatives).
        entries: list[dict] = []
        for i, c in enumerate(top_comps):
            entries.append({
                "index": i,
                "volume_mm3": float(c.get("volume_mm3") or 0),
                "match": None,
                "outcome": None,
                "skipped": False,
                "error": None,
                "plan_steps": None,
                "dt_s": None,
                "signature_class": None,
                "instance_count": 1,
                "representative": False,
                "lod": None,            # 'full' | 'coarse' — set after RE
                "result_grade": None,  # 'measured' | 'estimate'
                "_body_ref": c.get("body_ref"),  # stripped before return
            })

        # 3. Group instances into signature CLASSES.
        #    dedup_instances=False ⇒ every component is its own class (the
        #    byte-identity escape: RE runs once per component, exactly the
        #    historic behaviour). dedup_instances=True ⇒ components that hash
        #    EQUAL share a class and the RE pipeline runs once for the class.
        classes = self._group_into_classes(entries, args.dedup_instances)

        # 4. Run RE once per class (process pool or serial), assigning each
        #    representative's result to every instance in the class. An opt-in
        #    content-addressed cache (keyed by geometric_signature) lets a
        #    re-run / a shared component skip re-detection.
        from phone_designer.skills.reverse_engineer._re_cache import ReCache

        re_cache = ReCache(enabled=args.cache, cache_dir=args.cache_dir)
        self._run_classes(classes, args, _MAX_FACE_COUNT, re_cache)

        # 5. Aggregate volume-weighted match across ALL instances + finalise.
        weighted_match = 0.0
        total_weight = 0.0
        for e in entries:
            m = e.get("match")
            w = e.get("volume_mm3") or 0.0
            if isinstance(m, (int, float)) and w > 0:
                weighted_match += float(m) * w
                total_weight += w
        aggregate = weighted_match / total_weight if total_weight > 0 else 0.0

        # Strip the private body_ref before returning (not serialisable / not
        # part of the schema). Re-sort by index so output is deterministic and
        # independent of class iteration / worker order.
        results = sorted(entries, key=lambda e: e["index"])
        for e in results:
            e.pop("_body_ref", None)

        re_runs = sum(
            1 for cl in classes if cl.get("ran")
        )
        coarse_count = sum(1 for e in results if e.get("lod") == "coarse")
        full_count = sum(1 for e in results if e.get("lod") == "full")
        skipped_count = sum(1 for e in results if e.get("skipped"))
        return SkillResult(
            body=body,
            history=EntityHistoryMap(),
            extras={
                "assembly_re_results": results,
                "aggregate_match_ratio": round(aggregate, 4),
                "components_total": len(comps),
                "components_processed": len(results),
                "signature_classes": len(classes),
                "re_runs": re_runs,
                # LOD coverage (phase-4): how complete the catalog is and at
                # what fidelity. coarse + full + skipped == components_processed.
                "components_full": full_count,
                "components_coarse": coarse_count,
                "components_skipped": skipped_count,
                "split_mode": split_extras.get(
                    "split_mode",
                    "solid" if args.split_solids else "shell",
                ),
                "cache_stats": re_cache.stats(),
            },
        )

    # ──────────────────────────────────────────────────────────────────────
    # Helpers

    @staticmethod
    def _group_into_classes(
        entries: list[dict], dedup: bool
    ) -> list[dict]:
        """Partition entries into signature classes.

        Each class dict: {"members": [entry, ...], "rep": entry, "ran": bool}.
        The representative is the FIRST member in index order (entries arrive
        index-sorted), so class iteration is deterministic.

        dedup=False ⇒ every entry is its own singleton class (historic
        per-component path). dedup=True ⇒ entries whose geometric_signature
        compares EQUAL share a class; an entry whose signature cannot be
        computed falls back to its own singleton class (fail-safe — never
        wrongly merge).
        """
        if not dedup:
            classes: list[dict] = []
            for k, e in enumerate(entries):
                e["signature_class"] = k
                e["instance_count"] = 1
                e["representative"] = True
                classes.append({"members": [e], "rep": e, "ran": False})
            return classes

        from phone_designer.skills.reverse_engineer._component_signature import (
            geometric_signature,
        )

        classes: list[dict] = []
        by_sig: dict[Any, dict] = {}
        for e in entries:
            body_ref = e.get("_body_ref")
            sig = None
            if body_ref is not None:
                try:
                    sig = geometric_signature(body_ref)
                except Exception:
                    sig = None
            if sig is not None and sig in by_sig:
                cl = by_sig[sig]
                cl["members"].append(e)
                e["instance_count"] = 0  # backfilled below
            else:
                cl = {"members": [e], "rep": e, "ran": False}
                classes.append(cl)
                if sig is not None:
                    by_sig[sig] = cl

        # Backfill instance_count + signature_class index on every member.
        for k, cl in enumerate(classes):
            n = len(cl["members"])
            for m in cl["members"]:
                m["signature_class"] = k
                m["instance_count"] = n
            cl["rep"]["representative"] = True
        return classes

    def _run_classes(
        self,
        classes: list[dict],
        args: "AssemblyReverseEngineer.Args",
        max_face_count: int | None,
        cache=None,
    ) -> None:
        """Run the RE pipeline once per class and fan the result out to every
        instance member. Uses a process pool unless max_workers==1 or there is
        only one runnable class (serial avoids pool overhead / pickling).

        ``cache`` (opt-in ``ReCache``) is consulted in THIS (parent) process:
        a class whose representative's geometric_signature is already cached
        skips the worker entirely, and a fresh result is written back. The
        cache object lives only in the parent — workers stay picklable-pure.
        """
        # Pre-flight per representative: skip dead components (no body / zero
        # volume) up front so we never spawn a worker for them.
        runnable: list[dict] = []
        for cl in classes:
            rep = cl["rep"]
            body_ref = rep.get("_body_ref")
            if body_ref is None or (rep.get("volume_mm3") or 0) <= 0:
                self._apply_class_result(
                    cl,
                    {
                        "match": None, "outcome": None, "plan_steps": None,
                        "error": "no body_ref or zero volume",
                        "skipped": True, "skip_reason": None,
                    },
                    dt_s=0.0,
                )
                cl["ran"] = False
                continue
            runnable.append(cl)

        # ── opt-in cache lookup (parent process) ───────────────────────────
        # A class whose signature is cached gets its result fanned out NOW and
        # is dropped from the runnable set, so no worker / brep write happens.
        if cache is not None and getattr(cache, "enabled", False):
            from phone_designer.skills.reverse_engineer._component_signature import (
                geometric_signature,
            )

            still: list[dict] = []
            for cl in runnable:
                sig = None
                try:
                    sig = geometric_signature(cl["rep"]["_body_ref"])
                except Exception:
                    sig = None
                cl["_sig"] = sig
                hit = cache.get(sig) if sig is not None else None
                if hit is not None:
                    cl["ran"] = False  # reused from cache, not re-detected
                    self._apply_class_result(cl, hit, dt_s=0.0)
                else:
                    still.append(cl)
            runnable = still

        if not runnable:
            return

        n_classes = len(runnable)
        # Worker cap: auto = min(cpu-2, n_classes), floor 1. Honour an explicit
        # max_workers but never exceed the number of runnable classes.
        cpu = os.cpu_count() or 2
        auto = max(1, min(cpu - 2, n_classes))
        workers = auto if args.max_workers is None else min(args.max_workers, n_classes)
        workers = max(1, workers)

        # Where to write per-class plan + brep scratch files.
        if args.plan_out_dir is not None:
            scratch_dir = Path(args.plan_out_dir)
            scratch_dir.mkdir(parents=True, exist_ok=True)
            tmp_ctx = None
        else:
            tmp_ctx = tempfile.TemporaryDirectory(prefix="assembly_re_")
            scratch_dir = Path(tmp_ctx.name)

        try:
            # Serialise each representative shell to a .brep file once. The
            # class key ``k`` is the runnable-order index → deterministic file
            # names so a re-run is reproducible.
            for k, cl in enumerate(runnable):
                rep = cl["rep"]
                cl["_brep_path"] = str(scratch_dir / f"class_{k}.brep")
                cl["_plan_path"] = str(scratch_dir / f"class_{k}_plan.yaml")
                _write_brep(_occt_shape(rep["_body_ref"]), cl["_brep_path"])

            if workers == 1:
                self._run_classes_serial(
                    runnable, args, max_face_count, cache
                )
            else:
                self._run_classes_pool(
                    runnable, args, max_face_count, workers, cache
                )
        finally:
            if tmp_ctx is not None:
                tmp_ctx.cleanup()

    @staticmethod
    def _cache_put(cache, cl: dict, res: dict) -> None:
        """Write a fresh (non-skipped, non-timeout) result back to the cache."""
        if cache is None or not getattr(cache, "enabled", False):
            return
        sig = cl.get("_sig")
        if sig is None or res.get("skipped"):
            return
        cache.put(sig, res)

    def _run_classes_serial(
        self,
        runnable: list[dict],
        args: "AssemblyReverseEngineer.Args",
        max_face_count: int | None,
        cache=None,
    ) -> None:
        for cl in runnable:
            t0 = time.perf_counter()
            res = _run_class_re(
                cl["_brep_path"], cl["_plan_path"],
                args.base_step_kind, max_face_count, args.lod_max_faces,
            )
            cl["ran"] = not res.get("skipped")
            self._cache_put(cache, cl, res)
            self._apply_class_result(
                cl, res, dt_s=round(time.perf_counter() - t0, 2)
            )

    def _run_classes_pool(
        self,
        runnable: list[dict],
        args: "AssemblyReverseEngineer.Args",
        max_face_count: int | None,
        workers: int,
        cache=None,
    ) -> None:
        from concurrent.futures import (
            ProcessPoolExecutor,
            TimeoutError as FuturesTimeout,
        )

        t0 = time.perf_counter()
        deadline = (
            None if args.per_class_timeout_s is None
            else t0 + float(args.per_class_timeout_s)
        )
        order: list = []
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for cl in runnable:
                    fut = pool.submit(
                        _run_class_re,
                        cl["_brep_path"], cl["_plan_path"],
                        args.base_step_kind, max_face_count,
                        args.lod_max_faces,
                    )
                    futures[fut] = cl
                    order.append(cl)
                timed_out = False
                for fut, cl in futures.items():
                    if timed_out:
                        # An earlier class blew the deadline and we tore the
                        # pool down — everything still pending is a KNOWN-GAP.
                        cl["_pending_timeout"] = True
                        continue
                    # Per-class wall-clock budget = remaining time to the
                    # shared deadline (best-effort; None ⇒ block forever).
                    rem = None if deadline is None else max(
                        0.1, deadline - time.perf_counter()
                    )
                    try:
                        res = fut.result(timeout=rem)
                    except FuturesTimeout:
                        # Hung / over-budget worker: record THIS class as a
                        # KNOWN-GAP and stop waiting on the rest. The pool's
                        # __exit__ (cancel_futures) reaps the workers so the
                        # whole assembly does not stall at 900 s.
                        cl["_pending_timeout"] = True
                        timed_out = True
                        continue
                    except Exception as exc:  # worker crashed hard
                        res = {
                            "match": None, "outcome": None,
                            "plan_steps": None,
                            "error": f"worker crashed: "
                                     f"{type(exc).__name__}: {str(exc)[:120]}",
                            "skipped": False, "skip_reason": None,
                        }
                    cl["ran"] = not res.get("skipped")
                    self._cache_put(cache, cl, res)
                    self._apply_class_result(
                        cl, res,
                        dt_s=round(time.perf_counter() - t0, 2),
                    )
                if timed_out:
                    # Don't wait for the abandoned workers on context exit.
                    pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            # Pool itself failed to start (rare on locked-down hosts) — fall
            # back to the serial in-process path so the assembly still gets a
            # catalog rather than an empty result.
            for cl in runnable:
                if cl["rep"].get("match") is not None or cl["rep"].get("error"):
                    continue
                self._run_classes_serial([cl], args, max_face_count, cache)
            return

        # Record the timed-out / abandoned classes as per-component KNOWN-GAPs.
        budget = args.per_class_timeout_s
        for cl in order:
            if cl.pop("_pending_timeout", False):
                cl["ran"] = False
                self._apply_class_result(
                    cl,
                    {
                        "match": None, "outcome": None, "plan_steps": None,
                        "error": None,
                        "skipped": True,
                        "skip_reason": (
                            f"timeout: class RE exceeded "
                            f"per_class_timeout_s ({budget}s) — recorded as "
                            f"KNOWN-GAP so the assembly catalog stays "
                            f"partial-but-complete instead of stalling"
                        ),
                    },
                    dt_s=round(time.perf_counter() - t0, 2),
                )

    @staticmethod
    def _apply_class_result(cl: dict, res: dict, dt_s: float) -> None:
        """Fan one class RE result out to every instance member."""
        skipped = bool(res.get("skipped"))
        skip_reason = res.get("skip_reason")
        # LOD / grade (phase-4). A skipped (KNOWN-GAP / timeout) class has no
        # reconstruction at all, so it carries no lod/grade; a coarse class
        # carries lod='coarse'/grade='estimate'; everything else is full.
        lod = res.get("lod")
        grade = res.get("result_grade")
        if skipped:
            lod = lod or None
            grade = grade or None
        else:
            lod = lod or "full"
            grade = grade or "measured"
        coarse_reason = res.get("coarse_reason")
        for m in cl["members"]:
            m["match"] = res.get("match")
            m["outcome"] = res.get("outcome")
            m["plan_steps"] = res.get("plan_steps")
            m["error"] = (
                res.get("error")
                if res.get("error") is not None
                else skip_reason
            )
            m["skipped"] = skipped
            m["dt_s"] = dt_s
            m["lod"] = lod
            m["result_grade"] = grade
            if res.get("face_count") is not None:
                m["face_count"] = res.get("face_count")
            if res.get("bbox_fill_ratio") is not None:
                m["bbox_fill_ratio"] = res.get("bbox_fill_ratio")
            if coarse_reason is not None and m.get("error") is None:
                # Surface WHY a coarse component was downgraded (it is not an
                # error, but callers want the reason in the per-component log).
                m["coarse_reason"] = coarse_reason
