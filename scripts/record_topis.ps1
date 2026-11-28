# ==================================================
# 시연용 실시간 기록 모으기 (키 필요: SEOUL_OPEN_API_KEY) (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\record_topis.ps1
#   1) 5분마다 120분 동안 실시간 돌발·소통을 받아
#   2) data\interim\topis\snapshots\에 남김. 인자는 그대로 넘김 (예: --every 5 --minutes 60)
# 설명: scripts\record_topis.py

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[실패] .venv가 없습니다. 먼저  .\scripts\setup.ps1  을 실행하세요." -ForegroundColor Red
    exit 1
}

& $VenvPython scripts\record_topis.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
