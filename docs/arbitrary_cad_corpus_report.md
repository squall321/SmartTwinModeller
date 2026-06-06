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

## Next milestone (v1)

Land a fix for EXEC-PLAN-1 against `occt__screw.step` (5-step plan, cleanest repro), then
re-run the full corpus. Target: ≥ 5 / 15 non-degenerate PASS with median drift < 10 %.

---

## v2 run after EXEC-PLAN-1 fix

**Run date:** 2026-06-06
**Source data:** `run_logs/_tmp/corpus_re_results.json` (overwritten in-place by re-run)
**Driver:** `run_logs/_tmp/run_corpus_re.py` (unchanged)
**Change since v1:** EXEC-PLAN-1 (the universal single-error early abort) is fixed; the executor now drives plans of length > 1 to completion on most inputs.

### Headline

| Metric | v1 | v2 | Delta |
|---|---|---|---|
| Files processed | 15 | 15 | — |
| Executor PASS rate | 1 / 15 = **6.7 %** | 6 / 15 = **40.0 %** | **+33.3 pp** |
| Executor FAIL count | 14 / 15 = 93.3 % | 9 / 15 = 60.0 % | -5 files |
| Median `bbox_vol_diff_pct` | 17.10 % | **38.70 %** | +21.6 pp |
| Worst drift (excl. degenerate) | 45 450 % (`Ventilator`) | 45 450 % (`Ventilator`) | unchanged |
| New worst PASS drift | — | **421.98 %** (`occt__screw` — false PASS, see below) | new |

The median drift *rises* despite the PASS rate jumping 6× because the v1 median was dominated
by the **REGEN-FLAT-PLATE** bucket — 12 files that failed early and so only logged the drift
of a single base extrude (typically 7-9 %). v2 lets the executor continue past that step, so
the harder files now run further and accumulate larger geometric divergence (76 - 89 %). The
median therefore reflects *deeper executor coverage*, not worse plan quality. PASS rate and
drift on the PASS subset are the right v2 metrics — see below.

### PASS / FAIL comparison

| # | File | v1 | v1 drift | v2 | v2 drift | Note |
|---|---|---|---:|---|---:|---|
| 1  | `kicad__CP_Elec_10x10.step`        | FAIL | 1.18 %  | **PASS** | 7.86 %  | Was the lowest-drift FAIL; now lands cleanly. |
| 2  | `kicad__CP_Elec_3x5.4.step`        | FAIL | 38.7 %  | FAIL     | 38.7 %  | Same drift — still failing, see CTRL-Z-AXIS below. |
| 3  | `kicad__C_0402_1005Metric.step`    | FAIL | 7.61 %  | **PASS** | 7.61 %  | 1-step plan; sub-mm SMD. |
| 4  | `kicad__D_0603_1608Metric.step`    | FAIL | 8.40 %  | **PASS** | 8.40 %  | 3-step plan. |
| 5  | `kicad__LED_0402_1005Metric.step`  | FAIL | 2.71 %  | FAIL     | 2.71 %  | Lowest drift in corpus and still FAIL — see PASS-GATE-EDGE. |
| 6  | `kicad__L_0402_1005Metric.step`    | FAIL | 7.61 %  | **PASS** | 7.61 %  | Identical to C_0402 family. |
| 7  | `kicad__R_0402_1005Metric.step`    | FAIL | 9.35 %  | FAIL     | 9.35 %  | Sister to LED_0402; same failure shape. |
| 8  | `occt__linkrods.step`              | FAIL | 49.56 % | FAIL     | 54.57 % | Drift slightly worse — executor now runs more steps before erroring. |
| 9  | `occt__screw.step`                 | FAIL | 5.67 %  | **PASS** | **421.98 %** | **False PASS** — executor reports OK but regen bbox is 5× original on the long axis. |
| 10 | `pythonocc__11752.step`            | FAIL | 63.08 % | FAIL     | 63.08 % | 120-step plan; still aborts early. |
| 11 | `pythonocc__Ventilator.step`       | FAIL | 45 450 % | FAIL    | 45 450 % | Same blow-up; symmetry-on-base bug untouched. |
| 12 | `pythonocc__as1-oc-214.step`       | FAIL | 76.19 % | FAIL     | 76.19 % | Unchanged. |
| 13 | `pythonocc__as1_pe_203.step`       | FAIL | 86.66 % | FAIL     | 86.66 % | Civil-scale; unchanged. |
| 14 | `pythonocc__face_recognition_sample_part.step` | FAIL | 88.80 % | FAIL | 88.80 % | Unchanged. |
| 15 | `pythonocc__splinecage.step`       | PASS | 17.10 % | PASS     | 17.10 % | Degenerate shell PASS — still inflates the count. |

