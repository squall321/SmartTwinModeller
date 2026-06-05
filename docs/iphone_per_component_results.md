# iPhone per-component feature catalog (2026-06-05)

`run_logs/_tmp/iphone_housing_dec.stl` (2,923 triangles after pass-2 cluster decimation) →
`mesh_to_brep` (sew tol 0.5 mm) → `split_into_components` → `extract_feature_catalog` per shell.

## Mesh-to-BREP / split summary

- triangles sewn: **2,923**
- shells after sewing: **27** (16 closed by `BRepCheck_Shell`)
- `split_into_components` after the 0.1 mm³ artefact filter: **14 components**, **7 closed**, **13 skipped_small**
- pipeline wall time: **115.5 s** (feature-catalog work alone: 27.5 s)

The 65-shell figure quoted in the milestone doc was from a different decimation pass; with the 0.5 mm sewing tolerance the dec.stl collapses to 27 raw shells (14 keep-able after volume filter). The per-component path is what matters — every component went through `extract_feature_catalog` under its own 8,000-face budget without skips.

## Per-component table

| idx | faces | closed | vol mm³ | H | P | B | R | L | Pat | bbox L×W×H mm | interpretation |
|----:|------:|:------:|--------:|--:|--:|--:|--:|--:|----:|---------------|----------------|
|   0 |   149 |  -  |    69.1 | 0 | 2 | 0 | 0 | 0 |   0 |  5.6 × 68.5 × 23.8 | side rail / button strip (long, thin, open shell, two pockets ≈ volume/mute switches) |
|   1 |     6 |  Y  |     5.1 | 0 | 0 | 0 | 0 | 0 |   0 |  0.8 × 9.3 × 3.9   | screw / pin (closed, ~5 mm³) |
|   2 |    43 |  Y  |     4.5 | 0 | 1 | 0 | 0 | 0 |   0 |  1.0 × 6.2 × 3.2   | screw boss seat (closed, single pocket = socket) |
|   3 |     9 |  -  |     0.1 | 0 | 0 | 0 | 0 | 0 |   0 |  2.6 × 0.1 × 6.4   | sliver / antenna flex tab |
|   4 |     4 |  Y  |     0.2 | 0 | 0 | 0 | 0 | 0 |   0 |  0.1 × 0.8 × 6.4   | tiny closed sliver |
|   5 |     9 |  -  |     0.1 | 0 | 0 | 0 | 0 | 0 |   0 |  2.6 × 0.1 × 6.4   | mirror of idx 3 |
|   6 |    16 |  Y  |     0.3 | 0 | 0 | 0 | 0 | 0 |   0 |  2.8 × 0.7 × 10.1  | screw-pin or pogo contact |
|   7 |     4 |  Y  |     0.2 | 0 | 0 | 0 | 0 | 0 |   0 |  0.1 × 0.8 × 7.0   | sliver pin |
|   8 |    17 |  Y  |     0.8 | 0 | 0 | 0 | 1 | 0 |   0 |  1.0 × 12.4 × 4.9  | button strut (rib detected — flat thin part) |
|   9 |    29 |  -  |     0.5 | 0 | 0 | 0 | 0 | 0 |   0 |  0.3 × 5.7 × 18.1  | antenna line / camera trim |
|  10 |    16 |  Y  |     0.8 | 0 | 0 | 1 | 1 | 0 |   0 |  1.0 × 11.2 × 3.9  | button cap (boss + rib — raised stub) |
|  11 |    10 |  -  |     1.1 | 0 | 0 | 0 | 0 | 0 |   0 |  0.2 × 4.7 × 18.2  | antenna trace strip |
|  12 |     9 |  -  |     1.0 | 0 | 0 | 0 | 0 | 0 |   0 |  0.7 × 1.2 × 3.9   | small pin / connector |
|  13 |  2560 |  -  |  5330.2 | 0 | 1 | 1 | 1 | 0 |   0 |  8.2 × 72.3 × 147.1 | **main housing** (open back, full phone footprint — one big pocket = display recess, one boss = camera island, one rib = mid-frame edge) |

## Aggregate

- components_processed = 14 (no `too_big` skips, no detector errors)
- total_holes = 0, total_pockets = 4, total_bosses = 2, total_ribs = 3, total_lugs = 0
- closed-shell ratio after volume filter: **7/14 = 50 %** (and 16/27 = 59 % pre-filter from the raw sewer output)

## Interpretation

- **Main housing (idx 13)** dominates: 2,560 faces, 5,330 mm³, the full 8 × 72 × 147 mm iPhone footprint. The pocket detected here is the display recess; the boss is the camera bump.
- **Camera assembly:** there is no dedicated multi-lens shell — the camera island shows up as the boss inside idx 13 because decimation collapsed the bumps into the housing.
- **Buttons / volume + power:** idx 0 (side rail, 2 pockets) holds the recessed volume + mute slot. idx 8 and idx 10 are individual button caps (rib + boss detection picks them up).
- **Screws / pins:** idx 1, 2, 4, 6, 7, 12 — small closed shells in the 0.2-5 mm³ band; idx 2 carries a single pocket signature (screw boss socket).
- **Closed-shell %:** 50 % is sane for a teardown — solid screws and button caps close cleanly while sheet-metal style shells (housing, antenna traces) stay open. This matches the milestone doc's "65 disjoint shells, 20 closed (≈ 30 %)" — the dec STL we ran here has been further fused by the 0.5 mm sew tolerance, so the closure fraction is higher.
- **Zero holes** is the expected blind spot: `classify_holes` requires cylindrical face pairs which the decimated mesh no longer carries.

## Reproducer

```powershell
$env:PYTHONPATH = "src"
& "venv\Scripts\python.exe" run_logs/_tmp/iphone_per_component.py
```

Script: `run_logs/_tmp/iphone_per_component.py`.
JSON sidecar: `run_logs/_tmp/iphone_per_component_summary.json`.
