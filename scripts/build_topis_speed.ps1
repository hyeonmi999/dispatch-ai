# ==================================================
# 평소 링크 속도 표 (TOPIS 서울시 차량통행속도, 키 없음) (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\build_topis_speed.ps1
#   1) 최근 2개월 월별 xlsx를 받아(달마다 약 35MB, 받은 달은 건너뜀)
#   2) 링크 × 요일유형 × 시간대 중앙값 → data\interim\topis\link_speed.parquet
# 끝나면 API 서버를 다시 시작해야 표를 읽습니다. 설명: scripts\build_topis_speed.py

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[실패] .venv가 없습니다. 먼저  .\scripts\setup.ps1  을 실행하세요." -ForegroundColor Red
    exit 1
}

& $VenvPython scripts\build_topis_speed.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