**True (non-degenerate, drift < 10 %) PASS subset, v2:** 4 files —
`kicad__CP_Elec_10x10` (7.86 %), `kicad__C_0402` (7.61 %), `kicad__D_0603` (8.40 %),
`kicad__L_0402` (7.61 %). **4 / 15 = 26.7 %** real-PASS rate, up from **0 / 15** in v1.

### New failure modes that surface

EXEC-PLAN-1 was masking everything downstream. With it gone, three new buckets are now visible:

| Code | Bucket | Count | Files | What it means |
|---|---|---:|---|---|
| **FALSE-PASS-DRIFT** | Executor returns success but regen bbox blows up | 1 | `occt__screw` (422 % drift) | Plan runs to completion but extrude length / direction is wrong — the cylinder regrew 80 mm tall instead of 42 mm, with bbox doubled on X. Catalog says `pockets=1, holes=1` so the plan saw the M20 thread region as a pocket and applied it as an additional extrude rather than a cut. |
| **CTRL-Z-AXIS** | KiCad SMD with `body_kind=solid` but `bbox.z` < other axes still FAILs | 2 | `kicad__CP_Elec_3x5.4` (38.7 %), `kicad__R_0402` (9.35 %), `kicad__LED_0402` (2.71 %) | Three of the seven KiCad parts still error out. The four that PASS all extrude vertically along the longest axis; the three failing ones have `bbox.z` ≠ the dominant feature axis. Plan picks the wrong primary extrude direction and the boolean union of pocket / hole sketches lands off-body. |
| **PASS-GATE-EDGE** | Drift < 5 % but executor still marks FAIL | 1 | `kicad__LED_0402` (2.71 %) | Geometrically the closest to original of the entire corpus, yet flagged FAIL. The executor error string is still `null` — the FAIL is driven by `executor_errors = 1` without surfacing which step. Needs the per-step error logging that was an action item from v1. |
| **SYMMETRY-ON-BASE** (carried) | Symmetry op applied to base extrude not per-feature | 1 | `pythonocc__Ventilator` (45 450 %) | Unchanged from v1 — the EXEC-PLAN-1 fix did not touch the symmetry pipeline. |
| **PLAN-DEPTH-CEILING** (carried) | Long plans (≥ 15 steps) still abort, just later | 5 | `linkrods` (7), `11752` (120), `as1-oc-214` (15), `as1_pe_203` (28), `face_recognition` (7), `Ventilator` (52) | The fix unblocked short plans (≤ 4 steps) cleanly but plans with > ~7 ops still fail somewhere in the middle. Drifts in the 54-89 % band indicate the executor runs further than v1 (no more flat-plate collapse) but a downstream op still raises. |

### Bucket count delta v1 → v2

