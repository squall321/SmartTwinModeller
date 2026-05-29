# architecture — 시스템 레이어 + 데이터 흐름

## 레이어 다이어그램

```
┌────────────────────────────────────────────────────────────────┐
│  Desktop UI  (PySide6 + pyvistaqt) — [[ui]]                    │
│   - VTK/GPU viewport (large model OK)                          │
│   - Component palette / placement gizmos                       │
│   - Plan editor (step 카드, drag-reorder, freeze 상태 표시)    │
│   - Chat panel (LLM)                                           │
│   - DFM warnings overlay                                       │
│   - Test 메뉴 → 시나리오 러너                                  │
└──────────────────────────┬─────────────────────────────────────┘
                           │ in-process function calls
                           ▼
┌────────────────────────────────────────────────────────────────┐
│  Application Core                                              │
│                                                                │
│   ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│   │  Planner     │  │  Plan Executor │  │  DFM Validator   │  │
│   │  - rule v0   │──▶│  - cache       │──▶│  - per process  │  │
│   │  - LLM v1    │  │  - rollback    │  │  - per skill    │  │
│   │  - editor    │  │  - freeze check│  │  - ray-march WT │  │
│   └──────┬───────┘  └────────┬───────┘  └────────┬─────────┘  │
│          │                   │                   │            │
│          ▼                   ▼                   ▼            │
│   ┌──────────────────────────────────────────────────────┐    │
│   │   Skill Registry (Manifest) — [[skills]]             │    │
│   │     atomic + macro + selectors + components +        │    │
│   │     processes                                        │    │
│   └────────────────────────┬─────────────────────────────┘    │
│                            ▼                                  │
│   ┌──────────────────────────────────────────────────────┐    │
│   │   CAD Kernel Adapter  (build123d / OCCT)             │    │
│   │   + EntityHistoryMap (PF-1)                          │    │
│   │   Mesh Analysis       (trimesh + scipy)              │    │
│   │   Tessellator         (OCCT → PolyData → VTK)        │    │
│   └──────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────┘
                           │
   ┌───────────────┬───────┴───────┬───────────────────┐
   ▼               ▼               ▼                   ▼
┌─────────┐  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐
│ Claude  │  │ STEP/glb │  │ Reference    │  │ Logging /       │
│ API     │  │ export   │  │ meshes/CAD   │  │ Bundle / Mail   │
│         │  │ (→ ANSYS)│  │              │  │ — [[dev-test]]  │
└─────────┘  └──────────┘  └──────────────┘  └─────────────────┘
```

## 모듈 매핑

| 레이어 | 모듈 |
|---|---|
| UI | `src/phone_designer/ui/` |
| Planner | `src/phone_designer/planner/` |
| Plan executor | `src/phone_designer/plan/` |
| DFM | `src/phone_designer/manufacturing/dfm/` |
| Skills | `src/phone_designer/skills/` |
| Selectors | `src/phone_designer/skills/_selectors.py` |
| Manifest | `src/phone_designer/skills/export_manifest.py` |
| CAD Adapter | (build123d 직접 + 부분적 OCP 직호출, history map 은 별도 `_history.py`) |
| Mesh I/O | `src/phone_designer/mesh_io/` |
| Reference (CAD) | `src/phone_designer/reference/` |
| Tessellator | `src/phone_designer/viz/tessellate.py` |
| LLM | `src/phone_designer/llm/` |
| Export | `src/phone_designer/export/` |
| Logging | `src/phone_designer/logging/` |
| Scenarios | `src/phone_designer/scenarios/` |

## 데이터 흐름 — 시나리오 별

### A. Galaxy Watch reproduction (회사 컴, Phase 3)

```
reference/galaxy_watch/original.x_t
  │ [SpaceClaim manual 변환, PF-3]
  ▼
reference/galaxy_watch/converted.step
  │
  ▼
StepReader (XDE) → [(name, shape, pose), ...]
  ▼
parts 분류 (naming rules)
  ├─ housing → TopologyAnalyzer → FeatureCatalog → feature_to_plan → plans/auto.yaml
  └─ display/battery/crown/coil/lug → catalogs/components/extracted/galaxy_watch/<name>.yaml
  ▼
PlanExecutor (auto.yaml) → reproduced.step
  ▼
face-level 비교: reproduced vs housing.shape
  ▼
log.jsonl + screenshots + bundle + mail
```

### B. simple_watch fixture reproduction (집/회사 공통, Phase 2)

```
fixtures/make_simple_watch.py
  │ [build123d 합성, 회사/집 동일]
  ▼
fixtures/simple_watch.step (XDE 어셈블리 + 5 부품 네이밍)
  │
  ▼
시나리오 phase2_simple_watch_repro 실행 → 동일 흐름 (A 와 같음, OEM 만 fixture 로 대체)
```

### C. Composition (Phase 6)

```
사용자 부품 배치 (or 자동 추출된 ComponentArrangement)
  ▼
HousingSynthesisPlanner v0 (rule) — [[components#합성-v0]]
  또는 LLM v1 — [[llm#planner-mode]]
  ▼
Plan executor → housing.step
  ▼
DFM validator → DFMReport
  ▼
(OEM CAD 있으면) face-level 비교 vs reference housing.step
  ▼
log + screenshots + bundle + mail
```

### D. Edit (Phase 7)

```
현재 plan + LLM (with manifest cache breakpoint A)
  ▼
LLM tool call (propose_step / replace_step / ...)
  ▼
Plan executor incremental
  ▼
viewport 갱신 + 채팅 응답
  ▼
(저장 시) plan autosave
```

## In-process 결정 사유

Web UI / REST 분리 하지 않은 이유:
- 큰 tessellation 데이터 (수백 MB) 의 HTTP 직렬화 비용 ↑
- viewport 의 picking 등 real-time interaction 의 latency 민감
- 단일 사용자 데스크탑 도구 (다중 사용자 X)
- in-process 가 단순 + 안정

headless CLI ([[ui#headless-mode]]) 가 별도 — 회사 컴 메일 워크플로의 일반적 모드.

## 환경 분리

집 / 회사 두 머신에서 동일 코드. 구분은 환경 변수 + 가용 파일로만:

| 환경 변수 | 의미 |
|---|---|
| `ANTHROPIC_API_KEY` | 없으면 [[llm#offline-mode]] |
| `PHONE_DESIGNER_LOG_LEVEL` | 회사 컴 = DEBUG, 집 컴 = INFO |
| `PHONE_DESIGNER_RUN_DIR` | 로그/번들 디렉토리 |

reference/galaxy_watch/ 의 존재 여부로 OEM 시나리오 가용성 자동 판정. 없으면 시나리오가
`SKIP_REQUIRES_OEM` 으로 graceful skip.
