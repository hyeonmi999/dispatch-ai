"""
기상청 지상(ASOS) 시간자료 수집 - 서울(108) 관측소

공공데이터포털 '기상청_지상(종관, ASOS) 시간자료 조회서비스'를 월 단위로 호출합니다.
(전날까지만 제공되므로 종료일은 어제로 자동 제한)

사용: python scripts/fetch_weather.py --start 2025-09-01 --end 2026-10-26
결과: data/interim/weather.parquet  (datetime, temp, precip, humidity, wind, snow)
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import DATA_GO_KR_API_KEY, INTERIM_DIR  # noqa: E402

URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
STATION_SEOUL = "108"


def fetch_range(start: date, end: date):
    params = {
        "serviceKey": DATA_GO_KR_API_KEY,
        "dataType": "JSON",
        "dataCd": "ASOS",
        "dateCd": "HR",
        "startDt": start.strftime("%Y%m%d"),
        "startHh": "00",
        "endDt": end.strftime("%Y%m%d"),
        "endHh": "23",
        "stnIds": STATION_SEOUL,
        "numOfRows": "999",
        "pageNo": "1",
    }
    for attempt in range(3):
        try:
            res = requests.get(URL, params=params, timeout=30)
            body = res.json()["response"]
            if body["header"]["resultCode"] != "00":
                raise RuntimeError(body["header"]["resultMsg"])
            return body["body"]["items"]["item"]
        except Exception as e:
            print(f"  ⚠️ {start}~{end} 재시도 {attempt + 1}: {e}")
            time.sleep(3)
    raise RuntimeError(f"{start}~{end} 수집 실패")


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-09-01")
    parser.add_argument("--end", default=str(date.today() - timedelta(days=1)))
    args = parser.parse_args()

    if not DATA_GO_KR_API_KEY:
        raise SystemExit("DATA_GO_KR_API_KEY가 .env에 없습니다.")

    start = date.fromisoformat(args.start)
    end = min(date.fromisoformat(args.end), date.today() - timedelta(days=1))

    rows, cur = [], start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=30), end)   # 31일 × 24h = 744행 < 999
        items = fetch_range(cur, chunk_end)
        rows += items
        print(f"✅ {cur} ~ {chunk_end}: {len(items)}행")
        cur = chunk_end + timedelta(days=1)

    df = pd.DataFrame({
        "datetime": pd.to_datetime([r["tm"] for r in rows]),
        "temp": [to_float(r.get("ta")) for r in rows],
        "precip": [to_float(r.get("rn")) or 0.0 for r in rows],     # 강수 없으면 빈 값 → 0
        "humidity": [to_float(r.get("hm")) for r in rows],
        "wind": [to_float(r.get("ws")) for r in rows],
        "snow": [to_float(r.get("dsnw")) or 0.0 for r in rows],
    }).drop_duplicates("datetime").sort_values("datetime")

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out = INTERIM_DIR / "weather.parquet"
    df.to_parquet(out, index=False)
    print(f"📦 {out}: {len(df):,}행 ({df['datetime'].min()} ~ {df['datetime'].max()})")


if __name__ == "__main__":
    main()
