import json

import pandas as pd

from app.config import MODELS_DIR, PROCESSED_DIR
from app.domains.bus.features import add_lags


def test_time_ordered_split_no_leakage():
    """학습 구간의 마지막 날짜 < early-stop 구간 < 검증 구간 (무작위 분할 금지)"""
    store = pd.read_parquet(PROCESSED_DIR / "bus_sample_store.parquet")
    last = {s: g["date"] for s, g in store.groupby("split")}
    assert last["train"].max() < last["early_stop"].min()
    assert last["early_stop"].max() < last["valid"].min()
    assert last["valid"].max() < last["forecast"].min()


def test_lags_only_use_values_at_least_21_days_old():
    """공개 시차 대응: 모든 lag 피처는 예측일 기준 21일 이상 과거 값 (21일 앞까지 예측 가능)"""
    from app.domains.bus.features import FEATURES, HORIZON_DAYS, LAG_DAYS, YEAR_LAG_DAYS

    assert min(LAG_DAYS + YEAR_LAG_DAYS) >= HORIZON_DAYS == 21
    assert not any(f in FEATURES for f in ("lag7", "lag14"))

    hist = pd.DataFrame({
        "route": "r", "stop_id": "s", "stop_seq": 1, "hour": 8,
        "date": pd.date_range("2026-01-01", periods=60), "boarding": range(60),
    })
    target = hist[hist["date"] == "2026-02-28"].drop(columns="boarding")   # 59번째 날
    out = add_lags(target, hist).iloc[0]
    assert out["lag21"] == 37 and out["lag42"] == 16 and pd.isna(out["lag364"])


def test_metrics_saved_with_baseline_and_improvement():
    m = json.loads((MODELS_DIR / "bus/sample/metrics.json").read_text())
    for key in ("stop_hour", "route_hour"):
        assert {"model_mae", "baseline_mae", "improvement_pct"} <= set(m[key])
    assert m["stop_hour"]["model_mae"] < m["stop_hour"]["baseline_mae"]


def test_blank_source_day_is_missing_not_zero():
    """승차 값이 하루 통째로 빈 날(버스는 정상 운행)은 0명이 아니라 결측으로 분류"""
    import numpy as np
    from app.domains.bus.data import BusDataset

    hb = {f"승차_승객_수_{h:02d}시": 10.0 for h in range(24)}
    hd = {f"버스운행횟수_{h:02d}시": 5.0 for h in range(24)}
    days = [f"202609{d:02d}" for d in range(1, 30)]
    b = pd.DataFrame([{"기준_날짜": d, "노선명": "1", **hb} for d in days])
    d = pd.DataFrame([{"기준_날짜": x, "노선명": "1", **hd} for x in days])
    b.loc[b["기준_날짜"] == "20260929", list(hb)] = np.nan      # 집계 누락
    d.loc[d["기준_날짜"] == "20260915", list(hd)] = 0.2         # 운행 거의 없음
    b.loc[b["기준_날짜"] == "20260915", list(hb)] = 0.1
    found = dict(zip(BusDataset.find_anomalies(b, d)["date"], BusDataset.find_anomalies(b, d)["reason"]))
    assert found == {"20260929": "승차 집계 누락", "20260915": "운행 중단(파업 등 추정)"}


def _calendar(rows: dict) -> pd.Series:
    return pd.Series(rows, name="daytype").rename(index=pd.Timestamp).sort_index()


def test_trips_source_skips_holiday_for_regular_day():
    """평일 예측에 휴일 배차가 섞이지 않게: 2주 전이 대체 휴일이면 그 전주 값을 사용"""
    from app.domains.bus.forecaster import trips_source_date

    cal = _calendar({"2026-09-28": "weekday", "2026-10-05": "holiday"})
    assert trips_source_date(pd.Timestamp("2026-10-19"), "weekday", cal) == pd.Timestamp("2026-09-28")


def test_trips_source_for_holiday_uses_latest_sunday_or_holiday():
    """휴일 예측은 요일과 상관없이 가장 최근 일요일·공휴일의 배차를 사용"""
    from app.domains.bus.forecaster import trips_source_date

    cal = _calendar({"2026-12-13": "holiday", "2026-12-18": "weekday", "2026-12-20": "holiday"})
    assert trips_source_date(pd.Timestamp("2026-12-25"), "holiday", cal) == pd.Timestamp("2026-12-20")


def test_trips_source_keeps_latest_same_weekday_on_regular_weeks():
    from app.domains.bus.forecaster import trips_source_date

    cal = _calendar({"2026-10-12": "weekday", "2026-10-19": "weekday"})
    assert trips_source_date(pd.Timestamp("2026-11-02"), "weekday", cal) == pd.Timestamp("2026-10-19")


def test_trips_source_falls_back_to_same_weekday_when_no_daytype_match():
    from app.domains.bus.forecaster import trips_source_date

    cal = _calendar({"2026-10-05": "holiday"})
    assert trips_source_date(pd.Timestamp("2026-10-19"), "weekday", cal) == pd.Timestamp("2026-10-05")
    assert trips_source_date(pd.Timestamp("2026-10-20"), "weekday", cal) is None
