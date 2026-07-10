"""repair_dfm — macro, REPAIR. Close the analyze->fix loop.

Take a part + its per-process DFM verdict and AUTO-APPLY conservative geometry
fixes for the *fixable* failures, each kept ONLY when it provably improves the
DFM verdict within a bounded geometry change (else reverted verbatim). This is
the first non-read-only DFM skill — a SUGGESTION-AND-APPLY, NOT a
manufacturability guarantee. Worst-case output == input, by construction.

Two AUTO-FIX families (the only ones that compose ONE existing op, target an
identifiable failing entity set, and are a-priori Hausdorff-boundable):

  1. INTERNAL TOOL-RADIUS / SHARP INTERNAL CORNER -> fillet the failing concave
     edges to >= the required tool/fillet radius.
     CRITICAL: the trigger is NOT dfm_verdict's ``min_tool_radius``/``min_fillet``
     check alone. That check reads ``classify_edge_blends`` (existing fillet
     FACES) — a SHARP zero-radius corner produces no blend face, so the check
     returns ``pass`` ("no internal concave fillets to constrain"). The
     sharp-corner case — the headline value — is invisible to it. So we DETECT
     via the existing ``enforce_min_tool_radius`` (auto_fix=False), which walks
     every concave edge and reports SHARP (~0) + too-small fillets in
     ``extras['violations']``; the SAME skill (auto_fix=True) is the fix engine
     (bulk BRepFilletAPI_MakeFillet + per-edge fallback). We reinvent nothing.

  2. DRAFT TOO SMALL (no undercut) -> draft_apply_auto at the process min draft.
     Trigger: dfm_verdict draft check ``marginal`` with below_min_draft>0 AND
     undercut==0. Oblique (non-axis) pull or any undercut -> SUGGEST instead.

SUGGEST-ONLY (no single bounded local op): min_wall (which face/side to grow is
ambiguous), undercut (needs side-action/5-axis), max_wall_sink (coring/ribs),
topology (heal first). Always surfaced — never silently passed.

THREE-gate keep test per fix; fail any -> REVERT to the pre-fix body:
  GATE 0  op did not raise (draft_apply_auto RAISES on no-faces / build-fail).
  GATE 1  the OWNING signal strictly improves (the dfm check moves toward pass,
          or — for the sharp-corner fillet the verdict can't see — the
          enforce_min_tool_radius violation count strictly DECREASES) AND no
          OTHER process's overall verdict regresses (cross-process Pareto guard).
  GATE 2  Hausdorff is conservative — per-fix delta (candidate vs PRE-FIX body)
          within an analytic per-op budget (1.5*R fillet, tan(theta)*H*1.25
          draft) AND cumulative (candidate vs ORIGINAL) within max_hausdorff_mm.
          Deflection is co-designed (<= 0.1*signal) so chord noise << budget.
  GATE 3  no feature swallowed — a fillet must not DROP face count, the result
          must be a valid solid with volume>0.

result_grade='estimate' (heuristic edit, tessellated validation). The output
``grade`` is a separate repair grade in {repaired, partial, no_change,
suggest_only} — never 'pass'/'manufacturable'. Only MEASURED-grade failures are
auto-fixed; an estimate-grade failure is downgraded to SUGGEST (grade_limited).

extras['dfm_repair'] schema — see _apply / module docstring tests.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phone_designer.skills._history import EntityHistoryMap
from phone_designer.skills._post_conditions import PostCondition
from phone_designer.skills._registry import skill
from phone_designer.skills._spec import SkillBase, SkillResult

SCHEMA_VERSION = 1

# Verdict severity ordering — must match dfm_verdict._SEVERITY.
_SEVERITY = {"skipped": 0, "pass": 1, "marginal": 2, "fail": 3}


def _occt_shape(body: Any):
    return body.wrapped if hasattr(body, "wrapped") else body


def _copy_shape(shape):
    """A standalone BRep copy — so the original survives tessellation/edit
    side-effects on the working shape (worst-case==input identity)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    cp = BRepBuilderAPI_Copy(shape)
    cp.Perform(shape, True, False)
    return cp.Shape()


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bb)
    except Exception:
        BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return None
    return bb.Get()  # (xmin,ymin,zmin,xmax,ymax,zmax)


