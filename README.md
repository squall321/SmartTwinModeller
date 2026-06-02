# Phone Designer

AI-assisted watch/phone housing designer (build123d + Claude + PyVista).

## 진입점

→ **[lat.md/lat.md](lat.md/lat.md)** — 전체 지식 그래프 인덱스

## 현재 상태 (집 컴 검증, 2026-05-26)

| 영역 | 결과 |
|---|---|
| Python 3.13.7 + venv + 의존성 | ✓ 설치 OK (build123d 0.10, OCP 7.8, PySide6 6.11, VTK 9.6) |
| `pytest tests` 전체 | ✓ **163/163 PASS** |
| `python fixtures/make_simple_watch.py` | ✓ 7 STEP 생성 (housing top fillet R=2 만 fail — Phase 4 polynomial 작업) |
| Phase 0/1/2/3 시나리오 전체 | ✓ PASS (oem repro = SKIPPED, 정상) |
| `phone-designer.skills.audit` | ✓ **29/29 skill propagate match 100%** (목표 70%) |
| **Phase 3 STEP topology + reverse engineer** | ✓ 완료 |
| **Phase 4 Skill 라이브러리 (5 batch, 29 skill)** | ✓ 완료 (polynomial_pocket 만 P1 v2) |
| **PySide6 inspector GUI** | ✓ 본격 시작 ([src/phone_designer/ui/inspector_main.py](src/phone_designer/ui/inspector_main.py)) |

회사 컴에서 추가 검증할 항목: [lat.md/work-pc-tests.md](lat.md/work-pc-tests.md)
발견된 작업 backlog: [lat.md/backlog.md](lat.md/backlog.md)

### 직접 실행

```powershell
.\venv\Scripts\Activate.ps1

# GUI inspector (plan 실행 + viewport + reference overlay)
python -m phone_designer inspect --plan plans\simple_watch_outer.yaml `
                                  --reference fixtures\simple_watch_housing_only.step

# Reference STEP → 자동 plan → 재현 → STEP export (Phase 3)
python -m phone_designer reproduce --reference fixtures\simple_watch_housing_only.step `
                                    --out out\auto.step `
                                    --plan-out out\auto_plan.yaml

# 자동 plan 결과 viewer
python -m phone_designer inspect --step out\auto.step `
                                  --reference fixtures\simple_watch_housing_only.step
```

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

- **Full pytest** (informational): `pytest -q` — runs the entire suite incl. slow/UI cases; not all results block merge.
- **Round-trip strict gate** (must-pass): `$env:FIDELITY_STRICT="1"; pytest -m fidelity`
  — every parametrized round-trip case is a hard FAIL. Any new code that breaks
  the reverse-engineering pipeline (cube collapse OR volume drift > tolerance)
  blocks merge.
  - Convenience wrapper: `pwsh scripts\ci_fidelity_gate.ps1`
  - Default (no env var): same test runs but currently-failing cases are
    reported as `xfail` so local dev runs stay green.

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
```

`corpus/oem/_sample/` 에 in-repo smoke fixture 가 있어 CLI 자체는 git
클론 직후에도 동작. 보고서는 `original_vol / regen_vol / drift % /
face_count / status (PASS / DRIFT / CUBE_COLLAPSE / EXEC_FAIL / ERROR)` 컬럼의
마크다운 테이블. exit code `0 = all within tolerance`, 그 외 `1`.

## 주요 문서

- 전체 계획: [lat.md/plan.md](lat.md/plan.md)
- 집/회사 워크플로 + 로깅/메일: [lat.md/dev-test.md](lat.md/dev-test.md)
- 개념 (Skill / Plan / Selector / Component): [lat.md/concepts.md](lat.md/concepts.md)
- Phase 별 계획: [lat.md/phases.md](lat.md/phases.md)
- Pre-flight 결정 (PF-1~7): [lat.md/decisions.md](lat.md/decisions.md)
- 위험 / 시한 폭탄: [lat.md/risks.md](lat.md/risks.md)

## 환경

- **집 컴** (이 머신): 개발 + 자체 시나리오 검증. 합성 fixture STEP 사용.
- **회사 컴**: 실제 OEM Galaxy Watch Parasolid CAD 테스트. 결과는 로그/스크린샷 zip → 메일 → 집 컴.

자세히는 [lat.md/dev-test.md#환경-분리](lat.md/dev-test.md#환경-분리).
