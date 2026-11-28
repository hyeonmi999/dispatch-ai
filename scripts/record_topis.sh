#!/usr/bin/env bash
# ==================================================
# 시연용 실시간 기록 모으기 (키 필요: SEOUL_OPEN_API_KEY) (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/record_topis.sh
#   1) 5분마다 120분 동안 실시간 돌발·소통을 받아
#   2) data/interim/topis/snapshots/에 남김. 인자는 그대로 넘김 (예: --every 5 --minutes 60)
# 설명: scripts/record_topis.py

set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv/bin/python ] || { echo "[실패] .venv가 없습니다. 먼저  bash scripts/setup.sh  를 실행하세요." >&2; exit 1; }

.venv/bin/python scripts/record_topis.py "$@"
