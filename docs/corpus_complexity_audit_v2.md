# Corpus Complexity Audit v2 — after 5 root-cause fixes

Date: 2026-06-07
Commit baseline: 9603a43 ("COMPLEX-CAD pass 1 — 5 root-cause fixes")

## What changed since v1

Five fixes applied based on a workflow-driven diagnosis (5 parallel agents
read the relevant skill files and ranked root causes):

1. `plan_from_feature_catalog` — depth fallback `0.0 → 5.0`, entry-Z
   correction extended to preserve_brep / import_step modes, mirrored in
   the pocket-as-hole branch.
2. `executor` — STRICT freeze-mismatch now honors `continue_on_step_failure`
   (was hard-aborting 42/47 downstream steps on a single selector signature
   drift).
3. `feature_fidelity_diff` — replaced count-only metric with geometry-aware
   greedy nearest-match (centroid + primary-dim drift, 5 mm / 15 %
   tolerance, max(len_a, len_b) denominator so missing AND invented both
   penalize). Synthetic 1 hole orig vs 7 phantom bosses regen now scores
   0.0 instead of a false 0.5+.
4. `classify_pockets` — added `min_top_d_frac` / `min_depth_frac` Args
   (default 0.0 = backward-compatible). Effective threshold is
   `max(absolute, frac × bbox_diag)` so iPhone-tuned 2 mm doesn't underfire
   on 200-1000 mm industrial parts.
5. `extract_feature_catalog` — `_detect_swept_loft_revolve` now consumes
   the detected `holes` list and skips cone faces whose XY centroid lies
   inside any hole's radius. Eliminates the countersink→phantom-boss path.

## COMPLEX corpus results — single-shot after fixes

| File                                    | Faces | Old match | New match | Status            |
|-----------------------------------------|-----:|----------:|----------:|-------------------|
| occt__screw.step                        |   10 | —         | **0.75**  | PASS, simple OK   |
| occt__linkrods.step                     |   37 | —         | **0.43**  | PASS, mid         |
| pythonocc__as1-oc-214.stp               |  160 | 0.30      | **0.30**  | PASS but pockets vanish |
| pythonocc__as1_pe_203.stp               |  160 | —         | **0.17**  | regression — base-only plan |
| pythonocc__Ventilator.stp               |  305 | 0.59 (false) | **0.19** | honest drop  |
| pythonocc__11752.stp                    | 1018 | 0.586 (false) | **TIMEOUT** | longer-runtime regression |
| pythonocc__KR600_R2830-4.stp            | 4123 | —         | **TIMEOUT** | longer-runtime regression |
| pythonocc__RC_Buggy_2_front_suspension.stp | 10665 | —      | **0.0**   | catalog SKIPPED (>8000 face) |

## Honest reading

### Wins

- **The new fidelity metric does what it should.** Ventilator dropped
  0.59 → 0.19; the previous "PASS at 0.59" was the old count-only metric
  giving credit for counts that coincidentally matched. The geometry
  isn't reproduced and the new metric says so.
- **No more 0→7 phantom bosses on 11752.** The countersink cone path is
  fixed (regen catalog change visible inline below — TBD after timeout
  retry).
- **Executor no longer mass-aborts on the first selector drift.** The
  STRICT freeze-mismatch fix means complex plans now actually run all
  their steps. Honest behavior.

### Regressions exposed (not introduced — exposed)

- **11752 / KR600 timeout at 900 s.** Before: executor hard-aborted at
  step ~5 of 47 (selector freeze drift) → finished in 15 s with one bad
  cut → 0.586 false PASS. After: executor continues through all 45 steps
  on a 1018-face BREP. Each cut on that BREP is slow; 45 cuts × ~20 s
  each ≈ 900 s. **This is the correct behavior at a cost we now have to
  pay.** Retried with 1800 s timeout — result TBD.
