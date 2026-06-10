# Round-trip fidelity strict gate — CI must-pass.
# Any new code that breaks the RE pipeline (cube collapse OR volume drift > tol)
# will hard-fail this script and block merge.
#
# Usage (local):
#     pwsh scripts\ci_fidelity_gate.ps1
#
# Usage (CI):
#     run the same command in the pipeline; nonzero exit blocks merge.

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
$env:FIDELITY_STRICT = "1"

# Fixtures are gitignored — regenerate so the strict gate never skips vacuously.
if (-not (Test-Path "fixtures\generated")) {
    & "venv\Scripts\python.exe" scripts\build_fidelity_fixtures.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& "venv\Scripts\python.exe" -m pytest -q -m fidelity
exit $LASTEXITCODE
