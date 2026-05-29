# scenarios — 자가검증 시나리오 정의

회사 컴에서 사용자가 1-커맨드로 실행하는 reproducible test 정의.
실행 시 자동으로 로그 + 스크린샷 + 결과를 zip 으로 묶어 메일 전송 가능.

## 실행

```powershell
python -m phone_designer test --scenario <name>            # 로컬 실행만
python -m phone_designer test --scenario <name> --mail     # zip + 메일
```

UI 의 `Test ▶ Run Scenario...` 메뉴에서도 가능.

## 시나리오 카탈로그

[[../lat.md/dev-test.md#phase-별-시나리오-카탈로그]] 참조.

## 시나리오 YAML 스키마

각 시나리오 = YAML 1개. 핵심 필드:

| 필드 | 의미 |
|---|---|
| `name` | 시나리오 식별자 (파일명과 동일) |
| `description` | 1-3줄 사람용 설명 |
| `phase` | Phase 0-9 중 어느 것의 검증인지 |
| `inputs` | 입력 자산 (STEP/glb 경로 등) |
| `steps` | 실행 단계 list (kind = 시나리오 러너 액션 종류) |
| `expected` | 검증 임계값 (face count, volume, outcome 등) |
| `mail` | 메일 본문 템플릿 (옵션) |

`steps[*].kind` 종류 (시나리오 러너가 해석):

| Kind | 의미 |
|---|---|
| `import_check` | Python 모듈 import 검증 |
| `run_python` | 스크립트 실행 |
| `check_file_exists` | 파일 존재 검증 |
| `load_step` | STEP 로드 → 변수에 바인딩 |
| `read_xde_step` | XDE 어셈블리 + 네이밍 추출 |
| `extract_part` | XDE 부품 1개 분리 |
| `load_plan` | Plan YAML 로드 |
| `execute_plan` | Plan executor 실행 |
| `compare` | 두 entity face/edge/volume/bbox 비교 |
| `viewport_offscreen` | PyVista offscreen 캡처 |
| `check_parts` | 추출된 부품 이름/개수 검증 |
| `face_count` | 부분별 face count 범위 검증 |
| `dfm_validate` | DFM v0 검증 |
| `mail_dryrun` | SMTP 자격증명만 검증 |
| `log_summary` | 로그 레벨 카운트 검증 |
| `system_info` | 환경 정보 캡처 |

신규 kind 가 필요하면 [[../src/phone_designer/scenarios/runner.py]] 에 추가.

## 디렉토리

```
scenarios/
├── README.md                   # 본 파일
├── phase0_env_smoke.yaml
├── phase0_fixture_make.yaml
├── phase1_skill_smoke.yaml
├── phase1_determinism.yaml
├── phase1_history_catalog.yaml
├── phase2_simple_watch_repro.yaml
├── phase2_spaceclaim_sanity.yaml     # requires_oem 표시
├── phase2_oem_watch_repro.yaml       # requires_oem 표시
├── ...
```

## requires_oem 표시

OEM CAD 가 필요한 시나리오는 yaml 에 `requires_oem: true`. 집 컴에서 실행 시
자동 skip + 메일 보고에 SKIPPED.
