#!/usr/bin/env bash
# ==================================================
# 평소 링크 속도 표 (TOPIS 서울시 차량통행속도, 키 없음) (macOS · Linux)
# ==================================================
# 사용: 프로젝트 루트에서  bash scripts/build_topis_speed.sh
#   1) 최근 2개월 월별 xlsx를 받아(달마다 약 35MB, 받은 달은 건너뜀)
#   2) 링크 × 요일유형 × 시간대 중앙값 → data/interim/topis/link_speed.parquet
# 끝나면 API 서버를 다시 시작해야 표를 읽습니다. 설명: scripts/build_topis_speed.py

set -euo pipefail
cd "$(dirname "$0")/.."

[ -x .venv/bin/python ] || { echo "[실패] .venv가 없습니다. 먼저  bash scripts/setup.sh  를 실행하세요." >&2; exit 1; }

.venv/bin/python scripts/build_topis_speed.py
