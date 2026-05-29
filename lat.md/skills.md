# skills — Skill 라이브러리 카탈로그

총 ~34개 (atomic ~25 + macro ~9). 모든 skill 은 [[concepts#skill|SkillSpec]] 메타데이터 포함.

## atomic vs macro

[[decisions#PF-5]] 의 분류 규칙. 모든 skill manifest 항목에 `level: atomic | macro` 필수.

| Level | 정의 | 예시 |
|---|---|---|
| atomic | OCCT 호출 1회 + selector 1회 | `fillet_edges_by_predicate`, `extrude_pocket`, `hole`, `chamfer_edges_by_predicate` |
| macro | atomic 시퀀스의 명명, manifest 에 `expansion: [...]` | `rounded_slab`, `disc_with_dome`, `crown_shaft_hole`, `boss_with_hole`, `grille_pattern` |

LLM 정책: [[llm#planner-mode]]:
- Reproduction / Composition 외피 → macro 우선
- Edit / agentic → atomic 우선

## 카탈로그

### Create

| Name | Level | Args | 용도 |
|---|---|---|---|
| `box` | atomic | L, W, H | 모든 prism 의 base |
| `cylinder` | atomic | D, H | 부품 placeholder 등 |
| `rounded_slab` | macro | L, W, T, corner_r | 폰 외피 base, expansion = box + fillet |
| `disc_with_dome` | macro | D, H, dome_rise, corner_r | **워치 외피 base** (Rev 5 신규) |
| `import_step` | atomic | path | OEM 부품 인서트 |

### Modify / 곡률

| Name | Level | Args | 용도 |
|---|---|---|---|
| `fillet_edges_by_predicate` | atomic | selector, radius_mm | |
| `chamfer_edges_by_predicate` | atomic | selector, width_mm | |
| `variable_radius_fillet` | atomic | selector, radius_curve | 위치별 가변 R |
| `loft_side_profile` | atomic | top_section, bottom_section | 측면 곡률 |
| `polynomial_pocket` | atomic | face, sketch, depth_curve, order | 다항식 깊이 변화 포켓 |
| `swept_relief` | atomic | path, profile | sweep 홈 |

### Modify / pocket·plateau

| Name | Level | Args | 용도 |
|---|---|---|---|
| `extrude_pocket` | atomic | face, sketch, depth | step-down (display bezel 등) |
| `extrude_plateau` | atomic | face, sketch, height | camera bump |
| `extrude_through` | atomic | face, sketch | 관통 cutout |
| `hole` | atomic | point, D, depth, kind | blind/through/threaded/cbore/csk |
| `hole_array` | atomic | points, hole_spec | speaker grille, mic |
| `grille_pattern` | macro | face, window, pattern, hole_d, spacing | hex/grid/radial 패턴 |

### Modify / boss·rib·snap

| Name | Level | Args | 용도 |
|---|---|---|---|
| `boss_with_hole` | macro | base_d, height, hole_spec | 스크류 보스 |
| `rib` | atomic | path, height, width, draft_deg | 내부 보강 |
| `snap_hook` | atomic | face, position, geometry | 사출 스냅 |
| `mounting_pad` | atomic | face, sketch, height, fastener | 부품 장착 패드 |

### Modify / 워치 특화 (Rev 5)

| Name | Level | Args | 용도 |
|---|---|---|---|
| `crown_shaft_hole` | macro | position, shaft_d, bearing_recess_d | 회전 크라운 베어링 + 샤프트 hole |
| `lug_pair` | atomic | position_y, length, width, thk, pin_d | 양 측면 스트랩 마운트 |
| `o_ring_groove` | atomic | path, profile (반원/사다리꼴) | 방수 그루브 sweep |

### Modify / 안테나

| Name | Level | Args | 용도 |
|---|---|---|---|
| `antenna_slit` | atomic | path, width, depth | 알루미늄 절연 슬릿 |
| `polymer_inlay` | atomic | slit_path, polymer_spec | 슬릿 충진 |

### Modify / 마감

| Name | Level | Args | 용도 |
|---|---|---|---|
| `final_fillet_all_sharp_edges` | atomic | min_angle, radius | 일괄 마감 |
| `surface_offset` | atomic | face, offset_mm | 두께 조정 |

### Compose / 부울

| Name | Level | Args | 용도 |
|---|---|---|---|
| `subtract` | atomic | other_part | 부품 envelope 빼기 |
| `union` | atomic | other_part | 합집합 |
| `tag_face` | atomic | selector, tag | 의미 태그 부여 |

## selectors

원자 selector ([[concepts#selector]] 의 요약):

| Kind | 인자 | 안정성 |
|---|---|---|
| `tagged` | tag | ★★★★★ history map propagated |
| `face_named` | name | ★★★★ |
| `axis_aligned_edges` | axis | ★★★ |
| `edges_on_face` | face: SelectorRef | ★★★ |
| `edges_by_length` | min, max | ★★ |
| `edges_by_position` | bbox | ★ (좌표 변경에 약함) |
| `edges_convex_only` / `edges_concave_only` | — | ★★ |
| `faces_by_normal` | direction, tol_deg | ★★★ |
| `faces_by_area` | min, max | ★★ |
| `vertices_corner` | n_edges | ★★ |

조합: `AndSel`, `OrSel`, `NotSel`, `FirstN(sel, n, sort_by)`, `LargestN(sel, n, sort_by)`.

## manifest 구조

`python -m phone_designer.skills.export_manifest > manifest.json` 으로 생성.

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-25T...",
  "skills": [
    {
      "name": "fillet_edges_by_predicate",
      "category": "modify/fillet",
      "level": "atomic",
      "summary": "...",
      "args_schema": {...},     // Pydantic JSON schema
      "selector_kinds": ["edges"],
      "history_rules": {...},
      "preconditions": [{"ref": "pc.radius_less_than_half_shortest_edge", "doc": "..."}],
      "produces_features": ["fillet_face"],
      "preserves": ["outer_envelope"],
      "manufacturing": {...},
      "failure_modes": [{"ref": "fm.self_intersection", "doc": "..."}],
      "cost_hint": 0.2
    },
    ...
  ],
  "selectors": [...],
  "processes": [...]
}
```

stable / dynamic 분리 ([[llm#caching]]):
- **stable**: atomic skill 의 definition (변경 적음)
- **dynamic**: macro skill, 사용자 카탈로그, 사용자 정의 process

## 새 skill 추가

1. `src/phone_designer/skills/<category>/<name>.py` 작성
2. `@skill(...)` 데코레이터로 메타데이터 선언 (level 필수)
3. `tests/skills/test_<name>.py` 단위 테스트 (입출력 위상 검증 + history map 정합성)
4. `python -m phone_designer.skills.export_manifest` 실행 → manifest.json 갱신
5. CI 가 manifest schema 검증 + history rule enum 검증
6. (LLM tool 로 노출 시 자동, 별도 작업 없음)

**비용**: 30분 ~ 1시간 / skill. 본 설계의 핵심 — skill 추가 비용 낮음.

## 깨짐 catalog (Phase 1 끝, [[persistent-naming]] 의 확장 검증 결과)

`docs/history_rule_catalog.md` 에 적재. 예시:

| Skill | 깨짐 케이스 | Fallback |
|---|---|---|
| `subtract` | face split 시 자식 idx 가 OCCT version 별 다름 | `face_named` chain |
| `chamfer_edges_by_predicate` | fillet 인접 edge 의 history 손실 | `edges_by_position` |
| `polynomial_pocket` | NURBS face 의 history map 미지원 | unique tag 부여 후 `tagged` |

70% 미만 propagate 시 PF-1 spec 갱신.
