# setup — 환경 셋업

> 회사 컴 1회 셋업의 모든 단계를 명시적으로 남겨놓는다. 사용자가 반복 검색·시행착오 없이
> 진행할 수 있어야 한다.

## 사전 요구사항 (양쪽 컴 공통)

- Windows 10/11
- **Python 3.11+** (3.12 권장) — `python --version` 으로 확인
  - 없으면 [Python.org installer](https://www.python.org/downloads/windows/) 로 설치, "Add Python to PATH" 체크
- **Git for Windows** — `git --version` 확인
- **인터넷 연결** (pip 의존성 다운로드 ~500MB. cadquery-ocp ~250MB 가 가장 큼)

## 회사 컴 추가 요구사항

- **SpaceClaim** — Parasolid → STEP 변환용
- **ANSYS Mechanical** — Phase 9 의 final 메쉬 검증 (선택)
- **메일 송신 자격증명**:
  - gmail 의 경우 [앱 비밀번호](https://myaccount.google.com/apppasswords) 생성 (2단계 인증 활성화 필요)
  - outlook 의 경우 SMTP `smtp.office365.com:587` + 평문 비번 가능

## 1-Command 셋업

### 회사 컴 1회 셋업

```powershell
# 1. clone (위치는 임의, 예: D:\WorkPCWorkspace)
cd D:\
git clone <repo-url> SmartTwinModeller
cd SmartTwinModeller

# 2. setup 스크립트 실행 — venv + 의존성 + smoke test
.\setup.ps1

# 3. 메일 자격증명 등록 (1회)
python -m phone_designer config mail
# → 대화형 프롬프트: 송신자, 수신자, SMTP 서버, 포트, 앱 비번
# → keyring 에 저장

# 4. (선택) ANTHROPIC_API_KEY 환경변수 — LLM 시나리오용
#    회사 컴에서는 LLM 비용 최소화 위해 mock 으로만 동작해도 OK
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 5. Galaxy Watch Parasolid → STEP 변환 (1회)
#    SpaceClaim 으로 .x_t 열기 → File → Save As → STEP (AP242)
#    → reference/galaxy_watch/converted.step 로 저장
#    상세 절차: lat.md/reference.md#parasolid-워크플로 참조

# 6. smoke 시나리오
python -m phone_designer test --scenario phase0_env_smoke
python -m phone_designer test --scenario phase0_fixture_make

# 7. 결과 메일 전송 확인
python -m phone_designer test --scenario phase0_env_smoke --mail
# → 집 컴 메일함에 zip 도착하는지 확인
```

`setup.ps1` 내부 동작: [[#setup-ps1-내용]].

### 집 컴 1회 셋업

회사 컴과 동일하되:
- SpaceClaim / ANSYS 불필요
- Galaxy Watch Parasolid 단계 (5) 생략 — fixture 만 사용
- 메일 송신자/수신자 설정 시 **수신만** 설정해도 무방

```powershell
cd D:\SmartTwinModeller
.\setup.ps1
# Anthropic API key — 집 컴은 풍부 사용
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 메일 수신 확인 (회사 → 집)
# gmail / outlook 클라이언트에서 회사 컴 메일을 받을 수 있는지만 확인
# 별도 SMTP 설정 없음 — 회사 컴에서 보낸 zip 첨부 메일을 받기만

# fixture 생성 (1회)
python fixtures/make_simple_watch.py
# → fixtures/simple_watch.step 생성

# smoke
python -m phone_designer test --scenario phase0_env_smoke
```

## 일상 워크플로

### 집 컴
```powershell
# 매 작업 시작
.\venv\Scripts\Activate.ps1   # venv 활성화 (setup.ps1 자동 생성)

# 코드 변경
# ...

# 자체 검증
pytest tests/                                                       # 단위
python -m phone_designer test --scenario phase<N>_<name>            # 시나리오

# git
git add -A
git commit -m "..."
git push
```

### 회사 컴
```powershell
.\venv\Scripts\Activate.ps1
git pull

# 검증
python -m phone_designer test --scenario <name> --mail

# 결과는 자동으로 zip + 메일
# 사용자는 그 외 디버깅 안 함 — 메일이 충분히 정보 풍부
```

## pyproject.toml

[[../pyproject.toml]] 참조. 핵심 의존성:

- CAD: `build123d`, `cadquery-ocp`
- Mesh: `trimesh`, `pygltflib`, `numpy`, `scipy`
- Viz: `pyvista`, `pyvistaqt`, `vtk`, `PySide6`
- LLM: `anthropic`
- IO/validation: `pydantic`, `pyyaml`
- CLI: `typer`
- 로깅/메일: `loguru` (구조화), `keyring` (자격증명), stdlib `smtplib`
- 보안: `simpleeval`

## setup.ps1 내용

[[../setup.ps1]] 참조. 동작:

1. Python 3.11+ 확인
2. `venv/` 생성 (없으면)
3. venv 활성화
4. `pip install --upgrade pip`
5. `pip install -e ".[dev]"` (editable + dev extras)
6. `python -c "import build123d, pyvista, PySide6"` 검증
7. `mkdir -p run_logs fixtures reference/galaxy_watch`
8. 완료 메시지 + 다음 단계 안내

실패 시 어떤 단계인지 명시 + 흔한 해결책 출력.

## 의존성 별 주의

- **cadquery-ocp** wheel 다운로드 250MB+. Windows wheel 자동.
- **VTK** ~120MB. PyVista 가 wheel 로 묶어 옴.
- **PySide6** ~180MB. LGPL.
- **bpy / FBX** — **사용 안 함** (Rev 4 결정). glb 와 OEM Parasolid → STEP 만으로 충분.

설치 실패 시 의존성 격리:
```powershell
pip install build123d
pip install pyvista pyvistaqt
pip install PySide6
pip install anthropic
pip install pydantic pyyaml typer loguru keyring simpleeval
pip install trimesh pygltflib
pip install -e .
```

## 환경 변수

| 변수 | 의미 | 기본값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM 사용 시 (없으면 자동으로 [[llm#offline-mode]]) | unset |
| `PHONE_DESIGNER_LOG_LEVEL` | 콘솔 로그 레벨 | INFO |
| `PHONE_DESIGNER_RUN_DIR` | 로그/번들 저장 위치 | `./run_logs` |
| `PHONE_DESIGNER_FIXTURES_DIR` | fixture 디렉토리 | `./fixtures` |

회사 컴은 `PHONE_DESIGNER_LOG_LEVEL=DEBUG` 권장 — 메일 1회 정보량 최대화.

## 디렉토리 구조 (셋업 후)

```
SmartTwinModeller/                      # repo root
├── lat.md/                             # 지식 그래프 (본 문서들)
├── src/phone_designer/                 # Phase 1 이후 채워짐
├── fixtures/
│   ├── make_simple_watch.py            # build123d 합성기
│   ├── make_simple_phone.py            # (선택) 추후
│   ├── simple_watch.step               # 생성됨 (gitignore)
│   ├── simple_watch_housing_only.step
│   └── simple_watch_components/
├── reference/
│   ├── galaxy_watch/                   # 회사 컴 only (gitignore)
│   │   ├── original.x_t                # 사용자 제공
│   │   ├── converted.step              # SpaceClaim 변환 결과
│   │   └── extracted/                  # 부품별 분리
│   └── iphone_12_teardown.glb          # 이미 보유, Phase 9 검증
├── scenarios/                          # 시나리오 정의 YAML
│   ├── phase0_env_smoke.yaml
│   ├── phase0_fixture_make.yaml
│   ├── phase1_skill_smoke.yaml
│   └── ...
├── plans/                              # 수기 / 자동 plan
│   └── simple_watch_outer.yaml
├── tests/                              # pytest
├── run_logs/                           # 모든 시나리오 실행 결과 (gitignore)
│   └── 20260525_143200/
│       ├── log.jsonl
│       ├── screenshots/
│       └── ...
├── docs/                               # PF-1 ~ PF-7 의 spec md (lat.md/ 와 별도, Phase 0 의 산출물)
├── pyproject.toml
├── setup.ps1
├── PHONE_DESIGNER_PLAN.md              # lat.md/lat.md 로 redirect
└── .gitignore
```

## .gitignore (핵심 항목)

```gitignore
venv/
run_logs/
reference/galaxy_watch/*.x_t
reference/galaxy_watch/*.step
reference/galaxy_watch/extracted/
fixtures/simple_watch*.step
fixtures/simple_watch_components/
manifest.json
__pycache__/
*.pyc
.env
```

`fixtures/make_simple_watch.py` 자체는 commit. 생성 결과 STEP 은 gitignore (재현 가능).
