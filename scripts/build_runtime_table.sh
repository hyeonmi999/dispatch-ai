#!/usr/bin/env bash
# ==================================================
# 시간대별 한 바퀴 운행시간 표 (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/build_runtime_table.sh
#   1) OA-21217 구간 운행시간 받기 (5개 노선만 남김, 약 0.8GB 받아 약 3MB로. 받은 조각은 건너뜀)
#   2) 표 만들기 → data/interim/runtime_table.parquet
# 끝나면 API 서버를 다시 시작해야 표를 읽습니다. 설명: scripts/build_runtime_table.py

set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv/bin/python ] || { echo "[실패] .venv가 없습니다. 먼저  bash scripts/setup.sh  를 실행하세요." >&2; exit 1; }

.venv/bin/python scripts/download_seoul_bus.py --datasets OA-21217
.venv/bin/python scripts/build_runtime_table.py
