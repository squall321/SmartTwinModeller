# Round-Trip Validation Report

**Date:** 2026-06-02
**Pipeline:** STEP -> extract_feature_catalog -> plan_from_feature_catalog -> executor.run_plan -> STEP
**Headline:** **7 / 7 files round-tripped successfully** (executor produced output without crashing on every input), but only **2 / 7 (28.6 %)** met a meaningful geometric fidelity bar (|vol diff| <= 5 %).

---

## 1. Aggregate Metrics

| Metric | Value |
|---|---|
| Files attempted | 7 |
| Files `ok=true` (executor completed) | 7 |
| Files within +/- 5 % volume | 2 (`demo_patterns_2_circular`, `auto_repro`) |
| Files within +/- 2 % volume | 2 |
| **avg_volume_diff_pct** | **+69.36 %** (signed mean across the 7 files) |
| mean(|volume_diff_pct|) | 83.97 % |
| **avg_face_count_ratio** (regen_F / orig_F) | **0.337** |
| Median face-count ratio | 0.214 |

The signed mean is dominated by the two heavily-truncated outliers (`simple_watch_housing_only` at +345 %, `simple_watch` at +132 %) where the planner fell back to a default 25 000 mm^3 primitive. Excluding those, the signed mean is -8.30 %.

---

## 2. Per-File Results

| File | Orig V (mm^3) | Regen V (mm^3) | % diff | Orig F | Regen F | Plan steps | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| fixtures/simple_watch.step | 10 785.26 | 25 000.00 | **+131.80 %** | 45 | 6 | 10 | fallback primitive |
| fixtures/simple_watch_housing_only.step | 5 614.68 | 25 000.00 | **+345.26 %** | 24 | 6 | 5 | fallback primitive |
| run_logs/_tmp/demo_advanced.step | 15 735.82 | 25 000.00 | **+58.87 %** | 60 | 6 | 33 | plan ignored, fallback |
| run_logs/_tmp/demo_sweep_revolve.step | 49 992.28 | 25 000.00 | **-49.99 %** | 22 | 6 | 4 | fallback primitive |
| run_logs/_tmp/demo_boss_sweep_loft.step | 25 244.02 | 24 981.89 | -1.04 % | 17 | 9 | 9 | OK (close) |
| run_logs/_tmp/demo_patterns_2_circular.step | 24 819.36 | 25 000.00 | +0.73 % | 28 | 6 | 23 | OK |
| run_logs/_tmp/auto_repro.step | 25 028.66 | 25 000.00 | -0.11 % | 7 | 6 | 4 | OK |

Pattern: the regen volume is exactly 25 000.00 mm^3 on 6 of 7 files. That number is the default size of the fallback box created when the planner / executor cannot synthesise a real plan, so a "ok=true" return masks the fact that the executor is silently emitting a placeholder cube instead of the intended geometry.

---

## 3. Failure Modes

Every file returned `ok=true` so there are no explicit `.error` strings. The failure modes below are inferred from the volume / face-count signature.

| # | Failure mode | Files affected | Evidence |
|---|---|---|---|
| 1 | **Executor fallback to 25 000 mm^3 placeholder cube** | 5 / 7 | regen_volume == 25000.0000 exactly and regen_face_count == 6 |
| 2 | **plan_from_feature_catalog emits a plan that the executor cannot consume** | `demo_advanced` (33 steps -> 6-face cube), `demo_patterns_2_circular` (23 steps -> 6-face cube), `simple_watch` (10 steps -> 6-face cube) | high plan_step_count combined with placeholder output |
| 3 | **Feature-catalog under-extraction (single-body parts lose all detail)** | `simple_watch_housing_only` (24 faces -> 5 steps), `demo_sweep_revolve` (22 faces -> 4 steps) | step count far below face count; planner has nothing to reconstruct |
| 4 | **Sweep / revolve features not round-tripping** | `demo_sweep_revolve.step` | named "sweep_revolve" yet planner produced only 4 steps and a cube |
| 5 | **Pattern features (linear / circular) not regenerated** | `demo_patterns_2_circular.step` | 23 plan steps but regen has 6 faces; instances clearly not materialised |
| 6 | **Loft / boss features partially preserved** | `demo_boss_sweep_loft.step` is the *only* file where regen_face_count (9) > 6, suggesting loft handling is the most mature path | sole near-match outside trivial cases |
| 7 | **No geometric-fidelity gate inside the executor** | all 7 | executor returns `ok=true` even when emitting a default cube, so CI cannot detect regressions |
| 8 | **Tessellation / face-count compression is extreme** | 5 / 7 files have regen_F / orig_F <= 0.27 | original 60-face advanced part collapses to a 6-face box |

---

## 4. Weakest Link & Recommendations

### Weakest link
**`plan_from_feature_catalog` is the dominant failure point**, with the executor's silent fallback as a close second.

Evidence:
- `extract_feature_catalog` returns step counts that scale roughly with original face count when the part is simple (`auto_repro`: 7 F -> 4 steps -> 0.11 % volume diff). It is *not* the bottleneck for at least the small-part cases.
- `plan_from_feature_catalog` produces 10-33 plan steps for complex parts, yet the executor still emits a 6-face cube. The plans are being generated but they are either (a) syntactically invalid for the executor schema or (b) reference skills the executor swallows silently.
- The executor never raises; it just returns the default primitive, so the bug stays invisible.

### Recommendations (priority order)

1. **Add a fidelity assertion to the executor / CI harness.** Any round-trip where `abs(volume_diff_pct) > 5 %` or `regen_face_count <= 6` while the original has >= 12 faces must fail the test, not return `ok=true`. Without this, every regression below is invisible.
2. **Instrument `plan_from_feature_catalog` step-by-step.** Log which steps the executor accepts, which it skips, and why. The 33-step `demo_advanced` plan collapsing to a cube is the single highest-signal debugging target.
3. **Stop emitting the 25 000 mm^3 default cube on planner failure.** Raise instead, or emit a zero-volume sentinel. The current behaviour is the reason every file shows `ok=true`.
4. **Sweep / revolve and pattern (linear, circular) regeneration paths need dedicated tests.** Both produced placeholder cubes despite their names being explicit about the feature type, indicating those branches of the planner/executor are simply not wired.
5. **Reverse-engineer the loft path** as the reference implementation. `demo_boss_sweep_loft` was the only non-trivial file with a real (9-face, 1 % volume diff) round-trip; copy that flow.
6. **Tighten `extract_feature_catalog` on housings.** A 24-face watch housing producing only 5 plan steps suggests cylindrical pocket / shell features are being dropped during extraction.

### Suggested next milestone
Move the success bar from "executor returned" to "regen_volume within +/- 5 % AND regen_face_count >= 0.5 * original_face_count". Under that bar, today's pass rate is **2 / 7 (29 %)**.

---

*Generated 2026-06-02 from the round-trip harness output.*
