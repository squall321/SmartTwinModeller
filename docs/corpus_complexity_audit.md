# Corpus Complexity Audit — HONEST verdict

Date: 2026-06-07

User raised the suspicion that the corpus is too simple to be a real test of
the reverse-engineering pipeline. This audit confirms the suspicion.

## Headline

| Category | Threshold | Count | PASS | True PASS (drift<10%) | Median match_ratio |
|---|---|---:|---:|---:|---:|
| TRIVIAL | face<10 AND feat≤2 | 5 (5%) | 5 | **0** | 0.889 |
| SIMPLE  | face<50 AND feat≤5 | 74 (74%) | 74 | **13** | 0.667 |
| MEDIUM  | face<500           | 20 (20%) | 19 | **2** | 0.500 |
| **COMPLEX** | face≥500       | **1 (1%)** | **0** | **0** | **0.586** |

**79 of 100 files are TRIVIAL or SIMPLE** — for these, "0% bbox drift" is
trivially achieved by ANY base-box of the right size. The headline 99/100
PASS rate is therefore misleading: it's dominated by 1-10mm SMD components
with ≤5 features.

## The one COMPLEX case (1018 faces): pythonocc__11752.stp

| Metric | Value |
|---|---|
| Orig faces | 1018 |
| Orig features detected | 147 (143 holes + 4 pockets) + 6 mirror planes + 67 patterns |
| Plan steps emitted | 47 |
| Executor outcome | **FAIL** (1 error) |
| bbox vol drift | **421%** |
| feature_match_ratio | 0.586 |
| Per-kind change | holes 143→69 (lost 74), pockets 4→7, bosses 0→7 invented, loft 6→1 |

The regen body **lost 51% of the holes**, **invented 7 bosses that don't
exist in the original**, and produced a body 4× the bbox volume of the
input. The 0.586 match ratio masks the directional failure — counts close
on a few kinds, but the ACTUAL topology is wrong.

## Suspect PASS — bbox-only success

3 files report `bbox_drift<5%` but `feature_match_ratio<0.5`. These pass
the headline gate but only because the regen happens to occupy a similar
bounding volume; the feature topology is largely wrong:

| File | bbox drift | match |
|---|---:|---:|
| kicad__LED_0402_1005Metric.step | 3.0% | 0.30 |
| kicad__CP_Elec_10x10.step       | 1.5% | 0.47 |
| kicad__DFN-10-1EP_2x3mm_P0.5mm.step | 2.6% | 0.32 |

## Honest verdict

- "99/100 executor PASS" hides that **79/100 are trivial enough that any
  base-box regen passes**.
- "15/100 true PASS (drift<10%)" is also dominated by simple solids.
- The **single COMPLEX case fails** (no PASS, 421% drift, 51% of holes
  lost).
- The pipeline has not been validated on parts with > 200 faces.

## What's needed

1. **More COMPLEX test cases** (multiple files with face_count > 500). The
   pythonocc/OCCT public test data has very few such models.
2. **Fix the 11752 failure** — investigate the executor's 1 error +
   the boss invention path.
3. **Feature-level fidelity gate** — replace bbox_drift<10% as the
   "true PASS" criterion with feature_match_ratio>0.8 AND no missing kind
   exceeds 25% loss.
4. Stop celebrating headline PASS rates without same-complexity controls.
