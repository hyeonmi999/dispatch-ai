"""평소 링크 속도 표 만들기 (이슈 #42 방법 2, docs/plans/park-next.md Task P-9)

TOPIS 자료실 「서울시 차량통행속도」(도로별 일자별 통행속도, 월별 xlsx, 키 없음)를 받아
링크 × 요일유형 × 시간대의 속도 중앙값 표를 만듭니다. 실시간 소통과 비교할 "평소" 값입니다.

  파일 열: 일자, 요일, 도로명, 링크아이디, 시점명, 종점명, 방향, 거리(m), 차선수, 기능유형구분, 도심/외곽구분, 권역구분,
           ~01시 … ~24시 (km/h. "~01시" = 0시대)
  결과:   data/interim/topis/link_speed.parquet  (link_id, daytype, hour, speed_kmh, days)
          data/interim/topis/links.parquet       (link_id, road_name, start_name, end_name, direction, length_m) — 링크 목록
  받은 원본: data/raw/topis_speed/<YYYY-MM>.xlsx (받은 달은 건너뜀)

사용: python scripts/build_topis_speed.py [--months 2]
"""

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import INTERIM_DIR, RAW_DIR, TOPIS_DIR, TOPIS_LINK_SPEED, TOPIS_LINKS, TOPIS_SPEED_MONTHS  # noqa: E402
from app.domains.bus.features import daytype_of, kr_holidays  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("build_topis_speed")

SITE = "https://topis.seoul.go.kr"
LIST_URL = f"{SITE}/refroom/selectRefRoomListASC.do"
FILE_URL = f"{SITE}/downloadFileRefRoom.do"
HEADERS = {"Referer": f"{SITE}/refRoom/openRefRoom_1.do"}
BOARD = "06"                  # 자료실 게시판 구분: 도로별 일자별 통행속도
TIMEOUT = 30
DOWNLOAD_TIMEOUT = 600
HOUR_COLS = [f"~{h:02d}시" for h in range(1, 25)]
RAW_SPEED_DIR = RAW_DIR / "topis_speed"
OUT_DIR = INTERIM_DIR / TOPIS_DIR


def list_months(year: int) -> list[dict]:
    """그 해에 올라온 달 목록 (파일이 있는 달만)"""
    try:
        res = requests.post(LIST_URL, data={"blbdDivCd": BOARD, "bdwrDivCd": str(year), "mainBdwrRowNum": 12},
                            headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        rows = res.json().get("rows", [])
    except (requests.RequestException, ValueError) as e:
        raise SystemExit(f"[실패] TOPIS 자료 목록을 받지 못했습니다 ({year}년): {e}")
    return [{"month": f"{year}-{r['months']}", "name": r["apndFileNm"], "path": r["apndFilePathNm"], "seq": r["bdwrSeq"]}
            for r in rows if r.get("apndFilePathNm")]


def download(item: dict) -> Path:
    RAW_SPEED_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_SPEED_DIR / f"{item['month']}.xlsx"
    if out.exists() and out.stat().st_size > 0:
        log.info("  %s 이미 있음", out.name)
        return out
    form = {"apndFileNm": item["name"], "apndFilePathNm": item["path"], "bdwrSeq": item["seq"], "blbdDivCd": BOARD}
    try:
        with requests.post(FILE_URL, data=form, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, stream=True) as res:
            res.raise_for_status()
            if "html" in res.headers.get("Content-Type", ""):
                raise SystemExit(f"[실패] {item['month']} 파일 대신 오류 화면이 왔습니다")
            tmp = out.with_suffix(".part")
            with tmp.open("wb") as f:
                for chunk in res.iter_content(1 << 20):
                    f.write(chunk)
            tmp.replace(out)
    except requests.RequestException as e:
        raise SystemExit(f"[실패] {item['month']} 파일을 받지 못했습니다: {e}")
    log.info("  %s 받음 (%.1f MB)", out.name, out.stat().st_size / 1e6)
    return out


def read_month(path: Path) -> pd.DataFrame:
    x = pd.read_excel(path, dtype={"링크아이디": str}, engine="openpyxl")
    missing = {"일자", "링크아이디", "거리", *HOUR_COLS} - set(x.columns)
    if missing:
        raise SystemExit(f"[실패] {path.name}에 열이 없습니다: {sorted(missing)}")
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", type=int, default=TOPIS_SPEED_MONTHS, help="최근 몇 개월 (기본 %(default)s)")
    args = ap.parse_args()

    this_year = dt.date.today().year
    available = list_months(this_year - 1) + list_months(this_year)
    picked = sorted(available, key=lambda r: r["month"])[-args.months:]
    if not picked:
        raise SystemExit("[실패] 받을 수 있는 달이 없습니다")
    log.info("평소 속도: %s", ", ".join(r["month"] for r in picked))

    frames = [read_month(download(item)) for item in picked]
    x = pd.concat(frames, ignore_index=True)
    x["date"] = pd.to_datetime(x["일자"].astype(str), format="%Y%m%d")
    hol = kr_holidays(sorted(x["date"].dt.year.unique().tolist()))
    daytypes = {d: daytype_of(d, hol) for d in x["date"].unique()}
    x["daytype"] = x["date"].map(daytypes)

    links = (x.sort_values("date").groupby("링크아이디").last().reset_index()
             .rename(columns={"링크아이디": "link_id", "도로명": "road_name", "시점명": "start_name",
                              "종점명": "end_name", "방향": "direction", "거리": "length_m"}))
    links = links[["link_id", "road_name", "start_name", "end_name", "direction", "length_m"]]

    long = x.melt(id_vars=["링크아이디", "daytype"], value_vars=HOUR_COLS, var_name="col", value_name="speed_kmh")
    long["hour"] = long["col"].str[1:3].astype(int) - 1
    long = long[pd.to_numeric(long["speed_kmh"], errors="coerce") > 0]
    long["speed_kmh"] = long["speed_kmh"].astype(float)
    table = (long.groupby(["링크아이디", "daytype", "hour"])["speed_kmh"].agg(["median", "size"]).reset_index()
             .rename(columns={"링크아이디": "link_id", "median": "speed_kmh", "size": "days"}))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    table.to_parquet(OUT_DIR / TOPIS_LINK_SPEED, index=False)
    links.to_parquet(OUT_DIR / TOPIS_LINKS, index=False)
    log.info("링크 %d개, 표 %d칸 → %s", len(links), len(table), OUT_DIR / TOPIS_LINK_SPEED)


if __name__ == "__main__":
    main()
