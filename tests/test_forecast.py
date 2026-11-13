import json

import pandas as pd
import pytest

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


def _profile(dataset, until):
    from app.domains.bus.features import add_calendar
    from app.domains.bus.forecaster import BusForecaster
    long = dataset.load_long()
    calendar = add_calendar(long[["date"]].drop_duplicates())
    return BusForecaster(dataset)._build_load_profile(long, calendar, until)


def test_load_profile_ignores_section_data_after_cutoff(sample_model, monkeypatch):
    """환산 비율은 기준일까지의 자료로만 만듦 (평가 구간의 실제 재차인원이 섞이면 안 됨)"""
    # 준비: 기준일 뒤의 재차인원을 5배로 부풀린 자료
    from app.domains.bus.data import BusDataset
    dataset = BusDataset("sample")
    section = dataset.load_section_long()
    until = sorted(section["date"].unique())[len(section["date"].unique()) // 2]
    inflated = section.copy()
    inflated.loc[inflated["date"] > until, "onboard"] *= 5

    # 실행
    profile, window = _profile(dataset, pd.Timestamp(until))
    monkeypatch.setattr(dataset, "load_section_long", lambda: inflated)
    profile_inflated, _ = _profile(dataset, pd.Timestamp(until))

    # 확인: 기준일 뒤 자료가 달라져도 비율은 그대로, 사용 구간은 기준일에서 끝남
    pd.testing.assert_frame_equal(profile.reset_index(drop=True), profile_inflated.reset_index(drop=True))
    assert window["end"] == f"{pd.Timestamp(until):%Y-%m-%d}"
    assert (profile["ratio"] >= 0).all() and len(profile) > 0


def test_load_profile_fails_clearly_without_data_before_cutoff(sample_model):
    """기준일 이전 자료가 하나도 없으면 빈 비율표를 조용히 만들지 않고 오류"""
    import pytest
    from app.domains.bus.data import BusDataset
    with pytest.raises(ValueError, match="환산 비율"):
        _profile(BusDataset("sample"), pd.Timestamp("2000-01-01"))


def test_training_stopped_before_tree_cap(sample_model):
    """트리 개수는 early stopping이 정해야 함 (상한에 걸리면 학습이 덜 끝난 것)"""
    m = json.loads((MODELS_DIR / "bus/sample/metrics.json").read_text(encoding="utf-8"))
    assert m["hit_tree_cap"] is False
    assert m["best_iteration"] < m["max_trees"] - 1


# ---------- 혼잡도의 시간 기준 (기점 출발 시각) ----------

def test_forward_window_sums_following_hours_within_the_day():
    """N시 출발 버스의 승객 = N시부터 몇 시간 동안의 승차 합. 날짜를 넘기지 않고, 창이 전부 비면 빈 값"""
    import numpy as np
    from app.domains.bus.forecaster import forward_window
    by_hour = pd.DataFrame([{4: 10.0, 5: 20.0, 6: np.nan, 22: 5.0, 23: 7.0}])

    out = forward_window(by_hour, hours=3).iloc[0]

    assert out[4] == 30.0 and out[5] == 20.0      # 4시: 4·5·6시 합 (6시는 빈 값이라 0으로 봄)
    assert out[22] == 12.0 and out[23] == 7.0     # 23시 뒤는 다음 날이라 더하지 않음
    assert pd.isna(out[6]) and pd.isna(out[10])   # 창 안이 전부 비어 있으면 빈 값


def test_departures_use_first_stops_only():
    """기점 출발 횟수는 앞쪽 정류장의 운행횟수. 뒤쪽 정류장에 버스가 몰려도 영향 없음"""
    from app.domains.bus.advisor import departures
    frame = pd.DataFrame({
        "stop_seq": [1, 2, 3, 4, 5, 60, 61],
        "hour": 8,
        "trips": [10, 10, 9, 10, 11, 3, 25],
    })

    assert departures(frame)[8] == 10


def test_actual_congestion_is_observed_onboard_per_departure(sample_model):
    """실제 혼잡도 = 원본 구간 재차인원 ÷ 기점 출발 횟수 ÷ 정원 (환산 비율로 추정한 값이 아님)"""
    from app.config import BUS_CAPACITY
    from app.domains.bus.advisor import BusAdvisor, departures
    from app.domains.bus.data import BusDataset
    from app.domains.bus.forecaster import BusForecaster
    dataset = BusDataset("sample")
    forecaster = BusForecaster(dataset)
    assert forecaster.load()
    advisor = BusAdvisor(None, forecaster, dataset)
    date = forecaster.store.loc[forecaster.store["split"] == "valid", "date"].max()
    route = dataset.routes[0]

    day = advisor.analyze_day(route, date)
    seg = day["segments"].dropna(subset=["congestion_actual"])
    row = seg.loc[seg["congestion_actual"].idxmax()]

    section = dataset.section_long
    observed = section[(section["date"] == date) & (section["route"] == route) & (section["hour"] == row["hour"])
                       & (section["stop_id"] == row["stop_id"]) & (section["stop_seq"] == row["stop_seq"])]["onboard"].iloc[0]
    trips = departures(day["frame"])[row["hour"]]
    assert day["actual_source"] == "observed"
    assert row["congestion_actual"] == pytest.approx(observed / trips / BUS_CAPACITY)


def test_rule_advice_never_exceeds_authorized_buses(sample_model, monkeypatch):
    """시간대별 권고(규칙)도 인가대수 안에서만 늘림. 넘으면 가능한 만큼만 권고하고 이유를 남김"""
    # 준비: 인가대수를 지금 필요한 대수보다 1대만 많게 줄인 노선 정보
    from app.domains.bus.advisor import BusAdvisor, departures_by_hour
    from app.domains.bus.data import BusDataset
    from app.domains.bus.fleet import peak_on_road
    from app.domains.bus.forecaster import BusForecaster
    dataset = BusDataset("sample")
    forecaster = BusForecaster(dataset)
    assert forecaster.load()
    advisor = BusAdvisor(None, forecaster, dataset)
    date = forecaster.store.loc[forecaster.store["split"] == "valid", "date"].max()
    forecaster.store.loc[forecaster.store["date"] == date, "pred"] *= 3     # 증차 권고가 나오도록 예측을 3배로
    original = dataset.route_info_for

    limited, checked = 0, 0
    for route in dataset.routes:
        day = advisor.analyze_day(route, date)
        info = original(route, day["daytype"])
        run = int(round(float(info["run_minutes"])))
        trips = departures_by_hour(day)
        for hour in range(24):
            if trips[hour] < 1:
                continue
            now = peak_on_road(trips, run, hour)
            monkeypatch.setattr(dataset, "route_info_for", lambda r, d, info=info, now=now: {**info, "buses": now + 1})

            # 실행
            rec = advisor.analyze_hour(route, date, hour)["recommendation"]

            # 확인
            checked += 1
            assert rec["buses_needed"] <= now + 1
            limited += rec["limited_by"] == "인가대수"
    assert checked > 0 and limited > 0, "인가대수에 막히는 시간대가 하나는 있어야 이 테스트가 의미 있음"
