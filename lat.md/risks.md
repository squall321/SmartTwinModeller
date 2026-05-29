# risks — 위험과 열린 질문

## risk-register

### 시한 폭탄 (사전 검증 필요)

| # | 폭탄 | 검증 시점 | 미검증 시 영향 |
|---|---|---|---|
| **A** | OCCT history map propagate 가 30 skill 중 어디서 깨지나 | [[phases#phase-1]] 끝 의 확장 검증 (7-8 skill catalog) | Phase 4-6 에서 폭발, 일정 +1-2주 |
| **B** | OCCT cross-platform 결정성 한계 | [[phases#phase-1]] 끝 (집 vs 회사 head-to-head) | LLM agentic plan 이 CI 에서 깨짐, 디버깅 +1주 |
| **C** | Anthropic prompt caching 실제 hit 율 + 비용 | [[phases#phase-7]] 시작 시 0.5주 dry-run | Phase 8 비용 한도 가정 깨짐 |
| **D** | Galaxy Watch Parasolid → STEP 의 부품 네이밍 손실 | [[decisions#PF-3]] 회사 컴 1회 변환 시 | Phase 3 의 자동 부품 분류 무력화 |
| **E** | [[decisions#PF-7]] voxel 알고리즘 작동 안 함 | Phase 5 말 spike | Phase 6 ribbing 불가, 백업안 / scope 축소 |

### 기타 위험

| 위험 | 영향 | 대응 |
|---|---|---|
| **OCCT history map 30% 깨짐** | selector fallback chain → 결정성 약화 | [[skills#깨짐-catalog]] 명시 + 사용자 경고 UX + freeze mismatch 명시 에러 |
| **Cross-platform strict 50% 미만** | 다른 OS plan 재실행 못 함 | default = same-machine strict, cross = loose, README 한계 명시 |
| **OEM CAD 부품 네이밍 inconsistent** | naming rule 매칭 실패 | unknown 분류 + UI 수동 + naming rule 점진 학습 |
| **disc_with_dome 의 dome 곡률 표현 한계** | 워치 base 부정확 | build123d BSplineSurface 또는 sphere section + loft. PF-3 의 dome 곡률 파악 후 결정 |
| **build123d polynomial pocket 미지원** | skill 일부 막힘 | OCP API 직접 사용, 1-2일 추가 |
| **OEM CAD 가 외피만, 부품 분리 안 됨** | 자동 추출 무력화 | 사용자 수기 추가 (USER_DEFINED) + 일반 카탈로그 fallback |
| **LLM caching hit 율 낮음** | 비용 2-3배 | Phase 7 dry-run + 한도 재조정 + 토큰 minimization |
| **wall thickness ray-march false positive** | 사용자 불신 | UI confidence 색상 + 사용자 무시 가능 + v0.3 개선 |
| **PySide6 + pyvistaqt 미묘한 호환** | UI 불안정 | PF-4 결정 시 확인 + PyQt6 fallback (라이선스 사후 정리) |
| **OCCT 에러 매핑 안 된 패턴** | 사용자가 cryptic 에러 봄 | [[ui#error-mapping]] dict 점진 확장 + raw fallback toggle |
| **Plan schema migration 누적** | v3, v4 도달 시 chain 복잡 | snapshot ↔ migration baseline 정기 갱신 |
| **회사 → 집 메일 전송 실패 (SMTP/방화벽)** | 피드백 루프 깨짐 | gmail 앱 비번 / outlook SMTP 2가지 옵션 + 실패 시 zip 만 로컬 저장 |
| **첨부 zip 크기 > 25MB (gmail)** | 메일 전송 실패 | PNG 압축률 ↑, DEBUG → INFO 다운샘플, 분할 첨부 |
| **회사 컴 Python/패키지 보안정책** | 일부 의존성 설치 차단 | setup.ps1 에 단계별 실패 진단 + 사내 mirror PyPI 대응 |
| **메일 latency** | 사이클당 반나절~하루 | 집 컴 자체 검증 최대화, 회사 호출 최소화 |
| **두 번째 reference (Phase 9 폰) 정밀도 부족** | mesh pipeline 검증 약화 | iPhone glb 정밀도가 임계값 이하면 mesh pipeline scope 축소 |

## open-questions

진행 중 결정될 항목 ([[decisions#향후-결정-예정]] 와 동일):

| # | 항목 | 옵션 | 결정 시점 |
|---|---|---|---|
| 1 | CAD 백엔드 보조 | build123d only / cadquery 동시 / OCP 직접 | Phase 1 |
| 2 | 합성 평가 v1 | rule 점수 / LLM 평가 / 사용자 피드백 | Phase 8 |
| 3 | DFM v0.3 정확도 | medial axis / cross-section / FEA-lite | Phase 5 종료 후 |
| 4 | 공정 카탈로그 확장 | sintering, MIM, ceramic | Phase 5 이후 |
| 5 | Parasolid 변환 자동화 v0.2 | manual / SpaceClaim Python / Parasolid SDK | v0.2 |
| 6 | MCP 노출 | 본 도구를 MCP server 로 | Phase 8 이후 |
| 7 | 리포지토리 위치 | SmartTwinModeller sub vs 별도 | Phase 0 시작 시 |
| 8 | LLM 모델 | Opus 4.7 고정 / Sonnet fallback / Haiku 보조 | Phase 7 dry-run |
| 9 | 부품 메타데이터 보강 UX | 수동 다이얼로그 / LLM 추론 / template inheritance | Phase 6 |
| 10 | 두 번째 기기 (Phase 9 이후) | 다른 사이즈 워치 / Apple Watch / 폴더블 | Phase 9 이후 |
| 11 | 메일 송신: gmail vs outlook | Phase 7 dry-run 시 둘 다 테스트 후 사용자 선호 | Phase 0 |
| 12 | 회사 컴 → 집 메일 자동화 (수동 Send Report vs 시나리오 종료 시 자동) | UX 결정 | Phase 0 |

## 위험 우선순위 (Phase 0 ~ 1 까지의 critical)

1. **시한 폭탄 D (Parasolid 네이밍 보존)** — Phase 0 의 PF-3 에서 즉시 검증.
   네이밍 손실 시 Phase 3 의 자동 분류가 무력화되므로 SpaceClaim 변환 옵션을 정확히 잡아야 함.
2. **메일 전송 dry-run** — Phase 0 의 `phase0_env_smoke --mail` 로 즉시 검증.
   실패 시 SMTP 자격증명 / 방화벽 / gmail 앱 비밀번호 절차 다시.
3. **시한 폭탄 A/B (OCCT history + cross-platform)** — Phase 1 끝의 확장 검증.
   70% / 50% 임계값 미만이면 즉시 spec 갱신.
4. **시한 폭탄 E (voxel ribbing)** — Phase 5 말 spike. 백업안 준비됨.

## 일정 risk

[[phases#일정-요약]] 의 20주 가정이 깨질 시나리오:

- OCCT propagate 70% 미만 → +1-2주
- cross-platform 결정성 50% 미만 → +0.5주 (정책 재검토)
- voxel 알고리즘 모두 부적합 → Phase 6 scope 축소 (-0.5-1주 절감, 기능 ↓)
- 메일 전송 SMTP 환경 trouble → +0.5-1주
- 회사 컴 패키지 보안 정책 차단 → +1주 (사내 mirror 협상)

worst case: 20주 → 24-25주 / 50% 병행 50주 (≈ 12개월).
