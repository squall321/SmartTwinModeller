# 개발 경험록 — 제조성 분석 → 제로베이스 생성 → MCP (2026-06)

이 세션에서 SmartTwinModeller를 **"CAD 역설계·분석 도구"** 에서 **"말로 시키면 형상을
만들고 제조까지 분석하는 MCP 파이프라인"** 으로 끌어올렸다. 무엇을 만들었는지보다 **어떻게
만들었고 무엇을 배웠는지**를 남긴다 (재현 가능한 방법론 + 정직성 규율).

> 결과: 360→383 스킬 + MCP 7툴. 전체 스위트 1920 passed / 0 failed (헤드리스).
> corpus-regress preserve_brep root 무회귀(게이트 통과). 트리 클린.

---

## 1. 만든 능력 (전부 corpus 검증 + 정직한 한계)

| 능력 | 무엇 | grade |
|---|---|---|
| `estimate_cost` (+판금 3공정) | 단가·사이클타임 (CNC·사출·laser+brake·turret·stamping) | estimate |
| `recognize_fits` | ISO 286 공차 + 표준 끼워맞춤 (추천 + 실측) | estimate |
| `measure_assembly_fit` | 두 솔리드 간 보어↔축·슬롯↔키 실측 끼워맞춤 | **measured** |
| `detect_sheet_metal` | 판금 인식 + 굽힘테이블 + 전개도 + 헴 | estimate |
| `recommend_process` | 비용순위·실현성-게이트 공정 선정 (정점) | estimate |
| `generate_from_spec` | 선언적 spec → 제로베이스 솔리드 (생성 엔진) | constructed |
| **MCP 서버** (7툴) | LLM이 모델링·분석·CAD출력 | — |

---

## 2. 방법론 — 매번 같은 루프를 돌렸다

### (a) 설계 패널 (도메인 지식이 필요한 것)
독립 설계 N개(서로 다른 각도) → **적대적 검증** 1개씩 → 종합. 단일 추측이 아니라 검증된
설계를 얻는다. 실제로 검증자들이 잡은 것:
- **판금 원가:** 4개 설계가 공유한 **선형 1/t 레이저속도 오류** → 거듭제곱 `10000·(1/t)^0.8`로 수정.
- **공정 advisor:** dfm 언더컷 중복카운트 / 5축은 자체 looser 스펙 필요 / `degenerate_geometry`·`below_scale` 과부하 / die-cast가 조용히 CNC 가격 매김 — 전부 소스를 읽고 잡음.
- **MCP 서버:** artifact `name` 경로 탈출 보안 / `status` ok·partial·error 의미 / raw bytes JSON 직렬화 크래시.

### (b) corpus 검증 루프 (기하 detector)
**합성 fixture 통과 ≠ 실제 정밀도.** 전체 161-파일 corpus에 돌려 → 원칙적 오탐 수정 →
**환원 불가 모호성은 confidence/flag로 정직하게 노출** → 대표 케이스 회귀 가드로 고정.
- detect_sheet_metal: 한 어셈블리가 두께 **254mm**, IC 리드를 굽힘으로 카운트 → 게이지 [0.15,6]mm + 굽힘선 ≥1.5t + 정직한 confidence(formed vs flat-ambiguous).
- estimate_cost: 음수 부피, $3.3M 어셈블리, 0402 미세부품 → reliability flag.
- measure_assembly_fit: RC_Buggy 256 fits 조합폭발, 불가능 억지끼움, 명목-모델 clr≈0 → dedup + sanity + `nominal` flag.

### (c) 적대적 리뷰 (구현 후)
diff/파일을 차원별 리뷰 → 발견마다 회의적 검증(false alarm 필터). 신호대잡음 좋음:
판금원가 18제기→1확정, 공정advisor 패널이 7개 실버그, 세션 리뷰 9제기→malformed-args 1실버그.

### (d) 해석적 성능 (느린 건 수학으로)
recommend_process가 공정×생산량 사다리로 ~42 estimate_cost 호출 → **모든 모델이 정확히
`unit(L)=base+T/L`** 임을 이용해 공정당 2호출로 전체 사다리+crossover를 closed-form 계산.
**테스트 55분 → 26초.**

