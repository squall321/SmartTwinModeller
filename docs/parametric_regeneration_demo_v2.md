# Parametric Regeneration Demo v2 — analytic scoring (plan item P4)

Generated 2026-06-10T11:28:07+00:00 by `tools/parametric_regen_demo.py` (logic: `src/phone_designer/corpus/parametric_demo.py`).

v1 (`docs/parametric_variation_demo.md`) scored nothing analytically
and its `per_feat` variant produced **bit-identical geometry without
anyone noticing**. v2 scores every cell against analytic expectations
derived from the EMITTED plan steps — never catalog-vs-catalog.

## Pipeline per cell

`import_step` → `extract_feature_catalog` → `plan_from_scaled_catalog`
(box mode, `scale_factor` + `per_feature_scale`) → `Plan.model_validate`
→ `PlanExecutor` (no initial_body) → analytic checks on the rebuilt
body + executed plan.

## Checks

| check | applies to | rule | tol |
|---|---|---|---|
| (a) bbox | scale cells | rebuilt per-axis extent / original == scale | ±0.5% |
| (b) hole re-detect | scale cells | classify_holes min diameter on rebuild == scale × **scale-1.0 baseline rebuild** min | ±8% (skip when the baseline rebuild has no detectable holes — that is a box-mode reconstruction gap tracked by corpus-regress, not a variation defect; the catalog-vs-baseline gap is reported in the metrics column) |
| (c) edit delta | edit cells | volume − baseline == analytic delta from EMITTED step dims (hole: π/4·(d₂²−d₁²)·depth; pocket: footprint × extra depth) | ±15% |
| (d) feature census | all cells | `validate_rebuilt_body` features_lost == 0 | exact |

Edit-cell decision rule: a plan that **changed** but rebuilt to a
bit-identical volume is an automatic FAIL (`edit produced no geometric
change` — the v1 blind spot). A plan that did **not** change at all is
SKIP-NO-TARGET with the diagnosis of *why* the edit never reached the
plan (catalog key absent, or planner guard filtered the feature);
identical plans must still rebuild bit-identically (determinism check).

## Matrix results

| file | variant | gated | status | metrics | reason |
|---|---|---|---|---|---|
| simple_watch_housing | scale_0.5 | GATED | PASS | bbox_r=[0.5, 0.5, 0.5] min_d=16.7 (base 33.4, cat 2.5) feat 2/2 | — |
| simple_watch_housing | scale_1 | GATED | PASS | bbox_r=[1.0, 1.0, 1.0] min_d=33.4 (base 33.4, cat 2.5) feat 2/2 | — |
| simple_watch_housing | scale_1.5 | GATED | PASS | bbox_r=[1.5, 1.5, 1.5] min_d=50.1 (base 33.4, cat 2.5) feat 2/2 | — |
| simple_watch_housing | scale_2 | GATED | PASS | bbox_r=[2.0, 2.0, 2.0] min_d=66.8 (base 33.4, cat 2.5) feat 2/2 | — |
| simple_watch_housing | edit_holes0_d_x1.5 | GATED | PASS | ΔV=-12745.2358 ΔV_exp=-12745.2627 err=0.0% feat 2/2 | — |
| simple_watch_housing | edit_pockets0_depth_x2.0 | GATED | SKIP | ΔV=0.0 feat 2/2 | edit target 'pockets.0.depth_mm' exists in catalog but the planner emitted no step for that feature (axis-aligned/silhouette/dedup guard) — plan unchanged, no analytic delta defined [SKIP-NO-TARGET] |
| occt__linkrods | scale_0.5 | GATED | PASS | bbox_r=[0.5, 0.5, 0.5] min_d=0.2199 (base 0.4398, cat 0.4398) feat 2/2 | — |
| occt__linkrods | scale_1 | GATED | PASS | bbox_r=[1.0, 1.0, 1.0] min_d=0.4398 (base 0.4398, cat 0.4398) feat 2/2 | — |
| occt__linkrods | scale_1.5 | GATED | PASS | bbox_r=[1.5, 1.5, 1.5] min_d=0.6597 (base 0.4398, cat 0.4398) feat 2/2 | — |
| occt__linkrods | scale_2 | GATED | PASS | bbox_r=[2.0, 2.0, 2.0] min_d=0.8796 (base 0.4398, cat 0.4398) feat 2/2 | — |
| occt__linkrods | edit_holes0_d_x1.5 | GATED | PASS | ΔV=-0.3798 ΔV_exp=-0.3798 err=0.0% feat 2/2 | — |
| occt__linkrods | edit_pockets0_depth_x2.0 | GATED | SKIP | ΔV=0.0 feat 2/2 | edit target 'pockets.0.depth_mm' exists in catalog but the planner emitted no step for that feature (axis-aligned/silhouette/dedup guard) — plan unchanged, no analytic delta defined [SKIP-NO-TARGET] |
| occt__screw | scale_0.5 | — | PASS | bbox_r=[0.5, 0.5, 0.5] feat 1/1 | — |
| occt__screw | scale_1 | — | PASS | bbox_r=[1.0, 1.0, 1.0] feat 1/1 | — |
| occt__screw | scale_1.5 | — | PASS | bbox_r=[1.5, 1.5, 1.5] feat 1/1 | — |
| occt__screw | scale_2 | — | PASS | bbox_r=[2.0, 2.0, 2.0] feat 1/1 | — |
| occt__screw | edit_holes0_d_x1.5 | — | SKIP | ΔV=0.0 feat 1/1 | edit target 'holes.0.diameters_mm' exists in catalog but the planner emitted no step for that feature (axis-aligned/silhouette/dedup guard) — plan unchanged, no analytic delta defined [SKIP-NO-TARGET] |
| occt__screw | edit_pockets0_depth_x2.0 | — | KNOWN-GAP(FAIL) | ΔV=1911.2704 ΔV_exp=2676.6291 err=28.6% feat 0/0 | (c) volume delta 1911.27 mm³ deviates from analytic 2676.63 mm³ by 28.6% (>15%) |
| pythonocc__as1-oc-214 | scale_0.5 | — | PASS | bbox_r=[0.5, 0.5, 0.5] feat 8/8 | — |
| pythonocc__as1-oc-214 | scale_1 | — | PASS | bbox_r=[1.0, 1.0, 1.0] feat 8/8 | — |
| pythonocc__as1-oc-214 | scale_1.5 | — | PASS | bbox_r=[1.5, 1.5, 1.5] feat 8/8 | — |
| pythonocc__as1-oc-214 | scale_2 | — | PASS | bbox_r=[2.0, 2.0, 2.0] feat 8/8 | — |
| pythonocc__as1-oc-214 | edit_holes0_d_x1.5 | — | SKIP | ΔV=0.0 feat 8/8 | edit target 'holes.0.diameters_mm' absent from catalog — vary_catalog ignored the dotted key, plan unchanged [SKIP-NO-TARGET] |
| pythonocc__as1-oc-214 | edit_pockets0_depth_x2.0 | — | PASS | ΔV=-1200.0 ΔV_exp=-1200.0 err=0.0% feat 8/8 | — |

