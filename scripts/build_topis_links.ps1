# ==================================================
# 노선 ↔ TOPIS 링크 대응표 (키 필요: SEOUL_OPEN_API_KEY) (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\build_topis_links.ps1
#   1) 링크 약 5천 개의 꼭짓점을 받아(중간에 멈추면 이어 받음)
#   2) 노선 경로와 겹치는 링크 → data\interim\topis\route_links.parquet
# 끝나면 API 서버를 다시 시작해야 표를 읽습니다. 설명: scripts\build_topis_links.py

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[실패] .venv가 없습니다. 먼저  .\scripts\setup.ps1  을 실행하세요." -ForegroundColor Red
    exit 1
}

& $VenvPython scripts\build_topis_links.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
