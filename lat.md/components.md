# components — Component 카탈로그 + 자동 합성

## 모델

```python
class Component:
    name: str
    bbox: BoundingBox                  # housing-local
    pose: Pose
    source: ComponentSource            # OEM_CAD | CATALOG | USER_DEFINED
    mount_interface: MountSpec | None
    clearance: ClearanceSpec | None
    ports: list[Port] | None
    process_constraints: dict
    raw_step_path: Path | None
```

### Component 출처 3가지

| Source | 의미 | 추출 |
|---|---|---|
| **OEM_CAD** | [[reference#cad-pipeline]] 의 XDE 어셈블리에서 자동 분리 | name, bbox, pose 자동 + mount/clearance/ports 사람 보강 |
| **CATALOG** | `catalogs/components/<category>/<name>.yaml` 의 일반화 부품 | 모든 필드 yaml |
| **USER_DEFINED** | UI 에서 사용자가 만든 customized 부품 | UI 폼 |

## 카탈로그 디렉토리

```
catalogs/components/
├── watch/
│   ├── displays/
│   │   ├── galaxy_watch_amoled_44.yaml
│   │   ├── apple_watch_oled_45.yaml
│   │   └── generic_round_oled_30.yaml
│   ├── batteries/
│   ├── crowns/
│   ├── coils/
│   ├── speakers/
│   └── sensors/
└── phone/                            # Phase 9 이후
    ├── displays/
    ├── batteries/
    ├── cameras/
    └── ports/
```

각 yaml 예시:

```yaml
# catalogs/components/watch/displays/galaxy_watch_amoled_44.yaml
name: "Galaxy Watch 44 AMOLED"
category: display
bbox:
  diameter: 33.0           # 원형 디스플레이
  thickness: 2.7
clearance:
  side_mm: 0.3
  back_mm: 0.5
  thermal_zone_mm: 1.0
mount_interface:
  kind: adhesive_perimeter
  width_mm: 1.5
  housing_surface_requirement:
    roughness_ra_um: 1.6
    flatness_mm: 0.1
ports:
  - name: connector_flex
    pose: {x: 0, y: -14, z: -1.0}
    requires_housing_cutout: false
  - name: front_glass
    pose: {x: 0, y: 0, z: 1.35}
    requires_housing_window: true
    window_shape: {kind: circle, diameter: 34.0}
```

## 충돌 검사

OBB 기반 (회전된 부품 정확 처리):

```python
from trimesh.collision import CollisionManager

def has_collision(a: Component, b: Component) -> bool:
    # 1. AABB broad-phase
    if not aabb_overlap(a.world_bbox, b.world_bbox):
        return False
    # 2. OBB SAT/GJK via trimesh
    mgr = CollisionManager()
    mgr.add_object("a", a.as_trimesh_at_pose())
    mgr.add_object("b", b.as_trimesh_at_pose())
    return mgr.in_collision_internal()
```

UI: 충돌 시 빨간 highlight + clearance 침범도 별도 색.

## 합성 v0 (rule-based)

[[decisions#PF-7]] 의 spike 결과 의존.

기본 알고리즘:

```
1. ComponentArrangement → InnerVolume (union of bbox + clearance)
2. MinimumHousingVolume = InnerVolume + outer_skin_thickness
3. 외피 plan:
   - 원형이면 disc_with_dome, 사각이면 rounded_slab
   - chamfer + 측면 곡률
4. 부품별 인터페이스 plan:
   - requires_housing_window → extrude_pocket / extrude_through
   - requires_housing_cutout → 측면 cutout
   - mount_interface →
       adhesive_perimeter → mounting_pad
       screw_boss        → boss_with_hole
       snap_fit          → snap_hook
5. 내부 ribbing (PF-7 결정 알고리즘):
   - 옵션 A: voxel + greedy ground structure
   - 옵션 B: Delaunay tetrahedralization
   - 옵션 C: skeleton (medial axis of void)
   - 옵션 D (fallback): 자동 ribbing 없이 사용자 manual
6. 워치 특화: antenna_slit, o_ring_groove (옵션)
7. final_fillet_all_sharp_edges
```

[[src/phone_designer/planner/housing_synth_rule.py]].

**Rev 5 강점**: 합성 결과 vs OEM 외피 face-level 비교 가능 → 휴리스틱 점수 함수 튜닝 가능.

## 합성 v1 (LLM agentic)

[[llm#planner-mode]] 의 agentic loop. 부품 배치 + ManufacturingBudget → skill step 들을
manifest 안에서 합성.

검증 metric ([[phases#phase-8]] 의 객관 지표):

- (a) DFM 위반율 (rule v0 vs LLM v1)
- (b) plan 길이
- (c) volume
- (d) 결정성 (재실행 일치)
- (e) **OEM 외피와의 face count / volume / bbox 일치도** (Rev 5 신규)

## OEM CAD 자동 추출 흐름

```
reference/galaxy_watch/converted.step
  ▼
[[reference#step-어셈블리-분석]] → parts list with names
  ▼
[[reference#부품-분류]] → category 매핑
  ▼
catalogs/components/extracted/<category>/<name>.yaml 자동 생성
  ▼
사용자가 mount/clearance/ports 보강
```

추출된 부품 yaml 의 `source: OEM_CAD` + `raw_step_path` 로 원본 추적.

## 회사 컴 vs 집 컴

| | 집 컴 | 회사 컴 |
|---|---|---|
| OEM_CAD source | fixture `simple_watch_components/*.step` | 실제 Galaxy Watch 부품 STEP |
| 추출 catalogs | `catalogs/components/extracted/_synthetic/` | `catalogs/components/extracted/galaxy_watch/` |
| 합성 검증 | fixture 자체와 비교 | OEM 외피와 face-level 비교 |

집 컴에서 fixture 로 모든 알고리즘 동작 확인, 회사 컴에서 OEM 으로 최종 sanity.
