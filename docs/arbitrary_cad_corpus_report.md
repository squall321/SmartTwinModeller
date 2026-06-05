# Arbitrary CAD RE Corpus — First Run

**Run date:** 2026-06-06
**Source data:** `run_logs/_tmp/corpus_re_results.json`
**Fetcher:** `run_logs/_tmp/fetch_cad_corpus.py`
**Pipeline driver:** `run_logs/_tmp/run_corpus_re.py`
**Corpus root:** `corpus/oem/` (STEP / IGES drops, gitignored except `_inventory.json` + `_sample/`)

---

## Headline

| Metric | Value |
|---|---|
| Files processed | **15** STEP solids (7 KiCad SMD, 2 OCCT, 6 pythonocc-demos) |
| Executor PASS rate | **1 / 15 = 6.7 %** (`pythonocc__splinecage.step`) |
| Executor FAIL count | **14 / 15 = 93.3 %** (all failed with `executor_errors = 1`) |
| Catalog detector skips | **0** — feature catalog ran on every file |
| Median `bbox_vol_diff_pct` | **17.10 %** |
| Mean `bbox_vol_diff_pct` (excl. Ventilator outlier) | **32.93 %** |
| Worst drift | `pythonocc__Ventilator.step` at **45 450 %** (regen bbox blew up 400×) |
| Best drift | `kicad__CP_Elec_10x10.step` at **1.18 %** (still executor FAIL) |

The corpus exercises the round-trip on real, third-party STEP geometry for the first time
outside of our synthetic iPhone teardown. The single PASS is a degenerate shell that the
catalog detector emitted 0 features for, so the executor trivially regenerated a bounding
box. Every solid in the corpus triggered the same single executor error — see Failure-Mode
Taxonomy below.

---

## Per-File Results

| # | Filename | Source | Orig faces | bbox (mm) | Pockets / Holes / Bosses / Ribs | Plan steps | Executor | bbox_vol drift % |
|---|---|---|---:|---|---|---:|---|---:|
| 1 | `kicad__CP_Elec_10x10.step`        | kicad     |   41 | 12.0 × 10.3 × 10.0           | 4 / 5 / 1 / 1   |  13 | FAIL |   1.175 |
| 2 | `kicad__CP_Elec_3x5.4.step`        | kicad     |   41 | 3.8 × 3.3 × 5.4              | 4 / 5 / 1 / 1   |  13 | FAIL |  38.700 |
| 3 | `kicad__C_0402_1005Metric.step`    | kicad     |   28 | 1.0 × 0.5 × 0.5              | 12 / 4 / 0 / 0  |  14 | FAIL |   7.613 |
| 4 | `kicad__D_0603_1608Metric.step`    | kicad     |   39 | 1.6 × 0.87 × 0.66            | 6 / 10 / 0 / 0  |  17 | FAIL |   8.398 |
| 5 | `kicad__LED_0402_1005Metric.step`  | kicad     |   50 | 1.0 × 0.5 × 0.5              | 8 / 4 / 0 / 0   |  11 | FAIL |   2.709 |
| 6 | `kicad__L_0402_1005Metric.step`    | kicad     |   28 | 1.0 × 0.5 × 0.5              | 12 / 4 / 0 / 0  |  14 | FAIL |   7.613 |
| 7 | `kicad__R_0402_1005Metric.step`    | kicad     |   26 | 1.0 × 0.5 × 0.35             | 8 / 4 / 0 / 0   |  11 | FAIL |   9.351 |
| 8 | `occt__linkrods.step`              | occt      |   37 | 5.02 × 1.5 × 2.0             | 3 / 2 / 1 / 0   |   7 | FAIL |  49.558 |
| 9 | `occt__screw.step`                 | occt      |   10 | 19.84 × 20.0 × 42.30         | 1 / 1 / 0 / 0   |   5 | FAIL |   5.670 |
| 10 | `pythonocc__11752.step`           | pythonocc | 1018 | 1280.16 × 144.27 × 133.35    | 4 / 143 / 0 / 0 | 120 | FAIL |  63.079 |
| 11 | `pythonocc__Ventilator.step`      | pythonocc |  305 | 80.0 × 80.0 × 18.08          | 1 / 14 / 0 / 0  |  52 | FAIL | **45 450.4** |
| 12 | `pythonocc__as1-oc-214.step`      | pythonocc |  160 | 200.0 × 150.0 × 84.0         | 11 / 0 / 3 / 0  |  15 | FAIL |  76.187 |
| 13 | `pythonocc__as1_pe_203.step`      | pythonocc |  160 | 5080 × 2209.8 × 3810 (mm)    | 18 / 7 / 4 / 0  |  28 | FAIL |  86.658 |
| 14 | `pythonocc__face_recognition_sample_part.step` | pythonocc | 23 | 315 × 105 × 225 | 1 / 6 / 0 / 0 | 7 | FAIL | 88.799 |
| 15 | `pythonocc__splinecage.step`      | pythonocc |    4 | 37.65 × 28.87 × 6.48 (shell) | 0 / 0 / 0 / 0   |   1 | **PASS** |  17.098 |