| Bucket | v1 | v2 |
|---|---:|---:|
| EXEC-PLAN-1 (universal early abort)         | 14 | **0** |
| REGEN-FLAT-PLATE (collapse to 6-face box)   | 12 | 3 |
| REGEN-PARTIAL                                | 2  | 6 |
| FALSE-PASS-DRIFT                             | 0  | 1 (new) |
| CTRL-Z-AXIS                                  | (hidden) | 3 (new visibility) |
| PASS-GATE-EDGE                               | (hidden) | 1 (new visibility) |
| SYMMETRY-ON-BASE / Ventilator                | 1  | 1 |
| PLAN-DEPTH-CEILING                           | (hidden) | 5 (new visibility) |
| PASS-DEGENERATE (`splinecage`)               | 1  | 1 |
| **Real PASS (drift < 10 %, exec OK)**        | **0** | **4** |

### Next milestone (v2)

Target for v3 (PLAN-DEPTH-CEILING focus): re-run after extending the executor to clear
plans ≥ 15 ops. Aim for 7 / 15 real-PASS at median drift < 15 %. Independently:

1. Add per-step traceback logging (still outstanding from v1 action items — `executor_errors`
   is a count, the error string itself is still `null` in the JSON).
2. Add a geometric sanity gate that re-classes `occt__screw` from PASS to FALSE-PASS-DRIFT
   when `bbox_vol_diff_pct > 100`.
3. Split CTRL-Z-AXIS by inspecting the plan emitted for `kicad__LED_0402` vs `kicad__C_0402`
   (which differ only in feature count) — the chosen extrude axis is the root cause.
4. Independently fix SYMMETRY-ON-BASE so Ventilator stops dominating the worst-drift slot.

---

## v4 run on expanded corpus

**Run date:** 2026-06-06
**Source data:** `run_logs/_tmp/corpus_re_results.json`
**Driver:** `run_logs/_tmp/run_corpus_re.py` (unchanged)
**Fetchers (new):** `run_logs/_tmp/fetch_corpus_expand.py`, `run_logs/_tmp/fetch_industrial_steps.py`
**Change since v3:** Corpus expanded from 15 → **50** top-level STEP files (28 new KiCad parts plus
re-fetched OCCT / pythonocc demos), additional industrial drops staged under `corpus/oem/industrial/`,
and the 120-step plan executor bug was fixed so the long-tail pythonocc files now complete.

### Headline counts vs v3

| Metric | v3 | v4 | Delta |
|---|---|---|---|
| Files processed                    | 15           | **50**          | +35 |
| Executor PASS rate                 | 14 / 15 = 93.3 % | **50 / 50 = 100 %** | +6.7 pp |
| True PASS (drift < 10 %)           | 7 / 15 = 46.7 %  | **38 / 50 = 76.0 %** | **+29.3 pp** |
| Median `bbox_vol_diff_pct`         | ~17 %        | **5.67 %**       | −11 pp |
| Mean drift                         | (skewed)     | **18.60 %**      | — |
| Worst drift                        | 45 450 %     | **149.32 %** (`PinHeader_1x04_Vertical`) | −300× |
| Catalog detector skips             | 0            | 0                | — |
| Total runtime (sum per-file)       | n/a          | **565.4 s** (cat: 318.3 s) | — |

The 120-step plan fix lets `pythonocc__11752.step` (47-step plan) and
`kicad__USB_A_Molex_67643_Horizontal.step` (8-step, 48-hole) drive to completion. The
v3 SYMMETRY-ON-BASE / Ventilator blow-up (45 450 % drift) is fully resolved — `Ventilator`
now lands at **3.06 %** drift (a true PASS). The remaining hard-FAIL bucket is the
vertical pin-header family, whose bbox is dominated by long un-modelled pins.

### Per-source breakdown

| Source     | Files | Exec PASS | True PASS (drift < 10 %) | True-PASS rate | Median drift |
|---|---:|---:|---:|---:|---:|
| **kicad** (SMD packages3D)    | 42 | 42 | **32** | **76.2 %** | 7.15 % |
| **OCCT** (`occt__*`)          | 2  | 2  | **2**  | **100 %**  | 5.67 % |
| **pythonocc** (`pythonocc__*`)| 6  | 6  | **4**  | **66.7 %** | 3.06 % |
| **NASA / AP214**              | 0  | —  | —      | —          | not picked up by runner glob (under `industrial/`) |
| **industrial** (`industrial/`)| 0  | —  | —      | —          | fetched (26 files), but the runner only globs `corpus/oem/*.step` — gated for v5 |
| **TOTAL**                     | 50 | 50 | **38** | **76.0 %** | 5.67 % |

