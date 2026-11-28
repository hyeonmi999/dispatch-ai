# ==================================================
# 시간대별 한 바퀴 운행시간  (이슈 #42 방법 1, docs/plans/park-next.md Task P-7)
# ==================================================
# 원천: 서울 열린데이터광장 OA-21217 「노선별 정류장 구간별 평균 운행시간」 (1일 1시간, 초)
#   열: 기준_날짜, 노선_ID, 출발_정류장_ID, 도착_정류장_ID, 운행시간(하루 평균), 운행시간_00시 ~ 23시,
#       출발_정류장_순서, 도착_정류장_순서
#   "N시" = 버스가 그 구간을 지난 시각 (기점 출발 시각이 아님). 0 = 그 시간대 기록 없음.
#   구간 1 ~ 마지막이 왕복 전체 (하루 평균 합이 노선 기본정보 운행시간과 ±4% 안, 2026-11-09 주 5개 노선).
#
# 1) 하루 값: 기점을 N시 30분에 떠난 버스를 따라가며 구간 시간을 더함.
#    지금 시각이 다음 시간대로 넘어가면 그 시간대 값을 쓰고, 자정을 넘으면 0시부터 다시 셈.
#    그 시간대 기록이 없는 구간은 그 구간의 하루 평균으로 채움. 채운 구간이 많으면 그날 값은 버림.
#    첫 구간에 그 시간대 기록이 없으면 그 시각에 출발한 버스가 없는 것이므로 계산하지 않음.
# 2) 표: 기준일(asof, 매주 일요일)마다 최근 8주의 같은 요일유형·출발 시각 하루 값 중앙값.
# 3) 읽기: 날짜 D에는 asof ≤ D − 21일인 표 중 가장 최근 것 (3주 앞 계획이 그때 모르는 값을 쓰지 않게).
#    표가 없거나 칸이 비면 None → 부르는 쪽이 노선 기본정보의 고정 운행시간을 씀.

import numpy as np
import pandas as pd

from app.config import (
    BUS_RUNTIME_LAG_DAYS, BUS_RUNTIME_MAX_FILL, BUS_RUNTIME_MIN_DAYS, BUS_RUNTIME_START_MINUTE, BUS_RUNTIME_WEEKS,
)
from app.domains.bus.features import daytype_of, kr_holidays

HOURS_PER_DAY = 24
HOUR_COLS = [f"운행시간_{h:02d}시" for h in range(HOURS_PER_DAY)]
TABLE_COLUMNS = ["asof", "route", "daytype", "hour", "minutes", "days"]


# ==================================================
# 1. 하루 값
# ==================================================

