"""
시간대별 한 바퀴 운행시간 표  (이슈 #42 방법 1, docs/plans/park-next.md Task P-7)

OA-21217 구간 운행시간(data/interim/runtime.parquet, scripts/download_seoul_bus.py --datasets OA-21217)으로
날짜 × 노선 × 기점 출발 시각별 한 바퀴 시간을 만들고, 기준일(매주 일요일)마다 최근 8주 중앙값 표를 만듭니다.
계산 방식·설정은 app/domains/bus/runtime.py, app/config.py의 BUS_RUNTIME_*.

출력 (커밋 제외)
  data/interim/runtime_trip.parquet   하루 값 (date, route, hour, minutes, fill)
  data/interim/runtime_table.parquet  표 (asof, route, daytype, hour, minutes, days) → 서버가 읽음 (서버 재시작 필요)

사용
  bash scripts/build_runtime_table.sh       # 받기 + 표 만들기 (macOS)
  .\\scripts\\build_runtime_table.ps1          # Windows
  python scripts/build_runtime_table.py     # 이미 받은 원본으로 표만 다시 만들기
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import BUS_RUNTIME_TABLE, BUS_RUNTIME_TRIPS, BUS_RUNTIME_WEEKS, INTERIM_DIR  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.runtime import build_table, daily_trips  # noqa: E402

SOURCE = INTERIM_DIR / "runtime.parquet"
SHOW_HOURS = [5, 8, 13, 18, 22]


def main():
    parser = argparse.ArgumentParser(description="시간대별 한 바퀴 운행시간 표 만들기")
    parser.add_argument("--source", type=Path, default=SOURCE, help="OA-21217 노선 필터링 결과")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"원본이 없습니다: {args.source}\n먼저 실행: python scripts/download_seoul_bus.py --datasets OA-21217")

    segments = pd.read_parquet(args.source)
    days = segments.groupby("노선명")["기준_날짜"].nunique()
    trips = daily_trips(segments)
    kept = trips.groupby("route")["date"].nunique()
    print(f"원본 {len(segments):,}행 · {segments['기준_날짜'].min()} ~ {segments['기준_날짜'].max()}")
    for route in sorted(days.index):
        print(f"  {route}번: {days[route]}일 중 {int(kept.get(route, 0))}일 사용")

    table = build_table(trips)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    trips.to_parquet(INTERIM_DIR / BUS_RUNTIME_TRIPS, index=False)
    table.to_parquet(INTERIM_DIR / BUS_RUNTIME_TABLE, index=False)
    print(f"하루 값 {len(trips):,}행 → {(INTERIM_DIR / BUS_RUNTIME_TRIPS).relative_to(ROOT)}")
    print(f"표 {len(table):,}행, 기준일 {table['asof'].nunique()}개 → {(INTERIM_DIR / BUS_RUNTIME_TABLE).relative_to(ROOT)}")
    if table.empty:
        return

    # 마지막 기준일의 평일 값과 노선 기본정보 고정값 비교 (확인용)
    info = BusDataset("real").route_info
    last = table[(table["asof"] == table["asof"].max()) & (table["daytype"] == "weekday")]
    print(f"\n기준일 {table['asof'].max():%Y-%m-%d} 평일, 최근 {BUS_RUNTIME_WEEKS}주 중앙값 (분)")
    print("노선   고정 " + "".join(f"{h:>6}시" for h in SHOW_HOURS))
    for route, g in last.groupby("route"):
        fixed = info[(info["route"] == route) & (info["daytype"] == "weekday")]["run_minutes"]
        by_hour = dict(zip(g["hour"], g["minutes"]))
        cells = "".join(f"{by_hour[h]:>7.0f}" if h in by_hour else f"{'-':>7}" for h in SHOW_HOURS)
        print(f"{route:<5}{float(fixed.iloc[0]) if not fixed.empty else float('nan'):>5.0f} {cells}")


if __name__ == "__main__":
    main()
