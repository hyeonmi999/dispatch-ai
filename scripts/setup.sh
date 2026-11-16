#!/usr/bin/env bash
# ==================================================
# 설치 (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/setup.sh
#   가상환경(.venv, Python 3.11 또는 3.12) -> 패키지 -> 프론트 패키지 -> .env -> 데이터·모델 확인
# 여러 번 실행해도 됩니다. 이미 있는 것은 건너뜁니다.

set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s\n' "$1"; }
fail() { printf '[실패] %s\n' "$1" >&2; exit 1; }

# --------------------------------------------------
# 1. Python 3.11 가상환경
# --------------------------------------------------
step "1/5 Python 가상환경"
# 3.11: 교수님 가이드 기준, 3.12: 검증에 쓴 버전
if [ ! -x .venv/bin/python ]; then
    py=""
    for v in 3.11 3.12; do
        if command -v "python$v" >/dev/null 2>&1; then py="python$v"; break; fi
    done
    [ -n "$py" ] || fail "Python 3.11 또는 3.12가 없습니다. 설치 후 다시 실행하세요:  brew install python@3.12"
    "$py" -m venv .venv
fi
version="$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$version" in
    3.11|3.12) ;;
    *) fail ".venv가 Python $version 입니다. 3.11 또는 3.12만 씁니다. .venv 폴더를 지우고 다시 실행하세요." ;;
esac
echo "Python $version (.venv)"

# --------------------------------------------------
# 2. 백엔드 패키지
# --------------------------------------------------
step "2/5 백엔드 패키지 (처음에는 몇 분 걸립니다)"
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt
if [ "$(uname)" = "Darwin" ] && ! brew list libomp >/dev/null 2>&1; then
    echo "[주의] macOS에서 XGBoost를 쓰려면 libomp가 필요합니다:  brew install libomp"
fi

# --------------------------------------------------
# 3. 프론트엔드 패키지
# --------------------------------------------------
step "3/5 프론트엔드 패키지"
command -v npm >/dev/null 2>&1 || fail "Node.js가 없습니다. 설치 후 다시 실행하세요:  brew install node"
(cd frontend && npm install)

# --------------------------------------------------
# 4. 환경변수 파일
# --------------------------------------------------
step "4/5 환경변수 파일"
for pair in ".env.example:.env" "docker/.env.example:docker/.env"; do
    src="${pair%%:*}"; dst="${pair##*:}"
    if [ -f "$dst" ]; then
        echo "$dst 있음 (그대로 둠)"
    else
        cp "$src" "$dst"
        echo "$dst 생성 ($src 복사)"
    fi
done

# --------------------------------------------------
# 5. 데이터·모델 스냅샷 확인
# --------------------------------------------------
step "5/5 데이터·모델 확인"
missing=0
check() {
    if [ -e "$1" ]; then echo "있음  $2  ($1)"; else echo "없음  $2  ($1)"; missing=1; fi
}
check data/interim/boarding.parquet "실데이터"
check models/bus/real/model.json "예측 모델"
check vectorstore/bus "규정 검색 인덱스"
if [ "$missing" = "1" ]; then
    cat <<'EOF'

없는 항목이 있습니다. 둘 중 하나를 하세요.
  A) README 3-3의 Release 스냅샷 3개를 받아 프로젝트 루트에서 압축 풀기
  B) 합성 데이터로 실행:
       .venv/bin/python scripts/make_sample_data.py
       .venv/bin/python scripts/train_bus.py --mode sample
       .venv/bin/python scripts/build_index.py
EOF
fi

printf '\n설치 완료. 실행:  bash scripts/run.sh\n'
