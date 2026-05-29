# persistent-naming — PF-1 spec

> **목표 (정직화, Rev 4)**: 30 skill 중 **70% 에서 history map propagation 작동**.
> 나머지 30% 는 fallback chain (tagged → face_named → position) + 사용자 경고.

상위 결정: [[decisions#PF-1]].

## 문제

CAD 의 parametric 시스템에서 skill 이 형상을 mutate 하면 face/edge 의 OCCT ID 가 바뀐다.
후속 selector 가 "방금 만든 fillet 자리" 를 어떻게 안정적으로 가리키나?

## 메커니즘

각 skill 의 `apply()` 가 반환:

```python
class SkillResult(BaseModel):
    body: Part                       # 변환된 새 body
    history: EntityHistoryMap        # 원본 entity → 결과 entity 매핑
    selector_freeze: SelectorFreeze  # 이 step 의 selector 매칭 결과 동결
```

`EntityHistoryMap` 의 구조:

```python
class EntityHistoryMap(BaseModel):
    rules: dict[EntityId, HistoryRule]    # 원본 → 변환 규칙
    children: dict[EntityId, list[EntityId]]  # 분할 시 부모 → 자식들
    new_entities: list[EntityId]          # GENERATED_NEW
    consumed: list[EntityId]              # CONSUMED
```

OCCT 에서 정보 추출:

```python
# build123d 가 wrapping 하는 OCP API
maker.Modified(shape)   # 변환된 결과 list
maker.Generated(shape)  # 부산물로 새로 생긴 결과
maker.IsDeleted(shape)  # 소멸 여부
```

## HistoryRule enum

```python
class HistoryRule(str, Enum):
    MODIFIED_INHERIT = "modified_inherit"   # face/edge 가 변형돼도 tag 유지
    SPLIT_BRANCH     = "split_branch"        # 1→N, 모든 결과가 부모 tag + 분기 idx
    CONSUMED         = "consumed"            # 입력 entity 소멸
    GENERATED_NEW    = "generated_new"       # 입력에 없던 새 entity
```

## Tag survival rule (skill 마다 다름)

각 skill 의 SkillSpec 에 `history_rules: dict[str, HistoryRule]`. 예:

```python
@skill(...)
class FilletEdgesByPredicate:
    history_rules = {
        "target_edges":   HistoryRule.CONSUMED,        # 원본 edge 사라짐
        "result_face":    HistoryRule.GENERATED_NEW,   # toroidal face 새로 생김
        "adjacent_faces": HistoryRule.MODIFIED_INHERIT, # 양옆 face 는 변형되어도 tag 유지
    }
```

## Selector 우선순위 (안정 → 불안정)

```
tagged > face_named > axis_aligned_edges > edges_on_face > edges_by_position
```

fallback chain: tagged 가 못 찾으면 face_named, 그것도 못 찾으면 position. fallback 동작 시
UI 경고 + log warning.

## PoC 범위 (Phase 0)

`tests/poc/persistent_naming.py`:

```python
def test_box_fillet_toroidal_face_traced():
    # 1. Box 생성, top edge 들에 tag "TOP_RIM"
    body = Box(20, 20, 10)
    body = tag_face(body, edges_by_position(z=10), "TOP_RIM")

    # 2. fillet 적용
    result = fillet_edges_by_predicate(body, selector=tagged("TOP_RIM"), radius_mm=2.0)

    # 3. result 의 history 에서 "TOP_RIM" 의 GENERATED_NEW 자식 (toroidal face) 추적
    new_faces = result.history.children_of_role("TOP_RIM", role="result_face")
    assert len(new_faces) > 0
    assert all(f.surface_type == "toroidal" for f in new_faces)
```

## 확장 검증 (Phase 1 끝)

7-8 skill 의 history propagate catalog. 다음 케이스 의도적으로 포함:

| 케이스 | 예상 깨짐 | 대응 |
|---|---|---|
| `box` → `fillet` (1 edge) | OK | — |
| `box` → `fillet` (전체) | OK | — |
| `box` → `chamfer` → `fillet` 인접 | 가능 | tag chain |
| `subtract` 후 face split | 가능 | SPLIT_BRANCH + 자식 idx |
| `boolean_union` 후 tag merge | 가능 | union 의 dominant tag 규칙 |
| `polynomial_pocket` | 미지원 가능 | NURBS face 의 history 별도 검증 |

결과: `docs/history_rule_catalog.md` 에 깨지는 케이스 + fallback 대응 명시.

**Go/No-Go**: 70% 미만이면 PF-1 spec 갱신 + 일정 +1주.

## Selector resolve 알고리즘

```python
def resolve(selector: Selector, body: Part, history: EntityHistoryMap) -> list[Entity]:
    if isinstance(selector, TaggedSelector):
        return history.entities_with_tag(selector.tag)
    elif isinstance(selector, FaceNamedSelector):
        return body.faces_with_named(selector.name)
    elif isinstance(selector, AxisAlignedEdgesSelector):
        return [e for e in body.edges() if is_axis_aligned(e, selector.axis)]
    # ...
```

resolve 실패 시 fallback chain 자동 적용 + log:

```jsonl
{"level":"WARNING","msg":"selector resolve fallback","primary":"tagged:TOP_RIM","used":"edges_by_position","step_id":"s4"}
```

## Cross-platform 한계

OCCT 의 entity ID 가 OS/version 별 다를 수 있어 **tagged 가 가장 안정**.
position 기반 selector 는 freeze 와 함께 사용 시 cross-platform mismatch 빈도 높음 → [[plan-determinism]] 의 loose 모드.
