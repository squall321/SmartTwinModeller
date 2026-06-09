# Corpus Complexity Audit v5 — Pattern + axis_origin convention fix

Date: 2026-06-10
Commit baseline: b0dbaad (`Pass 18 REVERTED — classify_pockets entry
standardiser regressed preserve_brep`)

## What changed since v4

Two more root-cause fixes ship in passes 16–17:

**Pass 16 — pattern emission is preserve_brep-only.** In box mode the
``circular_pattern`` / ``linear_pattern`` skills use ``face_named``
selectors that resolve to the placeholder slab's outer face. Ventilator's
13-hole ring at world z=6.93 ended up at the box top face z=18 — off
by 11 mm. Skip pattern emission in box mode; the per-hole loop then
emits each ring hole via the generic ``hole`` skill at world coords
(pass 12 fix path).

**Pass 17 — bbox-face proximity entry standardiser.** Pass 7c's
BRepClass3d probe only worked when exactly one cylinder endpoint sat
inside SOLID material. For through-holes whose BOTH endpoints are in
the void (Ventilator cylinders pierce the body bottom), the probe fell
through and axis_origin kept the detector's arbitrary convention —
orig stored the closed cap (inside the body), regen stored the open
end (on the body surface), so the SAME physical hole got represented
at endpoints ~9 mm apart and the fidelity diff failed to pair them.

Stage-2 fallback: when both BRepClass3d samples are out-of-solid, pick
the endpoint whose distance to the nearest body bbox face is SMALLEST.
That endpoint sits on a body outer surface, which is by definition the
drill entry. Both orig and regen catalogues now agree on the
convention.

**Pass 18 — pocket entry standardiser. REVERTED.** Tried importing the
same standardiser into classify_pockets. Mixed result: linkrods box
+0.08 and as1_pe_203 pockets matched 14→15, but as1_pe_203 preserve_brep
regressed from 1.0 → 0.97. The pocket's natural axis_origin (floor
centroid) is what the preserve_brep round-trip identity needs.
Different conventions for hole vs pocket — defensible because the
planner emits cylinder cuts FROM the entry, while pockets get
extrude_pocket whose sketch references the floor centroid. Reverted
the pocket import; pass 17 hole standardiser stays.

## Final state (commit b0dbaad)

True box-mode reconstruction (placeholder slab → cuts):

| File           | box mode (TRUE)  | preserve_brep | what's matching |
|----------------|------------------|---------------|-----------------|
| linkrods       | **0.833**        | 1.0           | holes 2/2 + pockets 2/3 |
| Ventilator     | **0.464**        | 0.38          | **holes 6/14 (was 0)** |
| as1-oc-214     | **0.823**        | 1.0           | pockets 8/11 |
| as1_pe_203     | **0.625**        | 1.0           | pockets 14/18 |

Net session progress (passes 11–17 inline):

| File         | Started box mode at | Now box mode |
|--------------|---------------------|--------------|
| linkrods     | 0.40                | **0.833**    |
| Ventilator   | 0.19                | **0.464**    |
| as1-oc-214   | 0.35                | **0.823**    |
| as1_pe_203   | 0.17                | **0.625**    |

Full root corpus (preserve_brep regression mode): 53/55 match ≥ 0.5,
35/55 PERFECT, 0 catalog-skips, 0 errors. Stable since pass 8.

## Honest remaining gaps

- **as1_pe_203 holes 0/7**: 200 mm depth clamp truncates 1016 mm
  counterbores. Pass 13/14 tried removing — regressed preserve_brep.
  Future work needs depth-emission ordering (holes before pockets) or
  per-feature confidence-based clamp.
- **Volume drift box mode still high**: pocket axis_origin uses floor
  centroid (preserve_brep needs that for self-match), but the regen
  cut's "natural" centroid is the entry. Pass 18 tried unifying —
  regressed preserve_brep. The trade-off is real.
- **8 Ventilator holes (out of 14) still unmatched**: small d=1 mm
  cylinders that the regen detector under-classifies. Detector-side
  improvement, not planner-side.
- **Trimmer_Bourns_3296 stuck at 0.44**: tiny regression vs pass 6
  baseline (0.53). Long-tail SMD case worth investigating but low
  impact.

## Convention summary (for future maintainers)

| Detector       | axis_origin convention (after pass 17/18) |
|----------------|-------------------------------------------|
| classify_holes | **Entry centroid** (BRepClass3d probe + bbox-face proximity stage-2 standardiser) |
| classify_pockets | **Floor centroid** (natural OCCT cylinder/floor anchor — unchanged) |

The asymmetry is deliberate: holes are drilled cylinders that downstream
RE wants to address FROM the entry; pockets are bounded cavities whose
floor is the unambiguous reference point both the orig STEP and the
regen body share.

## Lessons

1. **Convention mismatches are silent killers.** Same physical hole,
   two endpoints, two detector conventions → pairing fails with no
   error, just a low match score.
2. **Universal "standardise everything" doesn't work.** Pass 18 tried
   to apply the hole convention to pockets; preserve_brep regressed.
   Different detector outputs have different downstream needs.
3. **Per-mode wiring matters.** Pattern skip in box mode + face_named
   removal in box mode + entry-Z override in preserve_brep — all are
   correct ONLY for one mode and would regress the other if applied
   globally.
