"""
서울 버스 전체 노선의 정류장 목록 수집 (길안내 F-28용)

공공데이터포털 '서울특별시 버스노선정보조회'의 getStaionByRoute로 노선마다 경유 정류장(순서·이름·좌표)을 받아
한 파일로 모읍니다. 경로 좌표(getRoutePath)는 받지 않습니다 (길안내는 정류장만 있으면 됨, 호출 수 절반).
소요 시간 계산용으로 정류장별 누적 거리(cum_m)와 공식 시간표의 누적 분(t_first: 첫차, t_last: 막차)도 함께 저장합니다.

- 노선 1개당 호출 1번 (전체 약 720번). 개발 계정 하루 호출 한도에 걸리면 다음 날 다시 실행하면 이어서 받습니다.
- 이미 받은 노선은 건너뜁니다 (data/interim/network/parts/<노선>.json).

사용
  python scripts/fetch_network_stops.py                 # 전체
  python scripts/fetch_network_stops.py --routes 402 472
결과
  data/interim/network/stops.parquet   노선·순번·정류장ID·이름·좌표 (길안내가 읽음)
  data/interim/network/routes.parquet  노선별 유형·기점·종점·배차간격·운행시간·첫차·막차 (평일 기준)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import DATA_GO_KR_API_KEY, INTERIM_DIR, RAW_DIR  # noqa: E402
from scripts.fetch_route_geo import ROUTE_ID_FILE, call_api  # noqa: E402

ROUTE_INFO_FILE = RAW_DIR / "서울시버스노선기본정보(20260902).xlsx"
OUT_DIR = INTERIM_DIR / "network"
PARTS_DIR = OUT_DIR / "parts"
PAUSE_SEC = 0.15          # 호출 사이 간격 (서버에 부담을 주지 않게)
MAX_FAILS_IN_A_ROW = 5    # 연속으로 이만큼 실패하면 멈춤 (호출 한도 초과·키 오류)


def _clock(text) -> int | None:
    """'04:03' → 243분. 비어 있거나 ':'만 있으면 None"""
    try:
        hour, minute = str(text).strip().split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return None


def _elapsed(clocks: list[int | None]) -> list[float | None]:
    """정류장별 시각 → 첫 정류장부터의 누적 분. 자정을 넘기면 하루를 더하고, 시각이 없는 곳은 None"""
    out, total, prev = [], 0, None
    for now in clocks:
        if now is None:
            out.append(None)
            continue
        if prev is not None:
            step = now - prev
            if step < -600:          # 23:58 → 00:02
                step += 1440
            total += max(step, 0)
        prev = now
        out.append(float(total))
    return out


def fetch_route(route: str, route_id: str) -> list[dict]:
    stations = sorted(call_api("getStaionByRoute", route_id), key=lambda s: int(s["seq"]))
    t_first = _elapsed([_clock(s.get("beginTm")) for s in stations])
    t_last = _elapsed([_clock(s.get("lastTm")) for s in stations])
    rows, cum_m = [], 0.0
    for s, first, last in zip(stations, t_first, t_last):
        cum_m += float(s.get("fullSectDist") or 0)       # 앞 정류장에서 이 정류장까지의 도로 거리(m)
        if not s.get("gpsY") or not s.get("gpsX"):
            continue
        rows.append({
            "route": route, "seq": int(s["seq"]), "station_id": s["station"], "ars_id": s.get("arsId", ""),
            "name": s["stationNm"], "lat": float(s["gpsY"]), "lng": float(s["gpsX"]),
            "direction": s.get("direction", ""), "is_turn": s["station"] == s.get("trnstnid"),
            "cum_m": cum_m, "t_first": first, "t_last": last,
        })
    return rows


def has_timing(part: Path) -> bool:
    """예전 형식(거리·시간표 없음)으로 받은 조각이면 False → 다시 받음"""
    try:
        rows = json.loads(part.read_text(encoding="utf-8"))
        return not rows or "cum_m" in rows[0]
    except (ValueError, OSError):
        return False


def write_routes() -> int:
    """노선 기본정보 엑셀(첫 시트 = 평일)에서 길안내에 필요한 값만 추림"""
    x = pd.read_excel(ROUTE_INFO_FILE, dtype={"노선번호": str})
    info = pd.DataFrame({
        "route": x["노선번호"], "route_type": x["유형"], "company": x["업체명"], "start": x["기점명"], "end": x["종점명"],
        "interval": pd.to_numeric(x["배차간격"], errors="coerce"),
        "run_minutes": pd.to_numeric(x["운행시간"], errors="coerce"),
        "first_bus": x["첫차시간"].astype(str), "last_bus": x["막차시간"].astype(str),
    }).drop_duplicates("route")
    info.to_parquet(OUT_DIR / "routes.parquet", index=False)
    return len(info)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--routes", nargs="+", default=None, help="받을 노선 (기본: 전체)")
    args = p.parse_args()
    if not DATA_GO_KR_API_KEY:
        raise SystemExit("DATA_GO_KR_API_KEY가 .env에 없습니다.")

    ids = pd.read_excel(ROUTE_ID_FILE, dtype=str)
    mapping = dict(zip(ids["노선명"], ids["ROUTEID"]))
    routes = args.routes or sorted(mapping)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)

    done = fails = 0
    for i, route in enumerate(routes, start=1):
        part = PARTS_DIR / f"{route}.json"
        if part.exists() and has_timing(part):
            continue
        if route not in mapping:
            print(f"- {route}: 노선ID정보에 없음, 건너뜀")
            continue
        try:
            rows = fetch_route(route, mapping[route])
        except Exception as error:
            fails += 1
            print(f"x {route}: {str(error)[:120]}")
            if fails >= MAX_FAILS_IN_A_ROW:
                print("연속 실패가 많아 멈춥니다. 호출 한도나 키를 확인한 뒤 다시 실행하면 이어서 받습니다.")
                break
            time.sleep(2)
            continue
        fails = 0
        part.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        done += 1
        if done % 50 == 0:
            print(f"  {i}/{len(routes)} 노선 처리 중 (이번 실행에서 {done}개 받음)", flush=True)
        time.sleep(PAUSE_SEC)

    # 받은 조각을 한 파일로 (정류장이 없는 노선은 빠짐)
    frames = [pd.DataFrame(json.loads(f.read_text(encoding="utf-8"))) for f in sorted(PARTS_DIR.glob("*.json"))]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise SystemExit("받은 노선이 없습니다.")
    stops = pd.concat(frames, ignore_index=True)
    stops.to_parquet(OUT_DIR / "stops.parquet", index=False)
    n_info = write_routes()
    print(f"완료: 노선 {stops['route'].nunique()}개, 정류장 줄 {len(stops):,}개, 서로 다른 정류장 {stops['station_id'].nunique():,}곳 "
          f"-> {OUT_DIR / 'stops.parquet'} / 노선 기본정보 {n_info}개")


if __name__ == "__main__":
    main()
