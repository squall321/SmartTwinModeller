# plan — Rev 6 구현 계획

> Rev 5 → Rev 6 핵심 변경: **집(개발)/회사(테스트) 환경 분리** + **합성 fixture STEP** +
> **구조화 로깅 + 자가검증 시나리오 + 자동 번들/메일** + lat.md 지식 그래프.

상위 개요는 [[project]], 용어는 [[glossary]] 참조.

---

## 1. 배경

회사 컴 (Parasolid CAD, SpaceClaim, ANSYS) 은 사용자 직접 디버깅 어렵고 Claude 사용량 적음 →
**집 컴에서 최대한 만들고, 회사 컴에서는 1-커맨드 실행 + 자동 메일 보고만**.

집/회사 환경 분리 상세: [[dev-test#환경-분리]].

## 2. 핵심 추상화

- [[concepts#skill]] — atomic vs macro, [[concepts#skillspec|SkillSpec]] 메타데이터
- [[concepts#plan]] — schema_version, freeze (PF-2), 실패 semantics
- [[concepts#selector]] — tagged > face_named > position
- [[concepts#component]] — housing-local 좌표, OEM CAD 자동 추출
- [[concepts#manifest]] — LLM tool schema + DFM 규칙의 single source
- [[persistent-naming]] — OCCT history map propagation (PF-1)
- [[plan-determinism]] — strict (same-machine) / loose (cross) (PF-2)

## 3. Pre-flight 7건

상세는 [[decisions]] 각 PF 섹션 참조.

| ID | 항목 | 시점 | 산출물 |
|---|---|---|---|
| PF-1 | Persistent naming spec + PoC | Phase 0 | [[persistent-naming]] + `tests/poc/persistent_naming.py` |
| PF-2 | Plan determinism spec + PoC | Phase 0 | [[plan-determinism]] + `tests/poc/determinism.py` |
| PF-3 | Parasolid → STEP 변환 + 정밀도 | Phase 0 (회사 컴 1회) | [[reference#parasolid-워크플로]] |
| PF-4 | PySide6 확정 (LGPL) | Phase 0 | [[decisions#PF-4]] |
| PF-5 | Atomic / Macro 분류 | Phase 0 | [[skills#atomic-vs-macro]] |
| PF-6 | iPhone glb 정밀도 (집 컴) | Phase 0 | [[reference#mesh-정밀도]] |
| PF-7 | Voxel ribbing 알고리즘 spike | Phase 5 말 | [[decisions#PF-7]] |

## 4. Skill 라이브러리

총 ~34개 (atomic ~25 + macro ~9). 카탈로그: [[skills#카탈로그]].

워치 특화 4개 (Rev 5 추가): `disc_with_dome` (macro), `crown_shaft_hole` (macro), `lug_pair`, `o_ring_groove`.

새 skill 추가 워크플로: [[skills#새-skill-추가]].

## 5. Reference 처리

두 경로:

- **CAD (1순위)**: [[reference#cad-pipeline]] — Parasolid → STEP → XDE 어셈블리 분석 + 부품 자동 추출 + topology 기반 reverse engineering
- **Mesh (Phase 9 일반화)**: [[reference#mesh-pipeline]] — `.glb` segmentation + ≥ 1mm feature 측정

**집 컴 합성 fixture**: [[dev-test#fixtures]] — `fixtures/make_simple_watch.py` 가
build123d 로 simple_watch.step 생성. 부품 5개 (housing, display, battery, crown, lug) 어셈블리 +
네이밍 보존. OEM CAD 없이도 §5 reference 파이프라인 검증 가능.

## 6. Component + 자동 합성

- [[components#카탈로그]] — 3가지 출처 (OEM_CAD / CATALOG / USER_DEFINED)
- [[components#충돌-검사]] — OBB + trimesh.collision
- [[components#합성-v0]] — rule-based ground-structure (PF-7 결과 의존)
- [[llm#planner-mode]] — agentic loop (v1, Phase 8)

## 7. 공정 + DFM

- [[manufacturing#처리-공정]] — 5종 (워치 1순위: die_cast_al, injection_mold_pa)
- [[manufacturing#dfm-v0]] — ray-march wall thickness + draft + undercut
- [[manufacturing#budget]] — 사용자 설정 가능
- [[manufacturing#string-evaluator]] — simpleeval, eval 금지

## 8. LLM 통합

- [[llm#editor-mode]] — Plan 끝에 step 추가
- [[llm#planner-mode]] — agentic loop, 부품 배치 → housing 자동 합성
- [[llm#caching]] — Anthropic prompt caching breakpoint 전략
- [[llm#비용]] — $0.14/step (caching off), Phase 7 dry-run
- [[llm#api-key]] — OS keyring + 환경변수 fallback
- [[llm#offline-mode]] — LLM 비가용 시 reproduction + rule composition + editor 그대로 동작

## 9. UI

- [[ui#layout]] — PySide6 + pyvistaqt + 5 patch 패널
- [[ui#undo-redo]] — Plan-level (Ctrl+Z = step 변경) + Editor-level (위젯 표준)
- [[ui#error-mapping]] — OCCT 친절 메시지 매핑
- [[ui#i18n]] — Qt tr(), KO + EN

## 10. 개발/테스트 인프라 (Rev 6 신규)

- [[dev-test#환경-분리]] — Dev (집) vs Test (회사) 워크플로
- [[dev-test#로깅-시스템]] — JSON Lines + 매 step viewport snapshot
- [[dev-test#시나리오-러너]] — Phase 별 자가검증 1-커맨드
- [[dev-test#자동-번들-메일]] — log + screenshots → zip → SMTP
- [[dev-test#fixtures]] — simple_watch.step 생성기 + 합성 부품 어셈블리

## 11. Phase 일정

상세는 [[phases]]. 요약:

| Phase | 기간 | 산출물 |
|---|---|---|
| 0. Pre-flight + 환경 + Parasolid 변환 | 2주 | 6개 spec md + 합성 fixture STEP + viewport PoC + 로깅/번들 골격 |
| 1. Skill 프레임워크 + 8 skill + 폭탄 A/B 검증 | 2주 | manifest.json + history catalog + 결정성 측정 |
| 2. Plan executor + simple_watch reproduction | 1.5주 | face-level metric 통과 + SpaceClaim sanity |
| 3. STEP topology + reverse engineering | 1.5주 | 자동 plan 생성 + 부품 분류 80%↑ |
| 4. UI + 26 skill | 3주 | 데스크탑 앱 + 워치 특화 skill |
| 5. DFM + PF-7 spike | 3주 | ray-march wall + draft + undercut, voxel spike 결정 |
| 6. Component + 합성 | 2–3주 | rule-based housing 합성 |
| 7. LLM Editor + 비용 dry-run | 1.5주 | $/step 실측 + CI mock fixture |
| 8. LLM Planner agentic + 객관 metric | 3주 | OEM 외피 face-level 비교 metric 통과 |
| 9. iPhone mesh pipeline 일반화 | 1.5주 | mesh 경로 검증 |
| **합계** | **20.5–22주** (Phase 6 의 PF-7 결과별 ±1주) | 50% 병행 시 약 40-44주 (≈ 10개월) |

Rev 5 → Rev 6: 로깅/번들/시나리오 인프라 +1.5–2주 → **18주 → 20주**.

각 phase 의 자가검증 시나리오: [[dev-test#phase-별-시나리오-카탈로그]].

## 12. 의존성

[[setup#pyproject-toml]] 참조. 핵심: build123d, pyvista, pyvistaqt, PySide6, trimesh, pygltflib,
anthropic, pydantic, pyyaml, typer, simpleeval, keyring + (로깅) `loguru`, (메일) `smtplib` (stdlib).

## 13. 검증 전략

- [[dev-test#단위-테스트]] — pytest + history map + freeze
- [[dev-test#시각-회귀]] — bbox + volume + face count (vertex hash 금지)
- [[dev-test#face-level-회귀]] — 자동 plan 결과 vs reference STEP (Rev 5)
- [[dev-test#llm-회귀]] — CI mock + nightly real
- [[dev-test#cross-platform-결정성-회귀]] — 집/회사 머신 freeze 일치율

## 14. 열린 질문

[[risks#open-questions]].

## 15. 위험 / 시한 폭탄

[[risks#risk-register]].

## 16. 다음 단계

**Phase 0 시작 전 사용자 확인 필요**:
1. Rev 6 방향성 승인
2. 리포지토리 위치 (이 repo 의 sub vs 별도)
3. 메일 송수신 SMTP 설정 — gmail 앱 비밀번호 또는 outlook (회사 → 집 메일)
4. (선택) 사용자가 더 정밀한 simple watch STEP 을 SpaceClaim 으로 만들지, 우리 합성 fixture 사용할지

**승인 시 시작**: [[phases#phase-0]] — Pre-flight 6건 + 환경 + 합성 fixture 생성 + 로깅 골격.

## 17. Rev 5 → Rev 6 변경 요약

- **[[dev-test#환경-분리]] 신설** — 집(개발) / 회사(테스트) 분리, 메일 피드백 사이클
- **[[dev-test#로깅-시스템]] 신설** — JSON Lines + viewport snapshot 자동 캡처
- **[[dev-test#시나리오-러너]] 신설** — Phase 별 자가검증 1-커맨드
- **[[dev-test#자동-번들-메일]] 신설** — log + screenshots → zip → SMTP, 회사 → 집 1-클릭
- **[[dev-test#fixtures]] 신설** — `fixtures/make_simple_watch.py` 합성 워치 STEP 생성기 (build123d 로 부품 5개 어셈블리 + 네이밍)
- **lat.md/ 지식 그래프 도입** — 단일 PHONE_DESIGNER_PLAN.md (~700줄) 을 토픽별 ~20 파일로 분해 + 위키 링크
- **회사 컴 1회 셋업 자동화** — `setup.ps1` (Windows) → venv + pip install + smoke test
- **일정 18주 → 20주** (로깅/번들/시나리오 인프라 +1.5–2주)
- **PHONE_DESIGNER_PLAN.md** 는 lat.md/ 로 가는 포인터로 축소
