# Corpus Complexity Audit v6 — Phantom multi-body fastener filter

Date: 2026-06-10
Commit baseline: d121f14 (`Pass 23 — entry_origin separation +
phantom multi-body fastener filter`)

## What changed since v5

**Pass 23 — three coordinated fixes around hole detection:**

(a) **`_body_entry_along_axis` helper** (`classify_holes.py`). Given a
cylinder's axis_origin + axis_dir + depth, compute the parametric
line-vs-body-bbox intersection. Returns the body-relative entry point
+ entry-depth + a boolean `intersects` flag that's False when the
cylinder segment [0, depth] has zero overlap with the body bbox.

(b) **Phantom multi-body fastener filter** in `classify_holes._apply`.
Assembly STEPs (as1_pe_203) ship cylinder faces that belong to OTHER
solids in the compound — fastener pins, mounting brackets — whose
axis_origin sits outside the inspected body's bbox AND whose direction
moves further away. Seven such cylinders previously polluted
as1_pe_203's hole catalog. The filter drops them when `intersects` is
False. A ±2 mm bbox margin tolerance survives sub-mm preserve_brep
round-trip drift on border-of-body cylinders (Crystal_SMD_3225-4Pin
holds 1.0 PERFECT).

(c) **Planner uses `entry_origin`/`entry_depth_mm`** in box mode
(`_hole_step`). Cuts land at the body face, not at a parametric
endpoint that may sit outside the body. `feature_fidelity_diff._xyz_of`
prefers `entry_origin` over `axis_origin` so the spatial pair gate
compares body-relative points. preserve_brep self-match path keeps
using `axis_origin` (unchanged).

## Final state (commit d121f14)

True box-mode reconstruction:

| File           | box mode (TRUE)  | preserve_brep | what's matching |
|----------------|------------------|---------------|-----------------|
| linkrods       | 0.833            | 1.0           | holes 2/2 + pockets 2/3 |
| Ventilator     | 0.464            | 0.381         | holes 6/14 |
| as1-oc-214     | 0.823            | 1.0           | pockets 8/11 |
| as1_pe_203     | **0.690**        | 1.0           | **holes a=7→4 after phantom drop**, pockets 14/18 |

Net session progress (passes 11–23):

| File         | Pass 11 start | Pass 23 (now) |
|--------------|---------------|---------------|
| linkrods     | 0.40          | **0.833**     |
| Ventilator   | 0.19          | **0.464**     |
| as1-oc-214   | 0.35          | **0.823**     |
| as1_pe_203   | 0.17          | **0.690**     |

Root corpus (preserve_brep regression mode):
- 35/55 PERFECT (unchanged)
- 53/55 ≥ 0.5 (unchanged)
- 0 errors

Quiet preserve_brep IMPRovements (no regression):
- SOIC-8 0.846 → 0.917
- SOT-223 0.846 → 0.917
- Trimmer_Bourns_3296 0.444 → 0.471
- USB_A_Molex 0.867 → 0.915
- USB_C_Receptacle 0.790 → 0.804
- pythonocc__11752 0.862 → 0.870

All driven by the phantom filter dropping cylinder faces from
neighbour solids that previously inflated the union denominator
without ever pairing.

## Honest remaining gaps

- **as1_pe_203 box still 0.69, holes 0 matched**: 4 surviving holes
  (axis_origin INSIDE body) still don't pair with 4 regen holes —
  the 200 mm depth clamp truncates 1016 mm cuts, so regen detects
  the cut endpoint at +200 mm rather than the orig's full-depth
  endpoint. Removing the clamp regressed pocket pairing (pass 21
  geometric overlap). Solving needs CSG-aware emission ordering or
  per-feature confidence-clamp.
- **Ventilator preserve_brep 0.38**: 8 of 14 catalog holes are tiny
  (Ø 1 mm) cylinders the regen detector under-classifies. Detector
  improvement, not planner.
- **Volume drift box mode high**: pocket axis_origin uses floor
  centroid (preserve_brep needs that for self-match); regen cut's
  natural centroid is entry. Unifying regresses preserve_brep
  (pass 18 lesson).
- **Trimmer_Bourns_3296 stuck at 0.47**: long-tail SMD case worth
  investigating but low impact.

## Convention summary

| Detector       | axis_origin convention | entry_origin convention |
|----------------|------------------------|-------------------------|
| classify_holes | **Entry centroid** (pass 17 standardizer) | **Body-bbox intersection** (pass 23) — separate field |
| classify_pockets | **Floor centroid** (unchanged) | n/a |

The asymmetry stays deliberate. Holes' new `entry_origin` field lets
the planner emit at the body face while diff still compares
body-relative points; `axis_origin` remains the immutable identity
that preserve_brep round-trips.

## Lessons (cumulative)

1. Convention mismatches are silent killers.
2. Universal "standardise everything" doesn't work (pass 18).
3. Per-mode wiring matters.
4. **(NEW pass 23)** Separate the field, don't mutate the identity.
   Pass 22 tried to overwrite `axis_origin` to fix box-mode emission
   — regressed preserve_brep. Pass 23 added `entry_origin` as a
   sibling field, kept `axis_origin` immutable, and preserve_brep
   self-match stayed intact while box mode and 6 other files
   improved.
5. **(NEW pass 23)** Multi-body assembly STEPs need detector-level
   filtering. The same cylinder face is "a feature" of one body and
   "noise" of another; the body bbox is the cheapest discriminator.
