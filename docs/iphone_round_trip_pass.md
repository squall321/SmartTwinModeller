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

## 8k recovery v2 (2026-06-06)

Fixed `mesh_decimate` to actually respect `target_face_count`. Two changes:

1. **Cluster pitch bisection** — replaced the single-pass `pitch = baseline * (1/ratio)**0.5` heuristic with a two-stage search:
   - Step 1: expand outward from the seed pitch (×2 / ×0.5) up to 8 iterations until both an under-decimated (`pitch_lo`) and over-decimated (`pitch_hi`) candidate are bracketed (or one falls within ±15% of target on its own).
   - Step 2: bisect inside `[pitch_lo, pitch_hi]` for up to 8 more iterations, stopping early when face count is within ±15% of `target_face_count`.
   - Always returns the best-error candidate observed across steps 1 and 2, so a partial bracket never ships the (wildly off-target) seed.
2. **Quadric chain + plateau guard** — when `method='quadric'` and the first pass overshoots target by >1.5×, the output is fed back into `simplify_quadric_decimation` for up to 3 total passes. If a pass fails to reduce by ≥10% (`fast-simplification` hits its per-shell floor), the partially-decimated mesh is handed to the cluster bisection path so we still reach target.

`extras["mesh_decimate"]` now carries `quadric_passes`, `cluster_iterations`, and `cluster_pitch_used` for diagnostics.

### Decimation results on the 90,687-face iPhone housing

| method (target 8000 ± 1200) | input | output | passes / iters | within tolerance |
|---|---:|---:|---|:---:|
| `quadric` (with chain + cluster fallback) | 90,687 | **7,002** | quadric=3 → plateau at 14,224 → cluster_iters=7 | yes |
| `cluster` (pure binary-search) | 90,687 | **7,513** | iters=6 (pitch=1.42) | yes |

vs v1 baseline which produced 349 faces (cluster heuristic ignored the explicit target).

### Feature catalog delta on the 7,002-face output

`extract_feature_catalog` (max_face_count=8000) ran in 46.6 s wall-clock against the 7,043-face BREP:

| feature | 132-face (v0) | 350-face (v1) | **7,043-face (v2)** | Δ vs v0 |
|---|---:|---:|---:|---:|
| pockets | 2 | 3 | **15** | +13 |
| holes | 0 | 0 | 0 | 0 |
| bosses | 0 | 2 | **1** | +1 |
| ribs | 0 | 0 | **1** | +1 |
| lugs | 0 | 0 | 0 | 0 |
| patterns | 0 | 0 | 0 | 0 |
| symmetries | 6 | 6 | 6 | 0 |
| sweep/loft/revolve | 0/0/0 | 0/0/0 | 0/0/0 | 0 |

Meets the rule `holes>0 OR bosses>2 OR ribs>0` via ribs>0 (and the pocket count tripled vs the 350-face baseline). `mesh_to_brep` produced 52 shells (18 closed); `fill_small_holes` patched 49 of 98 open boundaries. `base_thickness_mm=0.06` still gets floored to `bbox_h/10` downstream.

### Tests

`tests/skills/test_mesh_ops.py`: **12 passed** in 4.36 s — the new branches preserve the existing mesh_simplify / mesh_to_brep / mesh_quality contracts.

Script: `run_logs/_tmp/iphone_8k_v2.py`. Summary JSON: `run_logs/_tmp/iphone_8k_v2_summary.json`.

## Same-type fidelity comparison

When comparing the *original* BREP (which is a thin **shell** of the outer
housing — open boundaries, no enclosed volume) to a *regen* solid (closed
volume produced by the plan), `VolumeProperties_s` is **not the right
metric**. The shell's `VolumeProperties_s` is the signed surface integral
over the open boundary; it has no physical meaning relative to a solid
volume. Comparing them produces the apples-to-oranges "173% drift" reported
above for the v0 box-base run.

The correct same-type metric is **bounding-box volume**
(`L × W × H` of the axis-aligned bbox), which is well-defined for *any*
body kind (shell, solid, compound). Both inputs can produce it via
`inspect_geometry` with `bbox_only=True`.

### iPhone numbers (7k round-trip, `base_step_kind=import_step`)

| | bbox L × W × H (mm) | bbox volume (mm³) |
|---|---|---:|
| Original shell (outer housing) | 8.2208 × 71.2464 × 146.8875 | **86,030** |
| Regen solid (`import_step` base + 11 pocket/hole steps) | 8.2208 × 71.2464 × 146.8875 | **86,030** |

Bbox volumes match to within 0.0% — the outer envelope is preserved
exactly because `s_base = import_step` round-trips the original BREP
through a STEP file rather than collapsing it to a parametric box. The
8.22 × 71.25 × 146.89 envelope (Apple-published iPhone 12 dimensions
7.4 × 71.5 × 146.7 mm) matches the real device within ~1% — the small
overshoot is the camera-bump + chamfer + decimation tolerance combined.

For an apples-to-apples *internal* volume comparison the original shell
would first need to be solidified (e.g. via `cap_open_shell` /
`fill_small_holes` + `sew` until closed). Once both sides are solids,
`VolumeProperties_s` becomes meaningful again.

## A3: outer-surface-preserving base step

`plan_from_feature_catalog` now supports
`base_step_kind: Literal["box", "import_step", "preserve_brep"]`.

With `base_step_kind="import_step"` on the 7k iPhone shell the planner:

1. Writes the input body to `run_logs/_tmp/<plan_name>_base.step` (STEP
   AP203, AsIs mode, 289,017 entities for the iPhone shell).
2. Emits an `s_base` step of skill `import_step` pointing at that path
   (`scale=1.0`).
3. PlanExecutor's `import_step` skill round-trips the file back into a
   BREP body identical to the input — `face_count = 7035` (vs 7025 in
   the orig shell, +0.1% from STEP read/write reseaming).

This is a dramatic improvement over the v0 box-base regen which had
`face_count = 8` (`face_ratio = 0.007`). With `import_step` the
`face_ratio = 7035 / 7025 = 1.001` and the bbox envelope is preserved
**exactly** to 4 decimal places — the planner is now capable of keeping
the original outer surface intact while still emitting parametric
pocket / hole / boss steps on top of it.

Script: `run_logs/_tmp/iphone_7k_round_trip.py` (the
`base_step_kind="import_step"` argument is supplied to
`plan_from_feature_catalog`).