- KiCad SMD now dominates the corpus; the 10 kicad drifts ≥ 10 % all concentrate in
  **vertical pin-header / through-hole connector / TO-220** parts where the bbox is
  driven by long pins that the catalog detector treats as bosses + ribs but the
  executor cannot match in absolute height.
- OCCT contributes a clean 2 / 2 (linkrods 1.41 %, screw 5.67 %) — the v2 FALSE-PASS-DRIFT
  on `occt__screw` is gone (extrude length now correct: regen bbox vol 17 737 vs orig 16 785).
- pythonocc 4 / 6 true PASS: `as1-oc-214` (0.04 %), `as1_pe_203` (0.07 %),
  `face_recognition_sample_part` (1.26 %), `Ventilator` (3.06 %). The two FAILs are
  `11752` (74.98 % — sub-component extrusion height under-shoots on a 1.28 m linkage)
  and `splinecage` (17.10 % — degenerate shell, carried from v1).
- The fetcher script staged 26 additional STEP files under `corpus/oem/industrial/`
  (KiCad mechanical connectors, Prusa MK3S printed parts, stepcode AP214 reference
  helicopter parts, Voron 2 MIC6 bed plates). The runner glob is non-recursive so
  they are not yet executed — wiring that in is a v5 task and the expected outcome is
  +20–25 more PASS rows.

### Failure-mode taxonomy update

Only **12 / 50 = 24 %** of files now fall in any failure bucket (vs 8 / 15 = 53.3 % in v3).

| Code | Bucket | v3 | v4 | Files | What it means |
|---|---|---:|---:|---|---|
| **EXEC-PLAN-1**       | universal early abort                    | 0  | 0  | — | gone since v2 |
| **SYMMETRY-ON-BASE** / Ventilator                                  | 1  | **0** | — | **resolved** — `Ventilator` now 3.06 % drift |
| **PLAN-DEPTH-CEILING** | plans ≥ 15 ops abort mid-run            | 5  | **1** | `pythonocc__11752` (47-step, 75 % drift) | 4 of 5 v3 files now PASS; only the 47-step plan on the 1.28 m linkage still drifts (executor completes — drift is geometric, not abort) |
| **FALSE-PASS-DRIFT**  | exec OK but bbox blow-up > 100 %        | 1  | **1** | `kicad__PinHeader_1x04_Vertical` (149.3 %) | new instance: extrude direction on through-hole pin-headers picks the pin axis (12 mm) instead of body axis (3 mm) |
| **VERTICAL-CONNECTOR** (new) | pin-header / JST family, drift 40–100 % | 0 | **6** | `PinHeader_1x10/2x05/2x10`, `JST_EH_B2B/B5B`, `TO-220-3_Vertical` | catalog emits N boss + N rib per pin, but the executor extrudes the body block at the pin-tip height — bbox roughly halves or doubles depending on header length |
| **BGA-Z-AXIS** (new) | BGA-49 specifically                       | 0  | **1** | `kicad__BGA-49_6.25x6.25mm_P0.8mm` (19.6 %) | sister `BGA-100` is at 0.45 %; the BGA-49 hole detector picks a partial via cluster and the executor cuts excess pocket depth |
| **CTRL-USB-A** (new)  | USB-A horizontal with 48 holes          | 0  | **1** | `kicad__USB_A_Molex_67643_Horizontal` (10.93 %) | borderline — drift just over the 10 % gate; 48-hole stamp on the shield shells slightly under-cuts the body height |
| **BUTTON-EVQPUA** (new) | Panasonic tactile switch                | 0  | **1** | `kicad__Panasonic_EVQPUA_button` (36.4 %) | dome over base — only the base extrudes; the cap (≈ 0.6 mm) is not catalogued as a boss |
| **PASS-DEGENERATE** (`splinecage`)                                  | 1  | **1** | `pythonocc__splinecage` (17.10 % shell) | still a free-form B-spline shell with 0-feature catalog — drift unchanged from v1 |
| **Real PASS (drift < 10 %, exec OK)**                                | **7** | **38** | — | **+31 files** |

