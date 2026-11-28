# ==================================================
# 시간대별 한 바퀴 운행시간 표 (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\build_runtime_table.ps1
#   1) OA-21217 구간 운행시간 받기 (5개 노선만 남김, 약 0.8GB 받아 약 3MB로. 받은 조각은 건너뜀)
#   2) 표 만들기 -> data\interim\runtime_table.parquet
# 끝나면 API 서버를 다시 시작해야 표를 읽습니다. 설명: scripts\build_runtime_table.py

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[실패] .venv가 없습니다. 먼저  .\scripts\setup.ps1  을 실행하세요." -ForegroundColor Red
    exit 1
}

& $VenvPython scripts\download_seoul_bus.py --datasets OA-21217
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython scripts\build_runtime_table.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
