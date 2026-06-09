# Corpus Complexity Audit v4 — TRUE box-mode reconstruction

Date: 2026-06-10
Commit baseline: 1d58d27 (`Pass 15 — pocket-as-hole branch: entry-Z
override preserve_brep-only`)

## What v3 did not say honestly

The v3 audit reported `as1_pe_203 match=1.000` and `as1-oc-214
match=1.000` as proof that the pipeline could reconstruct industrial
parts. Those numbers were technically true but came from
``base_step_kind="preserve_brep"`` — a mode where the executor starts
from the ORIGINAL body, every cut tends to no-op against the
already-present feature, and the final regen body is bit-identical to
the orig. ``regen_catalog == orig_catalog`` is trivially satisfied and
the match score reports 1.0. It measured "does the catalog detector
self-consistently identify the same features twice on the same body" —
not "can we BUILD this body from a placeholder slab".

The honest test is ``base_step_kind="box"``: the executor starts from a
six-face axis-aligned bbox and ALL the feature topology has to be
recovered by ``extrude_pocket``/``hole``/etc. cuts. Run that mode and
the v3 PERFECT scores drop dramatically:

| File           | preserve_brep (v3) | box mode (v3) | gap  |
|----------------|-------------------:|--------------:|-----:|
| linkrods       | 1.000              | 0.40          | 0.60 |
| Ventilator     | 0.36               | 0.19          | 0.17 |
| as1-oc-214     | 1.000              | 0.35          | 0.65 |
| as1_pe_203     | 1.000              | 0.17–0.19     | 0.81 |
| 11752 (1018 f) | 0.86               | 0.41          | 0.45 |

The pass 6/7/8 docs implied 1.0 was real reconstruction. It was not.

## What changed in passes 11-15

Five surgical fixes ship real box-mode reconstruction:

**Pass 11 — extrude_pocket_world skill + planner wiring.** A new
``modify_pocket/extrude_pocket_world`` skill places a rectangular pocket
tool by WORLD coordinates + axis_dir directly, bypassing the
``face_named`` selector machinery. The planner emits this skill in box
mode (``shift != (0,0,0)``) so cuts land at the catalog axis_origin
verbatim instead of being projected onto the slab's outer face. Diag
of the "skill not in registry" failure cleared the path: the test
harness needed to ``pkgutil.walk_packages`` to register the new skill
before the executor could find it. As-shipped result on as1-oc-214:
**0.35 → 0.823, pockets matched 0/11 → 8/11**.

**Pass 12 — generic ``hole`` skill + no entry-Z override in box mode.**
In box mode, force ``thread_spec = None`` so the planner emits the
generic ``hole`` skill (which takes world position + direction) instead
of ``counterbore_hole`` / ``clearance_hole`` / ``tap_drill_hole`` (which
use ``face_named`` + ``position_xy``). Also gate the entry-Z bbox-face
override on ``shift == (0,0,0)`` so it only fires in preserve_brep mode
where it's needed for axis_origin convention mismatches.
**linkrods 0.60 → 0.833, as1_pe_203 0.484 → 0.625, pockets 9/18 → 14/18**.

**Pass 13 — depth clamp experiments REVERTED.** Tried removing the
200 mm depth clamp so a 1016 mm counterbore in as1_pe_203 would emit
its full depth, but it regressed preserve_brep (1.0 → 0.87) and didn't
help box mode (the cut now overlaps adjacent pocket regions and trips
zero-delta SKIPs). Reverted.

**Pass 15 — pocket-as-hole branch parity.** Mirrors the pass-12 fix on
``_pocket_step``'s pocket-as-hole branch (``depth/top_d >= 1.5``):
entry-Z override is preserve_brep-only there too. Defensive — none of
the four test files take this branch in box mode, but the fix prevents
a future file with deep narrow pockets on an industrial body from
regressing.

## Final state (commit 1d58d27)

| File           | preserve_brep | **box (TRUE RE)** | pocket matched |
|----------------|---------------|-------------------|----------------|
| linkrods       | 1.000         | **0.833**         | 2/3 + holes 2/2 |
| Ventilator     | 0.36          | 0.19              | 1/1 (single pocket) |
| as1-oc-214     | 1.000         | **0.823**         | **8/11**       |
| as1_pe_203     | 1.000         | **0.625**         | **14/18**      |

For the FIRST time on an industrial-scale STEP file, the pipeline
genuinely rebuilds the part from a placeholder slab — not "body
unchanged self-matches itself". The 0.83 / 0.62 box-mode scores
reflect that 73 %–78 % of the catalog pockets and (on small bodies)
all the holes pair geometrically with the cuts our plan actually
performed.

## Honest remaining gaps

- **Ventilator 0.19**: the central hole is a stepped feature
  (d=80 mm outer + d=14.9 mm inner) the planner can't yet express as a
  single cut sequence. Catalog detects 14 holes; cut emits 14 holes;
  but pairing fails on dim drift (the stepped concentric topology
  comes out as different shape). Not a coordinate issue — a
  feature-class one.
- **as1_pe_203 holes 0/7**: the catalog stores depth=1016 mm
  (counterbore through the 2 m chassis). The planner clamps to 200 mm.
  Depth drift is 80 %, well outside the 8 % hole-pair tolerance.
  Either the clamp lifts (but pass 13 showed that regresses
  preserve_brep) or the metric becomes depth-aware. Future work.
- **The 200 mm hole depth clamp is load-bearing** even on industrial
  parts. We can't unilaterally raise it without breaking preserve_brep
  matching.
- **Volume drift remains high in box mode** — the regen body's
  classify_pockets reports the floor-face centroid, not the entry-face
  centroid; the ``frame_translation_mm`` correction subtracts the
  world-to-box shift but a ~3 mm pocket-depth-half offset remains.
- **boss / rib / lug emission** still uses face_named selectors in box
  mode. Bosses on industrial parts likely lose the same way pockets
  did before pass 11. (Confirmed for as1-oc-214: catalog has 3 bosses,
  regen has 0 — the boss path hasn't received the world-coord
  treatment yet.)

## Lessons

1. **Trust no metric you didn't probe end-to-end.** preserve_brep
   produced PERFECT 1.0 scores for nine commits' worth of "industrial
   parts work great" claims. User skepticism — "is this real
   reconstruction?" — exposed that they weren't.
2. **Box-mode + world-coord placement is the ONLY honest test.** Until
   the pipeline can rebuild a part FROM A PLACEHOLDER, the match score
   is a self-consistency check, not a build verification.
3. **Coordinate-frame consistency is most of the work.** The placeholder
   slab's frame, the catalog's world frame, the shift between them,
   the face_named selector's outer-face resolution, and the regen
   detector's centroid convention all had to be aligned before a
   single pocket would pair.
