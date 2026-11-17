#!/usr/bin/env bash
# ==================================================
# 설치 (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/setup.sh
#   가상환경(.venv, Python 3.11 또는 3.12) -> 패키지 -> 프론트 패키지 -> .env -> 데이터·모델 확인 -> 길안내용 전체 노선
# 여러 번 실행해도 됩니다. 이미 있는 것은 건너뜁니다.

set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n== %s\n' "$1"; }
fail() { printf '[실패] %s\n' "$1" >&2; exit 1; }

# --------------------------------------------------
# 1. Python 3.11 가상환경
# --------------------------------------------------
step "1/6 Python 가상환경"
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
step "2/6 백엔드 패키지 (처음에는 몇 분 걸립니다)"
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt
if [ "$(uname)" = "Darwin" ] && ! brew list libomp >/dev/null 2>&1; then
    echo "[주의] macOS에서 XGBoost를 쓰려면 libomp가 필요합니다:  brew install libomp"
fi

# --------------------------------------------------
# 3. 프론트엔드 패키지
# --------------------------------------------------
step "3/6 프론트엔드 패키지"
command -v npm >/dev/null 2>&1 || fail "Node.js가 없습니다. 설치 후 다시 실행하세요:  brew install node"
(cd frontend && npm install)

# --------------------------------------------------
# 4. 환경변수 파일
# --------------------------------------------------
step "4/6 환경변수 파일"
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
step "5/6 데이터·모델 확인"
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

# --------------------------------------------------
# 6. 길안내용 서울 버스 전체 노선 (없으면 길안내가 예측하는 5개 노선만 앎)
# --------------------------------------------------
step "6/6 길안내용 전체 노선 정류장"
network="data/interim/network/stops.parquet"
has_key="$(.venv/bin/python -c 'from app.config import DATA_GO_KR_API_KEY as k; print(bool(k))')"
if [ ! -f "data/raw/서울시버스노선ID정보(20260902).xlsx" ]; then
    echo "건너뜀: 노선 목록 파일(data/raw)이 없습니다. 합성 데이터로는 전체 노선 길안내를 쓰지 않습니다."
elif [ "$has_key" != "True" ]; then
    if [ -f "$network" ]; then
        echo "있음  전체 노선 정류장  ($network)"
    else
        echo "없음  전체 노선 정류장: .env의 DATA_GO_KR_API_KEY가 비어 있어 받지 못했습니다."
        echo "      공공데이터포털에서 '서울특별시_버스노선정보조회' 활용신청 후 디코딩 키를 .env에 넣고 다시 실행하세요."
        echo "      (없어도 실행됩니다. 길안내가 5개 노선만 안내합니다.)"
    fi
else
    # 이미 받은 노선은 건너뛰고, 예전 형식(거리·시간표 없음)이면 다시 받음. 처음에는 약 5분
    .venv/bin/python scripts/fetch_network_stops.py \
        || echo "[주의] 전체 노선 정류장을 받지 못했습니다. 나중에 다시 실행하면 이어서 받습니다."
fi

printf '\n설치 완료. 실행:  bash scripts/run.sh\n'
