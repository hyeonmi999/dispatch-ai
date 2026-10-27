"""
서울 버스 노선 좌표 수집 (공공데이터포털 - 서울특별시 버스노선정보조회)

- getStaionByRoute : 노선별 경유 정류소 (순서, 표준 정류소ID, ARS번호, 이름, 좌표)
- getRoutePath     : 노선 경로 좌표 (지도 폴리라인)

정류소 사이 경로를 잘라 "정류장 구간(segment)" 단위 좌표를 만들어 두면
프론트 지도에서 구간별 혼잡도 색을 칠할 수 있습니다.

사용: python scripts/fetch_route_geo.py [--routes 143 272]
결과: data/interim/geo/<노선번호>.json
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import BUS_ROUTES, DATA_GO_KR_API_KEY, INTERIM_DIR, RAW_DIR  # noqa: E402

BASE_URL = "http://ws.bus.go.kr/api/rest/busRouteInfo"
ROUTE_ID_FILE = RAW_DIR / "서울시버스노선ID정보(20260902).xlsx"


def call_api(operation, route_id):
    res = requests.get(
        f"{BASE_URL}/{operation}",
        params={"serviceKey": DATA_GO_KR_API_KEY, "busRouteId": route_id},
        timeout=30,
    )
    res.raise_for_status()
    header = re.search(r"<headerCd>(\d+)</headerCd>.*?<headerMsg>(.*?)</headerMsg>", res.text, re.S)
    if header and header.group(1) != "0":
        raise RuntimeError(f"{operation} 오류: {header.group(2)}")
    items = re.findall(r"<itemList>(.*?)</itemList>", res.text, re.S)
    return [dict(re.findall(r"<(\w+)>([^<]*)</\1>", item)) for item in items]


def nearest_index(path, lat, lng, start):
    """정류소 좌표와 가장 가까운 경로점 인덱스 (경로 진행 방향으로만 탐색)"""
    best, best_d = start, math.inf
    for i in range(start, len(path)):
        d = (path[i][0] - lat) ** 2 + (path[i][1] - lng) ** 2
        if d < best_d:
            best, best_d = i, d
    return best


def build_route_geo(route_name, route_id):
    stations = call_api("getStaionByRoute", route_id)
    path_items = call_api("getRoutePath", route_id)

    path = [[float(p["gpsY"]), float(p["gpsX"])] for p in sorted(path_items, key=lambda p: int(p["no"]))]
    stops = [
        {
            "seq": int(s["seq"]),
            "station_id": s["station"],
            "ars_id": s.get("arsId", ""),
            "name": s["stationNm"],
            "lat": float(s["gpsY"]),
            "lng": float(s["gpsX"]),
            "direction": s.get("direction", ""),
            "is_turn": s["station"] == s.get("trnstnid"),
        }
        for s in sorted(stations, key=lambda s: int(s["seq"]))
    ]

    # 정류소마다 가장 가까운 경로점 → 정류소 i ~ i+1 사이 경로를 구간 좌표로 사용
    idx, cursor = [], 0
    for stop in stops:
        cursor = nearest_index(path, stop["lat"], stop["lng"], cursor)
        idx.append(cursor)

    segments = []
    for i in range(len(stops) - 1):
        a, b = idx[i], max(idx[i + 1], idx[i])
        coords = path[a:b + 1] if b > a else [[stops[i]["lat"], stops[i]["lng"]], [stops[i + 1]["lat"], stops[i + 1]["lng"]]]
        segments.append({
            "seq": stops[i]["seq"],
            "from_station": stops[i]["station_id"],
            "to_station": stops[i + 1]["station_id"],
            "coords": coords,
        })

    first = stations[0] if stations else {}
    return {
        "route": route_name,
        "route_id": route_id,
        "route_type": first.get("routeType", ""),
        "stops": stops,
        "path": path,
        "segments": segments,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", nargs="+", default=BUS_ROUTES)
    args = parser.parse_args()

    if not DATA_GO_KR_API_KEY:
        raise SystemExit("DATA_GO_KR_API_KEY가 .env에 없습니다.")

    ids = pd.read_excel(ROUTE_ID_FILE, dtype=str)
    mapping = dict(zip(ids["노선명"], ids["ROUTEID"]))

    out_dir = INTERIM_DIR / "geo"
    out_dir.mkdir(parents=True, exist_ok=True)

    for route in args.routes:
        geo = build_route_geo(route, mapping[route])
        (out_dir / f"{route}.json").write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
        print(f"✅ {route}: 정류소 {len(geo['stops'])}개, 경로점 {len(geo['path'])}개, 구간 {len(geo['segments'])}개")


if __name__ == "__main__":
    main()