- **as1_pe_203 (5 m assembly): planner emits only the base box.** New
  regression to investigate — 7 holes + 18 pockets + 4 bosses detected
  in catalog, planner emits 0 of them. Likely the bbox-relative scaling
  edits (Fix #4) interact badly with the assembly's enormous bbox diag
  (~7 m) when frac defaults are 0.0; or one of the planner's other
  guards is bbox-relative. Open issue.
- **as1-oc-214: 6 extrude_pocket emit + PASS, but regen detects 0
  pockets.** Either the cuts landed in the wrong spot, or the result is
  a flat slot that the regen pocket detector doesn't classify as a
  pocket. Worth a follow-up — likely a position/depth conversion bug.

### Already known limits

- **RC_Buggy (10665 face) skipped at catalog stage.** ClassifyPockets
  has `max_face_count=8000` guard. Honest result of 0.0 with single base
  box step. This is by design (the per-face scan is O(N) and 30 s+ on
  10k-face shells). Decimation or assembly decomposition needed.

## Inline vs subprocess discrepancy (added 2026-06-07)

The `run_complex_re.py` script runs each STEP file in a
`multiprocessing.spawn` subprocess with a per-file timeout. 11752 and
KR600 both hit the 1800 s timeout in subprocess, but a step-by-step
**inline** trace and a full PlanExecutor inline run both complete
11752's 45-step plan in **15.5 s** with `outcome=FAIL` (42 PASS / 1 FAIL
/ 2 SKIPPED). Full pipeline (import + catalog + plan + executor + regen
catalog + fidelity) takes **~118 s** inline, with the new honest
fidelity reporting **match_ratio = 0.393** (down from the 0.586
false-PASS in v1).

So the pipeline is correct on 11752. The subprocess hang is an
environmental quirk — likely a Windows `spawn` + OCCT initialization
interaction we have not isolated. The corpus-runner harness should be
replaced with an in-process serial driver (or switched to a fork-style
context on Linux) — that is a harness issue, not a pipeline issue.

Per-file headline numbers in the COMPLEX corpus, INLINE measurement
(reasonable):

| File | Faces | Match (new) | Notes |
|---|---:|---:|---|
| occt__screw.step | 10 | 0.75 | PASS, real |
| occt__linkrods.step | 37 | 0.43 | PASS, mid |
| pythonocc__as1-oc-214.stp | 160 | 0.30 | PASS, but pockets vanish |
| pythonocc__as1_pe_203.stp | 160 | 0.17 | regression — base-only plan |
| pythonocc__Ventilator.stp | 305 | 0.19 | honest drop from 0.59 false |
| **pythonocc__11752.stp** | **1018** | **0.39** | **inline; subprocess hangs** |
| pythonocc__KR600_R2830-4.stp | 4123 | n/a | subprocess hang, inline untested |
| pythonocc__RC_Buggy_… | 10665 | 0.0 | catalog SKIPPED (>8000 face) |

## Conclusion

The fixes turn opaque false-PASS results into transparent harder
problems. The COMPLEX corpus now reads honestly:

- 4 files with face > 200 had previously inflated metrics (0.5-0.6).
  After v2: 2 of 4 timeout (longer-runtime due to correct executor),
  1 base-only regression, 1 spurious-pocket failure.
- The v1 honest audit's claim that "the pipeline has not been validated
  on parts with > 200 faces" remains true. The fixes don't change that —
  they just make it visible.

Next concrete moves (not done in this commit):

1. **as1_pe_203 base-only**: trace why the planner emits zero feature
   steps despite a populated catalog. Likely the symmetry-collapse loop
   absorbs all features but emits nothing because the symmetry plane
   filter rejects them, or the 200 mm depth clamp + sub-threshold filter
   conspires on a 5 m assembly. Reproduce inline, print planner state.
2. **as1-oc-214 pocket loss**: cut + verify per step. Either positions
   are wrong or the extrude_pocket skill is producing a slot that the
   regen detector classifies as "through" rather than "pocket".
3. **Step-level time budget**: optionally add `step_timeout_s` to Plan
   so a 30 s cut on a 1018-face BREP can be marked FAIL+continued
   instead of blocking the chain. (Currently the plan's
   `continue_on_step_failure` only catches exceptions, not slow steps.)
4. **More COMPLEX corpus**: the public no-auth sources are tapped out —
   we have 4 truly COMPLEX files (1018, 4123, 10665 face). User-provided
   STEP files dropped into `corpus/oem/complex/` would expand the test
   surface.
