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

## Run 3 — 풀 외곽 housing 풀파이프라인 (decimate + fill 후, e831979 fix 적용 후)

| Stage | Result |
|---|---|
| Decimation 90,687 → 4,123 (cluster fallback) | mesh_decimate skill 의 body_present post-cond 버그로 fallback 동작 |
| `mesh_to_brep` | 4,123 tri → shell, open_edges=12,095 |
| `fill_small_holes` (max_perimeter=80mm) | found=3,974 boundaries, **filled=194**, skipped_big=0 |
| `inspect_geometry` | face_count=**4,306**, edge_count=12,240, bbox **8.2×71.1×146.7mm** |
| `detect_mirror_symmetry` | X=0.376 / Z=0.296 / Y=0.280 (내부 components 가 대칭 깨뜨림) |
| **`extract_feature_catalog` 정상 동작 ✓** | **bosses=315, ribs=365, patterns=20, symmetries=6**, holes=0, pockets=0 |
| STEP export | 20.7 MB clean |
| volume_mm3 | **-71** (shell orientation issue, not a solid) |

이번이 **풀파이프라인 첫 완주** — `e831979` 의 4개 fix 가 실제로 작동한 결과:
- Fix 1 (face_count guard ≤5000) 덕분에 extract_feature_catalog 가 hang 없이 완주
- Fix 3 (shell-aware face count) 덕분에 4,306 face 가 제대로 카운트됨
- Fix 4 (fill_small_holes) 가 194개의 작은 boundary 를 채움
- Fix 2 (catalog path) 는 wall_thickness 호출에만 영향, 이번엔 미사용

## 다음 보강 path (Run 3 에서 노출됨)

1. **`mesh_decimate` body_present 버그** — io 카테고리 skill 이 body input=None 일 때 post_cond 실패. `allow_no_change` 옵션 또는 io skill 들의 post_cond 재검토 필요
2. **`fast_simplification` 설치** — cluster decimation 이 3,780개의 boundary fragment 를 만듦; quadric 이면 훨씬 깨끗
3. **shell-tolerant hole/pocket detector** — open shell 에서도 boundary loops 로 hole 추론. 현재는 closed solid 가정
4. **face orientation 통일** — shell 의 face orientation 반전 섞이면 volume 이 음수가 됨. ShapeFix_Shell 적용 후 retry
5. **interior-component 제거 옵션** — 외곽만 분석하려면 inside_body / motherboard mesh 제외. 또는 `back_cover` 처럼 단일 외곽 sub-mesh 선택
6. **shell-aware inspect_wall_thickness** — 현재는 ray-march 가 solid 가정. shell 에서도 거리 측정 가능하게