Notes:
- Every executor run reports exactly one error. That points at a single shared early-abort
  in the plan pipeline rather than per-feature failures (see taxonomy bucket **EXEC-PLAN-1**).
- IGES files in the corpus (`occt-iges__*`, `pythonocc-iges__*`) were fetched but never reached
  the pipeline — the runner only enumerated `*.step`.

---

## Failure-Mode Taxonomy

| Code | Bucket | Count | What it means |
|---|---|---:|---|
| **PARSE-OK**     | STEP import succeeded                                       | 15 | OCC reader loaded every file; `inspect_orig` returned non-zero face count. |
| **DETECT-OK**    | Catalog detector ran (no skip)                              | 15 | `catalog.skipped = false` on all rows. |
| **DETECT-TRIVIAL** | Catalog returned 0 pockets / 0 holes / 0 bosses / 0 ribs | 1  | `pythonocc__splinecage.step` (shell, not solid) — degenerate. |
| **EXEC-PLAN-1**  | Executor failed with `executor_errors = 1` after plan emit  | 14 | Universal failure mode in this run — same single error fires regardless of file size, plan length (5 → 120 steps), or feature mix. Strongly suggests one early-step bug (likely the base-block / extrude-from-sketch handoff). |
| **REGEN-BBOX-BLOWUP** | Regen bbox far exceeds original (drift > 100 %)       | 1  | `pythonocc__Ventilator.step` regen produced a 400× larger bbox — the executor extruded the fallback base into the wrong coordinate frame. |
| **REGEN-FLAT-PLATE** | Regen collapses to ≈ 6 faces / flat plate              | 12 | Regen volume reduced to a single extruded box (face_count = 6) — the pocket / hole / boss operations never landed; only the base extrude survived. |
| **REGEN-PARTIAL** | Regen kept some sketch features                            | 2  | `kicad__CP_Elec_10x10` (27 faces) and `pythonocc__Ventilator` (146 faces) — partial replay before the executor error. |
| **PASS-DEGENERATE** | Trivial PASS with 0-feature plan                         | 1  | `pythonocc__splinecage.step` — counts as PASS but is not a real win. |

**True PASS rate (non-degenerate):** 0 / 15.

---

## Diversity Assessment

The corpus is intentionally heterogeneous — drawn from three independent upstreams
(OCCT regression assets, pythonocc demos, KiCad 3D library):

| Axis | Range observed |
|---|---|
| Source projects | 3 (OCCT data/, pythonocc-demos assets/, KiCad packages3D) |
| Domains | SMD electronic components, mechanical hardware (screws, link rods), assemblies, mech-part demo geometry, free-form spline cage |
| Body-kind | 14 × `solid`, 1 × `shell` (`splinecage`) |
| Bounding-box smallest dim | 0.35 mm (`R_0402`) — sub-mm SMD passives |
| Bounding-box largest dim | 5 080 mm (`as1_pe_203`) — civil-scale assembly |
| **Linear scale range** | **≈ 14 500×** (0.35 mm → 5 080 mm) |
| Face count min / median / max | 4 / 41 / 1 018 |
| Plan-step count min / median / max | 1 / 13 / 120 |
| Surface complexity | Mostly planar + cylindrical primitives; `splinecage` is pure free-form B-spline; `11752` and `Ventilator` contain many small holes (143 and 14 respectively); `as1_pe_203` mixes large planar walls with pockets and bosses. |
| File-size range | 28 KB (`splinecage`) → 2.26 MB (`Ventilator`); 1.79 MB (`linkrods`) is also large. |

