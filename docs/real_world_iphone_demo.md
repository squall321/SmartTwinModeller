# Real-world demo — iPhone 12 teardown glb → BREP → analyze

마지막 갱신: 2026-06-04

이 문서는 "외곽 surface 한 번이라도 진짜로 만들어본 적 있는가" 의 첫 실증.

## Setup

- 입력: `iphone/iphone_12_teardown.glb` (128 MB, trimesh.Scene 159 sub-meshes)
- 단위: glb 원본은 cm — auto-scale x10 적용 후 mm
- 파이프라인 스크립트: `run_logs/_tmp/iphone_backcover.py`

## Run 1 — 전체 외곽 housing (90,687 face, 18 sub-meshes 통합)

| Stage | 결과 |
|---|---|
| trimesh concatenate (profile_housing + front_panel + back_cover + inside_body) | 90,687 face |
| auto unit-scale (diag 16 → 163 mm) | bbox **146.7 × 71.1 × 8.2 mm** ← iPhone 12 spec 정확 |
| `mesh_to_brep` | 90,459 / 90,687 sewn, 228 degenerate skipped, **open_edges = 4,713** |
| `is_shell` / `is_solid` | shell=True, solid=False |
| STEP export | **238 MB** clean |
| `extract_feature_catalog` | **HANG** — 90k face shell 의 face adjacency graph 분석 부담 (10+ 분 미응답) |

## Run 2 — back_cover 단일 sub-mesh (4,773 face)

| Stage | 결과 |
|---|---|
| 입력 sub-mesh | `back_cover_mat_color_body_0`, 4,773 face |
| auto-scale | bbox **0.8 × 68.9 × 144.4 mm** ← iPhone 12 back panel 사이즈 정확 |
| `mesh_to_brep` | 4,773 / 4,773 sewn (100%), 0 degenerate, open_edges 413 |
| `inspect_geometry` | volume 866 mm³ (open shell — 의미 약함), bbox 정확 |
| `detect_mirror_symmetry` | **X-plane score 0.833 ★** (좌우대칭 정확 감지), Y 0.15, Z 0.09 |
| STEP export | 11.9 MB clean |

### 좌우 대칭 검출

`detect_mirror_symmetry` 가 X-plane (즉 YZ plane, 핸드폰 길이 방향 중심 대칭) 에 **0.833 / 1.0** score 부여. 이건 실제 iPhone 의 좌우 대칭 (카메라 빼고) 을 정확히 포착한 것.

## 검증된 것

- ✅ Real-world 3D scan / teardown mesh → BREP shell 변환 가능
- ✅ Mesh 단위 자동 감지 (cm → mm)
- ✅ Sub-mesh 합치기 + sewing (90k tri 까지 OK)
- ✅ `inspect_geometry` BREP shell 에서 bbox / volume 측정
- ✅ `detect_mirror_symmetry` real-world geometry 에서 의미 있는 대칭 score 반환
- ✅ STEP export (open shell 그대로)

## 한계 확인됨

- ❌ Open shell (413~4,713 open edges) → solid 화 자동 안 됨 (teardown 의 카메라/버튼/포트 cutout 때문)
- ❌ `extract_feature_catalog` on 90k face shell — 너무 느려 hang (decimation 필요)
- ❌ `inspect_wall_thickness` — catalog path 버그 (`catalogs/dfm_inspect/default_thresholds.yaml` 못 찾음)
- ❌ `inspect_geometry` 의 face_count = 0 (shell 의 face 안 셈 — inspect 가 solid-only 가정)

## 다음 보강 path

1. **Mesh decimation 의무화** — 5k face 이하로 자동 줄여서 extract_feature_catalog 부담 ↓
2. **inspect_wall_thickness catalog 경로 fix** — `pathlib.Path(__file__).parents[N]` 으로 resolve
3. **shell-aware inspect_geometry** — shell 의 face 도 enumerate (현재 solid 가정)
4. **open-shell heal v2** — fill_holes 같이 의도된 cutout 만 채우고 나머지는 두기
5. **Round-trip on back_cover** — symmetry-aware re-generate 가능한가?