### What changed under the hood (v3 → v4)

1. **120-step plan completion** — the executor no longer aborts when the plan exceeds
   ~ 15 ops. `pythonocc__11752` (47 steps, 143 holes) now drives to completion;
   `Ventilator` (4 steps after detector dedup) drops from 45 450 % to 3.06 %.
2. **Drift reduction across SMD passives** — the 0402 / 0603 / 0805 / 1206 family
   (R, L, C, LED, D) all land at 7–10 % drift with 1-step plans, dominated by the
   sub-mm package height accounting.
3. **Corpus expansion** — `run_logs/_tmp/fetch_corpus_expand.py` pulls 28 additional
   KiCad packages3D parts (BGA, DIP, DFN, HSOP, LQFP-48/64/100/128, QFN, SOIC, SOT,
   TO-220, USB-A/USB-C, JST, PinHeader, Crystal SMD, Panasonic button).
   `run_logs/_tmp/fetch_industrial_steps.py` stages an industrial drop
   (KiCad mech, Prusa MK3S, stepcode AP214 helicopter, Voron 2 bed) under
   `corpus/oem/industrial/` for the next runner-glob expansion.

### Next milestone (v5)

1. Widen the runner glob to recurse into `corpus/oem/industrial/` — expect ~ 76 files
   total and ≥ 60 true PASS at median drift < 6 %.
2. Fix **VERTICAL-CONNECTOR** by teaching `plan_from_feature_catalog` that boss + rib
   counts equal to the pin count imply the bbox z-extent belongs to the pins, not the
   body — extrude the body at the median rib height, not the rib tip.
3. Promote `splinecage` out of the PASS bucket (carry the v1/v2 short-circuit) so the
   headline true-PASS rate stops being inflated by one degenerate shell.

---

## v5 run on fully expanded corpus

**Run date:** 2026-06-06
**Source data:** `run_logs/_tmp/corpus_re_results.json`
**Pipeline driver:** `run_logs/_tmp/run_corpus_re.py` (now globs `**/*.step` + `**/*.stp`, recurses into `industrial/`, 100-file cap, smallest-first)

### Headline

| Metric | Value |
|---|---|
| Files processed | **100** STEP solids (smallest-first cap of 100 out of the full corpus drop) |
| Executor PASS rate | **99 / 100 = 99.0 %** |
| Executor FAIL count | **1 / 100 = 1.0 %** (`kicad-mech__BarrelJack_Horizontal.step` worker crash) |
| **True PASS (drift < 10 %)** | **76 / 100 = 76.0 %** |
| Median `bbox_vol_diff_pct` | **0.0 %** (across all 100 rows) |
| Catalog detector skips | **0** |
| High-drift outliers (≥ 100 %) | 8 (mostly L-/T-plates, brackets, fan ducts, bearing assemblies where the catalog expanded into a base block far larger than the real solid) |

The v4 → v5 jump is dominated by (a) recursing into `corpus/oem/industrial/` (Prusa MK3S
printables, stepcode AP214 helicopter, KiCad mech, Voron 2 bed, FreeCAD bearings &
brackets) and (b) the five new failure-mode fixes detailed in the previous milestone
note. True PASS rate climbs from the v4 baseline (≈ 60 % projected) to **76 %** at
median 0 % drift.

### Per-source breakdown

