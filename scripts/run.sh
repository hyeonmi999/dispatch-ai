#!/usr/bin/env bash
# ==================================================
# 실행 (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/run.sh
#   MariaDB(Docker) -> API 서버(8000) -> 화면(5173)
# 끝낼 때: Ctrl+C (API 서버도 함께 종료)

set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT=8000
WEB_PORT=5173

[ -x .venv/bin/python ] || { echo "[실패] .venv가 없습니다. 먼저  bash scripts/setup.sh  를 실행하세요." >&2; exit 1; }

# --------------------------------------------------
# 1. 같은 포트를 잡고 있는 이전 프로세스 확인
# --------------------------------------------------
for port in "$API_PORT" "$WEB_PORT"; do
    pid="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
    if [ -n "$pid" ]; then
        echo "[실패] 포트 $port 를 이미 쓰고 있습니다 (PID $pid)." >&2
        echo "       이전 서버를 끄고 다시 실행하세요:  kill $pid" >&2
        exit 1
    fi
done

# --------------------------------------------------
# 2. MariaDB (없어도 API는 동작, 이력만 저장 안 됨)
# --------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    docker compose -p dispatch_ai -f docker/maria_db.yml up -d \
        || echo "[주의] MariaDB를 띄우지 못했습니다. Docker Desktop이 켜져 있는지 확인하세요. DB 없이 계속합니다."
else
    echo "[주의] Docker가 없어 DB 없이 실행합니다 (응답에 db_saved: false)."
fi

# --------------------------------------------------
# 3. API 서버 (백그라운드) + 화면 (이 터미널)
# --------------------------------------------------
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM

echo "API 서버: http://127.0.0.1:$API_PORT/docs"
echo "화면:     http://localhost:$WEB_PORT"

cd frontend
npm run dev
