#Requires -Version 5.1
<#
.SYNOPSIS
    Phone Designer 환경 1-Command 셋업 (Windows).

.DESCRIPTION
    venv 생성, 의존성 설치, smoke import 검증을 일괄 수행.
    회사 컴 / 집 컴 둘 다 동일 스크립트로 셋업 가능.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -Reinstall
    .\setup.ps1 -SkipFixture
#>

[CmdletBinding()]
param(
    [switch]$Reinstall,          # venv/ 지우고 처음부터
    [switch]$SkipFixture,        # fixture STEP 생성 단계 skip
    [switch]$SkipDevExtras       # dev tools (pytest, ruff, mypy) skip
)

$ErrorActionPreference = "Stop"
$REPO = $PSScriptRoot
$VENV = Join-Path $REPO "venv"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host ""
    Write-Host "[ERROR] $msg" -ForegroundColor Red
    Write-Host "도움말은 lat.md/setup.md 참조." -ForegroundColor Yellow
    exit 1
}

# 1. Python 확인 (stderr 캡처 X — PS 5.1 의 native exe stderr 가 $? 를 false 로 만듦)
Write-Step "Python 3.11+ 확인"
$pyVersion = & python --version
if ($LASTEXITCODE -ne 0 -or -not $pyVersion) {
    Fail "Python 이 PATH 에 없다. python.org 에서 설치 + 'Add to PATH' 체크."
}
Write-Host "  $pyVersion"
$verNum = $pyVersion -replace 'Python\s+(\d+\.\d+).*', '$1'
if ([version]$verNum -lt [version]"3.11") {
    Fail "Python 3.11+ 필요. 현재 $verNum."
}

# 2. venv 재생성?
if ($Reinstall -and (Test-Path $VENV)) {
    Write-Step "기존 venv 제거"
    Remove-Item -Recurse -Force $VENV
}

# 3. venv 생성
if (-not (Test-Path $VENV)) {
    Write-Step "venv 생성 → $VENV"
    & python -m venv $VENV
    if ($LASTEXITCODE -ne 0) { Fail "venv 생성 실패." }
}

# 4. 활성화
$activate = Join-Path $VENV "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) { Fail "Activate.ps1 가 없음 — venv 손상?" }
. $activate
Write-Host "  venv 활성화: $env:VIRTUAL_ENV"

# 5. pip upgrade
Write-Step "pip 업그레이드"
& python -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip 업그레이드 실패." }

# 6. 의존성 설치 (editable)
Write-Step "의존성 설치 (build123d, pyvista, PySide6, anthropic, ...)"
Write-Host "  주의: 첫 설치는 ~500MB 다운로드 (cadquery-ocp ~250MB, VTK ~120MB)"
if ($SkipDevExtras) {
    & pip install -e . --quiet
} else {
    & pip install -e ".[dev]" --quiet
}
if ($LASTEXITCODE -ne 0) {
    Fail @"
의존성 설치 실패. 가능한 원인:
  - 인터넷 단절 / 사내망 프록시
  - 디스크 공간 부족 (~2GB 필요)
  - cadquery-ocp wheel 호환 — Python 버전 / 64bit 확인
회피: 의존성을 개별 pip install 로 격리해서 어디서 실패하는지 확인.
"@
}

# 7. import 검증
Write-Step "Smoke import 검증"
$importTest = @"
import sys
mods = ["build123d", "pyvista", "pyvistaqt", "PySide6", "anthropic",
        "pydantic", "yaml", "typer", "loguru", "keyring", "simpleeval",
        "trimesh", "pygltflib", "OCP"]
fails = []
for m in mods:
    try:
        __import__(m)
        print(f"  ok  {m}")
    except ImportError as e:
        print(f"  FAIL {m}: {e}")
        fails.append(m)
sys.exit(1 if fails else 0)
"@
$importTest | & python -
if ($LASTEXITCODE -ne 0) { Fail "일부 모듈 import 실패 — 위 로그 확인" }

# 8. 디렉토리 준비
Write-Step "런타임 디렉토리 준비"
foreach ($d in @("run_logs", "fixtures", "reference\galaxy_watch", "plans", "scenarios")) {
    $p = Join-Path $REPO $d
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        Write-Host "  + $d"
    }
}

# 9. fixture STEP 생성 (집 컴 + 회사 컴 공통, OEM 없이도 파이프라인 검증 가능)
if (-not $SkipFixture) {
    Write-Step "합성 워치 fixture STEP 생성"
    $fixScript = Join-Path $REPO "fixtures\make_simple_watch.py"
    if (Test-Path $fixScript) {
        & python $fixScript
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [warn] fixture 생성 실패 (build123d API 변동 가능) — 수동 실행 필요" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [warn] $fixScript 없음 — skip" -ForegroundColor Yellow
    }
}

# 10. 완료
Write-Step "셋업 완료"
Write-Host ""
Write-Host "  다음 단계:" -ForegroundColor Green
Write-Host "    1. (회사 컴) python -m phone_designer config mail   # 메일 자격증명 등록"
Write-Host "    2. (회사 컴) Galaxy Watch .x_t → SpaceClaim 으로 변환 → reference/galaxy_watch/converted.step"
Write-Host "    3. Smoke 시나리오:"
Write-Host "         python -m phone_designer test --scenario phase0_env_smoke"
Write-Host "    4. (회사 컴) 결과 메일 전송 테스트:"
Write-Host "         python -m phone_designer test --scenario phase0_env_smoke --mail"
Write-Host ""
Write-Host "  자세한 절차: lat.md\setup.md" -ForegroundColor Green
Write-Host ""
