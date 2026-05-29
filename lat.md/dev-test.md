# dev-test — 개발/테스트 워크플로 (Rev 6 핵심)

## 환경 분리

| | 집 컴 (Dev) | 회사 컴 (Test) |
|---|---|---|
| OS | Windows 10/11 | Windows |
| Python | 3.11+ (Phase 0 셋업) | 3.11+ (Phase 0 셋업, 1회) |
| Claude 사용량 | 풍부 | 적음 |
| 대화형 디버깅 | 가능 (사용자 직접) | **불가** (메일 피드백만) |
| OEM Galaxy Watch Parasolid | **없음** | 있음 |
| SpaceClaim | 없음 | 있음 |
| ANSYS Mechanical | 없음 | 있음 |
| Reference 자산 | `iphone/iphone_12_teardown.glb`, `fixtures/simple_watch.step` (합성) | OEM `.x_t` → 변환된 STEP |
| 역할 | 코드 작성 + 자체 검증 + plan | 실제 OEM CAD 로 final sanity |

### 사이클

```
[집 컴]                                  [회사 컴]
1. 코드/문서 작성
2. 자체 시나리오로 검증 (simple_watch.step)
3. git push  ──────────────────────▶  4. git pull
                                       5. python -m phone_designer test \
                                              --scenario <name>
                                       6. 자동 zip 번들 (log + PNG + STEP)
                                       7. Send Report 버튼 → SMTP
8. 메일 수신 (gmail/outlook) ◀────────  ← 회사 → 집 메일
9. zip 첨부 → Claude 채팅에 drop
10. 분석 → 코드 수정 → 1로 복귀
```

**사이클 latency**: 반나절~하루. 따라서:

- **집 컴에서 자체 검증 가능한 범위를 최대화**. 회사 컴 호출 횟수 최소화.
- 회사 컴 단일 실행이 충분히 정보 풍부해야 → 풍부한 로그 + 다각 viewport snapshot.

### 회사 → 집 메일에 들어가는 것

