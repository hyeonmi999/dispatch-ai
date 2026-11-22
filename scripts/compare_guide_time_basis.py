# ==================================================
# 길안내 혼잡도의 시간 기준 비교 (park-next P-3)
# ==================================================
# 같은 날·같은 시각에 노선의 여러 위치(기점 근처 ~ 종점 근처)에서 5정류장을 타는 구간의 혼잡도를
#   전: 승객이 타는 시계 시각의 시간대 값
#   후: 그 시각에 그 정류장에 오는 버스가 기점을 떠난 시간대 값 (BusAdvisor.departure_hour)
# 으로 읽어 비교합니다. 실데이터와 길안내용 전체 노선 시간표(data/interim/network/)가 필요합니다.
#
#   python scripts/compare_guide_time_basis.py --date 2026-10-28 --hour 8 --name 1
#   → eval/results/guide_time_basis_<name>.json
#
# API 서버를 끄고 돌립니다 (실데이터를 같이 올리면 메모리가 모자람).

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATA_MODE"] = "real"
os.environ["ASYNC_DB_URL"] = ""          # 비교만 하고 기록은 남기지 않음

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import EVAL_RESULTS_DIR, GUIDE_CLOCK_MINUTE  # noqa: E402
from app.core.registry import registry  # noqa: E402
from app.main import app  # noqa: E402

POSITIONS = (0.05, 0.25, 0.5, 0.75, 0.9)    # 노선 정류장 순번 중 탈 위치 (비율)
RIDE_STOPS = 5                              # 타고 가는 정류장 수


def compare(date: pd.Timestamp, hour: int) -> pd.DataFrame:
    adapter = registry.get("bus")
    guide, advisor = adapter.guide_engine, adapter.advisor
    guide._build()
    rows = []
    for route in adapter.dataset.routes:
        day = advisor.analyze_day(route, date)
        seg = day["segments"]
        line = guide.by_route[route]
        for pos in POSITIONS:
            i = int(len(line) * pos)
            board, alight = line[i], line[min(i + RIDE_STOPS, len(line) - 1)]
            clock = hour * 60 + GUIDE_CLOCK_MINUTE
            offset = guide.minutes_from_start(board, hour)
            start = advisor.departure_hour(route, day["daytype"], board["seq"], clock, offset)

            def worst(h):
                ridden = seg[(seg["hour"] == h) & (seg["stop_seq"] >= board["seq"]) & (seg["stop_seq"] < alight["seq"])]
                value = ridden["congestion"].max()
                return None if pd.isna(value) else round(float(value), 3)

            rows.append({"route": route, "position": pos, "seq": int(board["seq"]), "board": board["name"],
                         "minutes_from_start": None if offset is None else round(offset, 1), "timetable": offset is not None,
                         "start_hour": start, "before": worst(hour), "after": worst(start)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-10-28")
    ap.add_argument("--hour", type=int, default=8)
    ap.add_argument("--name", default="1")
    args = ap.parse_args()

    with TestClient(app):                     # 시작 처리(모델·데이터 로드)
        df = compare(pd.Timestamp(args.date), args.hour)
    print(df.to_string(index=False))

    both = df.dropna(subset=["before", "after"]).assign(diff=lambda d: d["after"] - d["before"])
    summary = {
        "date": args.date, "hour": args.hour, "clock_minute": GUIDE_CLOCK_MINUTE, "ride_stops": RIDE_STOPS,
        "rows": len(df), "timetable_used": int(df["timetable"].sum()),
        "start_hour_changed": int((df["start_hour"] != args.hour).sum()),
        "mean_abs_diff": round(float(both["diff"].abs().mean()), 3),
        "by_position": [{"position": p, "mean_diff": round(float(g["diff"].mean()), 3),
                         "mean_abs_diff": round(float(g["diff"].abs().mean()), 3), "n": len(g)}
                        for p, g in both.groupby("position")],
        "rows_detail": df.to_dict("records"),
    }
    out = EVAL_RESULTS_DIR / f"guide_time_basis_{args.name}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows_detail"}, ensure_ascii=False, indent=1))
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
