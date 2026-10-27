# ==================================================
# 버스 수요 예측 피처
# ==================================================
# y : 노선·정류장·시간대별 승차 승객 수
# X : 요일·시간대·정류장·날씨·공휴일·운행횟수 + 과거 같은 요일·시간 승객 수(lag)
#
# 예측 거리 21일 (공개 시차 대응)
# - 서울시 승객 데이터는 주가 끝나고 8일 뒤 공개 → 가장 최근 실측이 늘 8~14일 전
# - 그래서 "최근" 정보는 21·28·35·42일 전 같은 요일·시간 값만 사용 → 실측 마지막 날 + 21일까지 예측
# - 작년 같은 주(357·364·371일 전)로 계절성 보강, 작년 그날이 공휴일이었는지도 함께 입력
#   (실험: 1주 전 값 대신 3~6주 전 + 작년 값을 써도 오차가 오히려 줄었음, README 참고)
#
# 미래 데이터 누수 방지
# - 위 lag는 모두 예측일 기준 21일 이상 과거 관측치
# - 운행횟수는 사전에 정해지는 배차 계획 값으로 보고 같은 시간대 값을 사용
#   (미래 날짜는 가장 최근 같은 요일의 운행횟수를 계획값으로 사용)

import holidays
import numpy as np
import pandas as pd

from app.config import SCHOOL_VACATIONS

HORIZON_DAYS = 21
LAG_DAYS = [21, 28, 35, 42]           # 최근: 3~6주 전 같은 요일
YEAR_LAG_DAYS = [357, 364, 371]       # 작년 같은 주 (같은 요일 ±1주)
REFERENCE_LAG_DAYS = [7]              # 평가 참고용(전주 같은 요일) - 피처로는 쓰지 않음

FEATURES = [
    "route_code", "stop_code", "stop_seq", "hour", "dow", "is_weekend", "is_holiday",
    "is_holiday_eve", "is_school_vacation", "temp", "precip", "humidity", "is_rain", "trips",
    "lag21", "lag28", "lag35", "lag42", "recent_mean",
    "lag357", "lag364", "lag371", "year_mean", "lag364_holiday",
]
CATEGORICAL = ["route_code", "stop_code"]


def kr_holidays(years) -> dict:
    return dict(holidays.KR(years=sorted(set(years))))


def daytype_of(ts: pd.Timestamp, hol: dict | None = None) -> str:
    """노선 기본정보 시트 구분과 같은 요일유형: weekday / saturday / holiday(일요일·공휴일)"""
    hol = hol if hol is not None else kr_holidays([ts.year])
    if ts.date() in hol or ts.day_of_week == 6:
        return "holiday"
    return "saturday" if ts.day_of_week == 5 else "weekday"


def add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    dates = pd.Series(df["date"].unique())
    hol = kr_holidays(dates.dt.year.tolist() + [int(dates.dt.year.max()) + 1])

    cal = pd.DataFrame({"date": dates})
    cal["dow"] = cal["date"].dt.day_of_week.astype("int8")
    cal["is_weekend"] = (cal["dow"] >= 5).astype("int8")
    cal["is_holiday"] = cal["date"].dt.date.isin(hol).astype("int8")
    cal["is_holiday_eve"] = (cal["date"] + pd.Timedelta(days=1)).dt.date.isin(hol).astype("int8")
    vac = np.zeros(len(cal), dtype="int8")
    for start, end in SCHOOL_VACATIONS:
        vac |= cal["date"].between(start, end).to_numpy().astype("int8")
    cal["is_school_vacation"] = vac
    cal["daytype"] = [daytype_of(d, hol) for d in cal["date"]]
    return df.merge(cal, on="date", how="left")


def add_weather(df: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """관측 날씨가 없는 날짜(미래)는 작년 같은 시기(±3일) 같은 시간대 평균으로 채웁니다.

    작년 자료도 없으면 직전 7일 같은 시간대 평균을 씁니다.
    (기상청 단기·중기예보 API를 신청하면 이 부분을 예보값으로 바꿀 수 있음)
    """
    cols = ["temp", "precip", "humidity"]
    out = df.merge(weather, on=["date", "hour"], how="left")
    missing = out["temp"].isna()
    if missing.any():
        for d in out.loc[missing, "date"].unique():
            ly = weather[weather["date"].between(d - pd.Timedelta(days=368), d - pd.Timedelta(days=362))]
            src = ly if not ly.empty else weather[weather["date"] >= weather["date"].max() - pd.Timedelta(days=6)]
            clim = src.groupby("hour")[cols].mean()
            m = missing & (out["date"] == d)
            for col in cols:
                out.loc[m, col] = out.loc[m, "hour"].map(clim[col]).to_numpy()
    out["precip"] = out["precip"].fillna(0)
    out["is_rain"] = (out["precip"] > 0).astype("int8")
    return out


def add_lags(df: pd.DataFrame, history: pd.DataFrame, days=None) -> pd.DataFrame:
    """history(과거 관측)에서 k일 전 같은 노선·정류장·시간 승객 수(lag{k})를 붙입니다."""
    keys = ["route", "stop_id", "stop_seq", "hour"]
    hist = history[keys + ["date", "boarding"]].dropna(subset=["boarding"])
    out = df
    for k in days or (LAG_DAYS + YEAR_LAG_DAYS):
        shifted = hist.assign(date=hist["date"] + pd.Timedelta(days=k)).rename(columns={"boarding": f"lag{k}"})
        out = out.merge(shifted, on=keys + ["date"], how="left")
    return out


def encode_categories(df: pd.DataFrame, route_cats: list[str], stop_cats: list[str]) -> pd.DataFrame:
    df["route_code"] = pd.Categorical(df["route"], categories=route_cats)
    df["stop_code"] = pd.Categorical(df["stop_id"], categories=stop_cats)
    return df


def build_features(df: pd.DataFrame, history: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    out = add_calendar(df)
    out = add_weather(out, weather)
    out = add_lags(out, history)
    out["recent_mean"] = out[[f"lag{k}" for k in LAG_DAYS]].mean(axis=1)
    out["year_mean"] = out[[f"lag{k}" for k in YEAR_LAG_DAYS]].mean(axis=1)
    hol = kr_holidays(range(out["date"].min().year - 1, out["date"].max().year + 1))
    out["lag364_holiday"] = (out["date"] - pd.Timedelta(days=364)).dt.date.isin(hol).astype("int8")
    # 베이스라인: 작년 같은 요일·같은 시간대 승객 수 (21일 앞 예측에서도 항상 알 수 있는 값)
    out["baseline"] = out["lag364"]
    return out
