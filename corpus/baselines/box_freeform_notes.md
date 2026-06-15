# Freeform corpus lane — honest per-mechanism score bands

PILLAR FREEFORM (phase-3, 2026-06-14). Companion note to
`corpus/baselines/box_freeform.json`.

This lane is **separate** from the prismatic `box_complex` lane. It exercises the
freeform reconstruction path (Phase-1 silhouette base, Phase-2 loft recovery)
on four committed fixtures, one per mechanism, each with a closed-form analytic
volume. Run / refresh with:

```
venv/Scripts/python.exe scripts/build_freeform_fixtures.py        # (re)build STEPs
venv/Scripts/python.exe scripts/freeform_corpus_regress.py --update   # write baseline
venv/Scripts/python.exe scripts/freeform_corpus_regress.py            # compare (gate)
```

Fixtures are built by `fixtures/freeform/__init__.py`; the generated `.step`
files are gitignored (root `.gitignore` globs `*.step`), exactly like the other
corpus STEP outputs.

## What each column means

The reconstruction is box-mode with `PlanFromFeatureCatalog(base_profile_mode=
"auto")` — the **shipping** path, which keeps a non-box base ONLY when the full
regen's geometry_deviation **hausdorff is strictly better** than the box's (the
`accept_freeform_base` revert-guard). PILLAR FREEFORM phase-4 (2026-06-15, ITEM
1) removed the old `match_ratio not worse AND` co-condition: it was
fake-accuracy in reverse — reverting a geometrically-tighter base on a
feature-COUNT metric. A freeform win is a hausdorff claim, never a match_ratio
claim. Every record also stores the two forced A/B halves so the win/loss is
auditable and impossible to fake:

| column | meaning |
| --- | --- |
| `match_ratio` | FeatureFidelityDiff overall match of the **auto** regen |
| `hausdorff_mm` | geometry deviation of the **auto** regen vs the original (box frame) |
| `box_hausdorff_mm` | forced plain bbox box base (`base_profile_mode="off"`) |
| `profile_hausdorff_mm` | forced silhouette-profile base (prismatic fixtures only) |
| `auto_base_is_profile` | did the shipping auto path actually keep a non-box base? |
| `volume_delta_pct` | (regen − orig)/orig × 100 |
| `analytic_volume_mm3` | the builder's closed-form ground-truth volume |

## Honest per-mechanism bands (the Phase-4 "before")

These are the recorded baseline numbers — measured, not aspirational. They are
the **before** picture for Phase-4 B-spline work.

### silhouette_extrude — `rounded_plate` (50×30, r6, h8)
- **WIN, now KEPT (phase-4 ITEM 1).** The recovered outline base is **9.3×
  tighter** than the box base: `profile_hausdorff_mm 0.272` vs
  `box_hausdorff_mm 2.523`. The shipping `auto` path now **keeps the profile**
  (`auto_base_is_profile=true`, `hausdorff_mm 0.272 == profile_hausdorff_mm`,
  `volume_delta_pct` −13.3 → −7.2). The phase-3 revert-guard had thrown this
  away because swapping to the profile base drops `match_ratio` 0.444 → ~0.31
  (the recovered composite loop changes the detected feature inventory) and the
  old guard refused any match regression — fake-accuracy in reverse. ITEM 1
  rekeys the guard on hausdorff alone, so the geometrically-better base lands.
- **Honest caveat — match_ratio here is non-deterministic** (observed 0.2917 ↔
  0.3333 across runs): the rounded-corner section's recovered face count jitters
  with OCCT tessellation. The baseline records the conservative **floor
  (0.2917)** so the lane's `MATCH_DROP_TOL` guard does not false-trip on this
  noise. This jitter is *exactly why* match_ratio must not gate the base choice;
  `hausdorff_mm` (0.271506) is rock-stable across every run.

### sweep — `swept_channel` (L-path, 6×4 section)
- **STRONG win, kept.** `auto_base_is_profile=true`: the L-channel is prismatic
  along Z, so the silhouette base recovers the true L-footprint and reconstructs
  to `hausdorff_mm 0.077` vs `box_hausdorff_mm 25.0` (a ~320× improvement) with
  `volume_delta_pct` +2.1 %. `match_ratio 0.667`. The best-behaved mechanism in
  the lane.

### loft_section — `loft_boss` (circle → rounded-square, equal area, h12)
- **PARTIAL.** Feature match is perfect (`match_ratio 1.0`) but the box base
  over-covers the tapered loft: `hausdorff_mm 4.13`, `volume_delta_pct +27.3 %`.
  The revolved/profile A/B does not fire (not prismatic, not a clean single-axis
  solid of revolution), so the box base is kept. Phase-2 loft section recovery
  is the lever to close this band.

### revolve — `revolve_frustum` (R15→R8, h20)
- **PARTIAL.** `match_ratio 1.0` but `hausdorff_mm 6.36`, `volume_delta_pct
  −44.6 %` — the box base badly mis-volumes the cone. The topology classifier
  labels the body `revolved`, but the revolved-primitive candidate does not pass
  the A/B revert-guard (it does not beat the box on hausdorff at the catalog
  scale), so the box base is kept. This is the widest band and the clearest
  Phase-4 target (recover the meridian and revolve it).

## Summary

| fixture | mechanism | match | auto haus (mm) | box haus (mm) | profile haus (mm) | vol Δ% | auto kept non-box? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| swept_channel | sweep | 0.667 | 0.077 | 25.0 | 0.081 | +2.1 | yes |
| rounded_plate | silhouette_extrude | 0.292* | **0.272** | 2.523 | 0.272 | **−7.2** | **yes** |
| loft_boss | loft_section | 1.000 | 4.134 | 4.134 | n/a | +27.3 | no |
| revolve_frustum | revolve | 1.000 | 6.363 | 6.363 | n/a | −44.6 | no |

`*` rounded_plate match_ratio is non-deterministic (0.2917 ↔ 0.3333); the
conservative floor is recorded. The **auto haus** column is what matters and is
deterministic.

Bands, lowest-deviation to highest: **sweep** (silhouette-prismatic, kept) →
**silhouette** (profile 9× tighter, NOW KEPT after ITEM 1) →
**loft / revolve** (partial; box base kept, the remaining Phase-4 work items).

No per-file tuning, no fake accuracy: the box-vs-profile A/B halves are both
recorded; the revert-guard now keys on hausdorff alone (ITEM 1), so a
geometrically-tighter base is kept and a worse one is still reverted to the box
(prismatic corpus files unaffected — their box bbox already is their tight
silhouette). The `hausdorff_mm` deviation metric reproduces exactly on every
run; only the secondary feature-count `match_ratio` carries tessellation noise,
which is why it no longer gates the base choice.