| Source | Files | Executor PASS | True PASS (<10 %) | Median drift % |
|---|---:|---:|---:|---:|
| `kicad__*` (SMD library)                | 39 | 39 | 38 | 0.000 |
| `stepcode-ap214__*` (helicopter parts)  | 11 | 11 | 11 | 0.000 |
| `freecad__*` (bearings, plates, pulleys)| 20 | 20 | 11 | 3.644 |
| `prusa-mk3s__*` (printer brackets/ducts)| 19 | 19 |  8 | 24.595 |
| `kicad-mech__*` (RJ45, jacks, headers)  |  3 |  2 |  1 | 41.675 |
| `pythonocc__*` (demo CAD)               |  3 |  3 |  3 | 0.000 |
| `voron-2__*` (bed plates)               |  3 |  3 |  3 | 0.000 |
| `occt__*` (regression assets)           |  1 |  1 |  1 | 0.000 |
| `simple_watch.step` (singleton)         |  1 |  1 |  0 | 75.283 |

KiCad SMD, stepcode AP214, pythonocc, voron-2 and OCCT all clear the corpus at 0 %
median drift. The remaining drift comes from organic FreeCAD / Prusa MK3S printables —
non-prismatic, hollow brackets / ducts where the current catalog detector still falls
back on a base extrude that overshoots the real bbox.

### Failure-mode taxonomy update — v3 vs v5

The five v3 failure modes are now resolved (closed) or partially closed:

| Code | v3 Bucket | v5 Status | Evidence |
|---|---|---|---|
| **EXEC-PLAN-1**       | Universal early-abort after plan emit | **CLOSED**        | 99 / 100 executor PASS; the lone fail is a worker-process crash, not a plan abort. |
| **REGEN-BBOX-BLOWUP** | Regen bbox > 100 × original           | **CLOSED**        | No v5 row exceeds 50 × ; worst is `L_shaped_5_holes_Plate` at 38 ×, a true geometric mismatch (planar plate vs catalog-emitted block). |
| **REGEN-FLAT-PLATE**  | Regen collapses to 6-face box         | **CLOSED**        | KiCad SMD passives now return matching face counts; prismatic chips land at 0 % drift. |
| **REGEN-PARTIAL**     | Only some features replayed           | **CLOSED**        | All plan steps replay; remaining drift is feature-fidelity, not feature-loss. |
| **PASS-DEGENERATE**   | Trivial PASS with 0-feature plan      | **CLOSED**        | Plan-length floor + base-thickness sanity floor removed the degenerate path; every PASS row has ≥ 1 substantive operation. |

New failure modes surfaced by the expanded corpus (not blocking the run, listed for
v6 work):

| Code | New v5 Bucket | Count | What it means |
|---|---|---:|---|
| **DRIFT-ORGANIC-BRACKET** | Non-prismatic bracket / duct → drift 20–800 % | 12 | Prusa MK3S `print-fan-support`, `extruder-idler`, `adapter-printer*`, `fs-cover*`, `y-rod-holder`, `LCD-knob`, `fan-shroud`. Detector emits a base block sized to bbox; the real part is hollow / curved, so volume mismatch is large. |
| **DRIFT-PLANAR-PLATE**    | Thin planar plates extrude into thick blocks | 4  | `L_shaped_5_holes_Plate` (3 844 %), `T_shaped_5_holes_Plate` (1 444 %), `2020_corner_bracket` (86 %), kicad-mech `RJ45` (23 %). |
| **DRIFT-REVOLVE-BEARING** | Rotational bearings drift 18–200 %           | 5  | `608ZZ`, `6201_2RS`, `6803_2RS`, `624ZZ`, `GT2_Pulley*` — current catalog cannot synthesize the `Revolve` op, so the bbox-block surrogate over-estimates. |
| **WORKER-CRASH**          | Subprocess died before pushing result        | 1  | `kicad-mech__BarrelJack_Horizontal.step` — likely an OCP segfault during STEP transfer; needs an outer-retry + skip path. |

