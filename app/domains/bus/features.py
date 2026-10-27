# ==================================================
# 버스 수요 예측 피처
# ==================================================
# y : 노선·정류장·시간대별 승차 승객 수
# X : 요일·시간대·정류장·날씨·공휴일·운행횟수 + 과거 같은 요일·시간 승객 수(lag)
#
# 미래 데이터 누수 방지
# - lag는 7·14·21·28일 전 값만 사용 (예측 시점 기준 최소 1주 전 관측치)
# - 운행횟수는 사전에 정해지는 배차 계획 값으로 보고 같은 시간대 값을 사용
#   (미래 날짜 예측 시에는 1주 전 같은 요일·시간 운행횟수를 계획값으로 사용)

import holidays
import numpy as np
import pandas as pd

from app.config import SCHOOL_VACATIONS

LAG_DAYS = [7, 14, 21, 28]

FEATURES = [
    "route_code", "stop_code", "stop_seq", "hour", "dow", "is_weekend", "is_holiday",
    "is_holiday_eve", "is_school_vacation", "temp", "precip", "humidity", "is_rain",
    "trips", "lag7", "lag14", "lag21", "lag28", "lag_mean4",
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
    cal["dow"] = cal["date"].dt.dayofweek.astype("int8")
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
    """날씨가 없는 날짜(미래)는 직전 7일 같은 시간대 평균으로 채웁니다."""
    out = df.merge(weather, on=["date", "hour"], how="left")
    missing = out["temp"].isna()
    if missing.any():
        recent = weather[weather["date"] >= weather["date"].max() - pd.Timedelta(days=6)]
        clim = recent.groupby("hour")[["temp", "precip", "humidity"]].mean()
        for col in ["temp", "precip", "humidity"]:
            out.loc[missing, col] = out.loc[missing, "hour"].map(clim[col]).to_numpy()
    out["precip"] = out["precip"].fillna(0)
    out["is_rain"] = (out["precip"] > 0).astype("int8")
    return out


def add_lags(df: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """history(과거 관측)에서 7·14·21·28일 전 같은 노선·정류장·시간 승객 수를 붙입니다."""
    keys = ["route", "stop_id", "stop_seq", "hour"]
    hist = history[keys + ["date", "boarding"]].dropna(subset=["boarding"])
    out = df
    for k in LAG_DAYS:
        shifted = hist.assign(date=hist["date"] + pd.Timedelta(days=k)).rename(columns={"boarding": f"lag{k}"})
        out = out.merge(shifted, on=keys + ["date"], how="left")
    out["lag_mean4"] = out[[f"lag{k}" for k in LAG_DAYS]].mean(axis=1)
    return out


def encode_categories(df: pd.DataFrame, route_cats: list[str], stop_cats: list[str]) -> pd.DataFrame:
    df["route_code"] = pd.Categorical(df["route"], categories=route_cats)
    df["stop_code"] = pd.Categorical(df["stop_id"], categories=stop_cats)
    return df


def build_features(df: pd.DataFrame, history: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    out = add_calendar(df)
    out = add_weather(out, weather)
    out = add_lags(out, history)
    # 베이스라인: 전주 같은 요일·같은 시간대 승객 수
    out["baseline"] = out["lag7"]
    return out
