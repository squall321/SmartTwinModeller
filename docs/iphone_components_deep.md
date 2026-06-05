# iPhone Component-Wise Deep Feature Catalog (PACK B)

**Input:** `run_logs/_tmp/iphone_housing_dec.stl` (decimated teardown mesh)
**Pipeline:** `mesh_to_brep` → `split_into_components` (closed only) → `extract_feature_catalog` per closed shell
**Script:** `run_logs/_tmp/iphone_components_deep.py`
**Sidecar JSON:** `run_logs/_tmp/iphone_components_deep_summary.json`
**Wall time:** 70.4 s total (5.0 s in EFC; remainder dominated by sewing + topology enumeration)

---

## Headline Counts

| metric                  | value |
|-------------------------|------:|
| mesh_to_brep shells     |    27 |
| closed shells (mesh)    |    16 |
| open edges after sew    |   656 |
| components (≥0.1 mm³)   |    14 |
| **closed components**   | **7** |
| small-shell artefacts skipped | 13 |
| components processed by EFC   |  7 |
| components skipped (>8000 fc) |  0 |
| components erroring           |  0 |
| **total holes**         |   0 |
| **total pockets**       |   1 |
| **total bosses**        |   1 |
| **total ribs**          |   2 |
| **total lugs**          |   0 |
| **total patterns**      |   0 |

**Reality check vs. PACK plan.** PACK B anticipated ~65 shells / ~20 closed
components. On `iphone_housing_dec.stl` we observe **27 shells / 16 closed**
out of mesh_to_brep, which collapse to **14 components / 7 closed** after the
0.1 mm³ artefact filter. The decimated mesh has fewer surviving sub-parts than
the original 65-shell raw teardown — the decimator merged or culled enough
triangles that several internal components dropped below the volume floor.

## Per-Closed-Component Table (sorted by volume)

| idx | fc | vol (mm³) | bbox (mm)            | H | P | B | R | L | Pat | top surface (area mm²) | status |
|----:|---:|----------:|----------------------|---:|---:|---:|---:|---:|----:|------------------------|--------|
|   1 |  6 |     5.06  | 0.8 ×  9.3 ×  3.9    | 0 | 0 | 0 | 0 | 0 |   0 | Plane (38.8)           | ok     |
|   2 | 43 |     4.49  | 1.0 ×  6.2 ×  3.2    | 0 | 1 | 0 | 0 | 0 |   0 | Plane (38.2)           | ok     |
|  10 | 16 |     0.79  | 1.0 × 11.2 ×  3.9    | 0 | 0 | 1 | 1 | 0 |   0 | Plane (79.8)           | ok     |
|   8 | 17 |     0.77  | 1.0 × 12.4 ×  4.9    | 0 | 0 | 0 | 1 | 0 |   0 | Plane (145.8)          | ok     |
|   6 | 16 |     0.28  | 2.8 ×  0.7 × 10.1    | 0 | 0 | 0 | 0 | 0 |   0 | Plane (58.8)           | ok     |
|   7 |  4 |     0.19  | 0.1 ×  0.8 ×  7.0    | 0 | 0 | 0 | 0 | 0 |   0 | Plane (11.3)           | ok     |
|   4 |  4 |     0.17  | 0.1 ×  0.8 ×  6.4    | 0 | 0 | 0 | 0 | 0 |   0 | Plane (10.3)           | ok     |

Every closed shell is essentially **planar-faced** — no Cylinder/Cone/BSpline
surfaces survived the decimation as primary surface kinds. That alone explains
why hole / boss / pattern detection is sparse: those detectors anchor on
cylindrical or planar-ring fingerprints that the decimator wiped out.

## Interpretation — Which Component Is Which?

Bounding box footprints (`sx × sy × sz`) plus the model's overall Z-extent
(the iPhone runs roughly Z=0…150 mm in this coordinate system) let us guess
each part's role:

- **idx 1, vol 5.06 mm³, bbox 0.8×9.3×3.9 @ Z≈142–146.** Sits at the very top
  of the phone, slab-shaped, the largest closed shell. Most plausibly the
  **top edge of the metal mid-frame** (antenna band / SIM tray cutout
  reinforcement), modeled as a thin solid slab.
- **idx 2, vol 4.49 mm³, bbox 1.0×6.2×3.2 @ Z≈139–142.** Adjacent in Z to
  idx 1, but slightly smaller and carries the *only detected pocket*. Likely
  the **SIM-tray pocket housing** or the **lightning/USB-C port well**
  immediately below the top reinforcement — pockets in this region match a
  port cavity.
- **idx 10, vol 0.79 mm³, bbox 1.0×11.2×3.9 @ Z≈45–49.** Mid-body strip;
  carries the only **boss** detected and a rib. Almost certainly a **camera
  bezel / lens-housing ring** — a raised stand-off matches "boss" semantics
  and the elongated XY footprint matches a horizontal lens island edge.
- **idx 8, vol 0.77 mm³, bbox 1.0×12.4×4.9 @ Z≈45–49.** Sibling of idx 10
  (same Z band, similar size, carries a rib). Likely the **second lens
  housing ring** or the camera-array mounting bracket adjacent to idx 10.
- **idx 6, vol 0.28 mm³, bbox 2.8×0.7×10.1 @ Z≈54–64.** Tall, narrow vertical
  sliver on the side of the chassis. Plausibly a **side button (volume up or
  power) or its mounting tab**.
- **idx 7, vol 0.19 mm³, bbox 0.1×0.8×7.0 @ Z≈38–45.** Razor-thin
  XY footprint, vertical extent ~7 mm. Reads as a **side-rail spring contact
  or antenna strap** — the X-extent of 0.1 mm is below any structural
  component thickness.
- **idx 4, vol 0.17 mm³, bbox 0.1×0.8×6.4 @ Z≈103–109.** Mirror of idx 7 in
  shape but higher up the chassis. Likely the **second antenna feed strap or
  upper-side contact spring**.

## Why So Few Features?

1. **Decimation kills detectors.** Every closed shell's dominant surface kind
   is `Plane`. The hole / boss / pattern detectors rely on cylindrical and
   circular-ring fingerprints. Triangle decimation collapses curved surfaces
   into faceted planar polygons, so the cylinder / cone / BSpline kinds that
   normally fire `classify_holes` simply do not survive.
2. **Open shells dominate.** Of the 27 sewn shells, 11 are open and skipped
   here. The largest part of the housing (back cover with cutouts) almost
   certainly went into one of those open shells — the sewing tolerance left
   gaps around lens cutouts and edge fillets, so it never reached the
   per-closed-component pipeline.
3. **Sub-millimetre artefact floor.** 13 micro-shells were dropped at the
   0.1 mm³ threshold; some of those may be the small screw heads PACK B was
   chasing.

## Takeaways for the Reconstruction Plan

- The component split *does* isolate physically distinct sub-parts (top
  reinforcement, lens housings, side buttons, antenna straps) — that part of
  the PACK B hypothesis is validated.
- The macro `extract_feature_catalog` face-count guard (8000) was never
  triggered on a per-component body — every closed shell has ≤43 faces.
- To recover the feature richness PACK B expected (~20 closed shells, screw
  holes, cylinder bosses), the upstream pipeline should rerun on
  `iphone_housing_full.stl` (4.5 MB, ~5× the triangle count) with a tighter
  sewing tolerance — that is where the cylindrical fingerprints survive.
- For the planner: the two ribs on idx 8 and idx 10 + the boss on idx 10 are
  the strongest signal — they support a `lens_housing` sub-feature in the
  reconstructed plan, separate from the slab/back-cover base.