---

## 3. 핵심 엔지니어링 교훈 (재사용 가능)

1. **anti-fake-accuracy가 최우선.** 환원 불가능한 모호성(평판 vs 박판, 쿨링핀 vs 굽힘,
   명목-모델 CAD, 자유곡면 loft/sweep)은 brittle 규칙으로 억지로 잡지 말고 **confidence /
   reliability flag / assumptions로 공개**한다. 재구성 win은 HAUSDORFF로만 주장(match_ratio 아님).

2. **base+T/L 법칙.** estimate_cost의 모든 공정 모델이 `단가 = base + 상각항/lot`. 한 번
   알면 생산량 곡선·crossover를 해석적으로 — 비싼 sweep 불필요.

3. **NL→spec은 LLM 클라이언트가 한다.** 별도 자연어 파서를 만들지 마라. 모든 스킬이
   JSON-Schema args를 노출하므로, MCP로 `cad_list_skills`+`cad_get_skill_schema`+`cad_generate`만
   주면 클라이언트(Claude)가 말→spec 번역을 자기가 한다. "말로 모델링"이 파서 없이 완성.

4. **상태는 파일로 흐른다.** MCP 툴은 stateless → 인메모리 바디는 호출 간 안 남는다.
   생성이 STEP를 쓰고 경로/body_id 반환, 분석은 그걸 받음. (세션 바디 캐시는 보조.)

5. **import 순서 버그 주의.** generate_from_spec의 스킬명 사전체크가 라이브러리 import 전에
   돌아 "hole"을 unknown으로 오기각 — 첫 빌드의 executor가 import를 해버려 *나중* 호출에선
   가려졌다. `_import_all_skills()` 선행으로 수정. 교훈: 레지스트리 조회 전 등록 보장.

6. **검증은 실제로 돌려라.** VTK 헤드리스 크래시(문서화된 env 한계)를 회귀로 오인할 뻔 →
   `PHONE_DESIGNER_UI_HEADLESS=1`로 전체 스위트 그린 확인. corpus-regress는 게이트(hausdorff)로
   판정하고, match_ratio 드롭은 면제.

---

## 4. 자동화 수준 도약

- **이전:** 기존 CAD 파일 → 분석 리포트 (L3 분석-조언).
- **이후:** 말(지시) → **discover → spec → 생성 → STEP/STL/.py → 제조분석 → 공정추천** 을
  MCP 한 세션에서. LLM 클라이언트가 인터프리터.

```
사용자 말 ──▶ [LLM 클라이언트 (MCP)]
   "벤트 케이스 커버"   │ cad_list_skills / cad_get_skill_schema   (op·args 발견)
                        │ cad_generate(spec) ─▶ body_id + STEP/STL/.py + file:// URI
                        │ cad_analyze / cad_estimate_cost / cad_recommend_process (body_id)
                        ▼
                  형상 + CAD파일 + 제조분석
```
실증: "벤트 슬롯 케이스 커버" → rounded_slab + 헥사 그릴(82홀) + 4 마운팅홀 → STEP → 82홀
검출, CNC $116/개(정직하게 비쌈 — 벤트 많으면 판금/레이저가 맞음).

---

## 5. 남은 정직한 한계 (다음 프론티어)

- **자유곡면 RE:** revolve만 깔끔한 파라메트릭 win, loft/sweep는 re-solidify 폴백.
- **판금 2D 전개도 외곽선**(컷아웃 포함 진짜 nest), 닫힌 헴.
- **복잡 판금부품 advisor 속도:** estimate_cost가 내부에서 detect_sheet_metal 재실행.
- **폐루프 없음(L4):** DFM 문제 자동 수정 / 비용 최소화 자동 재설계는 아직.
- **자연어 직접:** 구조화 spec은 됨. 진짜 free-text는 MCP 클라이언트(LLM)에 의존.

상세는 `docs/manufacturing_analysis_2026-06.md` + 각 스킬 docstring + 출력 `assumptions[]`.
