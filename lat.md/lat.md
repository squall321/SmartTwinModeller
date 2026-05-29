# lat.md — Phone Designer 지식 그래프 인덱스

> **목적**: 본 프로젝트의 모든 설계 결정, 인프라, 절차를 토픽별로 분리·연결해
> **집 컴 (이 머신, 풍부한 Claude 사용량) 에서 최대한 만들고 회사 컴 (실제 OEM CAD, SpaceClaim, ANSYS) 에서
> 최소한의 반복으로 검증** 할 수 있게 한다.
>
> 회사 컴은 사용자 직접 디버깅 불가 — **로그/스크린샷 자동 번들 → 메일** 로만 피드백.

## 빠른 시작

- **회사 컴 1회 셋업**: [[setup#회사-컴-1회-셋업]]
- **집 컴 1회 셋업**: [[setup#집-컴-1회-셋업]]
- **자가검증 시나리오 실행**: [[dev-test#시나리오-러너]]
- **로그/스크린샷 메일 보내기**: [[dev-test#자동-번들-메일]]

## 본 계획서

- [[plan]] — 전체 Rev 6 구현 계획 (Phase 0–9, 일정, 의존성)
- [[project]] — 목적·범위·non-goals·Rev 변경 이력
- [[glossary]] — 용어 사전 (Skill / Plan / Selector / Component / Manifest / ...)

## 핵심 개념

- [[concepts]] — Skill / Plan / Selector / Component / Manifest 정의
- [[persistent-naming]] — PF-1: OCCT history map propagation
- [[plan-determinism]] — PF-2: plan freeze + cross-platform 한계

## 아키텍처

- [[architecture]] — 시스템 레이어 + 데이터 흐름
- [[skills]] — Skill 라이브러리 (atomic ~25 + macro ~9, 카탈로그)
- [[components]] — Component 모델·OEM CAD 자동 추출·좌표계
- [[reference]] — STEP/Parasolid 처리 + mesh 처리 + reverse engineering
- [[manufacturing]] — 5종 공정 + DFM v0 (ray-march) + budget
- [[llm]] — Claude API 통합·caching·editor·planner·offline·API key UX
- [[ui]] — PySide6 + pyvistaqt + Undo/Redo + i18n + 친절 에러 매핑

## 개발/테스트 인프라 (Rev 6 신규 — 집/회사 분리)

- [[dev-test#환경-분리]] — Dev(집) vs Test(회사) 워크플로
- [[dev-test#로깅-시스템]] — 구조화 JSON Lines + 매 step viewport snapshot
- [[dev-test#시나리오-러너]] — Phase 별 자가검증 1-커맨드 실행
- [[dev-test#자동-번들-메일]] — log + screenshots → zip → 자동 메일
- [[dev-test#fixtures]] — simple_watch.step (집 컴에서 생성 가능한 합성 reference)
- [[setup]] — 집/회사 1회 셋업 절차
- [[work-pc-tests]] — **회사 컴 전용 검증 항목 체크리스트** (Phase 별)
- [[backlog]] — **진행 중 발견된 작업 항목 누적** (P0/P1/P2/P3 우선순위)

## 결정 기록 (Pre-flight 7건)

- [[decisions#PF-1]] — Persistent naming (70% 목표)
- [[decisions#PF-2]] — Determinism (strict = same-machine)
- [[decisions#PF-3]] — Parasolid → STEP 변환 + CAD 정밀도
- [[decisions#PF-4]] — PySide6 (LGPL)
- [[decisions#PF-5]] — Atomic vs Macro 분류 규칙
- [[decisions#PF-6]] — Secondary reference (iPhone glb)
- [[decisions#PF-7]] — Voxel ground-structure spike

## Phase 별 계획

- [[phases#phase-0]] — Pre-flight + 환경 + Parasolid 변환 (2주)
- [[phases#phase-1]] — Skill 프레임워크 + 8 skill + 폭탄 A/B 검증 (2주)
- [[phases#phase-2]] — Plan executor + simple_watch reproduction (1.5주)
- [[phases#phase-3]] — STEP topology + reverse engineering (1.5주)
- [[phases#phase-4]] — UI + 26 skill (3주)
- [[phases#phase-5]] — DFM + PF-7 spike (3주)
- [[phases#phase-6]] — Component + 합성 (2–3주, PF-7 결과별)
- [[phases#phase-7]] — LLM Editor + 비용 dry-run (1.5주)
- [[phases#phase-8]] — LLM Planner agentic + 객관 metric (3주)
- [[phases#phase-9]] — iPhone mesh pipeline 일반화 (1.5주)

**총**: 약 20주 (50% 병행 시 40주 ≈ 10개월). [[plan#일정-요약]] 참조.

## 위험 / 열린 질문

- [[risks#open-questions]] — 진행 중 결정될 항목
- [[risks#risk-register]] — 시한 폭탄 + 대응

## 외부 자산

- `iphone/iphone_12_teardown.glb` — Phase 9 mesh pipeline 검증용 (Sketchfab CC BY 4.0)
- `reference/galaxy_watch/` — 회사 컴 전용, Parasolid OEM CAD (gitignore)
- `fixtures/simple_watch.step` — 집 컴에서 생성, 파이프라인 검증 fixture

## lat.md 규약 (본 프로젝트)

- 위키 링크: `[[파일#섹션]]`, `[[파일#섹션#하위섹션]]`
- 코드 참조: `[[src/phone_designer/skills/_registry.py#SkillSpec]]`
- 소스 코드 백링크: 주석에 `# @lat: [[skills#fillet_edges_by_predicate]]` (구현 시)
- 새 결정 사항은 [[decisions]] 에 ADR 식으로 누적
- 변경 이력은 [[project#changelog]] 한 곳에만
