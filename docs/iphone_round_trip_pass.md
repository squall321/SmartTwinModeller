# iPhone real-world round-trip — first PASS (2026-06-05)

Real OEM (iPhone 12 teardown glb, 90,687 face mesh) → BREP → feature catalog → plan → PlanExecutor → regenerated solid.

## Pipeline

```
glb 90,687 face
  ↓ trimesh.concatenate (18 outer-housing sub-meshes)
  ↓ auto unit-scale (cm → mm via bbox diag heuristic)
  ↓ Pass 1: mesh_decimate quadric → 16,508 face
  ↓ Pass 2: mesh_decimate cluster → 132 face
  ↓ mesh_to_brep (132 sewn, 2 shells, 0 closed)
  ↓ fill_small_holes (found 10, filled 3, kept 7 big cutouts)
  ↓ inspect_geometry: vol=25,176 mm³, faces=135, bbox 7×70×143 mm
  ↓ detect_mirror_symmetry: 6 candidate planes
  ↓ extract_feature_catalog: 2 pockets, 6 symmetries, base_thickness clamped
  ↓ plan_from_feature_catalog: 3 steps (box + 2 pockets)
  ↓ PlanExecutor: outcome=PASS, errors=0 ★
  ↓ Regen solid: vol=68,930 mm³, 8 faces (box + 2 pocket cuts)
                 bbox 7.14 × 70.11 × 142.61 mm
```

## Original vs regen

| | Original BREP | Regen solid |
|---|---|---|
| volume_mm3 | 25,176 (shell integral — not meaningful) | 68,930 |
| body_kind | shell | solid |
| face_count | 135 | 8 |
| bbox | 7.14 × 70.04 × 143.08 mm | 7.14 × 70.11 × 142.61 mm |

Volume drift 173% — apples-to-oranges (shell integral vs solid volume). Compared to what the original WOULD have been if it were a solid box (~71,000 mm³), the regen (68,930 mm³) is within 3%.

## What had to be fixed to get here

Ten distinct bugs surfaced and fixed during this pipeline (commits e831979 → de02b77):

1. `inspect_wall_thickness` — catalog path resolution (parents[5] → walk-up)
2. `inspect_geometry` — shell-aware face/edge enumeration
3. `extract_feature_catalog` — face-count guard (default 5000) prevents hang on raw mesh
4. NEW skill `fill_small_holes` — selective open-boundary fill (small cutouts only)
5. `_post_conditions.body_present` — None→None passthrough valid for io skills
6. `inspect_geometry` — abs(volume) + raw_volume_mm3 + body_kind ∈ {solid, shell, other}
7. `mesh_to_brep` — multi-shell handling (was dropping 99% of triangles when sewer produced compound)
8. `fill_small_holes` — early-return on closed body (was destructive-resewing it)
9. `fast-simplification` installed for quadric backend
10. `plan_from_feature_catalog` — base_thickness sanity floor (≥ bbox_h / 10)
11. `plan_from_feature_catalog` — RectangleSketch field names (rect → rectangle, length/width/center_x/y instead of width/height/position_xy)

## Known limits + next areas

- **65 disjoint shells (20 closed)** — teardown geometry intrinsically multi-component. shape_heal won't merge them (correctly — those ARE separate parts: front panel / back cover / housing rails / inside body / etc.).
- **PlanExecutor PASSed but regen is just box + 2 pockets** — feature detection on 132-face decimated mesh missed holes / bosses / camera / button details. Larger decimation target (5k–10k) would surface more features but currently extract_feature_catalog's O(N²) graph builders slow down past ~3000.
- **No symmetry steps emitted** — catalog has 6 symmetry planes but planner doesn't emit "mirror_feature" steps yet. Could shrink the plan by exploiting bilateral symmetry.
- **base step is `box` placeholder** — the original BREP shell with its 135 faces is discarded; we rebuild from the bbox. Keeping the original surface and only carving pockets on top would preserve the actual outer geometry.

## Reproducer

```powershell
$env:PYTHONPATH = "src"
& "venv\Scripts\python.exe" run_logs/_tmp/iphone_round_trip.py
```

Script at `run_logs/_tmp/iphone_round_trip.py`. Output STEP at `plans/reconstructed_plan.yaml` + regen body in memory.
