# concepts — 핵심 추상화

본 시스템을 이해하는 5개 1급 개념. 모든 후속 결정은 이 위에 쌓인다.

## Skill

입력 `Part + 인자 + Selector` → 출력 `Part + EntityHistoryMap + SelectorFreeze` 의
순수 함수 + 메타데이터 descriptor.

### Atomic vs Macro

PF-5 의 분류 규칙 ([[decisions#PF-5]] 참조).

- **Atomic**: OCCT 호출 1회 + selector 1회. 예: `fillet_edges_by_predicate`, `extrude_pocket`, `hole`.
- **Macro**: 2개 이상 atomic 의 명명된 시퀀스. manifest 에 `expansion: [...]`.
  예: `rounded_slab` = `box` + `fillet`, `disc_with_dome` = `cylinder` + `sphere section` + `fillet`,
  `crown_shaft_hole` = `boss_with_hole` + `mounting_pad`.

LLM Planner 정책 ([[llm#planner-mode]]):
- Reproduction / Composition 외피 단계 → macro 우선 (가독성)
- Edit / agentic → atomic 우선 (제어력)

### SkillSpec (메타데이터 스키마)

```python
@dataclass
class SkillSpec:
    name: str
    category: str                              # "create" | "modify/fillet" | ...
    level: Literal["atomic", "macro"]
    summary: str                               # 1줄 사람·LLM 용
    args_model: type[BaseModel]                # Pydantic, 범위 포함
    selector_kinds: list[str]                  # 받을 selector 의 kind 들
    history_rules: dict[str, HistoryRule]      # entity_role → enum
    preconditions: list[PreconditionRef]       # named ref, 함수 X (manifest serializable)
    postconditions: list[PostconditionRef]
    produces_features: list[str]
    preserves: list[str]
    manufacturing: ManufacturingSpec
    failure_modes: list[FailureModeRef]
    cost_hint: float                           # 0..1, Phase 1 에서 실측
    expansion: list[str] | None                # macro only
```

`preconditions`/`failure_modes` 가 함수가 아닌 **named ref** 인 이유: manifest 가
JSON 직렬화 가능해야 LLM 의 system prompt 에 들어가고 caching 도 잘 됨.
실제 함수는 별도 registry 에 등록.

[[persistent-naming#history_rule]] 의 enum:

```python
class HistoryRule(str, Enum):
    MODIFIED_INHERIT = "modified_inherit"   # face/edge 변형되어도 tag 유지
    SPLIT_BRANCH     = "split_branch"        # 1→N split, 모든 결과가 부모 tag + 분기 idx
    CONSUMED         = "consumed"            # 입력 entity 소멸
    GENERATED_NEW    = "generated_new"       # 입력에 없던 새 entity
```

## Selector

Face/edge/vertex 부분집합을 선언적으로 지정하는 predicate.

원자 selector 카탈로그 + 조합 (And/Or/Not/FirstN/LargestN). 자세히는 [[skills#selectors]].

우선순위 (안정 → 불안정):
`tagged > face_named > axis_aligned_edges > edges_on_face > edges_by_position`

LLM 표현 = JSON 트리:

```json
{"kind": "and",
 "left":  {"kind": "edges_on_face", "face": {"kind": "face_named", "name": "top"}},
 "right": {"kind": "axis_aligned_edges", "axis": "Z"}}
```

## Plan

Skill 호출의 순서화된 시퀀스. YAML 직렬화, 결정성 동결, schema 버전 관리.

```yaml
schema_version: 1
plan_name: simple_watch_outer
steps:
  - id: s1
    skill: disc_with_dome
    args: {diameter_mm: 44.0, height_mm: 11.0, dome_rise_mm: 1.5, corner_r_mm: 2.0}
  - id: s2
    skill: chamfer_edges_by_predicate
    args:
      selector: {kind: edges_on_face, face: {kind: face_named, name: bottom}}
      width_mm: 0.5
    selector_freeze:
      matched_count: 1
      sort_key: lexicographic_bbox_center
      topology_signature: "9f8a2c1e"
```

### Freeze (PF-2)

[[plan-determinism]] 의 메커니즘. 각 step 의 selector 매칭 결과를 plan 에 동결.
재실행 시 `(matched_count, topology_signature)` 일치 검사.

- **`strict`** (same-machine default): mismatch = 에러 + diff 리포트
- **`loose`** (cross-platform): mismatch = 경고 + 새 매칭 사용

### Schema 버전 관리

- `schema_version: 1` 부터 시작
- skill 인자 추가는 minor (default 처리)
- skill 이름 변경/제거 = major bump + `migrations/v1_to_v2.py` 핸들러
- plan 로드 시 자동 migration 시도, 실패 = 명시 에러

### 실패 semantics

Step k 실패 시:
- 캐시: step 1..k-1 의 결과 Part 보존
- Plan 의 step k = `status: failed` + 사유 메타데이터
- 후속 step (k+1..) 자동 비활성
- LLM/사용자에 [[ui#error-mapping|친절한 에러 메시지]] + 원본 stacktrace 토글

## Component

부품의 parametric 모델 + 인터페이스 메타데이터.

```python
class Component:
    name: str                          # OEM CAD 의 부품 네이밍 또는 사용자 지정
    bbox: BoundingBox                  # housing-local 좌표
    pose: Pose
    source: ComponentSource            # OEM_CAD | CATALOG | USER_DEFINED
    mount_interface: MountSpec | None  # 자동 추출 안 됨, 사람 보강
    clearance: ClearanceSpec | None
    ports: list[Port] | None
    process_constraints: dict
    raw_step_path: Path | None         # OEM 의 원본 부품 STEP
```

OEM CAD 자동 추출 항목: `name, bbox, pose, raw_step_path`.
사람이 보강해야 하는 항목: `mount_interface, clearance, ports`.

상세: [[components]].

### Housing-local 좌표계

- 원점: housing bbox 중심
- X: 길이축 (긴 변)
- Y: 너비축
- Z: 두께축 (디스플레이 = +Z, 후면 = −Z)
- Component.pose 는 모두 housing-local
- Housing 재생성 시 component 는 로컬 좌표 유지 (새 housing 의 새 원점 기준 재해석)

## Manifest

모든 skill / selector / component / process 메타데이터의 머신 리더블 catalog.

- 빌드 시점 자동 생성 (`python -m phone_designer.skills.export_manifest`)
- **LLM tool schema + DFM 규칙의 single source of truth**
- diff 가능 → 릴리즈 별 변경 추적
- [[llm#caching]] 의 stable core 가 manifest 의 atomic 부분

자세히는 [[skills#manifest-구조]].

## 5개 개념의 의존성

```
Manifest
   │
   ├── Skill (atomic + macro) ── needs ──▶ Selector
   │       │
   │       └── operates on ──▶ Part (build123d.Part)
   │
   ├── Component ─── placed in ──▶ housing-local 좌표
   │
   └── Plan ── sequence of ──▶ Skill 호출
              │
              └── freeze 검증 by ──▶ Selector resolved entities
```

[[architecture]] 의 레이어 다이어그램에서 이 의존성이 실제 모듈 트리에 어떻게 매핑되는지 참조.