**Verdict:** the spread is broad enough to be a meaningful first stress test — five orders
of magnitude in linear scale, two orders in face count, both shells and solids, and a mix of
prismatic-machined and free-form geometry. The universal executor failure therefore cannot
be blamed on any single geometric class — it is a pipeline issue, not a data issue.

---

## Concrete Remaining Issues (per bucket)

### 1. EXEC-PLAN-1 (14 / 15 files) — top priority

**Symptom:** every solid produces `executor_errors = 1` regardless of plan length.
**Evidence:** failures happen across 5-step and 120-step plans, across 4-face and 1018-face
inputs, on simple cylinders (`occt__screw`) and on complex assemblies (`as1_pe_203`).

**Action items:**
- Capture the per-step executor traceback (currently swallowed — `error: null` in JSON).
  Add `executor.first_error_step_idx` + `executor.first_error_op` + `executor.traceback`
  to the per-file record.
- Re-run the failing executor for `occt__screw.step` (smallest plan, 5 steps) under
  `--debug` and inspect the first failing op. That file is the cleanest minimal repro.
- Suspected root cause: the post-`plan_from_feature_catalog` step that picks the
  extrude direction / base-thickness floor (commits `413f451` and `de02b77` touched this
  area for the iPhone pipeline) does not generalise to arbitrary inputs.

### 2. REGEN-FLAT-PLATE (12 / 14 fails)

**Symptom:** regen ends up as a single extruded box (face_count = 6) — i.e. the executor
gave up after step 1 and never replayed pockets/holes/bosses.

**Action items:**
- This is the visible consequence of EXEC-PLAN-1: only the base extrude executes before
  the first error. Confirm by inspecting `plan.yaml` for `occt__screw` and stepping through.
- After fixing EXEC-PLAN-1, this bucket should empty out automatically.

### 3. REGEN-BBOX-BLOWUP — `pythonocc__Ventilator.step` (1 file)

**Symptom:** regen bbox is `400.5 × 460.6 × 285.8` mm vs original `80 × 80 × 18` mm — 5×
larger on every axis, 45 000 % volume drift. 146 faces survive, so the executor ran further
on this file than on most.

**Action items:**
- Check unit interpretation — does the STEP header specify millimetres but the executor
  treat it as inches? (Inch → mm = 25.4× ; 5× is suspicious but not that ratio.)
- Likely a coordinate-frame / mirror-pattern bug — Ventilator has `symmetries: 6` and the
  detector emitted 14 holes; if the symmetry op was applied to the *base* instead of
  *per-feature*, the part would multiply outward.
- Re-run with `--no-symmetry` to isolate.

### 4. PASS-DEGENERATE — `pythonocc__splinecage.step` (1 file)

**Symptom:** PASS counts a 1-step plan that emits a bounding box for a free-form B-spline
shell. Drift is still 17 %.

**Action items:**
- Add a `body_kind == "shell"` short-circuit that either (a) routes to the
  `mesh_to_brep` path (commit `08fb374`) or (b) marks the file as `not_applicable` rather
  than PASS. As-is, this inflates the PASS rate.
- Independently, raise the bar: define PASS as `executor_errors == 0 AND drift < 10 %`,
  not just zero executor errors.

### 5. Corpus / runner gaps

- IGES files in `_inventory.json` (4 files) are fetched but never executed. Either widen
  the runner glob to include `*.iges` or remove the IGES drop from `fetch_cad_corpus.py`.
- The runner reports `files_processed = 15` while the `results` array contains 15 entries —
  consistent — but per-file `error` is always `null` even on FAIL. Wire the executor error
  string into that field so we don't need to dig through stdout to triage.
- `total_time_s` dominated by import (`kicad__CP_Elec_10x10`: 76.6 s import for an 82 KB
  file). STEP reader cold-start is suspect; see if the reader can be cached / reused.

---

## Next milestone

Land a fix for EXEC-PLAN-1 against `occt__screw.step` (5-step plan, cleanest repro), then
re-run the full corpus. Target: ≥ 5 / 15 non-degenerate PASS with median drift < 10 %.
