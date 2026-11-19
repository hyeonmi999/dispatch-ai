# ==================================================
# 실행 (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\run.ps1
#   MariaDB(Docker) -> API 서버(새 창, 8000) -> 화면(이 창, 5173)
# 끝낼 때: 이 창에서 Ctrl+C, API 서버 창은 닫기

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ApiPort = 8000
$WebPort = 5173
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[실패] .venv가 없습니다. 먼저  .\scripts\setup.ps1  을 실행하세요." -ForegroundColor Red
    exit 1
}

# --------------------------------------------------
# 1. 같은 포트를 잡고 있는 이전 프로세스 확인
# --------------------------------------------------
foreach ($port in @($ApiPort, $WebPort)) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        Write-Host "[실패] 포트 $port 를 이미 쓰고 있습니다: $($proc.ProcessName) (PID $($conn.OwningProcess))" -ForegroundColor Red
        Write-Host "       이전 서버를 끄고 다시 실행하세요:  Stop-Process -Id $($conn.OwningProcess)"
        exit 1
    }
}

# --------------------------------------------------
# 2. MariaDB (없어도 API는 동작, 이력만 저장 안 됨)
# --------------------------------------------------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker compose -p dispatch_ai -f docker/maria_db.yml up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[주의] MariaDB를 띄우지 못했습니다. Docker Desktop이 켜져 있는지 확인하세요. DB 없이 계속합니다." -ForegroundColor Yellow
    }
} else {
    Write-Host "[주의] Docker가 없어 DB 없이 실행합니다 (응답에 db_saved: false)." -ForegroundColor Yellow
}

# --------------------------------------------------
# 3. API 서버 (새 창) + 화면 (이 창)
# --------------------------------------------------
Start-Process powershell -WorkingDirectory $Root -ArgumentList @(
    "-NoExit", "-Command",
    "& '$VenvPython' -m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort"
)
Write-Host "API 서버: http://127.0.0.1:$ApiPort/docs (새 창)" -ForegroundColor Green
Write-Host "화면:     http://localhost:$WebPort" -ForegroundColor Green

Set-Location frontend
& npm run dev