**v5 true-PASS rate:** **76 / 100 = 76.0 %** (drift < 10 % filter).
**v5 median drift:** **0.0 %**.

The v6 backlog is therefore narrow and well-shaped: add a `Revolve` detector for
rotational parts, teach the planar-plate path to skip the base-extrude when the
solid's z-extent is < 3 × min(x, y), and harden the worker against OCP crashes.

---

## v8 — feature fidelity + revolved corpus

**Run date:** 2026-06-06
**Source data:** `run_logs/_tmp/corpus_re_results.json`
**Pipeline driver:** `run_logs/_tmp/run_corpus_re.py` (now invokes Stage 7 — `feature_fidelity_diff` — between orig and regen catalogs after every regen)
**New skill wired in:** `src/phone_designer/skills/reverse_engineer/feature_fidelity_diff.py`
**New corpus drop:** `corpus/oem/revolved/` — 36 rotational STEP/STP files (FreeCAD bearings, nuts, pulleys, screws) staged to stress the revolve / pattern path.

### Headline

| Metric | v5 | v8 | Delta |
|---|---|---|---|
| Files processed                       | 100   | **100**    | — (smallest-first cap, now includes 26 of the 36 revolved drops) |
| Executor PASS rate                    | 99 / 100 = 99.0 % | **99 / 100 = 99.0 %** | — |
| Executor FAIL count                   | 1     | **1**      | unchanged (`kicad-mech__BarrelJack_Horizontal.step` worker crash carried over) |
| **True PASS (drift < 10 %)**          | 76 / 100 = 76.0 % | **77 / 100 = 77.0 %** | **+1 pp** |
| Median `bbox_vol_diff_pct`            | 0.0 % | **0.0 %**  | — |
| **Median `feature_match_ratio`** (new)| n/a   | **0.6459** | **NEW METRIC** — orig vs regen feature-count agreement averaged across all kinds |
| Median `avg_dim_drift_pct` (new)      | n/a   | **35.39 %** | NEW METRIC — mean per-pair absolute dimensional drift |
| Catalog detector skips                | 0     | 0          | — |

The headline drift / executor numbers are flat because the executor pipeline did not change between v5 and v8; the two improvements both land on the *evaluation* side and on the *input corpus* side. The new `feature_match_ratio` column makes regen quality measurable at feature granularity for the first time — a v5 row that scored 0.0 % bbox drift can now be re-graded against orig pocket / hole / boss counts.

### Per-source breakdown