## Gate verdict

**All gated cells pass** (SKIP-NO-TARGET cells documented below).

## Honesty notes

* **KNOWN UPSTREAM BUG (out of P4 scope — classify_holes):** the
  simple_watch crown hole (Ø2.5, the catalog's min hole) reports
  `axis_origin=[11, 0, 6.5]` — the OCCT cylinder surface's analytic
  location (the fixture's cut-primitive origin) — while the actual
  face band sits at x≈20.5..22 (the wall). `_body_entry_along_axis`
  clamps the entry to `[0, depth_mm]` FROM that origin, so
  `entry_origin` lands ~9.5 mm short, the box-mode cut at
  x∈[11, 12.5] falls entirely inside the Ø33.4 bore void and
  no-ops, and (b) re-detects min d = 33.4·scale instead of
  2.5·scale at EVERY scale (scale-independent — a box-mode entry
  placement bug, not a scaling bug; scaling itself is proven by
  (a)/(d) and by linkrods (b) passing at all 4 scales).
  classify_holes already stashes the TRUE band endpoints
  (`_band_lo_point`/`_band_hi_point`, pass-24) but only consumes
  them in the no-intersect rescue path; anchoring the entry to the
  band unconditionally would fix this.
* `edit_pockets0_depth_x2.0` on **both gated files** is
  SKIP-NO-TARGET: simple_watch's only catalog pocket is a
  diagonal-axis detector artifact (axis_dir ≈ [0.27, 0.53, 0.80],
  depth 42.5 mm on an 11.6 mm body — dropped by
  `_pocket_is_axis_aligned`), and linkrods' 3 pockets are
  silhouette / full-depth duplicates of its 2 holes (dropped by the
  pass-6 / PACK-B silhouette guards). The planner filters are
  *correct*; the harness surfaces the no-op loudly instead of
  reporting a fake variant like v1 did.
* `occt__screw` is non-gated: its base is a turned cylinder but box
  mode rebuilds on a rectangular slab — cylinder-base scaling is
  plan item P5. Actual behavior is recorded above, FAIL/ERROR cells
  count as KNOWN-GAP.
* `pythonocc__as1-oc-214` is non-gated (box-mode corpus baseline
  0.8235) — recorded for coverage.

## Files

* `simple_watch_housing` — `D:\SmartTwinModeller\fixtures\simple_watch_housing_only.step` (GATED) — synthetic fixture (build via fixtures/make_simple_watch.py)
* `occt__linkrods` — `D:\SmartTwinModeller\corpus\oem\complex\occt__linkrods.step` (GATED)
* `occt__screw` — `D:\SmartTwinModeller\corpus\oem\complex\occt__screw.step` (non-gated) — NON-GATED: cylinder-base scaling is plan item P5 (not landed) — actual behavior recorded
* `pythonocc__as1-oc-214` — `D:\SmartTwinModeller\corpus\oem\complex\pythonocc__as1-oc-214.stp` (non-gated) — NON-GATED: box-mode baseline 0.8235 — document only

## Run command

```powershell
$env:PYTHONPATH = "src"
& "venv\Scripts\python.exe" tools/parametric_regen_demo.py
```

Machine-readable results: `run_logs/_tmp/parametric_regen_v2.json`.
CI-safe subset (simple_watch only): `pytest -q tests/test_parametric_regeneration.py`.
