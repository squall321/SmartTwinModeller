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
         "outcome": "PASS"|"FAIL", "skipped": bool, "error": str | None,
         "plan_steps": int | None, "signature_class": int,
         "instance_count": int, "representative": bool},
        ...
    ]
    extras["aggregate_match_ratio"]   = float in [0, 1]
    extras["components_total"]        = int
    extras["components_processed"]    = int
    extras["signature_classes"]       = int   # distinct components RE'd
    extras["re_runs"]                 = int   # how many times RE actually ran

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
) -> dict:
    """Run the full RE round-trip on ONE representative component.

    Returns a picklable dict with keys: match, outcome, plan_steps, error,
    face_count, skipped, skip_reason. Never raises — failures land in
    ``error``. This is a module-level function (not a closure / method) so
    ``ProcessPoolExecutor`` can pickle + ship it to a worker.
    """
    out: dict = {
        "match": None,
        "outcome": None,
        "plan_steps": None,
        "error": None,
        "face_count": None,
        "skipped": False,
        "skip_reason": None,
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

        # 1. Split into components, sort biggest-first.
        split_extras = SplitIntoComponents().apply(
            body, {"min_volume_mm3": args.min_volume_mm3}
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
                "_body_ref": c.get("body_ref"),  # stripped before return
            })

        # 3. Group instances into signature CLASSES.
        #    dedup_instances=False ⇒ every component is its own class (the
        #    byte-identity escape: RE runs once per component, exactly the
        #    historic behaviour). dedup_instances=True ⇒ components that hash
        #    EQUAL share a class and the RE pipeline runs once for the class.
        classes = self._group_into_classes(entries, args.dedup_instances)

        # 4. Run RE once per class (process pool or serial), assigning each
        #    representative's result to every instance in the class.
        self._run_classes(classes, args, _MAX_FACE_COUNT)

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
    ) -> None:
        """Run the RE pipeline once per class and fan the result out to every
        instance member. Uses a process pool unless max_workers==1 or there is
        only one runnable class (serial avoids pool overhead / pickling)."""
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
                self._run_classes_serial(runnable, args, max_face_count)
            else:
                self._run_classes_pool(
                    runnable, args, max_face_count, workers
                )
        finally:
            if tmp_ctx is not None:
                tmp_ctx.cleanup()

    def _run_classes_serial(
        self,
        runnable: list[dict],
        args: "AssemblyReverseEngineer.Args",
        max_face_count: int | None,
    ) -> None:
        for cl in runnable:
            t0 = time.perf_counter()
            res = _run_class_re(
                cl["_brep_path"], cl["_plan_path"],
                args.base_step_kind, max_face_count,
            )
            cl["ran"] = not res.get("skipped")
            self._apply_class_result(
                cl, res, dt_s=round(time.perf_counter() - t0, 2)
            )

    def _run_classes_pool(
        self,
        runnable: list[dict],
        args: "AssemblyReverseEngineer.Args",
        max_face_count: int | None,
        workers: int,
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
                self._run_classes_serial([cl], args, max_face_count)
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
            if res.get("face_count") is not None:
                m["face_count"] = res.get("face_count")