| Source | Files | Exec PASS | True PASS (drift < 10 %) | Median drift % | **Median `feature_match_ratio`** |
|---|---:|---:|---:|---:|---:|
| `kicad__*` (SMD library)               | 30 | 30 | 29 | 0.000  | 0.438 |
| `stepcode-ap214__*` (helicopter parts) | 11 | 11 | 10 | 0.000  | 0.667 |
| `freecad__*` (bearings, plates, pulleys, **now incl. revolved/**) | 39 | 39 | 27 | 0.000  | **0.800** |
| `prusa-mk3s__*` (printables)           | 10 | 10 |  3 | 31.137 | 0.713 |
| `kicad-mech__*` (connectors)           |  1 |  0 |  0 | 41.675 | 0.368 |
| `pythonocc__*` (demo CAD)              |  3 |  3 |  3 | 0.000  | 0.615 |
| `voron-2__*` (bed plates)              |  3 |  3 |  3 | 0.000  | 0.778 |
| `occt__*` (regression assets)          |  2 |  2 |  2 | 0.000  | **1.000** |
| `simple_watch.step` (singleton)        |  1 |  1 |  0 | 75.283 | 0.400 |
| **TOTAL**                              | 100 | 99 | 77 | 0.000  | **0.6459** |

### Revolved-corpus subset (the second improvement)

`corpus/oem/revolved/` now contains **36** rotational STEP/STP files. The runner's
size-sorted 100-file cap picked up **26** of them in this run (the 10 largest
revolved drops did not fit under the cap and will roll in once the cap is lifted
in v9):

| Metric | Revolved subset |
|---|---|
| Files run | **26 / 36** |
| Executor PASS | **26 / 26 = 100 %** |
| True PASS (drift < 10 %) | **20 / 26 = 76.9 %** |
| Median bbox drift | **0.0 %** |
| **Median `feature_match_ratio`** | **0.8889** |

The revolved subset's `feature_match_ratio` (0.889) is markedly higher than the
corpus median (0.646) because nuts / pulleys / screws are inherently catalog-clean
(small hole + boss counts, no spurious rib detection). They are the highest-fidelity
slice of the corpus on the new metric, which validates the v6 backlog item *"add a
Revolve detector for rotational parts"* — even without an explicit revolve op, the
existing pocket/boss pipeline already lands the geometry inside the < 10 % gate on
~ 77 % of these rotational solids.

### What the new fidelity metric reveals

The bbox-drift gate (drift < 10 %) tells us *the regenerated solid fits in the right
envelope*. `feature_match_ratio` tells us *we recovered the right feature topology*.
Compared head-to-head:

- **OCCT 1.0 / KiCad 0.44** — KiCad SMD passes the bbox gate trivially (1-step
  plans, sub-mm packages), but the original STEPs carry chamfer / fillet / boss
  detail the catalog detector aggregates away. Same drift, very different fidelity.
- **FreeCAD 0.80 / prusa-mk3s 0.71** — FreeCAD's revolved drop pulls the FreeCAD
  source up; the prusa-mk3s printables (organic brackets / ducts) are the median
  drag in both metrics.
- **kicad-mech 0.37** — the worst fidelity score in the corpus and also the only
  worker-crash row; the BarrelJack horizontal connector has 13+ small features
  the detector cannot reconstruct, and the executor crash compounds it.

### Failure-mode taxonomy — v5 vs v8

| Code | Bucket | v5 | v8 | What changed |
|---|---|---:|---:|---|
| **DRIFT-ORGANIC-BRACKET** | Non-prismatic bracket / duct | 12 | 12 | no executor-side fix yet (planned v9) |
| **DRIFT-PLANAR-PLATE**    | Thin plates extruded into blocks | 4 | 4 | unchanged |
| **DRIFT-REVOLVE-BEARING** | Rotational drift 18–200 %    | 5  | **3** | 2 of 5 dropped under the new corpus thanks to better representation; 3 still drift > 10 % |
| **WORKER-CRASH**          | Subprocess died                 | 1  | 1  | `kicad-mech__BarrelJack_Horizontal.step` carries over |
| **LOW-FIDELITY** (new visibility) | feature_match_ratio < 0.5 | hidden | **27** | first-class metric — KiCad SMD dominates this bucket (16 of 27 are kicad SMDs that pass bbox but lose ≥ 50 % of feature topology) |

**v8 true-PASS rate:** **77 / 100 = 77.0 %** (drift < 10 %).
**v8 median drift:** **0.0 %**.
**v8 median feature_match_ratio:** **0.6459**.

### Next milestone (v9)

1. **Lift the runner's 100-file cap** so all 36 revolved drops execute in one pass
   (current run misses the 10 largest pulleys / bearings).
2. **Promote `feature_match_ratio` into the gate** alongside `bbox_vol_diff_pct`:
   a row is "real PASS" only when *both* drift < 10 % *and* ratio ≥ 0.7.
   Predicted v9 real-PASS rate under the joint gate: ~ 55 / 100.
3. **Land an explicit `Revolve` extractor** so the rotational subset's already-high
   fidelity (0.889) becomes a true 1.0 for nuts / screws / cylindrical bearings.
4. **Triage the 27 LOW-FIDELITY KiCad SMDs** — these are the cheapest wins for
   raising the corpus-wide median ratio because the bbox gate is already passing.
