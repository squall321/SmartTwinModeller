# work-pc-tests — 회사 컴 전용 검증 항목

> 집 컴에서는 시뮬레이션 / SKIP 처리되거나 합성 fixture 로 대체되는 항목들.
> 회사 컴에서 git pull 후 1-실행 + 메일 보고 형태로 검증한다.
>
> 각 항목은 **메일 1회로 충분한 정보** 가 와야 함 — log/screenshot/zip 자동 번들 ([[dev-test#자동-번들-메일]]).

## 실행 환경

회사 컴 1회 셋업: [[setup#회사-컴-1회-셋업]].

### 사전 주의 사항 (집 컴 검증에서 발견)

| 항목 | 상태 | 회사 컴 영향 |
|---|---|---|
| **setup.ps1 인코딩** | UTF-8 + BOM 필수 (한글 주석) | BOM 없으면 PS 5.1 파서 깨짐. git 으로 받으면 정상 (BOM 보존) |
| **Python 3.13 OK** | 집 컴 3.13.7 검증 | cadquery-ocp 0.10 wheel 가용 |
| **build123d 0.10** | 0.10.0 검증 | API 안정 |
| **Pytest 파일명** | `test_*.py` 필수 | 이미 적용됨 |
| **PoC fillet API** | OCP `MakeFillet.Add(R, edge)` OK | 검증됨 |
| **chamfer API** | OCP `MakeChamfer.Add(d1, d2, edge, face)` (양쪽 거리) | 검증됨 |
| **TopExp_Explorer 결과** | TopoDS_Shape — `TopoDS.Edge_s/Face_s` cast 필수 | 검증됨 |
| **Sub-shape 중복** | `TopTools_MapOfShape` 로 dedupe | 검증됨 |
| **TopTools_ListOfShape iter** | STL iter 미노출 — destructive `Assign + RemoveFirst` 사용 | 검증됨 |
| **TDataStd_Name 추출** | `Get_s` 안 됨 — `GetID_s()` + `FindAttribute()` 사용 | 검증됨 |
| **YAML save_plan** | `model_dump(mode='json')` + `safe_dump` | 검증됨 |
| **viewport offscreen** | PyVista off_screen + VTK 9.6 OK | 검증됨 |

필요 자산:
- SpaceClaim (Parasolid → STEP 변환)
- (선택) ANSYS Mechanical
- `reference/galaxy_watch/original.x_t` — 사용자 보유 OEM Parasolid
- `reference/galaxy_watch/converted.step` — SpaceClaim 으로 1회 변환한 STEP (AP242, XDE)
- 메일 SMTP 자격증명 ([[llm#api-key]] 와 동일 keyring 또는 환경변수)

## 검증 카탈로그

체크박스: 회사 컴에서 한 번이라도 PASS 면 ✓ 체크. 일정 회귀 후 fail 시 ✗ 로 변경 + 사유 메모.

### Phase 0 — 환경

| # | 항목 | 시나리오/명령 | 기대 |
|---|---|---|---|
| 0.1 | [ ] Python 3.11+ + venv 생성 | `setup.ps1` | smoke import 성공 |
| 0.2 | [ ] OCP / build123d / PySide6 / pyvistaqt import | `python -m phone_designer test --scenario phase0_env_smoke --mail` | run_logs zip + 첫 메일 |
| 0.3 | [ ] 메일 SMTP 전송 dry-run | (위 동일, --mail) | 집 컴 메일함에 zip 도착 |
| 0.4 | [ ] 합성 fixture STEP 생성 | `python -m phone_designer test --scenario phase0_fixture_make --mail` | 5 부품 어셈블리 + 네이밍 보존 |
| 0.5 | [ ] **PF-3 manual**: Parasolid → STEP 변환 | SpaceClaim 으로 .x_t 열고 AP242 STEP export | `reference/galaxy_watch/converted.step` |
| 0.6 | [ ] PF-3 변환 결과의 부품 네이밍 확인 | `python -c "..."` 또는 (Phase 2 의 read_xde_step 시나리오) | 어셈블리 트리 + 부품 ≥ 5개 |

### Phase 1 — Skill + Pre-flight 검증

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 1.1 | [ ] 8 skill 단위 테스트 통과 | `pytest tests/skills -v` | ALL PASS |
| 1.2 | [ ] manifest export | `python -m phone_designer.skills.export_manifest --out manifest.json` | 8 skill + selectors |
| 1.3 | [ ] **PF-1 PoC** persistent naming | `pytest tests/poc/persistent_naming.py -v` | toroidal face 추적 OK |
| 1.4 | [ ] **PF-1 확장 검증** — history rule catalog | `python -m phone_designer.skills.audit` | docs/reports/history_rule_catalog.md, **propagate ≥ 70%** |
| 1.5 | [ ] **PF-2 PoC** determinism | `pytest tests/poc/determinism.py -v` | 5회 재실행 일치 |
| 1.6 | [ ] **PF-2 확장** — cross-platform freeze 비교 | 회사 컴 audit 결과를 집 컴 audit 결과와 비교 | freeze signature 일치율 50%+ |
| 1.7 | [ ] phase1_skill_smoke 시나리오 | `python -m phone_designer test --scenario phase1_skill_smoke --mail` | manifest 생성 + pytest 통과 |

### Phase 2 — Plan executor

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 2.1 | [ ] plan 단위 테스트 | `pytest tests/plan -v` | ALL PASS |
| 2.2 | [ ] simple_watch reproduction (fixture vs plan 결과) | `phase2_simple_watch_repro` | compare metric 통과 |
| 2.3 | [ ] **SpaceClaim STEP import 호환** (수동) | `phase2_spaceclaim_sanity` → zip 받은 후 SpaceClaim 에서 import | 정상 로드 + face tree 표시 |
| 2.4 | [ ] OEM Galaxy Watch 어셈블리 트리 추출 | `phase2_oem_watch_repro` | 부품 ≥ 5 + housing 분류 |
| 2.5 | [ ] OEM 외피 face-level 비교 (수기 plan, Phase 4 후) | (TBD plans/galaxy_watch_outer.yaml 작성 후) | face_count ±10%, volume ±2% |

### Phase 3 — Reverse engineer

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 3.1 | [ ] STEP topology analyzer | `pytest tests/reference -v` (Phase 3 작업) | feature catalog 검출 |
| 3.2 | [ ] 자동 plan 생성 (fixture) | `phase3_reverse_engineer_simple_watch` | 자동 plan vs 수기 plan 유사 |
| 3.3 | [ ] **OEM CAD reverse engineer** | `phase3_reverse_engineer_oem` | 자동 plan 생성 + 부품 분류 80%+ |

### Phase 5 — DFM

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 5.1 | [ ] DFM 위반 검출 (의도적 위반 body) | `phase5_dfm_smoke` | wall/draft/undercut 검출 |
| 5.2 | [ ] OEM 외피의 DFM 보고 | `phase5_dfm_oem` (requires_oem) | 위반 highlight + 사람 검토 가능 수준 |

### Phase 6 — Composition

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 6.1 | [ ] 합성 부품 배치 → housing | `phase6_synth_simple_watch` | 충돌 없음 + DFM 통과 |
| 6.2 | [ ] **OEM 부품 배치 → housing 합성 → OEM 외피와 face-level 비교** | `phase6_oem_compare` (requires_oem) | metric 통과 |

### Phase 7 — LLM Editor

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 7.1 | [ ] mock LLM 12 시나리오 회귀 | `phase7_llm_editor_mock` | golden response 일치 |
| 7.2 | [ ] **real LLM dry-run** ($ 사용) | `phase7_cost_dryrun` | $/step + cache hit 율 측정 |
| 7.3 | [ ] 오프라인 모드 graceful degrade | `phase7_offline_mode` | Chat disabled, 나머지 정상 |
| 7.4 | [ ] API key keyring 저장 | (manual) `phone-designer config api-key` | OS keyring 에 저장 + 재시작 후 로드 |

### Phase 8 — LLM Planner agentic

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 8.1 | [ ] mock LLM planner | `phase8_llm_planner_mock` | agentic loop 정상 종료 |
| 8.2 | [ ] **real LLM 객관 metric** | `phase8_objective_metric` (requires_oem + real LLM) | DFM 위반율 / plan 길이 / volume / 결정성 / OEM face-level 5종 |
| 8.3 | [ ] nightly CI 비용 monitor | (자동) | 12 시나리오 × 비용 < 한도 |

### Phase 9 — Mesh pipeline + E2E

| # | 항목 | 시나리오 | 기대 |
|---|---|---|---|
| 9.1 | [ ] iPhone 12 glb → mesh pipeline | `phase9_mesh_pipeline` | Hausdorff ≤ 2.0mm |
| 9.2 | [ ] **STEP → ANSYS 메쉬 (수동)** | (manual) Phase 2 의 STEP 을 ANSYS 에서 import + 메쉬 생성 | 메쉬 generate 성공, 사람 시각 확인 |

## 시나리오 실행 명령 cheat sheet

```powershell
# 환경
.\setup.ps1
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:PHONE_DESIGNER_SMTP_HOST = "smtp.gmail.com"
$env:PHONE_DESIGNER_SMTP_PORT = "587"
$env:PHONE_DESIGNER_SMTP_USER = "you@gmail.com"
$env:PHONE_DESIGNER_SMTP_PASS = "<app password>"
$env:PHONE_DESIGNER_MAIL_TO   = "home@example.com"

# Phase 0
python -m phone_designer test --scenario phase0_env_smoke --mail
python -m phone_designer test --scenario phase0_fixture_make --mail

# Phase 1
pytest tests/skills -v
python -m phone_designer.skills.audit
python -m phone_designer test --scenario phase1_skill_smoke --mail

# Phase 2
pytest tests/plan -v
python -m phone_designer test --scenario phase2_simple_watch_repro --mail
python -m phone_designer test --scenario phase2_spaceclaim_sanity --mail
python -m phone_designer test --scenario phase2_oem_watch_repro --mail

# 추후 phase 들 동일 패턴
```

## 회신 형식

회사 컴에서 시나리오 실행 후 메일이 집 컴에 도착하면:
1. 사용자가 메일 zip 첨부를 다운로드
2. Claude 채팅에 zip 을 첨부 (drag/drop)
3. Claude 가 zip 분석 → 다음 수정 사항 도출
4. 변경 commit → 회사 컴 git pull → 재실행

사이클 latency: 반나절~하루. **집 컴 자체 검증 최대화** 가 핵심 (대부분 fixture 만으로 충분).

## 미정/추후

- PF-3 의 Parasolid 변환 자동화 (SpaceClaim Python automation 또는 OpenCascade reader) — v0.2
- 사용자가 simple watch CAD 를 SpaceClaim 으로 별도 제작 → fixture 보다 정밀한 회사 컴 reference 로 사용 (선택)
- ANSYS 메쉬 자동화 (현재는 수동) — Phase 후반
