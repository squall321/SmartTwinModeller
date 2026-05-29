# manufacturing — 공정 제약 + DFM

## 처리 공정

워치 기준 우선순위 (Rev 5):

| 코드 | 설명 | 1순위 | 핵심 DFM |
|---|---|---|---|
| `die_cast_al` | 알루미늄 다이캐스팅 (mid-range 외피) | ★ | 벽 ≥ 1.0mm, draft ≥ 1°, undercut 불가 |
| `injection_mold_pa` | 폴리아미드 사출 (low-end / 내부) | ★ | 벽 ≥ 0.8mm, draft ≥ 0.5°, 두께 균일 ±25%, 보스 D=벽×2 |
| `cnc_3axis` | 3축 CNC (Classic 베젤 등) | 보조 | tool R ≥ 0.5mm, undercut 불가, 깊은 포켓 종횡비 제한 |
| `cnc_5axis` | 5축 CNC | 후순위 | undercut 일부 허용, 곡면 가능 |
| `sheet_metal_stamp` | 판금 프레스 (백 커버) | 후순위 | 균일 두께, R ≥ 두께, 90° flange |

각 공정은 `catalogs/processes/<code>.yaml` 에 규칙. 새 공정 추가는 yaml 한 파일만:

```yaml
# catalogs/processes/die_cast_al.yaml
code: die_cast_al
name: "Aluminum Die Casting"
rules:
  min_wall_mm: 1.0
  min_draft_deg: 1.0
  undercut_allowed: false
  min_fillet_r_mm: 0.5
  max_aspect_ratio_pocket: 4.0
  slide_core_cost_factor: 2.0       # 사용 시 비용 가중
applicable_to_skills:                # manifest 와 cross-reference 검증
  - rounded_slab
  - disc_with_dome
  - extrude_pocket
  - extrude_plateau
  - boss_with_hole
  - rib
  - fillet_edges_by_predicate
  - chamfer_edges_by_predicate
  - mounting_pad
not_applicable_to:
  - polynomial_pocket          # NURBS 곡면은 다이캐스팅 어려움
  - antenna_slit               # 별도 후공정 (CNC) 필요
```

## DFM v0 (ray-march, Phase 5)

[[decisions#PF-7]] 옆 결정. v0 는 정직 범위 — medial axis 제외, ray-march 만.

### Wall thickness (ray-march)

```python
def wall_thickness_raymarch(body: Part, n_samples: int = 1000) -> list[ThicknessSample]:
    samples = []
    for face in body.faces():
        for pt, normal in sample_face(face, n=n_samples // len(body.faces())):
            # 면의 안쪽 방향 (normal 반대) 로 ray 발사
            inward = -normal
            t = ray_intersect(body, pt + ε * inward, inward)
            samples.append(ThicknessSample(point=pt, thickness=t))
    return samples

def wall_thickness_violations(samples, required_min_mm: float):
    return [s for s in samples if s.thickness < required_min_mm]
```

정확도 약 80% — 보스 내부, sharp corner 근처에서 false positive 가능. UI 의 confidence
색상으로 사용자가 무시 판단 가능.

### Draft

각 face 의 normal vs pull direction 의 각도. die_cast_al / injection_mold 의 경우
pull direction = +Z 와 -Z (자동 결정, 또는 사용자 지정).

```python
def draft_angle(face: Face, pull_direction: Vector) -> float:
    n = face.normal_at_center()
    return 90.0 - degrees(angle_between(n, pull_direction))
```

### Undercut

pull direction 에서 가려진 영역 = undercut. ray-trace 방식:

```python
def has_undercut(body: Part, face: Face, pull_direction: Vector) -> bool:
    for pt, _ in sample_face(face):
        # pt 에서 pull direction 방향으로 ray
        if body intersects ray(pt, pull_direction):
            return True
    return False
```

### DFM Report

```python
class DFMReport(BaseModel):
    process: str
    wall_violations: list[ThicknessSample]
    draft_violations: list[FaceViolation]
    undercut_violations: list[FaceViolation]
    confidence: float                  # 0..1, ray-march sampling 신뢰도
    summary: str
```

UI 의 DFM Report panel: 위반 face 색상 (red = wall, yellow = draft, orange = undercut),
confidence < 0.7 이면 reduce saturation.

### v0.3 이후 (Phase 5 종료 후 결정)

[[risks#open-questions]] 의 #3:
- medial axis (CGAL / scikit-fem)
- cross-section 기반 두께
- FEA-lite stiffness (ribbing 평가)

## ManufacturingBudget

사용자 설정:

```yaml
# budgets/al_unibody.yaml
allowed_processes: [die_cast_al, cnc_3axis, injection_mold_pa]
complexity_budget: high      # low | medium | high
draft_relaxation: strict     # strict | moderate | lenient
slide_core: false             # 슬라이드 코어 허용
```

설계 의도: "어려운 공정도 허용" → AI 가 복잡 형상 시도. "엄격" → 단순 형상 수렴.

## String expression evaluator (PF-안전성)

`manufacturing.processes` 의 일부 규칙이 args 의존 (예: `"args.radius_mm * 0.5"`).

`simpleeval` + 화이트리스트:

```python
from simpleeval import EvalWithCompoundTypes

ALLOWED_FUNCTIONS = {"min", "max", "abs", "round"}
ALLOWED_NAMES = {}  # args 는 SimpleEval 의 names 로 동적 주입

def evaluate_rule(expr: str, args_obj) -> float:
    e = EvalWithCompoundTypes(
        names={"args": args_obj},
        functions=ALLOWED_FUNCTIONS,
    )
    return e.eval(expr)
```

**`eval()` / `exec()` 절대 금지**. 평가 실패 → fallback = True (검증 skip + 경고).

## Skill ↔ process 호환

manifest 의 각 skill 에 `manufacturing` 속성 → process_code 의 매핑. ManufacturingBudget 의
`allowed_processes` ∩ skill.manufacturing.processes == 빈집합 → plan 검증 단계에서 거부.

```python
def validate_plan_against_budget(plan: Plan, budget: ManufacturingBudget) -> ValidationResult:
    for step in plan.steps:
        skill_proc = manifest.skills[step.skill].manufacturing.processes.keys()
        if not (budget.allowed_processes & skill_proc):
            return Fail(f"Step {step.id} ({step.skill}) requires {skill_proc}, "
                        f"but budget allows {budget.allowed_processes}")
    return Pass()
```
