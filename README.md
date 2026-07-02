# Phone Designer (SmartTwinModeller)

AI-assisted CAD 시스템 (build123d + OCCT 7.8 + Claude + PyVista).
워치/폰 하우징 designer 로 출발해 general-CAD 로 확장 — **419 skills** + MCP server
(LLM client 가 곧 NL→spec 인터프리터).

## 진입점

→ **[lat.md/lat.md](lat.md/lat.md)** — 전체 지식 그래프 인덱스
→ **[plans/NEXT_ROADMAP.md](plans/NEXT_ROADMAP.md)** — 통합 로드맵
   (Phase 1 MCP 세션화 + Phase 2 도면/plan-v2/assembly 는 **출하됨**; Phase 3 은
   go/no-go 게이트 뒤)

## 현재 상태 (2026-07-03 갱신)

| 영역 | 결과 |
| --- | --- |
| Skill 라이브러리 | **419 skills / 15 selector 스키마** (`export_manifest` 실측) |
| 5-pillar | RE / large-assembly / report / variants / compare — 전부 MCP 노출 |
| General-CAD verbs | transform 카테고리: move/mirror/scale/split_body + intersect + pattern_seed + **deform_body**(twist/taper); direct-edit move_face/replace_face |
| From-scratch sketch→solid | sketch_extrude / sketch_revolve(Z-axis-locked) / sketch_sweep / sketch_loft + helix_sweep + poles_spline |
| Scan-to-CAD 입구 | **mesh_import**(OBJ/PLY→BRep) + **point_cloud_import** — `is_solid` 는 정직 semantics (TopAbs_SOLID **이면서** volume>0 일 때만 true; 아니면 volume=None) |
| Engineering drawing | **hlr_view + drawing_sheet** — 3각법 front/top/right + iso, hidden lines, title block, 치수 테이블; **'DRAFT FOR REVIEW' 라벨이 아티팩트에 고정** |
| Interop | STEP/IGES/BREP/STL 왕복 + DXF/OBJ/PLY/**glTF** export, mesh↔brep |
| Parametric plan v2 | `parameters:` + `{"$expr": "wall*2"}` + **plan_reexecute**(override→재실행→deviation 리포트); param-less plan 은 v1 wire format **byte-identical** 유지 |
| Assembly 분석 | **analyze_assembly** — signature dedup(볼트 50개=1회 분석), 표준부품은 catalog-part 라인(기계가공 원가 산정 금지); interference/clearance 는 **static_pose_only** 라벨 |
| RFQ | **quote_package** — one-call zip + manifest; 모든 가격은 **grade='estimate'** (모델이지 견적 아님), 라벨이 아티팩트 내부에도 삽입 |
| MCP server | 23개 `cad_*` tool, sessionful + hang-proof worker + 58 recipes (아래 절) |
| PySide6 inspector GUI | [src/phone_designer/ui/inspector_main.py](src/phone_designer/ui/inspector_main.py) |
| CI | fidelity strict + full suite(blocking, OCCT 7.8 pin) + headless-linux + corpus-regress (아래 절) |

회사 컴에서 추가 검증할 항목: [lat.md/work-pc-tests.md](lat.md/work-pc-tests.md)
발견된 작업 backlog: [lat.md/backlog.md](lat.md/backlog.md)

### 직접 실행

```powershell
.\venv\Scripts\Activate.ps1

# MCP server (stdio) — Claude 등 LLM client 용
python -m phone_designer.mcp_server

# GUI inspector (plan 실행 + viewport + reference overlay)
python -m phone_designer inspect --plan plans\simple_watch_outer.yaml `
                                  --reference fixtures\simple_watch_housing_only.step

# Reference STEP → 자동 plan → 재현 → STEP export
python -m phone_designer reproduce --reference fixtures\simple_watch_housing_only.step `
                                    --out out\auto.step `
                                    --plan-out out\auto_plan.yaml

# 자동 plan 결과 viewer
python -m phone_designer inspect --step out\auto.step `
                                  --reference fixtures\simple_watch_housing_only.step
```

## MCP server — LLM 이 직접 모델링하는 표면

`python -m phone_designer.mcp_server` (stdio, FastMCP). **LLM client 가 곧
NL→spec 인터프리터** — 커스텀 자연어 파서 없음. client 는 `cad_list_skills` /
`cad_get_skill_schema` 로 419개 skill 의 JSON-Schema args 모델을 발견하고, build
spec `[{"op": …, "args": {…}}, …]` 을 조립해 `cad_generate` 를 부른다.

23개 tool, 네 그룹:

- **발견** — `cad_list_skills` · `cad_get_skill_schema` · `cad_find_recipe`
  (EN/KR intent 검색, 검증된 few-shot spec 반환)
- **세션 모델링** — `cad_generate` · `cad_import`(STEP→body_id; oversize 는
  structured refusal) · `cad_modify`(원본 spec 재전송 없이 추가 step, 새 body_id
  발급 — body 는 immutable) · `cad_undo`(lineage 부모로 복귀 + 복원 volume 반환) ·
  `cad_measure`(mass/obb/…) · `cad_preview`(iso/front/top PNG — no-GL 환경은 빈
  이미지가 아니라 honest skip-marker) · `cad_preflight`(실행 없이 op/args/selector
  match 검사) · `cad_export`
- **분석** — `cad_analyze` · `cad_estimate_cost` · `cad_recommend_process` ·
  `cad_repair_dfm` · `cad_dfm_workflow` · `cad_compare` · `cad_variants` ·
  `cad_cheapest_variant`(strict viability gate — marginal 후보 절대 미선정) ·
  `cad_analyze_assembly`(static_pose_only)
- **산출물** — `cad_quote_package`(one-call RFQ) · `cad_drawing`(DRAFT FOR REVIEW) ·
  `cad_reexecute`(plan v2 parameter override 재실행)

실행 안전장치: build step 은 세션당 1회 스폰되는 **warm worker subprocess** 에서
hang-proof 실행된다 — `PHONE_DESIGNER_SKILL_TIMEOUT_S`(기본 120s, 0=inline) 초과 시
`{ok:false, error:"TIMEOUT …"}` 반환 + worker respawn. OCCT hang 한 번이 세션을
죽이지 못한다. tool 은 절대 서버를 crash 시키지 않고 structured error 를 돌려주며,
실패에는 `likely_cause` / suggested fix 가 붙는다(원문 error string 은 가리지 않음).
workspace 는 `$PHONE_DESIGNER_MCP_WORKSPACE`(기본 temp dir). 모든 비용/공정 수치는
**grade='estimate'** 로 표기된다.

## recipes/ — executed-and-pinned few-shot corpus

`recipes/*.yaml` **58개** (그중 `neg_*` 3개는 structured refusal 을 가르치는 음성
예제). cold LLM 을 무는 idiom 우선(wire-cast hang trap, Z-locked revolve, selector
관용구 …). 각 recipe 의 expected invariant(is_solid, volume 범위, bbox)는 **실제
실행으로 측정된 값**이고, `tests/test_recipes_execute.py` 가 전부 재실행해 rot 를
차단한다. 숫자를 추측으로 적는 recipe 는 금지 — 상세: [recipes/README.md](recipes/README.md).

## 빠른 시작

```powershell
# 1회 셋업
.\setup.ps1

# 합성 워치 STEP fixture 생성 (이미 setup.ps1 가 호출함, 재실행 가능)
python fixtures\make_simple_watch.py

# Phase 0 smoke 시나리오
python -m phone_designer test --scenario phase0_env_smoke
```

상세 절차: [lat.md/setup.md](lat.md/setup.md).

## CI gates

- **Round-trip strict gate** (blocking): `$env:FIDELITY_STRICT="1"; pytest -m fidelity`
  — parametrized round-trip 케이스는 전부 hard FAIL. RE 파이프라인을 깨는 코드
  (cube collapse OR volume drift > tolerance)는 merge 를 막는다.
  - Convenience wrapper: `pwsh scripts\ci_fidelity_gate.ps1`
  - Default (no env var): 같은 테스트가 돌지만 currently-failing 케이스는 `xfail`
    로 보고돼 로컬 dev 는 green 유지.
- **Full pytest suite** (blocking, windows headless — `suite.yml`): cadquery-ocp<7.9
  및 build123d<0.11 pin 이후 다시 blocking. 이전 CI 실패는 env drift(OCCT 7.9 로
  미끄러진 unpinned 설치)로 확인됨 — 동일 커밋이 로컬 OCCT 7.8 에서 full suite
  1964/0 통과. kernel 을 고정하면 deterministic.
- **headless-linux smoke** (`scripts/headless_smoke.py`, ubuntu): MCP surface 가
  UI/GL/Qt 스택 없이 generate + STEP/STL export + cost 가능함을 증명하는
  cross-platform 게이트.
- **corpus-regress vs committed baselines** (`suite.yml`):
  - `preserve_brep` root — **BLOCKING.** 이 재구성은 deterministic/byte-identical
    (55개 root 파일에서 max abs diff 0.0 실측)이므로 exit 1 은 항상 진짜 geometry
    회귀다 — match_ratio 하락 / hausdorff 상승 / 신규 error. `--timeout-s 600` 로
    runner-speed flake 차단.
  - `box` root — **informational only** (continue-on-error). box 재구성은 아직
    non-deterministic (자기 baseline 대비 run-to-run match_ratio/hausdorff 흔들림)
    이라 hard gate 가 될 수 없다. blocking 승격은 determinism 증거(3연속 green
    sweep — roadmap 2-4) 확보 후 — **아직 승격 전.**
- **NL→spec replay 평가**: `phone-designer nl2spec-eval` — 기록된 reference spec 의
  deterministic 재실행. LIVE LLM lane 은 비결정성 때문에 CI gate 가 아니다.

## Reverse-engineering corpus testing

OEM STEP/IGES/BREP 묶음에 대해 `extract_feature_catalog → plan_from_feature_catalog →
PlanExecutor` 파이프라인을 일괄 실행하고 fidelity 보고서를 생성:

```powershell
# 1. 파일을 corpus/oem/ 아래로 드롭 (confidential — .gitignore 가 막아줌)
copy C:\drops\AcmeWatch_v3.step corpus\oem\acme\

# 2. 실행
phone-designer corpus-test                                # 기본: corpus/oem/, docs/oem_corpus_report.md
phone-designer corpus-test --dir corpus\oem\acme `
                            --report-out docs\acme_report.md `
                            --tolerance-pct 25.0

# 3. 커밋된 baseline 대비 회귀 게이트 (detector/planner 변경은 반드시 이걸로)
phone-designer corpus-regress --mode preserve_brep --subset root --timeout-s 600
```

`corpus/oem/_sample/` 에 in-repo smoke fixture 가 있어 CLI 자체는 git
클론 직후에도 동작. 보고서는 `original_vol / regen_vol / drift % /
face_count / status (PASS / DRIFT / CUBE_COLLAPSE / EXEC_FAIL / ERROR)` 컬럼의
마크다운 테이블. exit code `0 = all within tolerance`, 그 외 `1`.

## 주요 문서

- 다음 로드맵: [plans/NEXT_ROADMAP.md](plans/NEXT_ROADMAP.md) (Phase 1+2 출하됨)
- 전체 계획: [lat.md/plan.md](lat.md/plan.md)
- 집/회사 워크플로 + 로깅/메일: [lat.md/dev-test.md](lat.md/dev-test.md)
- 개념 (Skill / Plan / Selector / Component): [lat.md/concepts.md](lat.md/concepts.md)
- Phase 별 계획: [lat.md/phases.md](lat.md/phases.md)
- Pre-flight 결정 (PF-1~7): [lat.md/decisions.md](lat.md/decisions.md)
- 위험 / 시한 폭탄: [lat.md/risks.md](lat.md/risks.md)
- Recipe corpus 규약: [recipes/README.md](recipes/README.md)

## 환경

- **집 컴** (이 머신): 개발 + 자체 시나리오 검증. 합성 fixture STEP 사용.
- **회사 컴**: 실제 OEM Galaxy Watch Parasolid CAD 테스트. 결과는 로그/스크린샷 zip → 메일 → 집 컴.

자세히는 [lat.md/dev-test.md#환경-분리](lat.md/dev-test.md#환경-분리).