[[#자동-번들-메일]] 참조. 매 시나리오 1회당:
- `log.jsonl` (구조화 로그, 모든 step + DFM + LLM 호출)
- `plan_final.yaml` + `plan_history.yaml` (각 step freeze + history)
- `screenshots/` (PNG, step 별 iso/top/side + DFM 위반 highlight)
- `exported.step` (결과)
- `system_info.txt` (OCCT/Python/PySide 버전)
- `error_report.md` (있다면, 매핑된 친절 에러 + 원본 stacktrace)

zip 크기 < 30MB 목표 (메일 첨부 한도 대응).

---

## 로깅 시스템

[[src/phone_designer/logging/structured.py]] 가 핵심.

### 포맷: JSON Lines

매 줄 1 JSON 객체. `log.jsonl` 파일에 append.

```json
{"ts":"2026-05-25T14:32:01.142Z","level":"INFO","phase":"execute_step","step_id":"s3","skill":"fillet_edges_by_predicate","args":{"radius_mm":0.5,"selector":{"kind":"axis_aligned_edges","axis":"Z"}},"duration_ms":142,"freeze":{"matched_count":4,"signature":"abc12345"},"history":{"target_edges":"consumed","result_face":"generated_new"}}
{"ts":"2026-05-25T14:32:01.355Z","level":"WARNING","phase":"dfm_validate","violation":"wall_thickness","min_mm":0.8,"required_mm":1.0,"position":[12.3,-5.1,0.4],"process":"die_cast_al"}
{"ts":"2026-05-25T14:32:02.001Z","level":"ERROR","phase":"execute_step","step_id":"s7","skill":"extrude_pocket","mapped_error":"Pocket depth exceeds local body thickness — adjust depth or position.","raw_error":"BRepFeat_MakePrism: invalid parameter","traceback":"..."}
```

### 레벨

| 레벨 | 사용 |
|---|---|
| DEBUG | OCCT 내부 호출, history map 세부 propagation, freeze 계산 중간값 |
| INFO | step 시작/종료, executor cache hit/miss, freeze 일치 |
| WARNING | DFM 위반, fallback chain 사용, mesh 측정 임계 도달, LLM tool 인자 clip |
| ERROR | skill 실패 (매핑된 메시지 + 원본), plan validation 실패, LLM tool schema 거부 |

### 회사 컴 모드

회사 컴에서는 자동으로 **DEBUG 레벨까지 기록**, 콘솔 출력은 INFO 이상.
이렇게 해야 1회 실행으로 디버깅 정보 충분히 모임.

```python
# src/phone_designer/logging/structured.py
def configure_for_test_environment():
    """회사 컴에서 호출. JSON Lines DEBUG 파일 + INFO 콘솔."""
    logger.add("run_logs/{time}/log.jsonl", level="DEBUG", serialize=True)
    logger.add(sys.stderr, level="INFO")
```

### Viewport snapshot 자동 캡처

매 step 실행 후 4-view (iso / top / side / front) PNG 저장:

```
run_logs/20260525_143200/screenshots/
├── step_001_box_iso.png
├── step_001_box_top.png
├── step_001_box_side.png
├── step_001_box_front.png
├── step_002_fillet_iso.png
├── ...
├── final_iso.png
├── dfm_violations.png       # DFM 위반 위치 highlight
└── compared_vs_reference.png  # 있다면, reference 와 overlay
```

DFM 위반 캡처: 위반 face 빨간색, 정상 회색, reference 가 있으면 wireframe 으로.

[[src/phone_designer/logging/viewport_capture.py]] 가 PyVista plotter offscreen 모드로 캡처.

---

## 시나리오 러너

### CLI

```powershell
# 회사 컴에서 사용자가 1-커맨드로 실행
python -m phone_designer test --scenario <name> [--input <step_or_glb>]

# 결과: run_logs/<timestamp>/ 디렉토리에 모든 산출물
# Send Report 시 위 디렉토리가 zip 됨
```

대안: PySide6 UI 의 메뉴 `Test → Run Scenario` 에서 드롭다운 선택.

### 시나리오 = 하나의 reproducible test

```python
# src/phone_designer/scenarios/<name>.py
class Scenario(BaseModel):
    name: str
    description: str
    inputs: list[InputSpec]            # 입력 STEP/glb/component 배치
    steps: list[ScenarioStep]          # 실행 동작 (load, run plan, synthesize, validate, ...)
    expected: ExpectedSpec             # face count, volume, DFM 위반 여부, ...
    capture_screenshots: bool = True
```

러너는 시나리오 정의를 읽고 자동 실행, 로그/PNG 캡처, **expected 와 actual 비교 → pass/fail**,
zip 번들 준비.

### Phase 별 시나리오 카탈로그

회사 컴에서 사용자가 1회 클릭으로 실행할 수 있게.

| Phase | 시나리오 | 입력 | 검증 |
|---|---|---|---|
| 0 | `phase0_env_smoke` | 없음 | 모든 의존성 import OK + viewport 1프레임 캡처 |
| 0 | `phase0_fixture_make` | 없음 | `make_simple_watch.py` 실행 → `simple_watch.step` 생성 + 로드 검증 |
| 1 | `phase1_skill_smoke` | `simple_watch.step` | 첫 8 skill 각각 단일 step plan 으로 실행, history map 정합성 |
| 1 | `phase1_determinism` | 같은 plan 5회 | freeze signature 동일 |
| 1 | `phase1_history_catalog` | 8 skill | history rule propagate 성공률 보고 |
| 2 | `phase2_simple_watch_repro` | simple_watch fixture | 수기 plan 으로 fixture 외피 재현 + face count ±10% |
| 2 | `phase2_spaceclaim_sanity` | exported.step | (회사 컴) STEP → SpaceClaim import OK |
| 2 | `phase2_oem_watch_repro` | (회사) Galaxy Watch STEP | OEM 외피 face-level 비교 |
| 3 | `phase3_topology_analysis` | simple_watch fixture / OEM | feature catalog (fillet, chamfer, ...) 검출 |
| 3 | `phase3_reverse_engineer` | simple_watch fixture / OEM | 자동 plan 생성 + 재현 |
| 4 | `phase4_ui_smoke` | 없음 | UI 띄우고 모든 panel 그리기 (offscreen 캡처) |
| 4 | `phase4_skill_full_catalog` | various | 26 skill 각각 실행 |
| 5 | `phase5_dfm_smoke` | 의도적 위반 body | wall thickness / draft / undercut 모두 검출 |
| 5 | `phase5_pf7_spike` | simple_watch 내부 부피 | voxel/Delaunay/skeleton 3가지 측정 + 비교 |
| 6 | `phase6_synth_smoke` | 합성 부품 배치 | housing 합성 + 충돌 없음 + DFM 통과 |
| 6 | `phase6_oem_compare` | (회사) OEM 부품 배치 | 합성 vs OEM 외피 face-level |
| 7 | `phase7_llm_editor` | mock LLM | 12 시나리오 응답 일관 |
| 7 | `phase7_cost_dryrun` | (선택, real LLM) | $/step 측정 + cache hit |
| 8 | `phase8_llm_planner` | mock LLM | agentic loop 정상 종료 |
| 8 | `phase8_objective_metric` | (회사) OEM 부품 | DFM 위반율 / plan 길이 / volume |
| 9 | `phase9_mesh_pipeline` | iPhone glb | mesh 측정 + reverse engineer |

각 시나리오는 회사 컴 한 번 실행으로 충분한 정보를 메일에 담는다.

### 시나리오 정의 예

```yaml
# scenarios/phase2_simple_watch_repro.yaml
name: phase2_simple_watch_repro
description: |
  simple_watch.step (합성 reference) 의 외피만 수기 plan 으로 재현.
  face count / volume / bbox 비교.
inputs:
  - kind: step
    path: fixtures/simple_watch.step
steps:
  - load_step:    input: 0,  as_name: ref
  - extract_part: from: ref, name: housing, as_name: ref_housing
  - load_plan:    path: plans/simple_watch_outer.yaml, as_name: plan
  - execute_plan: plan: plan, as_name: result
  - capture_screenshot: subject: result, views: [iso, top, side, front]
  - compare:      a: result, b: ref_housing
expected:
  face_count_diff_pct: {max: 10}
  edge_count_diff_pct: {max: 10}
  volume_diff_pct: {max: 2}
  bbox_diff_mm: {max: 0.5}
```

---

## 자동 번들 + 메일

### 번들 형식

`run_logs/<timestamp>/` 디렉토리를 zip:

```
<scenario_name>_<timestamp>.zip
├── meta.json                    # 시나리오명, OS, Python, OCCT 버전, 결과 (pass/fail)
├── log.jsonl
├── plan_final.yaml
├── plan_history.yaml
├── scenario_definition.yaml
├── screenshots/                 # PNG 들
├── exported.step                # 결과 STEP (있다면)
├── exported.gltf                # 결과 glTF (web viewer)
├── system_info.txt
└── error_report.md              # 실패 시 매핑 + 원본 stacktrace
```

[[src/phone_designer/logging/bundle.py]] 가 디렉토리 → zip.

### 메일 전송

[[src/phone_designer/logging/mailer.py]]:

- **gmail SMTP** (앱 비밀번호 필요) 또는 **outlook SMTP**
- 첫 실행 시 Settings → "Test mail" 다이얼로그: 송신자/수신자/SMTP 서버/포트/비밀번호
- 비밀번호는 OS keyring 에 저장 ([[llm#api-key]] 와 같은 메커니즘)
- 본문 = 1줄 요약 (PASS/FAIL + 시나리오명 + 시각) + meta.json 의 핵심 필드 평문
- 첨부 = 위 zip

### Send Report UX

회사 컴 PySide6 UI 또는 CLI `--mail` 플래그:

```powershell
python -m phone_designer test --scenario phase2_simple_watch_repro --mail
```

CLI 가 메일 전송까지 자동 수행. UI 에서는 시나리오 완료 후 dialog: "Send report?" → Send.

zip 크기 한계 (gmail 25MB) 초과 시:
- PNG 압축률 ↑
- DEBUG → INFO 로 다운샘플
- 또는 분할 첨부

---

## fixtures

### 합성 워치 STEP 생성 (집 컴에서 OEM CAD 없이도 파이프라인 검증)

`fixtures/make_simple_watch.py` — build123d 로 단순화된 워치 형상 + 부품 어셈블리 생성.

**부품 구성** (Galaxy Watch 흉내, 단순화):
1. `housing` — 원형 disc + 상부 dome + corner fillet (외피)
2. `display` — 납작한 원형 디스크 (OLED placeholder)
3. `battery` — 원형 디스크 (배터리 placeholder)
4. `crown` — 원기둥 + 측면 부착 위치
5. `lug_pair` — 양 측면 스트랩 마운트 (사각 + hole)

각 부품은 STEP XDE 어셈블리의 label 로 네이밍 보존. 어셈블리 트리 + pose 유지.

**용도**:
- §5 reference 파이프라인 (CAD 처리, 부품 분류, reverse engineer) 의 검증 입력
- Phase 2 reproduction 시나리오의 reference
- Phase 6 합성 시나리오의 부품 배치 입력

자세한 생성 절차: [[#fixture-사용]].

### fixture 사용

```powershell
# 집 컴 / 회사 컴 둘 다 (Python venv 활성화 후)
python fixtures/make_simple_watch.py

# 출력
# → fixtures/simple_watch.step  (XDE 어셈블리 + 5 부품 + 네이밍)
# → fixtures/simple_watch_housing_only.step  (외피만)
# → fixtures/simple_watch_components/*.step  (부품별 분리)
```

생성 후:
- `python -m phone_designer test --scenario phase0_fixture_make` 로 fixture 로드 검증
- 이후 시나리오들이 fixture 를 입력으로 사용

### 회사 컴 vs 집 컴

| 시나리오 | 집 컴 입력 | 회사 컴 입력 |
|---|---|---|
| `phase2_simple_watch_repro` | `fixtures/simple_watch.step` | `fixtures/simple_watch.step` (동일) |
| `phase2_oem_watch_repro` | (실행 불가, skip) | `reference/galaxy_watch/converted.step` |
| `phase3_topology_analysis` | `fixtures/simple_watch.step` | `fixtures/simple_watch.step` + `reference/galaxy_watch/converted.step` |

→ 회사 컴은 **fixture 시나리오 + OEM 시나리오 양쪽** 실행. 집 컴은 fixture 만으로 모든 알고리즘 검증.

---

## 단위 테스트

`pytest tests/` — 집 컴에서 매 commit 마다.

```
tests/
├── poc/
│   ├── persistent_naming.py     # PF-1
│   └── determinism.py            # PF-2
├── skills/
│   ├── test_box.py
│   ├── test_rounded_slab.py
│   ├── ...
├── plan/
│   ├── test_executor.py
│   ├── test_freeze.py
│   └── test_schema_migration.py
├── reference/
│   ├── test_step_reader.py       # XDE 어셈블리 + 네이밍
│   └── test_topology_analyzer.py
├── components/
│   └── test_collision.py
├── manufacturing/
│   └── test_dfm_wall_thickness.py
├── llm/
│   └── test_tools_from_manifest.py
└── scenarios/
    ├── test_scenario_runner.py
    └── golden_responses/         # CI mock LLM 응답
```

## 시각 회귀

vertex hash 금지 (cross-platform 깨짐). 대신:

- face count
- edge count
- volume (build123d.Volume)
- bbox (XYZ 각 축의 min/max)
- **반복 실행 5회 동일성** (freeze)

각 plan 의 baseline 을 `tests/visual_baseline/<plan_name>.json` 에 commit.

## face-level 회귀 (Rev 5 이후)

Reference STEP (fixture 또는 OEM) 과 결과 STEP 의:

| Metric | 임계값 (fixture) | 임계값 (OEM) |
|---|---|---|
| face count diff | ±15% | ±10% |
| edge count diff | ±15% | ±10% |
| volume diff | ±5% | ±2% |
| bbox diff | ±1.0mm | ±0.5mm |
| 주요 feature 위치 | ±0.5mm | ±0.3mm |

OEM 비교는 회사 컴 시나리오에서만 실행, fixture 비교는 집/회사 양쪽.

## LLM 회귀

- **CI (집 컴)**: mock LLM + golden response 비교. 결정성 회귀만.
- **Nightly (집 컴, 또는 수동)**: real LLM 12 시나리오, 비용 monitor.
- **수동 회귀**: 릴리즈 직전 real LLM 전체.

## cross-platform 결정성 회귀

Phase 1 끝에서 측정한 baseline 을 commit. CI 가 양쪽 OS (가능하면) 또는 single OS 에서
freeze 일치율 모니터. 50% 미만 떨어지면 plan determinism 정책 재검토 ([[plan-determinism#실행-모드]]).
