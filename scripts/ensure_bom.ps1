<#
.SYNOPSIS
    setup.ps1 (및 한글 주석 포함된 .ps1) 에 UTF-8 BOM 보장.

.DESCRIPTION
    PowerShell 5.1 (Windows) 은 BOM 없는 UTF-8 .ps1 의 한글 주석을 CP949 로 잘못
    해석해 파서 깨짐. 본 스크립트는 .ps1 들에 BOM 이 없으면 추가한다.
    git pull 후 line-ending 보존 잘 되면 BOM 도 보존되지만, 일부 git 설정에서
    BOM 이 stripped 될 수 있어 보호.

.EXAMPLE
    .\scripts\ensure_bom.ps1
#>

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$utf8WithBom = [System.Text.UTF8Encoding]::new($true)

$ps1Files = Get-ChildItem -Path $repo -Filter "*.ps1" -Recurse |
    Where-Object { $_.FullName -notmatch "\\venv\\" }

foreach ($f in $ps1Files) {
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if (-not $hasBom) {
        $text = [System.IO.File]::ReadAllText($f.FullName, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($f.FullName, $text, $utf8WithBom)
        Write-Host "  + BOM added: $($f.FullName)" -ForegroundColor Green
    } else {
        Write-Host "  ok          : $($f.FullName)"
    }
}