def trip_minutes(seconds: np.ndarray, daily: np.ndarray, start_minute: float) -> tuple[float, float] | None:
    """start_minute(그날 0시부터 분)에 기점을 떠난 버스의 한 바퀴 시간(분)과 하루 평균으로 채운 구간 비율

    seconds: (구간 수, 24) 시간대별 구간 운행시간(초, 0·NaN = 기록 없음), 구간 순서대로
    daily:   (구간 수,) 구간 하루 평균(초)
    첫 구간에 출발 시간대 기록이 없거나, 기록도 하루 평균도 없는 구간이 있으면 None
    """
    n = len(seconds)
    if n == 0 or not seconds[0, int(start_minute // 60) % HOURS_PER_DAY] > 0:
        return None
    clock, filled = float(start_minute), 0
    for i in range(n):
        value = seconds[i, int(clock // 60) % HOURS_PER_DAY]
        if not value > 0:
            value, filled = daily[i], filled + 1
            if not value > 0:
                return None
        clock += value / 60.0
    return clock - start_minute, filled / n


def daily_trips(segments: pd.DataFrame, route_col: str = "노선명") -> pd.DataFrame:
    """OA-21217 원본(대상 노선만) → 날짜 × 노선 × 기점 출발 시각별 한 바퀴 시간 (date, route, hour, minutes, fill)

    그날 구간 행이 몇 개 빠졌으면(150번은 약 4일에 1일 1~4개) 그 구간은 앞 날짜 중 가장 가까운 날의
    같은 순서 구간 하루 평균으로 채우고 '채운 구간'으로 셉니다. 앞 날짜에도 없으면 그날은 뺍니다.
    """
    rows = []
    for route, r in segments.groupby(route_col, sort=True):
        # 구간 순서별 하루 평균을 날짜순으로 앞에서 채운 값: 빠진 구간 행의 대체값 (그날보다 앞 날짜 값만 씀)
        daily_by_seq = r.pivot_table(index="기준_날짜", columns="출발_정류장_순서", values="운행시간", aggfunc="first")
        daily_by_seq = daily_by_seq.where(daily_by_seq > 0).sort_index()
        earlier = daily_by_seq.ffill().shift(1)
        for date, g in r.groupby("기준_날짜", sort=True):
            g = g.drop_duplicates("출발_정류장_순서").set_index("출발_정류장_순서")
            last = int(g.index.max())
            seqs = np.arange(1, last + 1)
            seconds = g.reindex(seqs)[HOUR_COLS].to_numpy(dtype=float)
            daily = g.reindex(seqs)["운행시간"].to_numpy(dtype=float)
            missing = np.isnan(daily) & np.isnan(seconds).all(axis=1)
            if missing.any():
                past = earlier.loc[date].reindex(seqs).to_numpy(dtype=float)
                daily = np.where(missing, past, daily)
            rows += _day_rows(date, str(route), seconds, daily)
    return pd.DataFrame(rows, columns=["date", "route", "hour", "minutes", "fill"])


def _day_rows(date, route: str, seconds: np.ndarray, daily: np.ndarray) -> list[dict]:
    rows = []
    for hour in range(HOURS_PER_DAY):
        got = trip_minutes(seconds, daily, hour * 60 + BUS_RUNTIME_START_MINUTE)
        if got is None or got[1] > BUS_RUNTIME_MAX_FILL:
            continue
        rows.append({"date": pd.Timestamp(str(date)), "route": route, "hour": hour,
                     "minutes": round(got[0], 2), "fill": round(got[1], 3)})
    return rows


# ==================================================
# 2. 기준일별 표
# ==================================================

def build_table(trips: pd.DataFrame, weeks: int = BUS_RUNTIME_WEEKS, min_days: int = BUS_RUNTIME_MIN_DAYS) -> pd.DataFrame:
    """하루 값 → 기준일(매주 일요일)별 노선 × 요일유형 × 출발 시각의 최근 weeks주 중앙값 표

    기준일의 값은 그날까지의 하루 값만 씁니다. 날이 min_days보다 적은 칸은 넣지 않습니다.
    """
    if trips.empty:
        return pd.DataFrame(columns=TABLE_COLUMNS)
    trips = trips.copy()
    trips["date"] = pd.to_datetime(trips["date"])
    hol = kr_holidays(trips["date"].dt.year.unique().tolist())
    trips["daytype"] = [daytype_of(d, hol) for d in trips["date"]]

    first, last = trips["date"].min(), trips["date"].max()
    sundays = pd.date_range(first, last, freq="W-SUN")
    frames = []
    for asof in sundays:
        window = trips[(trips["date"] > asof - pd.Timedelta(weeks=weeks)) & (trips["date"] <= asof)]
        agg = window.groupby(["route", "daytype", "hour"])["minutes"].agg(["median", "size"]).reset_index()
        agg = agg[agg["size"] >= min_days]
        frames.append(pd.DataFrame({"asof": asof, "route": agg["route"], "daytype": agg["daytype"],
                                    "hour": agg["hour"].astype(int), "minutes": agg["median"].round(1),
                                    "days": agg["size"].astype(int)}))
    if not frames:
        return pd.DataFrame(columns=TABLE_COLUMNS)
    return pd.concat(frames, ignore_index=True)[TABLE_COLUMNS]


# ==================================================
# 3. 표 읽기
# ==================================================

def usable_asof(table: pd.DataFrame, date, lag_days: int = BUS_RUNTIME_LAG_DAYS) -> pd.Timestamp | None:
    """날짜 date의 계획에 쓸 수 있는 가장 최근 기준일 (asof ≤ date − lag_days). 없으면 None"""
    if table is None or table.empty:
        return None
    limit = pd.Timestamp(date).normalize() - pd.Timedelta(days=lag_days)
    ok = table.loc[table["asof"] <= limit, "asof"]
    return pd.Timestamp(ok.max()) if not ok.empty else None


def profile(table: pd.DataFrame, route: str, daytype: str, asof: pd.Timestamp) -> list[float | None]:
    """기준일 asof 표에서 노선·요일유형의 출발 시각별 한 바퀴 시간 24칸 (빈 칸은 None)"""
    rows = table[(table["asof"] == asof) & (table["route"] == route) & (table["daytype"] == daytype)]
    by_hour = dict(zip(rows["hour"].astype(int), rows["minutes"].astype(float)))
    return [by_hour.get(h) for h in range(HOURS_PER_DAY)]
