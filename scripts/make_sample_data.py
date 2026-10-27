"""
합성(샘플) 데이터 생성 - 실데이터 없이도 전체 파이프라인 실행용

실데이터(data/interim)와 같은 스키마로 data/sample 에 저장합니다.
  boarding.parquet / dispatch.parquet / section.parquet  (OA-21219 / 21220 / 21218 형식)
  weather.parquet, route_info.parquet, geo/<노선>.json

수요 생성 규칙 (실제 서울 버스 패턴을 단순화)
  - 평일 출퇴근 피크, 주말은 낮 시간대 완만
  - 공휴일 ×0.6, 방학 ×0.93, 비 오는 시간 ×0.9
  - 상행은 아침, 하행은 저녁에 더 붐빔 / 환승 거점 정류장은 승객 3배
  - 운행횟수는 피크 10회/h, 낮 7회/h, 새벽·심야 3~5회/h (주말 ×0.8)

사용: python scripts/make_sample_data.py [--weeks 12] [--end 2026-10-18] [--seed 42]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import BUS_SAMPLE_ROUTES, SAMPLE_DIR, SCHOOL_VACATIONS  # noqa: E402
from app.domains.bus.features import kr_holidays  # noqa: E402

# 노선별 경유 지점 (위도, 경도) - 서울 시내 가상 노선
WAYPOINTS = {
    "901": [(37.5552, 126.9369), (37.5759, 126.9768), (37.5712, 127.0094), (37.5612, 127.0371)],   # 신촌-광화문-동대문-왕십리
    "902": [(37.6380, 127.0257), (37.5822, 127.0019), (37.5547, 126.9707), (37.5216, 126.9243)],   # 수유-혜화-서울역-여의도
    "903": [(37.5133, 127.1001), (37.5088, 127.0631), (37.4979, 127.0276), (37.4766, 126.9816)],   # 잠실-삼성-강남-사당
}
DAILY_LEVEL = {"901": 17000, "902": 21000, "903": 24000}
STOPS_ONE_WAY = 28

HOUR_WEEKDAY = np.array([0.25, 0.05, 0, 0, 0.15, 0.5, 1.3, 2.7, 2.3, 1.25, 0.95, 0.95,
                         1.05, 1.0, 1.0, 1.15, 1.45, 2.0, 2.1, 1.45, 1.05, 0.85, 0.65, 0.45])
HOUR_WEEKEND = np.array([0.3, 0.05, 0, 0, 0.1, 0.3, 0.55, 0.8, 1.0, 1.2, 1.35, 1.45,
                         1.5, 1.5, 1.45, 1.4, 1.35, 1.3, 1.2, 1.0, 0.85, 0.7, 0.55, 0.4])
TRIPS_WEEKDAY = np.array([3, 0, 0, 0, 3, 5, 8, 10, 10, 8, 7, 7, 7, 7, 7, 7, 8, 10, 10, 8, 7, 6, 5, 4])


def interpolate(points, step_km=0.08):
    """경유 지점 사이를 약 80m 간격 좌표로 보간"""
    path = []
    for (a_lat, a_lng), (b_lat, b_lng) in zip(points[:-1], points[1:]):
        dist = np.hypot((b_lat - a_lat) * 111, (b_lng - a_lng) * 88)
        n = max(2, int(dist / step_km))
        for t in np.linspace(0, 1, n, endpoint=False):
            bend = 0.0015 * np.sin(np.pi * t)   # 직선이 아닌 도로처럼 살짝 휘게
            path.append([a_lat + (b_lat - a_lat) * t + bend, a_lng + (b_lng - a_lng) * t])
    path.append(list(points[-1]))
    return path


def make_geo(route):
    out_path = interpolate(WAYPOINTS[route])
    back_path = [[lat - 0.0004, lng + 0.0004] for lat, lng in reversed(out_path)]   # 반대 차로
    path = out_path + back_path

    idx_out = np.linspace(0, len(out_path) - 1, STOPS_ONE_WAY).astype(int)
    idx_back = len(out_path) + np.linspace(0, len(back_path) - 1, STOPS_ONE_WAY).astype(int)
    stop_idx = list(idx_out) + list(idx_back)
    hubs = {0, 9, 18, 27, 28, 37, 46, 55}

    stops = []
    for seq, i in enumerate(stop_idx, start=1):
        stops.append({
            "seq": seq,
            "station_id": f"9{route}{seq:05d}",
            "ars_id": f"{route[-1]}{seq:04d}",
            "name": f"{route}번 {'환승' if seq - 1 in hubs else ''}정류장 {seq}",
            "lat": round(path[i][0], 6),
            "lng": round(path[i][1], 6),
            "direction": "상행" if seq <= STOPS_ONE_WAY else "하행",
            "is_turn": seq == STOPS_ONE_WAY,
            "hub": seq - 1 in hubs,
        })
    segments = [{
        "seq": stops[k]["seq"],
        "from_station": stops[k]["station_id"],
        "to_station": stops[k + 1]["station_id"],
        "coords": path[stop_idx[k]:stop_idx[k + 1] + 1],
    } for k in range(len(stops) - 1)]
    return {"route": route, "route_id": f"9000{route}", "route_type": "샘플", "stops": stops,
            "path": path, "segments": segments}


def make_weather(dates, rng):
    rows = []
    for d in dates:
        doy = d.day_of_year
        base = 14 + 13 * np.cos((doy - 205) / 365 * 2 * np.pi)
        rain_day = rng.random() < 0.22
        rain_hours = set(rng.choice(24, size=rng.integers(3, 10), replace=False)) if rain_day else set()
        for h in range(24):
            temp = base + 4 * np.sin((h - 9) / 24 * 2 * np.pi) + rng.normal(0, 1)
            precip = round(float(rng.gamma(1.5, 2.0)), 1) if h in rain_hours else 0.0
            rows.append((d + pd.Timedelta(hours=h), round(temp, 1), precip, int(np.clip(60 + 30 * (precip > 0) + rng.normal(0, 8), 20, 100)), 2.0, 0.0))
    return pd.DataFrame(rows, columns=["datetime", "temp", "precip", "humidity", "wind", "snow"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=12)
    parser.add_argument("--end", default="2026-10-18")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    end = pd.Timestamp(args.end)
    dates = pd.date_range(end - pd.Timedelta(weeks=args.weeks) + pd.Timedelta(days=1), end)
    hol = kr_holidays(dates.year.unique().tolist())
    missing = set(rng.choice(dates[30:], size=2, replace=False))   # 실데이터처럼 원천 결측일 2일

    weather = make_weather(dates, rng)
    precip = weather.set_index("datetime")["precip"]

    out = SAMPLE_DIR
    (out / "geo").mkdir(parents=True, exist_ok=True)
    b_rows, d_rows, s_rows, info_rows = [], [], [], []
    hours = [f"{h:02d}시" for h in range(24)]

    for route in BUS_SAMPLE_ROUTES:
        geo = make_geo(route)
        (out / "geo" / f"{route}.json").write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
        stops = geo["stops"]
        n = len(stops)
        weight = rng.lognormal(0, 0.5, n) * np.array([3.0 if s["hub"] else 1.0 for s in stops])
        weight[STOPS_ONE_WAY - 1] = weight[-1] = 0.2      # 회차·종점 직전은 승차 적음
        weight /= weight.sum()
        upbound = np.array([s["seq"] <= STOPS_ONE_WAY for s in stops])
        alight = np.array([0.35 if s["hub"] else 0.12 for s in stops])
        alight[STOPS_ONE_WAY] = 0.95                      # 회차 후 대부분 하차

        for d in dates:
            if d in missing:
                continue
            is_hol = d.date() in hol or d.day_of_week == 6
            weekend = d.day_of_week >= 5 or is_hol
            prof = HOUR_WEEKEND if weekend else HOUR_WEEKDAY
            level = DAILY_LEVEL[route] * (0.6 if is_hol else 0.75 if d.day_of_week == 5 else 1.03 if d.day_of_week == 4 else 1.0)
            if any(pd.Timestamp(s) <= d <= pd.Timestamp(e) for s, e in SCHOOL_VACATIONS):
                level *= 0.93
            trips_h = np.round(TRIPS_WEEKDAY * (0.8 if weekend else 1.0)).astype(int)

            lam = np.zeros((n, 24))
            for h in range(24):
                rain = 0.9 if precip.get(d + pd.Timedelta(hours=h), 0) > 0 else 1.0
                tilt = np.where(upbound, 1.25 if h < 12 else 0.8, 0.8 if h < 12 else 1.25)
                lam[:, h] = level * prof[h] / prof.sum() * weight * tilt * rain
            board = rng.poisson(lam)
            date_s = d.strftime("%Y%m%d")

            for k, s in enumerate(stops):
                trips = np.clip(trips_h + rng.integers(-1, 2, 24) * (trips_h > 0), 0, None)
                b_rows.append([date_s, route, f"9000{route}", s["station_id"], s["seq"], board[k].sum(), *board[k]])
                d_rows.append([date_s, route, f"9000{route}", s["station_id"], s["seq"], trips.sum(), *trips])

            # 구간 재차인원: 앞 구간 재차 × (1-하차율) + 이번 정류장 승차 (최근 8주만 제공)
            if d > end - pd.Timedelta(weeks=8):
                onboard = np.zeros((n, 24))
                for k in range(n):
                    prev = onboard[k - 1] if k > 0 else 0
                    onboard[k] = prev * (1 - alight[k]) + board[k]
                for k in range(n - 1):
                    vals = np.round(onboard[k]).astype(int)
                    s_rows.append([date_s, route, f"9000{route}", stops[k]["station_id"], stops[k + 1]["station_id"],
                                   stops[k]["seq"], vals.sum(), *vals])

        run_minutes = 150 + 20 * BUS_SAMPLE_ROUTES.index(route)
        for daytype, interval in [("weekday", 7.0), ("saturday", 9.0), ("holiday", 11.0)]:
            info_rows.append({
                "route": route, "daytype": daytype, "company": "샘플운수", "route_type": "간선(샘플)",
                "start": stops[0]["name"], "end": stops[STOPS_ONE_WAY - 1]["name"],
                "buses": float(np.ceil(run_minutes / 7.0)), "interval": interval, "distance_km": 30.0,
                "run_minutes": float(run_minutes), "min_interval": 4.0, "max_interval": 13.0,
                "first_bus": "04:30", "last_bus": "23:00", "buses_estimated": False,
            })

    ids = ["기준_날짜", "노선명", "노선_ID", "정류장_ID", "정류장_순서"]
    boarding = pd.DataFrame(b_rows, columns=ids + ["승차_승객_수"] + [f"승차_승객_수_{h}" for h in hours])
    dispatch = pd.DataFrame(d_rows, columns=ids + ["버스운행횟수"] + [f"버스운행횟수_{h}" for h in hours])
    section = pd.DataFrame(s_rows, columns=["기준_날짜", "노선명", "노선_ID", "출발_정류장_ID", "도착_정류장_ID",
                                            "정류장_순서", "재차인원합_일"] + [f"재차인원합_{h}시" for h in range(24)])

    boarding.to_parquet(out / "boarding.parquet", index=False)
    dispatch.to_parquet(out / "dispatch.parquet", index=False)
    section.to_parquet(out / "section.parquet", index=False)
    weather.to_parquet(out / "weather.parquet", index=False)
    pd.DataFrame(info_rows).to_parquet(out / "route_info.parquet", index=False)

    print(f"✅ 합성 데이터 생성: {out}")
    print(f"   기간 {dates.min():%Y-%m-%d} ~ {dates.max():%Y-%m-%d} ({len(dates)}일, 결측일 {len(missing)}일) | 노선 {BUS_SAMPLE_ROUTES}")
    print(f"   boarding {len(boarding):,}행 / dispatch {len(dispatch):,}행 / section {len(section):,}행 / weather {len(weather):,}행")


if __name__ == "__main__":
    main()
