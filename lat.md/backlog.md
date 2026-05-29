# backlog — 진행 중 발견된 작업 항목 (꼼꼼히 누적)

> 본 문서는 [[plan]] 의 phase 별 계획과 별도로, **실제 구현 진행 중 발견된**
> 누락·미흡·추가 요구사항을 누적한다. 우선순위 + 어느 phase 로 편입할지 표기.
>
> 새 항목 추가 시: `[ ]` 체크박스 + 발견 일자 + 한 줄 설명 + 권고 phase.

## 범례

- 우선순위: **P0** = 다음 turn 에 해결 / **P1** = phase 진행 중 / **P2** = 정리 시점 / **P3** = nice-to-have
- 상태: [ ] todo, [x] done, [~] partial / blocked

---

## P0 — 다음 turn 안에 해결

### Inspector / 가시화
- [ ] **PySide6 본격 inspector GUI** — viewport + plan step list + step navigation + viewport diff (current vs reference) ([[ui#layout]] 의 일부 즉시 필요)
  - QMainWindow + pyvistaqt.QtInteractor + QListWidget (steps) + QSplitter
  - File menu: Open Plan, Open STEP, Open glb, Compare
  - 우선 single-window. Chat / DFM panel 은 Phase 7+ / 5+ 에서.
- [ ] Inspector 에서 plan 실행 → step 별 mesh 캐싱 → 클릭으로 viewport 갱신
- [ ] Inspector 의 `Compare with reference` 토글 — 두 STEP overlay (반투명)
- [ ] CLI: `phone-designer inspect [--plan <p> | --step <s>]`
- [ ] CLI: `phone-designer ui` (빈 inspector 실행)

### LFS / 자산 정리
- [ ] `iphone/iphone_12_teardown.glb` (39MB) 가 git 에 들어가면 비대 — `.gitignore` 추가 또는 Git LFS 결정

### 빠른 cleanup
- [x] setup.ps1 UTF-8 BOM (`scripts/ensure_bom.ps1` 가 가드)
- [x] PoC `test_*.py` rename
- [x] PoC `from poc.persistent_naming` → relative import

---

## P1 — Phase 진행 중

### Phase 1 잔여
- [x] `disc_with_dome` 의 `cut_box` D×1.5 확대
- [x] `chamfer_predicate` 의 `MapShapesAndAncestors` hash 이슈 → 직접 IsSame iteration
- [ ] `disc_with_dome` 의 **top fillet 자동 fail safe** — build123d 의 max_fillet() 호출해서 R 자동 축소
- [ ] `_resolvers._heuristic_named_face` 의 `top` 이 dome 같은 곡면 매칭 시 `_face_plane_normal` 에서 NotImplemented — `face_named` 결과의 surface_type 노출해서 사용자/LLM 에게 알림
- [ ] 8 skill 의 `cost_hint` 실측 자동 갱신 (`audit.py` 의 duration_ms 기반)
- [ ] **history_rule_catalog cross-platform 비교** — Windows 외 Linux/macOS 에서도 100% 검증 (현재는 Windows 1대)

### Phase 2 잔여 (Plan executor 의 본격 v2)
- [ ] **Incremental rebuild** — step k 만 수정 시 k-1 까지 캐싱, k부터 재실행
- [ ] **Rollback after fail** — 실패 step 자동 제거 또는 사용자 선택
- [ ] Plan diff — 두 plan 의 step-level diff (LLM 합성 비교용)
- [ ] STEP export 에 XDE 어셈블리 + 부품 네이밍 (현재 generate 는 단일 shape 단순 STEP)
- [ ] `compare` runner kind 의 **face-level Hausdorff distance** (현재 face count/volume/bbox 만)
- [ ] `compare` 의 **feature 위치 일치도** (특정 feature 의 bbox 매칭)

### Phase 3 (진행 중)
- [ ] `mesh_io.loaders` (.glb / .fbx) — Phase 9 의 mesh pipeline. 현재는 CAD 경로만.
- [ ] `mesh_io.analyzer` — PCA, 표면 segmentation, 곡률 (Phase 9)
- [ ] `mesh_io.measure` — bbox, corner R, chamfer 폭, camera bump (Phase 9)
- [x] `reference.step_reader` (XDE) — framework 으로 승격 (`src/phone_designer/reference/step_reader.py`)
- [x] `reference.topology_analyzer` — v1: face surface type + fillet/hole/chamfer 검출
- [x] `reference.feature_to_plan` — v1: base (disc/slab) + corner_r + hole
- [ ] **Phase 3 v2**: pocket/plateau 검출 (face cluster + base-side 관계)
- [ ] **Phase 3 v2**: chamfer 의 인접 평면 각도 계산 (현재 휴리스틱 45°)
- [ ] **Phase 3 v2**: hole 의 depth 측정 (u/v param 으로 z extent)
- [ ] **Phase 3 v2**: polynomial pocket (BSpline 곡면)
- [ ] **Phase 3 v2**: swept feature
- [ ] **Phase 3 v2**: feature 의 'belong to' 관계 (예: boss 가 여러 face)
- [ ] **Phase 3 v2**: `_resolvers._heuristic_named_face` 의 history map 연동 (face_named 가 reverse engineer 후 안정적으로 매칭)

### Phase 4 진행 — Batch 별

#### Batch 4-1 (완료, 7 skill, +Tag system)
- [x] `import_step(path, scale)` — OEM 부품 인서트
- [x] `subtract(other)` + `union(other)` — boolean. OtherBodySpec: step_path / fixture_part / primitive_box / primitive_cylinder
- [x] `extrude_through(face_selector, sketch)` — 관통 cutout (사각/원형 sketch)
- [x] `hole_array(points, hole_spec)` — macro (hole 반복)
- [x] `final_fillet_all_sharp_edges(radius, min_angle)` — 일괄 마감 (bulk + individual fallback)
- [x] `tag_face(selector, tag)` — body._pd_tags + SkillBase.apply 가 chain propagate
- [x] **`tagged` selector** body._pd_tags 의 nearest-match (Phase 4 v1, edge tag 는 v2)

#### Batch 4-2 (완료, 워치 특화 macro 3)
- [x] `crown_shaft_hole(position, direction, shaft_d, bearing_recess_d, plinth)` — macro `[union, hole, hole]`. direction = outward normal (사용자 친근, inner hole 자동 반대)
- [x] `lug_pair(axis, offset, lug dims, pin_d)` — macro `[union, union, hole, hole]`. +Y/-Y 또는 +X/-X
- [x] `o_ring_groove(face_selector, outer_d, inner_d, depth)` — atomic v1 (circular ring on planar +Z/-Z face). Phase 4 v2: 임의 path sweep + profile

#### Batch 4-3 (완료, 보스/리브/스냅)
- [x] `boss_with_hole(position, direction, boss_d, boss_height, hole_d, hole_depth)` — macro `[union, hole]`
- [x] `rib(start, end, width, height, draft_deg, up_axis)` — atomic v1 직선 path. v2: 곡선
- [x] `snap_hook(base_position, cantilever dims, lip dims, axis)` — atomic, cantilever beam + lip
- [x] `mounting_pad(face_selector, sketch, height, roughness_ra, flatness)` — atomic, Phase 5 DFM 연동용 metadata

#### Batch 4-4 (완료, 고급 곡률)
- [x] `variable_radius_fillet(selector, radii_mm)` — atomic, edges × radii (cycle)
- [x] `loft_side_profile(bottom, top)` — atomic, ThruSections (circle/rect 동일 kind)
- [x] `swept_relief(start, end, width, depth)` — atomic v1 XY-plane. v2: dz!=0 + 곡선
- [x] `surface_offset(offset_mm, tolerance)` — atomic, BRepOffsetAPI_MakeOffsetShape.PerformByJoin
- [x] `grille_pattern(center, pattern, window, hole_d, spacing, depth, direction)` — macro `[hole_array]`. hex/grid/radial
- [ ] **`polynomial_pocket(face, sketch, depth_curve, order)`** — NURBS 깊이 곡면. P1 v2 — build123d BSplineSurface 직접 구성

#### Batch 4-5 (완료, 안테나)
- [x] `antenna_slit(start, end, width, depth)` — atomic v1 직선. thin linear cut
- [x] `polymer_inlay(start, end, width, depth)` — atomic. slit 동일 geometry union. material attribute v2

### Phase 4 종합 (완료)
- **29 skill**: atomic 22 + macro 7 (polynomial_pocket 제외)
- **163 PASS** 단위 테스트
- **audit 29/29 propagate match 100%**
- **모든 Phase 4 skill 이 plan executor 에서 사용 가능**

### Phase 4 의 tag 시스템 follow-up (v2)
- [ ] **edge tag 지원** — 현재 face 만. selector edge resolver 에 tag chain 추가
- [ ] **tag propagate 후 형상 변경 시 nearest-match 의 한계** — 큰 mutation 후 tag 어긋남. history map 의 GENERATED/MODIFIED 와 연동
- [ ] **tag store 가 build123d Part 의 attribute** 라 deepcopy/직렬화 깨질 수 있음 — Plan executor 의 cache 와 호환 확인

### Phase 4 UI (P0 inspector 너머)
- [ ] Component palette panel (drag/drop)
- [ ] Plan editor panel (drag-reorder steps, edit args inline)
- [ ] DFM Report panel
- [ ] Chat panel (Phase 7 의 LLM editor 와 연동)
- [ ] Process Budget panel
- [ ] Status bar (cost monitor)
- [ ] Test menu (시나리오 런처 GUI)
- [ ] Undo/Redo 두 stack (plan-level + editor-level)
- [ ] Picking → selector 자동 제안
- [ ] i18n KO/EN PO

### Phase 5 (core 완료, P1 v2 잔여)
- [x] `manufacturing.processes` 5종 YAML — die_cast_al / injection_mold_pa / cnc_3axis / cnc_5axis / sheet_metal_stamp
- [x] `ProcessRegistry` + `ProcessDefinition` (YAML loader + applicable_to_skills)
- [x] `ManufacturingBudget` + YAML loader + `validate_plan` (skill ↔ process 호환)
- [x] **simpleeval string evaluator** (`safe_eval` 화이트리스트)
- [x] **wall_thickness_raymarch v0** — face sample + ray-cast (confidence 0.7)
- [x] **draft 검사 v0** — planar face normal vs pull_direction
- [x] **undercut 검출 v0** — planar face 의 -dot product
- [x] `run_dfm` 통합 + `DFMReport` + `DFMViolation`
- [x] `cli validate` 본격 구현
- [x] 2 sample budget YAML (watch_al_unibody / watch_plastic)
- [x] `phase5_dfm_smoke` 시나리오
- [ ] **manifest cross-reference** — process 의 applicable_to_skills 와 skill 의 manufacturing dict 가 일치하는지 자동 검증 script
- [ ] **wall_thickness v0.3** — medial axis 또는 cross-section (false positive 측정 후 결정)
- [ ] **draft 의 cylinder/conical face 지원** — 현재는 planar 만
- [ ] **multi-axis pull direction 자동 결정** — 양면 mold, 슬라이드 코어
- [ ] LLM caching breakpoint A 활성화 (Phase 7 dry-run 후)

### PF-7 spike (Phase 5 말)
- [ ] voxel + greedy ribbing 실측
- [ ] Delaunay tetrahedralization 대안
- [ ] Skeleton (medial axis of void) 대안
- [ ] 결정 → Phase 6 알고리즘 fixing

### Phase 6 (core 완료, P1 v2 잔여)
- [x] Component 모델 + ports/clearance/mount/source (4 mount kinds) + YAML loader
- [x] 초기 카탈로그 5종 (display/battery/crown/sensor/coil) + sample arrangement
- [x] OEM CAD 자동 추출 (`extract.py` — shape_to_bbox + classify_parts + save_extracted_to_catalog)
- [x] AABB 충돌 + clearance 검사 (`collision.py`) — v1 회전 미적용
- [x] `ComponentArrangement` + estimate_inner/housing_bbox
- [x] `housing_synth_rule v0` — 외피 + window/cutout + mount 별 pad/boss/snap + final_fillet
- [x] CLI `synthesize` + sample arrangement YAML
- [x] `phase6_synth_smoke` 시나리오 (27초 PASS)
- [ ] **OBB 정확 충돌** (trimesh.collision.CollisionManager + 회전) — v2
- [ ] **PF-7 ribbing** spike (voxel + greedy / Delaunay / skeleton)
- [ ] **OEM CAD vs 합성 결과 face-level 비교** (회사 컴 `phase6_oem_compare` 시나리오)
- [ ] sketch 의 `rounded_rect` 본격 지원 (현재 rectangle 로 fallback)
- [ ] housing_synth 의 `dome_rise > 0` 지원 — face_named=top 의 곡면 매칭 (Phase 4 v2)

### Phase 7 (LLM Editor)
- [ ] `llm.client` (Anthropic SDK + caching)
- [ ] `llm.tools` (manifest → tool schema)
- [ ] `planner.editor` (자연어 → plan diff)
- [ ] UI Chat panel
- [ ] **API key keyring** 저장 (`llm.keyring_storage`)
- [ ] **Offline mode** graceful degrade (Chat panel disable + tooltip)
- [ ] 비용 dry-run 시나리오 (`phase7_cost_dryrun`)
- [ ] mock LLM golden response fixtures

### Phase 8 (LLM Planner agentic)
- [ ] `planner.housing_synth_llm` agentic loop
- [ ] 실패 모드별 회귀 (ban list, retry, backtrack)
- [ ] 객관 metric 5종 (DFM 위반율 / plan 길이 / volume / 결정성 / **OEM face-level 일치도**)
- [ ] nightly LLM CI (비용 monitor)

### Phase 9 (Mesh pipeline)
- [ ] iPhone 12 glb → reverse engineer
- [ ] mesh 경로 정확도 한계 measurement
- [ ] STEP → ANSYS 메쉬 (수동 단계, 사용자 검증)

---

## P2 — 정리 시점 (각 phase 끝)

### Plan 시스템
- [ ] **Plan schema migration v1→v2 핸들러** 골격은 있지만 실제 케이스 없음 — Phase 4 의 새 skill 도입 시 첫 migration
- [ ] **autosave + recovery** — 매 step 실행 후 `~/.phone_designer/autosave/<ts>.yaml`, 시작 시 자동 로드 옵션
- [ ] Plan history stack (Undo/Redo 의 backbone)

### Manifest 시스템
- [ ] 모든 skill 의 `preconditions` / `failure_modes` 가 named ref 만 — 실제 함수 레지스트리 (`_precondition_registry.py`) 만들기
- [ ] `cost_hint` 자동 측정 (Phase 1 잔여 항목과 연동)
- [ ] manifest 의 **stable core / dynamic delta** 분리 (LLM caching breakpoint)

### Test infra
- [ ] **Cross-platform 결정성 회귀** — Windows + Linux 양쪽 CI (가능하면)
- [ ] **시각 회귀** — Phase 2 plan 결과의 face count / volume baseline 고정
- [ ] **face-level 회귀** — OEM 외피 비교 metric baseline
- [ ] pytest 의 `requires_oem` marker 가 conftest 와 scenario runner 양쪽 일관 (현재 OK 확인)

### 문서
- [ ] **lat.md anchor 일관성** — `[[decisions#PF-1]]` 가 GitHub anchor (`pf-1--persistent-naming`) 와 차이. `lat check` tool 의 fuzzy match 의존 → heading 단순화 또는 `[[decisions#pf-1-persistent-naming-occt-history-map]]` 풀형 사용
- [ ] discovered-tasks 의 본 문서 자체 — index 등록 ([[lat#개발-테스트-인프라]])
- [ ] README 에 "현재 상태" 표 자동 갱신 (수동 → script)

### 에러/UX
- [ ] **OCCT 에러 매핑** ([[ui#error-mapping]]) — `ERROR_MAPPING` dict 점진 확장 (현재 stub)
- [ ] Plan executor 의 실패 step 에 friendly 메시지 부착 (Phase 2 의 `FailureMeta.mapped_message`)

### Reference 처리
- [ ] **Parasolid → STEP 변환 자동화 v0.2** — SpaceClaim Python automation 또는 OpenCascade reader
- [ ] **부품 네이밍 inconsistent 케이스** 처리 (heuristic + 사용자 수기 분류 UI)

---

## P3 — Nice-to-have (시간 여유 시)

- [ ] FBX 지원 부활 (bpy) — Rev 4 결정으로 제외, 필요 시 별도 phase
- [ ] **MCP server 노출** — 본 도구를 Claude Code 등 host 에서 사용 가능
- [ ] **다른 LLM 모델** (Sonnet fallback, Haiku 보조)
- [ ] **PR review 시나리오** (CI 통합)
- [ ] **i18n 추가 언어** (KO/EN 외)
- [ ] 다른 OEM (Apple Watch, Pixel Watch, Z Fold) reference 확보

---

## 운영 / 인프라

### 사용자 측 결정 대기
- [ ] **회사 컴 setup.ps1 실행** + 시나리오 dry-run + 결과 메일 → 집 컴 (사이클 검증)
- [ ] **gmail 앱 비밀번호 또는 outlook SMTP** 자격증명 등록
- [ ] **Galaxy Watch Parasolid** 회사 컴에 위치 + PF-3 변환 1회
- [ ] **iPhone 12 glb 유지 여부** — `.gitignore` 추가 또는 LFS

### 미구현 CLI 커맨드
- [ ] `phone-designer config api-key` (현재 stub — 안내만)
- [ ] `phone-designer config mail` (현재 stub)
- [ ] `phone-designer validate` (DFM, Phase 5)
- [ ] `phone-designer reproduce` (Phase 3 후)
- [x] `phone-designer view` (단순 viewer)
- [x] `phone-designer screenshots` (PNG 다각도)
- [ ] `phone-designer inspect` (본격 GUI inspector) ← P0
- [ ] `phone-designer ui` (빈 GUI) ← P0

### 자동화 / Hooks
- [ ] commit 전 자동 `ensure_bom.ps1` (pre-commit hook)
- [ ] commit 전 자동 manifest export 검증
- [ ] manifest schema diff 알림 (LLM caching 안정성)

---

## 사용자가 직접 진행 가능한 검증 (현재 시점)

### 집 컴 (이 머신)
```powershell
.\venv\Scripts\Activate.ps1

# 모든 자동 test
pytest tests -v

# 직접 STEP 보기
python -m phone_designer view fixtures\simple_watch.step
python -m phone_designer view fixtures\simple_watch.step --second fixtures\simple_watch_housing_only.step

# 시나리오 실행 (PNG 저장)
python -m phone_designer test --scenario phase0_fixture_make
# → run_logs\<ts>\screenshots\ 의 PNG 확인

# Plan 실행 → STEP 출력
python -m phone_designer generate --plan plans\simple_watch_outer.yaml --out out\my_watch.step
python -m phone_designer view out\my_watch.step

# Phase 1 audit 보고
python -m phone_designer.skills.audit
# → docs\reports\history_rule_catalog.md
```

### 회사 컴 (사용자 셋업 후)
[[work-pc-tests#시나리오-실행-명령-cheat-sheet]] 참조.

---

## 이력

- 2026-05-25: backlog 신설. P0 inspector + LFS, P1 ~22 skill + Phase 3/5/6/7/8/9 전체 항목 매핑, P2 정리, P3 nice-to-have. 발견된 빠른 cleanup 11종 (chamfer TopAbs, OCP cast, TopTools_ListOfShape iter, TDataStd_Name 등) 은 done 으로 표시.
