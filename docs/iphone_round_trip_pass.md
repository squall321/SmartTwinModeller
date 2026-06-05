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

## 8k face decimation re-run

Re-ran the pipeline targeting `target_face_count=8000` (matching the new macro
cap in `extract_feature_catalog`). The quadric pass left 16,508 faces (over the
10% headroom of the 8k target) so the cluster fallback fired and collapsed it to
349 faces — the cluster backend's pitch heuristic over-decimates on this mesh
(target 8000 → actual 349). mesh_to_brep produced 3 open shells (350 faces),
fill_small_holes patched 1 of 5 boundaries.

`extract_feature_catalog` ran in 12.1 s wall-clock against the 350-face shell
(under the 8000 cap so no skip) with these per-detector hotspots:

| detector | seconds |
|---|---|
| classify_pockets | 7.54 |
| detect_mirror_symmetry | 2.66 |
| detect_circular_array | 0.62 |
| detect_linear_array | 0.61 |
| detect_lugs | 0.40 |
| all others | <0.1 each |

### Feature delta vs 132-face baseline

| feature | 132-face | 350-face | Δ |
|---|---|---|---|
| pockets | 2 | **3** | +1 |
| holes | 0 | 0 | 0 |
| bosses | 0 | **2** | +2 |
| ribs | 0 | 0 | 0 |
| lugs | 0 | 0 | 0 |
| patterns | 0 | 0 | 0 |
| symmetries | 6 | 6 | 0 |
| sweep/loft/revolve | 0/0/0 | 0/0/0 | 0 |

The richer (350-face vs 132-face) mesh surfaced **2 new bosses** and **1 extra
pocket** that the aggressive cluster pass had washed out. Symmetry detection
was stable (6 planes both ways — bilateral symmetry is dominant). No new
holes/ribs/lugs — those would require an even finer mesh (~3-5k) plus the
quadric backend (cluster smears small features). `base_thickness_mm` collapsed
to 0.07 mm (an artifact of multi-shell pairs being picked), so the planner
floor (`bbox_h/10`) will still take over for the regen.

### Next-decimation lever

Cluster's pitch heuristic ignores the explicit `target_face_count` once the
input is already moderately decimated. To realistically hit the 8000 face
budget we'd need either (a) a per-input pitch search in `mesh_decimate`'s
cluster path, or (b) chain another quadric pass (16,508 → 8000) since quadric
respects the target tightly. The macro cap is plenty of headroom; the bottleneck
is the decimator's target-overshoot, not `extract_feature_catalog`.

Script: `run_logs/_tmp/iphone_8k_demo.py`.
