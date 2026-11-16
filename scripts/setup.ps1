# ==================================================
# 설치 (Windows PowerShell)
# ==================================================
# 사용: 프로젝트 루트에서  .\scripts\setup.ps1
#   가상환경(.venv, Python 3.11 또는 3.12) -> 패키지 -> 프론트 패키지 -> .env -> 데이터·모델 확인
# 여러 번 실행해도 됩니다. 이미 있는 것은 건너뜁니다.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "[실패] $text" -ForegroundColor Red; exit 1 }

# --------------------------------------------------
# 1. Python 3.11 가상환경
# --------------------------------------------------
Step "1/5 Python 가상환경"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Allowed = @("3.11", "3.12")      # 3.11: 교수님 가이드 기준, 3.12: 검증에 쓴 버전
if (-not (Test-Path $VenvPython)) {
    $Found = $null
    foreach ($v in $Allowed) {
        & py "-$v" --version *> $null
        if ($LASTEXITCODE -eq 0) { $Found = $v; break }
    }
    if (-not $Found) {
        Fail "Python 3.11 또는 3.12가 없습니다. 설치 후 다시 실행하세요:  winget install Python.Python.3.12"
    }
    & py "-$Found" -m venv .venv
}
$Version = & $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($Allowed -notcontains $Version) {
    Fail ".venv가 Python $Version 입니다. 3.11 또는 3.12만 씁니다. .venv 폴더를 지우고 다시 실행하세요."
}
Write-Host "Python $Version (.venv)"

# --------------------------------------------------
# 2. 백엔드 패키지
# --------------------------------------------------
Step "2/5 백엔드 패키지 (처음에는 몇 분 걸립니다)"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install 실패" }
# NVIDIA 그래픽카드가 있으면 로컬 LLM이 GPU를 쓰도록 안내 (기본 설치는 CPU용 torch)
$HasCuda = & $VenvPython -c "import torch; print(torch.cuda.is_available())"
if ((Get-Command nvidia-smi -ErrorAction SilentlyContinue) -and $HasCuda -ne "True") {
    Write-Host "[안내] NVIDIA 그래픽카드가 있지만 torch가 CPU용입니다. 로컬 LLM을 빠르게 쓰려면:" -ForegroundColor Yellow
    Write-Host "       .venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall --no-deps"
}

# --------------------------------------------------
# 3. 프론트엔드 패키지
# --------------------------------------------------
Step "3/5 프론트엔드 패키지"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "Node.js가 없습니다. 설치 후 다시 실행하세요:  winget install OpenJS.NodeJS.LTS"
}
Push-Location frontend
& npm install
$NpmExit = $LASTEXITCODE
Pop-Location
if ($NpmExit -ne 0) { Fail "npm install 실패" }

# --------------------------------------------------
# 4. 환경변수 파일
# --------------------------------------------------
Step "4/5 환경변수 파일"
foreach ($pair in @(@(".env.example", ".env"), @("docker\.env.example", "docker\.env"))) {
    if (Test-Path $pair[1]) {
        Write-Host "$($pair[1]) 있음 (그대로 둠)"
    } else {
        Copy-Item $pair[0] $pair[1]
        Write-Host "$($pair[1]) 생성 ($($pair[0]) 복사)"
    }
}

# --------------------------------------------------
# 5. 데이터·모델 스냅샷 확인
# --------------------------------------------------
Step "5/5 데이터·모델 확인"
$Needed = @(
    @("data\interim\boarding.parquet", "실데이터"),
    @("models\bus\real\model.json", "예측 모델"),
    @("vectorstore\bus", "규정 검색 인덱스")
)
$Missing = @()
foreach ($item in $Needed) {
    if (Test-Path $item[0]) { Write-Host "있음  $($item[1])  ($($item[0]))" }
    else { Write-Host "없음  $($item[1])  ($($item[0]))" -ForegroundColor Yellow; $Missing += $item[1] }
}
if ($Missing.Count -gt 0) {
    Write-Host "`n없는 항목이 있습니다. 둘 중 하나를 하세요." -ForegroundColor Yellow
    Write-Host "  A) README 3-3의 Release 스냅샷 3개를 받아 프로젝트 루트에서 압축 풀기"
    Write-Host "  B) 합성 데이터로 실행:"
    Write-Host "       .venv\Scripts\python scripts\make_sample_data.py"
    Write-Host "       .venv\Scripts\python scripts\train_bus.py --mode sample"
    Write-Host "       .venv\Scripts\python scripts\build_index.py"
}

Write-Host "`n설치 완료. 실행:  .\scripts\run.ps1" -ForegroundColor Green
