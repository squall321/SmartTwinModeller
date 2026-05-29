# catalogs/processes/

공정 정의. 각 YAML 이 1개 process. ProcessRegistry 가 본 디렉토리를 자동 로드.

## 형식

```yaml
code: <unique_id>           # 예: die_cast_al
name: <human readable>
description: |
  본 공정의 특성 + 적용 범위.
rules:
  min_wall_mm: <float>
  min_draft_deg: <float | null>
  undercut_allowed: <bool>
  min_fillet_r_mm: <float | null>
  ...
applicable_to_skills:        # manifest 의 skill name list
  - <skill_name>
  - ...
not_applicable_to: []        # 적용 안 되는 skill 명시
```

`rules` 의 값은 정수/실수 또는 simpleeval expression (`"args.radius_mm * 0.5"`) —
[[../../src/phone_designer/manufacturing/string_eval.py]] 가 처리.

## 등록 공정 (Phase 5 초기 5종)

- `die_cast_al` — Aluminum Die Casting
- `injection_mold_pa` — Polyamide Injection Molding
- `cnc_3axis` — CNC 3-Axis Milling
- `cnc_5axis` — CNC 5-Axis Milling
- `sheet_metal_stamp` — Sheet Metal Stamping

새 공정 추가는 YAML 1개 더 + `applicable_to_skills` 채우기.

## 관련 문서

- [[../../lat.md/manufacturing.md]] — 공정 + DFM spec
- [[../../lat.md/backlog.md#Phase-5]] — 진행 상태