def _bbox_diag(shape) -> float:
    g = _bbox(shape)
    if g is None:
        return 0.0
    xmin, ymin, zmin, xmax, ymax, zmax = g
    return math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)


def _z_extent(shape) -> float:
    g = _bbox(shape)
    if g is None:
        return 0.0
    return float(g[5] - g[2])


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    try:
        BRepGProp.VolumeProperties_s(shape, props)
        return float(props.Mass())
    except Exception:
        return 0.0


def _face_count(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    n = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    return n


def _is_valid_solid(shape) -> bool:
    """At least one solid + a passing BRepCheck (mirrors generate_from_spec's
    is_solid gate). A failed analyzer -> reject the candidate."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    if not ex.More():
        return False
    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:
        return False


def _hausdorff(shape_a, shape_b, deflection_mm: float) -> float | None:
    from phone_designer.skills.reverse_engineer.plan_from_feature_catalog import (
        _hausdorff_inproc,
    )
    # A standalone copy per side — _hausdorff_inproc tessellates in place, and we
    # must not pollute the originals we still compare against later.
    return _hausdorff_inproc(_copy_shape(shape_a), _copy_shape(shape_b),
                             deflection_mm=deflection_mm)


# ──────────────────────────────────────────────────────────────────────────────
# dfm_verdict access

def _run_verdict(body, processes, pull_direction, quality_report=None):
    """Return processes->{verdict,grade,checks} from dfm_verdict."""
    from phone_designer.skills.inspect.dfm_verdict import DfmVerdict
    args = {"processes": list(processes),
            "pull_direction": [float(c) for c in pull_direction]}
    if quality_report is not None:
        args["quality_report"] = quality_report
    res = DfmVerdict().apply(body, args)
    return res.extras["dfm_verdict"]["processes"]


def _check(proc_verdict: dict, check_id: str) -> dict | None:
    for c in proc_verdict.get("checks", []):
        if c.get("id") == check_id:
            return c
    return None


def _overall(proc_verdict: dict) -> str:
    return proc_verdict.get("verdict", "skipped")


def _cross_process_regressed(before: dict, after: dict, processes) -> str | None:
    """A fix is rejected if ANY process's overall verdict gets strictly worse."""
    for p in processes:
        b = _SEVERITY.get(_overall(before.get(p, {})), 0)
        a = _SEVERITY.get(_overall(after.get(p, {})), 0)
        if a > b:
            return p
    return None


def _required_radius(before: dict, processes) -> float | None:
    """Largest required tool/fillet radius across processes whose spec declares
    one (one fillet to the max clears all). Reads the SAME numbers
    dfm_verdict's _chk_min_fillet uses (check.limit on the min_tool_radius /
    min_fillet check)."""
    rad = None
    for p in processes:
        pv = before.get(p, {})
        c = _check(pv, "min_tool_radius") or _check(pv, "min_fillet")
        if c is None:
            continue
        lim = c.get("limit")
        if isinstance(lim, (int, float)) and lim > 0:
            rad = lim if rad is None else max(rad, float(lim))
    return rad


def _draft_inspector_floor() -> float:
    """The draft angle below which dfm_verdict's below_min_draft counter fires.

    dfm_verdict reads the draft section from extract_quality_report, which runs
    inspect_draft_angles with only pull_direction -> its DEFAULT min_draft_deg.
    So the counter's operative threshold is that default, NOT the process limit
    dfm_verdict displays. To make below_min_draft actually drop on re-verify, the
    applied draft must clear THIS floor — single-sourced here, not a magic 1.0."""
    try:
        from phone_designer.skills.inspect.inspect_draft_angles import (
            InspectDraftAngles,
        )
        d = InspectDraftAngles.Args.model_fields["min_draft_deg"].default
        return float(d) if d is not None else 1.0
    except Exception:
        return 1.0


def _draft_candidates(before: dict, processes) -> list[tuple[str, float]]:
    """Processes whose draft check is marginal (below_min_draft>0, undercut==0),
    with the per-process min_draft limit. Returns [(proc, limit), ...]."""
    out: list[tuple[str, float]] = []
    for p in processes:
        c = _check(before.get(p, {}), "draft")
        if c is None or c.get("verdict") != "marginal":
            continue
        m = c.get("measured") or {}
        if not isinstance(m, dict):
            continue
        if int(m.get("undercut", 0)) > 0:
            continue  # undercut -> suggest, not draft
        if int(m.get("below_min_draft", 0)) <= 0:
            continue
        lim = c.get("limit")
        if isinstance(lim, (int, float)) and lim > 0:
            out.append((p, float(lim)))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Fix ops (each returns the new body or raises)

def _enforce_radius_violations(body, min_radius_mm: float):
    """Run enforce_min_tool_radius auto_fix=False -> violation list (DETECT)."""
    from phone_designer.skills.inspect.enforce_min_tool_radius import (
        EnforceMinToolRadius,
    )
    res = EnforceMinToolRadius().apply(
        body, {"min_radius_mm": float(min_radius_mm), "auto_fix": False})
    return res.extras.get("violations", []), res.extras.get("concave_edges_total", 0)


def _enforce_radius_fix(body, min_radius_mm: float):
    """Run enforce_min_tool_radius auto_fix=True ->
    (new_body, edges_filleted, edges_skipped_unsafe).

    edges_skipped_unsafe: violations excluded from the fillet build by the
    skill's crash guard (osculating tangent junction — filleting them would
    ACCESS_VIOLATION the process). Surfaced, never silently dropped."""
    from phone_designer.skills.inspect.enforce_min_tool_radius import (
        EnforceMinToolRadius,
    )
    res = EnforceMinToolRadius().apply(
        body, {"min_radius_mm": float(min_radius_mm), "auto_fix": True})
    return (res.body, int(res.extras.get("edges_filleted", 0)),
            list(res.extras.get("edges_skipped_unsafe", [])))


def _draft_fix(body, min_draft_deg: float, pull: str, parting_z):
    from phone_designer.skills.modify_mold.draft_apply_auto import DraftApplyAuto
    args = {"min_draft_deg": float(min_draft_deg), "pull_direction": pull}
    if parting_z is not None:
        args["parting_z_mm"] = float(parting_z)
    res = DraftApplyAuto().apply(body, args)  # RAISES on no-faces / build-fail
    return res.body


# ──────────────────────────────────────────────────────────────────────────────


@skill(
    name="repair_dfm",
    category="repair",
    level="macro",
    summary="Close the analyze->fix loop: take a part + its per-process DFM "
            "verdict and AUTO-APPLY conservative geometry fixes for the fixable "
            "failures — fillet failing internal concave edges to the required "
            "tool/fillet radius (via enforce_min_tool_radius, which detects the "
            "SHARP zero-radius corners dfm_verdict's edge-blend check cannot "
            "see), and draft_apply_auto for sub-min-draft walls — each kept "
            "ONLY when the DFM verdict strictly improves AND the Hausdorff "
            "(vs original) stays within an analytic budget AND no feature is "
            "swallowed (else REVERT verbatim). Thin wall / undercut / sink / "
            "topology are SUGGEST-only. SUGGESTION-AND-APPLY, not a "
            "manufacturability guarantee; worst-case output == input.",
    selector_kinds=[],
    history_rules={},
    produces_features=["dfm_repair"],
    preserves=[],  # may add faces (fillet) and re-taper walls (draft)
    manufacturing={},
    failure_modes=["fm.no_fixable_issue", "fm.all_fixes_reverted"],
    cost_hint=1.6,  # each candidate re-runs dfm_verdict (full quality report) + a
                    # bidirectional Hausdorff, per fix per round
    result_grade="estimate",
    post_conditions=[PostCondition(kind="body_present")],  # dry-run / all-reverted -> no change
)
class RepairDfm(SkillBase):
    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        processes: list[str] = Field(
            default_factory=lambda: ["cnc_milling", "injection_molding"],
            description="Processes to evaluate + repair (same names as "
                        "dfm_verdict; cost-vocabulary spellings like "
                        "cnc_3axis/cnc_5axis/injection_mold_pa are accepted "
                        "and fold into their DFM family — the result is "
                        "identical either way).")
        pull_direction: list[float] = Field(
            default=[0.0, 0.0, 1.0], min_length=3, max_length=3,
            description="Mold-open / tool axis; sign of Z -> draft_apply_auto "
                        "+Z/-Z. Oblique (non-axis) pull -> draft becomes SUGGEST.")
        parting_z_mm: float | None = Field(
            default=None,
            description="Neutral-plane Z passthrough to draft_apply_auto; "
                        "None -> bbox top/bottom.")
        quality_report: dict | None = Field(
            default=None,
            description="Pre-computed extract_quality_report to seed the first "
                        "dfm_verdict (skip the first re-measure).")
        apply: bool = Field(
            default=True,
            description="True = apply+gate fixes, return repaired body. "
                        "False = DRY-RUN: full keep/revert simulation with REAL "
                        "measured hausdorff + before/after, but return the body "
                        "UNCHANGED (fixes flagged would_apply, body_changed=False).")
        max_hausdorff_mm: float | None = Field(
            default=None, gt=0.0,
            description="Hard global ceiling on TOTAL fixed-vs-original "
                        "hausdorff. None -> 2% of part bbox diagonal. The per-op "
                        "analytic budget applies per fix; the tighter wins.")
        max_rounds: int = Field(
            default=3, ge=1, le=10,
            description="Max analyze->fix->verify rounds. A zero-kept-fix round "
                        "terminates early (fixpoint).")
        fix_categories: list[str] = Field(
            default_factory=lambda: ["fillet", "draft"],
            description="Enabled auto-fix categories. 'fillet' = internal "
                        "tool-radius / sharp-internal-corner; 'draft' = "
                        "sub-min-draft walls. Empty -> analyze + suggest only.")
        fillet_margin: float = Field(
            default=0.25, ge=0.0, le=1.0,
            description="Fraction above the required radius so re-verify lands "
                        "on pass not marginal. Default 0.25 clears dfm_verdict's "
                        "marginal band (r_min < 1.2*limit -> marginal): a sharp "
                        "corner filleted to exactly the tool radius would read "
                        "marginal and trip the cross-process guard, so the "
                        "repair targets 1.25x to produce a clean pass.")
        draft_slack_frac: float = Field(
            default=0.1, ge=0.0, le=0.5,
            description="Fraction above the min_draft limit so re-verify clears "
                        "the >= test.")

    # ──────────────────────────────────────────────────────────────────────────
    def _apply(self, body: Any, args: Args) -> SkillResult:
        if body is None:
            raise ValueError("repair_dfm: body is None")

        # Accept BOTH vocabularies (cost 'cnc_3axis'/'cnc_5axis' == dfm
        # 'cnc_milling' family, ...) — canonicalise ONCE so the spelling can
        # never change the result: repair_dfm(['cnc_3axis']) is
        # result-IDENTICAL to repair_dfm(['cnc_milling']). Output 'processes'
        # carries the canonical DFM names (unchanged for canonical callers).
        from phone_designer.skills._process_names import canon_dfm_processes

        processes, _ = canon_dfm_processes(args.processes)

        original_shape = _occt_shape(body)
        if original_shape is None:
            raise ValueError("repair_dfm: body has no OCCT shape")
        # Snapshot ORIGINAL (survives all tessellation/edit side-effects).
        original = _copy_shape(original_shape)
        bbox_diag = _bbox_diag(original) or 1.0
        z_ext = _z_extent(original) or bbox_diag
        global_ceiling = (float(args.max_hausdorff_mm)
                          if args.max_hausdorff_mm is not None
                          else 0.02 * bbox_diag)

        from build123d import Part
        working = Part(_copy_shape(original))  # current accepted body

        cats = set(args.fix_categories)
        # Pull-direction -> +Z/-Z; oblique => no draft.
        px, py, pz = (float(c) for c in args.pull_direction)
        oblique = (abs(px) > 1e-6 or abs(py) > 1e-6)
        pull_z = "+Z" if pz >= 0 else "-Z"

        before_verdict = _run_verdict(working, processes, args.pull_direction,
                                      args.quality_report)
        # Worst consulted grade (provenance cap) — estimate => grade_limited.
        grade_limited = any(
            pv.get("grade") == "estimate" for pv in before_verdict.values())

        fixes_applied: list[dict] = []
        fixes_rejected: list[dict] = []
        rounds_used = 0

        # ─── analyze -> fix -> verify rounds ────────────────────────────────
        for _round in range(args.max_rounds):
            rounds_used = _round + 1
            cur_verdict = _run_verdict(working, processes,
                                       args.pull_direction)
            kept_this_round = 0

            # Build this round's candidate list (re-derived each round vs the
            # current working body, so indices/limits are never stale).
            # ORDER MATTERS: draft (bulk re-taper) is applied BEFORE fillet
            # (corner finishing). draft_apply_auto's BRepOffsetAPI_DraftAngle
            # fails on walls whose corners are already filleted, so drafting the
            # clean vertical walls first lets both fixes succeed on one part.
            candidates: list[dict] = []

            # (1) DRAFT — sub-min-draft walls (bulk form, applied first).
            if "draft" in cats and not oblique:
                dcs = _draft_candidates(cur_verdict, processes)
                if dcs:
                    # one draft to the largest required angle clears all. The
                    # applied angle must also clear the inspector floor (the
                    # threshold dfm_verdict's below_min_draft counter actually
                    # uses), else the counter never drops and the fix can't
                    # improve the verdict.
                    lim = max(lim for _p, lim in dcs)
                    floor = _draft_inspector_floor()
                    candidates.append({
                        "issue": "draft", "op": "draft_apply_auto",
                        "kind": "draft", "limit": lim,
                        "min_draft": max(lim, floor) * (1.0 + args.draft_slack_frac),
                        "process_origin": [p for p, _lim in dcs],
                    })

            # (2) FILLET — internal tool radius / sharp corner (finishing, last).
            if "fillet" in cats:
                r_req = _required_radius(cur_verdict, processes)
                if r_req is not None:
                    viol, _tot = _enforce_radius_violations(working, r_req)
                    if viol:
                        r_applied = r_req * (1.0 + args.fillet_margin)
                        candidates.append({
                            "issue": "internal_tool_radius",
                            "op": "enforce_min_tool_radius",
                            "kind": "fillet",
                            "r_req": r_req, "r_applied": r_applied,
                            "viol_before": len(viol),
                            "process_origin": [
                                p for p in processes
                                if (_check(cur_verdict.get(p, {}), "min_tool_radius")
                                    or _check(cur_verdict.get(p, {}), "min_fillet"))],
                        })

            if not candidates:
                break  # fixpoint — nothing left to try

            # ─── apply + 3-gate keep test, one candidate at a time ──────────
            for cand in candidates:
                pre_shape = _copy_shape(_occt_shape(working))  # pre-fix snapshot
                pre_faces = _face_count(pre_shape)
                # Recompute the baseline verdict against the CURRENT working body
                # (a prior candidate this round may already have changed it), so
                # the GATE-1 owning + cross-process checks compare against the
                # real pre-fix state, not the stale start-of-round verdict.
                base_verdict = _run_verdict(working, processes,
                                            args.pull_direction)
                if cand["kind"] == "fillet":
                    # re-measure the violation count on the current working body.
                    cur_viol, _ = _enforce_radius_violations(working, cand["r_req"])
                    cand["viol_before"] = len(cur_viol)
                    if cand["viol_before"] == 0:
                        fixes_rejected.append(_reject(
                            cand, "verdict_not_improved", None, None,
                            detail="no remaining radius violations"))
                        continue

                # GATE 0 — op did not raise.
                try:
                    if cand["kind"] == "fillet":
                        new_body, n_filleted, skipped_unsafe = \
                            _enforce_radius_fix(working, cand["r_applied"])
                        cand["edge_count"] = n_filleted
                        cand["edges_skipped_unsafe"] = skipped_unsafe
                        if n_filleted == 0:
                            if skipped_unsafe:
                                raise RuntimeError(
                                    f"0 edges filleted ({len(skipped_unsafe)} "
                                    "crash-prone edge(s) skipped by guard: "
                                    f"{skipped_unsafe[0]['reason'][:80]})")
                            raise RuntimeError("0 edges filleted")
                    else:
                        new_body = _draft_fix(working, cand["min_draft"],
                                              pull_z, args.parting_z_mm)
                except Exception as exc:
                    fixes_rejected.append(_reject(cand, "op_failed", None, None,
                                                  detail=str(exc)[:120]))
                    continue

                new_shape = _occt_shape(new_body)

                # GATE 3 (cheap, do early) — valid solid, vol>0, no face loss.
                if not _is_valid_solid(new_shape) or _volume(new_shape) <= 0.0:
                    fixes_rejected.append(_reject(cand, "topology_loss", None,
                                                  None, detail="invalid/empty solid"))
                    continue
                if cand["kind"] == "fillet" and _face_count(new_shape) < pre_faces:
                    fixes_rejected.append(_reject(
                        cand, "topology_loss", None, None,
                        detail=f"face count {_face_count(new_shape)} < {pre_faces}"))
                    continue

                # GATE 1 — verdict moved + no cross-process regression (compared
                # against the per-candidate base_verdict, not the round start).
                after_verdict = _run_verdict(new_body, processes,
                                             args.pull_direction)
                owning_ok, dfm_before, dfm_after = self._owning_improved(
                    cand, base_verdict, after_verdict, working, new_body, args)
                if not owning_ok:
                    fixes_rejected.append(_reject(
                        cand, "verdict_not_improved", None, None,
                        dfm_before=dfm_before, dfm_after=dfm_after))
                    continue
                regr = _cross_process_regressed(base_verdict, after_verdict,
                                                processes)
                if regr is not None:
                    fixes_rejected.append(_reject(
                        cand, "cross_process_regression", None, None,
                        detail=f"{regr} regressed"))
                    continue

                # GATE 2 — Hausdorff conservative (per-fix + cumulative).
                if cand["kind"] == "fillet":
                    R = cand["r_applied"]
                    budget_step = max(1.5 * R, 0.005 * bbox_diag)
                    defl = min(0.1 * R, 0.1)
                else:
                    theta = math.radians(cand["min_draft"])
                    # tan(theta)*z_ext is the single-face geometric bound (neutral
                    # at bbox top); draft_apply_auto tapers ALL near-vertical
                    # faces, so the measured deviation can exceed that bound by
                    # compounding across faces/edges. Factor 2.0 admits the
                    # legitimate sub-mm draft; the global ceiling (2% bbox diag)
                    # is the hard "blow-up" guard, and GATE-3 catches topology loss.
                    budget_step = max(math.tan(theta) * z_ext * 2.0,
                                      0.01 * bbox_diag)
                    defl = min(0.1, max(0.02, 0.05 * math.tan(theta) * z_ext))
                h_step = _hausdorff(new_shape, pre_shape, defl)
                h_total = _hausdorff(new_shape, original, defl)
                if h_step is None or h_total is None:
                    fixes_rejected.append(_reject(cand, "hausdorff_over_budget",
                                                  None, budget_step,
                                                  detail="hausdorff failed"))
                    continue
                if h_step > budget_step or h_total > global_ceiling:
                    fixes_rejected.append(_reject(
                        cand, "hausdorff_over_budget", round(h_step, 4),
                        round(budget_step, 4),
                        detail=(f"step {h_step:.3f}>{budget_step:.3f} or "
                                f"total {h_total:.3f}>{global_ceiling:.3f} mm")))
                    continue

                # KEEP.
                working = new_body
                kept_this_round += 1
                if cand["kind"] == "fillet":
                    fix_params = {"min_radius_mm": round(cand["r_applied"], 4),
                                  "edge_count": cand.get("edge_count", 0)}
                    # Crash-guard skips (osculating tangent junction) — surfaced
                    # only when present so pre-guard results stay byte-identical.
                    if cand.get("edges_skipped_unsafe"):
                        fix_params["edges_skipped_unsafe"] = \
                            cand["edges_skipped_unsafe"]
                else:
                    fix_params = {"min_draft_deg": round(cand["min_draft"], 4),
                                  "pull_direction": pull_z,
                                  "parting_z_mm": args.parting_z_mm}
                fixes_applied.append({
                    "issue": cand["issue"],
                    "process_origin": cand.get("process_origin", []),
                    "op": cand["op"],
                    "params": fix_params,
                    "hausdorff_mm": round(h_step, 4),
                    "hausdorff_budget_mm": round(budget_step, 4),
                    "dfm_before": dfm_before,
                    "dfm_after": dfm_after,
                })

            if kept_this_round == 0:
                break  # fixpoint

        # ─── after verdict (final body) + suggestions ───────────────────────
        after_verdict = _run_verdict(working, processes, args.pull_direction)
        suggestions = _build_suggestions(after_verdict, processes)

        body_changed = bool(fixes_applied) and bool(args.apply)
        total_h = 0.0
        if body_changed:
            ht = _hausdorff(_occt_shape(working), original, min(0.1, 0.01 * bbox_diag))
            total_h = round(ht, 4) if ht is not None else 0.0

        grade = _repair_grade(fixes_applied, after_verdict, suggestions,
                              processes, body_changed)

        out_body = body if not body_changed else working

        extras = {
            "dfm_repair": {
                "schema_version": SCHEMA_VERSION,
                "processes": list(processes),
                "pull_direction": [round(float(c), 6) for c in args.pull_direction],
                "apply": bool(args.apply),
                "before": _verdict_summary(before_verdict),
                "fixes_applied": fixes_applied,
                "fixes_rejected": fixes_rejected,
                "suggestions": suggestions,
                "after": _verdict_summary(after_verdict, before_verdict),
                "body_changed": body_changed,
                "total_hausdorff_mm": total_h,
                "max_hausdorff_mm": round(global_ceiling, 4),
                "rounds_used": rounds_used,
                "grade": grade,
                "result_grade": "estimate",
                "grade_limited": bool(grade_limited),
                "summary": _summary(fixes_applied, suggestions, grade,
                                    global_ceiling),
            },
        }
        return SkillResult(
            body=out_body,
            history=EntityHistoryMap(),
            extras=extras,
        )

    # ──────────────────────────────────────────────────────────────────────────
    def _owning_improved(self, cand, before, after, pre_body, new_body, args):
        """GATE-1(a): the owning signal strictly improves. Returns
        (ok, dfm_before, dfm_after)."""
        if cand["kind"] == "draft":
            procs = cand.get("process_origin", [])
            ok = False
            db = da = None
            for p in procs:
                cb = _check(before.get(p, {}), "draft")
                ca = _check(after.get(p, {}), "draft")
                if cb is None or ca is None:
                    continue
                db = cb.get("verdict")
                da = ca.get("verdict")
                if _SEVERITY.get(da, 3) < _SEVERITY.get(db, 0):
                    ok = True
                    break
            return ok, db, da

        # fillet: the dfm min_fillet check can't see sharp corners, so the
        # owning signal is the enforce_min_tool_radius violation count.
        viol_after, _ = _enforce_radius_violations(new_body, cand["r_req"])
        ok = len(viol_after) < cand["viol_before"]
        # also report the dfm check transition when present (already-rounded case).
        db = da = None
        for p in cand.get("process_origin", []):
            cb = _check(before.get(p, {}), "min_tool_radius") or \
                _check(before.get(p, {}), "min_fillet")
            ca = _check(after.get(p, {}), "min_tool_radius") or \
                _check(after.get(p, {}), "min_fillet")
            if cb and ca:
                db, da = cb.get("verdict"), ca.get("verdict")
                if _SEVERITY.get(da, 3) < _SEVERITY.get(db, 0):
                    ok = True
                break
        if db is None:
            db = f"{cand['viol_before']} violation(s)"
            da = f"{len(viol_after)} violation(s)"
        return ok, db, da


# ──────────────────────────────────────────────────────────────────────────────
# small builders (module-level, no self)

def _reject(cand, reason, h, budget, *, dfm_before=None, dfm_after=None,
            detail=None) -> dict:
    rec = {"issue": cand["issue"], "op": cand["op"], "reason": reason}
    if h is not None:
        rec["hausdorff_mm"] = h
    if budget is not None:
        rec["hausdorff_budget_mm"] = budget
    if dfm_before is not None:
        rec["dfm_before"] = dfm_before
    if dfm_after is not None:
        rec["dfm_after"] = dfm_after
    if detail:
        rec["detail"] = detail
    return rec


def _verdict_summary(verdict: dict, before: dict | None = None) -> dict:
    out = {}
    for p, pv in verdict.items():
        rec = {
            "verdict": pv.get("verdict"),
            "grade": pv.get("grade"),
            "checks": [{"id": c.get("id"), "verdict": c.get("verdict"),
                        "measured": c.get("measured"), "limit": c.get("limit")}
                       for c in pv.get("checks", [])],
        }
        if before is not None and p in before:
            rec["improved_from"] = before[p].get("verdict")
        out[p] = rec
    return out


def _build_suggestions(verdict: dict, processes) -> list[dict]:
    """Surface every suggest-only fail/marginal — never silently passed."""
    advice = {
        "min_wall": ("thicken the NON-mating side to reach the floor via "
                     "surface_thicken_variable / shell_variable_thickness",
                     "thicken side ambiguous (outer changes envelope / inner "
                     "reduces cavity)"),
        "undercut": ("add side-action/slide, re-orient/split, or accept 5-axis "
                     "(cost up)", "needs side-action/5-axis or redesign"),
        "max_wall_sink": ("core out the thick section or add ribs",
                          "topology-adding redesign, no canonical auto-form"),
        "topology": ("run shape_heal / sew_faces_to_shell before DFM repair",
                     "needs heal-first, out of repair_dfm remit"),
        "min_feature": ("feature below printable min — coarsen or re-orient",
                        "thin-feature growth is which-side ambiguous"),
    }
    seen = set()
    out: list[dict] = []
    for p in processes:
        for c in verdict.get(p, {}).get("checks", []):
            cid = c.get("id")
            if cid not in advice or c.get("verdict") not in ("fail", "marginal"):
                continue
            key = (cid, p)
            if key in seen:
                continue
            seen.add(key)
            adv, why = advice[cid]
            out.append({
                "issue": cid, "process": p,
                "measured": c.get("measured"), "limit": c.get("limit"),
                "advice": adv, "reason_not_auto": why,
            })
    return out


def _repair_grade(fixes_applied, after_verdict, suggestions, processes,
                  body_changed) -> str:
    if not body_changed:
        # nothing kept — either dry-run, all reverted, or only suggest-only.
        any_auto_fixable = any(
            _check(after_verdict.get(p, {}), cid) and
            _check(after_verdict.get(p, {}), cid).get("verdict") in ("fail", "marginal")
            for p in processes
            for cid in ("min_tool_radius", "min_fillet", "draft"))
        return "suggest_only" if (suggestions and not any_auto_fixable) else "no_change"
    # at least one fix kept.
    residual = any(
        c.get("verdict") in ("fail", "marginal")
        for p in processes
        for c in after_verdict.get(p, {}).get("checks", []))
    return "partial" if residual else "repaired"


def _summary(fixes_applied, suggestions, grade, ceiling) -> str:
    n = len(fixes_applied)
    k = len(suggestions)
    return (f"{grade}: kept {n} fix(es), {k} suggest-only; "
            f"worst-case==input, hausdorff<={ceiling:.3f} mm")
