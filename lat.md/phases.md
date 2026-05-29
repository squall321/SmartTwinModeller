# phases — Phase 별 계획

총 약 20주 (50% 병행 시 40주 ≈ 10개월). Rev 5 → Rev 6 의 +1.5-2주 는 [[dev-test|로깅/번들/시나리오 인프라]].

각 phase 의 자가검증 시나리오: [[dev-test#phase-별-시나리오-카탈로그]].

---

## phase-0 — Pre-flight + 환경 + 로깅 골격 (2주)

### Pre-flight 6건 (1.5주)
- [ ] [[persistent-naming|PF-1]] spec + PoC (1일)
- [ ] [[plan-determinism|PF-2]] spec + PoC (0.5일)
- [ ] [[reference#parasolid-워크플로|PF-3]] — 회사 컴에서 Parasolid → STEP 변환 + 정밀도 측정 (1일)
- [ ] [[decisions#PF-4]] PySide6 확정 (0.5일)
- [ ] [[decisions#PF-5]] atomic/macro 분류 규칙 (0.5일)
- [ ] [[decisions#PF-6]] iPhone glb 정밀도 측정 (집 컴, 0.5일)
- PF-7 은 spec 만 — 실제 spike 는 Phase 5 말

### 환경 (0.5주)
- [ ] `setup.ps1` 양쪽 컴에서 OK
- [ ] `python fixtures/make_simple_watch.py` → `simple_watch.step` 생성
- [ ] viewport PoC (PyVista + Qt 띄우고 STEP 로드 표시)
- [ ] **로깅 골격 (Rev 6 신규)**: `loguru` 기반 JSON Lines + viewport offscreen 캡처 기초
- [ ] **시나리오 러너 골격 (Rev 6)**: `python -m phone_designer test --scenario <name>` CLI 윤곽
- [ ] **메일 번들 골격 (Rev 6)**: SMTP wrapper + zip 번들러

### 자가검증 시나리오
- `phase0_env_smoke` — 모든 의존성 import + viewport 캡처 + 메일 전송 dry-run
- `phase0_fixture_make` — fixture STEP 생성 + 로드 + 부품 5개 네이밍 확인
- `phase0_pf_1_2_poc` — persistent naming + determinism PoC 실행

### 산출물
- **Spec 갱신** (lat.md/ 가 single source): [[persistent-naming]], [[plan-determinism]],
  [[decisions#PF-3]] / [[decisions#PF-4]] / [[decisions#PF-5]] / [[decisions#PF-6]],
  [[skills#atomic-vs-macro]], [[reference#parasolid-워크플로]],
  [[reference#mesh-정밀도]]
- **측정 보고서 (자동 생성, gitignore)** — `docs/reports/`:
  - `docs/reports/history_rule_catalog.md` (Phase 1 끝의 깨짐 catalog)
  - `docs/reports/cross_platform_determinism.md`
  - `docs/reports/iphone_mesh_precision.json`
  - `docs/reports/galaxy_watch_cad_precision.json` (회사 컴)
- **PoC 코드** (CI 회귀에 포함): [[../tests/poc/persistent_naming.py]], [[../tests/poc/determinism.py]]
- **합성 fixture STEP** (집 컴 자체 생성): `fixtures/simple_watch.step`
- **OEM CAD 변환물** (회사 컴 1회): `reference/galaxy_watch/converted.step`
- **코드 골격**: `src/phone_designer/logging/`, `src/phone_designer/scenarios/`

---

## phase-1 — Skill 프레임워크 + 첫 8 skill + 폭탄 A/B 검증 (2주)

### 작업
- [ ] `@skill` 데코레이터, SkillSpec (`level`, `history_rules` enum, named ref)
- [ ] EntityHistoryMap + SelectorFreeze 통합
- [ ] Selector 시스템 ([[skills#selectors]] 의 9 원자 + 조합)
- [ ] Manifest export (`python -m phone_designer.skills.export_manifest`)
- [ ] 첫 8 skill + 단위 테스트:
  1. `box` (atomic)
  2. `rounded_slab` (macro) — 폰 base
  3. `disc_with_dome` (macro) — 워치 base (Rev 5)
  4. `fillet_edges_by_predicate` (atomic)
  5. `chamfer_edges_by_predicate` (atomic)
  6. `extrude_pocket` (atomic)
  7. `extrude_plateau` (atomic)
  8. `hole` (atomic)

### 확장 검증 (마지막 3일)
- [ ] **시한 폭탄 A**: history propagate 7-8 skill 측정 → 70% 목표 catalog
- [ ] **시한 폭탄 B**: 동일 plan 5회 재실행 결정성 (same-machine)
- [ ] cross-platform 결정성 측정 (집 vs 회사 head-to-head)
- [ ] 깨짐 케이스 fallback chain 명시 + 사용자 경고 UX

### Go/No-Go
- propagate < 70% → PF-1 spec 갱신
- cross-platform 일치율 < 50% → default = loose 로 정책 변경

### 자가검증 시나리오
- `phase1_skill_smoke` — 8 skill 각각 single-step plan 실행
- `phase1_determinism` — 같은 plan 5회 → 결과 동일
- `phase1_history_catalog` — propagate 성공률 보고

---

## phase-2 — Plan executor + simple_watch reproduction + SpaceClaim sanity (1.5주)

### 작업
- [ ] Plan / Step + schema_version + YAML io
- [ ] PlanExecutor (cache, rollback, freeze check, 실패 semantics)
- [ ] Migration 골격 (v1 만)
- [ ] 수기 `plans/simple_watch_outer.yaml` 작성
- [ ] 실행 결과 vs `simple_watch_housing_only.step` face-level 비교
- [ ] **SpaceClaim STEP import sanity check** — 회사 컴 시나리오

### 자가검증 시나리오
- `phase2_simple_watch_repro` — 수기 plan 결과가 fixture 외피와 face count ±15%, volume ±5%
- `phase2_spaceclaim_sanity` (회사) — exported.step 이 SpaceClaim 에서 정상 로드
- `phase2_oem_watch_repro` (회사) — Galaxy Watch OEM 외피와 face-level 비교 (수기 plan)

---

## phase-3 — STEP topology + reverse engineering (1.5주)

### 작업
- [ ] `reference.step_reader` — XDE 어셈블리 + 네이밍
- [ ] `reference.topology_analyzer` — face/edge → FeatureCatalog
- [ ] `reference.feature_to_plan` — catalog → plan 초안
- [ ] Naming rule 매칭 + 부품 자동 분류
- [ ] 자동 plan vs Phase 2 수기 plan 비교

### 자가검증 시나리오
- `phase3_topology_analysis` — fixture STEP 던지면 fillet/chamfer/pocket/hole 검출
- `phase3_reverse_engineer` — fixture STEP → 자동 plan → 재현 → metric 통과
- `phase3_oem_reverse_engineer` (회사) — OEM Galaxy Watch CAD → 자동 plan + 부품 분류 80%↑

---

## phase-4 — UI + 나머지 ~26 skill (3주, 분리 진행)

### 작업 (1.5주 + 1.5주, 또는 2인 시 병행 2주)

**Skill 확장** (1.5주):
- polynomial_pocket, variable_radius_fillet, loft_side_profile, swept_relief, surface_offset
- extrude_through, grille_pattern (macro)
- boss_with_hole (macro), rib, snap_hook, mounting_pad
- **워치 특화 (Rev 5)**: crown_shaft_hole (macro), lug_pair, o_ring_groove
- antenna_slit, polymer_inlay
- subtract, union, tag_face, import_step
- final_fillet_all_sharp_edges

**UI** (1.5주):
- PySide6 main window + 5 panel
- Picking → selector 자동 제안
- Undo/Redo 두 stack
- i18n (KO/EN PO 파일)
- Error mapping (OCCT cryptic → 친절)
- Test 메뉴 + 시나리오 러너 GUI

### 자가검증 시나리오
- `phase4_ui_smoke` — 모든 panel 그리기 (offscreen 캡처)
- `phase4_skill_full_catalog` — 26 skill 각각 single-step plan 통과
- `phase4_picking_to_selector` — 자동 selector 제안 정확성

---

## phase-5 — DFM + PF-7 spike (3주)

### 기본 작업 (2주)
- [ ] `manufacturing.processes` 5종 YAML
- [ ] 모든 skill 의 `manufacturing` 메타데이터
- [ ] DFM v0: wall_thickness_raymarch + draft + undercut
- [ ] DFM Report panel + viewport highlight
- [ ] ManufacturingBudget UI
- [ ] LLM caching breakpoint A 활성화 (수동 toggle)

### PF-7 spike (1주, Phase 5 말)
- [ ] voxel 1/2/5mm 측정
- [ ] 백업안 (Delaunay / skeleton) 비교
- [ ] Phase 6 알고리즘 결정

### 자가검증 시나리오
- `phase5_dfm_smoke` — 의도적 위반 body (얇은 벽, no draft, undercut) → 모두 검출
- `phase5_dfm_oem` (회사) — Galaxy Watch OEM 외피의 DFM 검증
- `phase5_pf7_spike` — 3가지 알고리즘 측정 보고

---

## phase-6 — Component 카탈로그 + rule-based 합성 (PF-7 결과별)

### 분기
- voxel + greedy: **3주**
- 백업안 (Delaunay/skeleton): **2.5주**
- scope 축소 (ribbing 제외): **2주**

### 작업
- [ ] Component 모델 + YAML 카탈로그 로더
- [ ] OEM CAD 자동 추출 → catalogs/extracted/
- [ ] 초기 카탈로그 ([[components#카탈로그-디렉토리]]):
  - watch: displays 2종, batteries 2, crowns, coils, speakers, sensors
- [ ] Component palette + drag/place + OBB 충돌 검사
- [ ] `housing_synth_rule` 알고리즘 구현
- [ ] 부품 5-6개 배치 → 자동 housing → 시각화

### 자가검증 시나리오
- `phase6_synth_smoke` — 합성 부품 배치 → 충돌 없음 + DFM 통과
- `phase6_synth_simple_watch` — fixture 부품 → 합성 결과 vs fixture 외피 metric
- `phase6_oem_compare` (회사) — OEM 부품 배치 → 합성 외피 vs OEM 외피 face-level

---

## phase-7 — LLM Editor + 비용 dry-run (1.5주)

### 비용 dry-run (0.5주)
- 5-10 시나리오 real LLM 호출 측정
- cache hit 율 측정
- 한도 ($5/세션) 재조정
- mock response golden fixture 생성

### Editor 구현 (1주)
- `llm.client` (Anthropic SDK + caching)
- `llm.tools` (manifest → tool schema)
- `planner.editor` (자연어 → plan diff)
- UI Chat panel
- API key UX (keyring)
- 오프라인 graceful degrade

### 자가검증 시나리오
- `phase7_llm_editor` (mock) — 12 시나리오 응답 일관성
- `phase7_cost_dryrun` (real LLM) — $/step 실측 + cache hit
- `phase7_offline_mode` — `ANTHROPIC_API_KEY` 미설정 시 UI 정상 동작

---

## phase-8 — LLM Planner agentic + 객관 metric (3주)

### 작업
- 객관 metric 측정 infra (0.5주)
- agentic loop happy path (1주)
- 실패 모드별 회귀 + ban list 등 (1주)
- nightly LLM CI 셋업 (0.5주)

### 객관 metric (Rev 5)
- (a) DFM 위반율
- (b) plan 길이
- (c) volume
- (d) 결정성
- (e) **OEM 외피와의 face count / volume / bbox 일치도**

### 자가검증 시나리오
- `phase8_llm_planner` (mock) — agentic loop 정상 종료
- `phase8_objective_metric` (회사, real LLM) — OEM 부품 → 합성 결과 metric 보고

---

## phase-9 — iPhone mesh pipeline 일반화 + E2E (1.5주)

### 작업
- [ ] `mesh_io.loaders / analyzer / measure` 구현
- [ ] iPhone 12 glb → 자동 plan
- [ ] mesh 경로 정확도 한계 명시
- [ ] E2E 시나리오 (집/회사 양쪽)

### 자가검증 시나리오
- `phase9_mesh_pipeline` — iPhone glb → 자동 plan → 재현 → Hausdorff ≤ 2.0mm
- `phase9_e2e` (회사) — 전체 파이프라인 + ANSYS 메쉬 (수동 단계 포함)

---

## 일정 요약

| Phase | 기간 | 누적 |
|---|---|---|
| 0 | 2주 | 2주 |
| 1 | 2주 | 4주 |
| 2 | 1.5주 | 5.5주 |
| 3 | 1.5주 | 7주 |
| 4 | 3주 | 10주 |
| 5 | 3주 | 13주 |
| 6 | 2-3주 | 15-16주 |
| 7 | 1.5주 | 16.5-17.5주 |
| 8 | 3주 | 19.5-20.5주 |
| 9 | 1.5주 | **21-22주** |

50% 병행 시 약 42-44주 (≈ 10개월).
