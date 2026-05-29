# project — 목적·범위·이력

## 한 줄 요약

> *사용자가 부품을 공간상에 배치하면, LLM이 메타데이터가 잘 정의된 [[skills|Skill 라이브러리]] 를
> 조합해 공정 제약을 만족하는 워치/폰 외곽·내부 기구 초안을 자동 생성하는 데스크탑 도구.*

## 무엇을 만드는가

세 모드 + 2-tier reference 입력:

1. **Reproduction 모드** — Parasolid/STEP CAD 또는 mesh (.glb) reference 를 읽어
   skill 라이브러리로 재현. Reference 와 face-level 비교.
2. **Composition 모드** — 사용자가 부품을 배치 → 외곽 + 내부 기구 초안.
   OEM CAD 가 있으면 자동 비교.
3. **Edit 모드** — 자연어/픽킹으로 변경.

타겟 진행 순서:

- **Phase 0–8**: Galaxy Watch — 집 컴에서는 [[dev-test#fixtures|simple_watch.step]],
  회사 컴에서는 Parasolid OEM CAD
- **Phase 9**: iPhone 12 `.glb` — mesh pipeline 일반화 검증

## 환경 (Rev 6 의 핵심 추가)

| 환경 | 역할 | 자산 | 제약 |
|---|---|---|---|
| **집 컴 (이 머신, Windows)** | 개발 + 자체 검증 + plan 작성 | `iphone/iphone_12_teardown.glb`, `fixtures/simple_watch.step` (합성), Claude 풍부 | OEM CAD 없음, SpaceClaim 없음, ANSYS 없음 |
| **회사 컴** | 실제 OEM CAD 테스트 + 결과 메일링 | Galaxy Watch Parasolid `.x_t`, SpaceClaim, ANSYS | Claude 사용량 적음, 직접 디버깅 불가 |

**워크플로**: 집에서 코드/테스트 → git push → 회사 컴 git pull →
[[dev-test#시나리오-러너]] 1회 실행 → [[dev-test#자동-번들-메일]] → 집에서 분석.

자세히는 [[dev-test#환경-분리]] 참조.

## Non-goals

- 회로/PCB 라우팅 자동화
- 공식 OEM 데이터의 외부 공개 (사내용)
- 메쉬 → STEP 직접 변환 (메쉬는 측정용)
- 자동 FEA closed-loop 최적화
- 다중 사용자 동시 편집

## changelog

| Rev | 시점 | 핵심 변경 |
|---|---|---|
| 1 | 초기 | iPhone 12 외곽 frame 1종, Three.js 웹 UI, stage 하드코딩 |
| 2 | 1차 재작성 | Skill+Plan 추상화, PyVista/Qt 데스크탑, 부품 자동 합성, LLM tool use |
| 3 | 셀프평가 반영 | Pre-flight 5건(PF-1~5), persistent naming spec, plan 결정성 동결, mesh 측정 범위 축소, 일정 +60% (16주) |
| 4 | 2차 셀프평가 | history_rule enum, 시한 폭탄 4개 정직화 (OCCT history 70%, cross-platform same-machine), PF-6/7 추가, 사용자 UX (에러/Undo/i18n/오프라인) — 22주 |
| 5 | Galaxy Watch 전환 | 1순위 = Galaxy Watch + Parasolid OEM CAD, STEP topology reverse engineering, 부품 자동 추출, face-level 검증 — 18주 |
| **6** | **집/회사 분리** | **`lat.md/` 지식 그래프, 합성 fixture STEP, 구조화 로깅, 자가검증 시나리오, 자동 번들/메일** — 20주 |

Rev 별 상세 변경은 git log 또는 [[decisions]] 의 dated ADR.

## 다음 단계

[[plan#16-다음-단계]] 참조.
