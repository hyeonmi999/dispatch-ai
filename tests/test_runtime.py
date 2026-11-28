# 시간대별 한 바퀴 운행시간 테스트 (이슈 #42 방법 1, docs/plans/park-next.md Task P-7)
import numpy as np
import pandas as pd
import pytest

from app.config import BUS_RUNTIME_LAG_DAYS
from app.domains.bus.fleet import checkpoints, on_road, peak_on_road
from app.domains.bus.runtime import HOUR_COLS, build_table, daily_trips, profile, trip_minutes, usable_asof


# ==================================================
# 1. 도로 위 버스 대수 (fleet.py)
# ==================================================

def test_same_value_profile_equals_fixed_run_minutes():
    """24칸이 모두 같으면 정수 하나를 준 것과 결과가 같음 (표가 없을 때와 같은 계산)"""
    trips = [0, 0, 0, 0, 3, 6, 8, 12, 12, 9, 7, 7, 7, 7, 7, 8, 9, 11, 12, 10, 7, 5, 4, 2]
    fixed, flat = 150, [150] * 24

    for hour in range(4, 24):
        assert peak_on_road(trips, flat, hour) == peak_on_road(trips, fixed, hour)
    for m in range(0, 26 * 60, 7):
        assert on_road(trips, flat, m) == pytest.approx(on_road(trips, fixed, m))
    assert set(checkpoints(fixed, [7, 8])) <= set(checkpoints(flat, [7, 8]))


def test_longer_run_at_departure_hour_needs_more_buses():
    """매시간 10회 출발, 평소 120분. 8시 출발만 180분이 걸리면 8시 출발 버스가 한 시간 더 도로에 남음"""
    trips = [10.0] * 24
    fixed = 120
    slow = [120] * 24
    slow[8] = 180

    assert peak_on_road(trips, fixed, 8) == 20
    # 9:00 ~ 11:00에 8시 출발 버스(최대 10대)가 아직 돌고 있어 7·9·10시 출발분 20대에 더해짐
    assert peak_on_road(trips, slow, 8) == 30
    # 다른 시간대 값은 그대로 쓰므로 8시와 겹치지 않는 시각은 그대로
    assert on_road(trips, slow, 6 * 60) == pytest.approx(on_road(trips, fixed, 6 * 60))


def test_fleet_need_uses_profile_from_state():
    from app.domains.bus.simulator import fleet_need
    hourly = pd.DataFrame({"hour": range(24), "trips": [10.0] * 24})
    slow = [120] * 24
    slow[8] = 180
    base = {"info": {"run_minutes": 120, "buses": 25}, "day": {"hourly": hourly}}

    fixed = fleet_need(base, 8, 10.0)
    timed = fleet_need({**base, "run": slow}, 8, 10.0)

    assert fixed["buses_needed"] == 20 and fixed["over"] is False
    assert timed["buses_needed"] == 30 and timed["over"] is True


def test_cpsat_respects_fleet_with_profile():
    """출퇴근 운행시간이 길면 같은 인가대수에서 늘릴 수 있는 운행이 줄어듦"""
    from app.config import BUS_CAPACITY, BUS_OPT_WEIGHTS, CONGESTION_TARGET
    from app.domains.bus.optimizer import solve_cpsat
    per_bus = CONGESTION_TARGET * BUS_CAPACITY
    current = [6 if 5 <= h <= 22 else 0 for h in range(24)]

    def inputs(run):
        cell = {"current": 6, "low": 4, "high": 15, "onboard": per_bus * 12, "ratio": 1.0}
        return {"routes": {"r1": {"current": current, "cells": {8: dict(cell)}, "fleet": 20,
                                  "fleet_estimated": False, "run_minutes": run}}}

    slow = [120] * 24
    slow[7] = slow[8] = slow[9] = 160
    fast_plan = solve_cpsat(inputs(120), BUS_OPT_WEIGHTS)[0]
    slow_plan = solve_cpsat(inputs(slow), BUS_OPT_WEIGHTS)[0]

    assert slow_plan["r1", 8] < fast_plan["r1", 8]
    trips = current.copy()
    trips[8] = slow_plan["r1", 8]
    assert max(on_road(trips, slow, m) for m in checkpoints(slow, [8])) <= 20 + 1e-6


# ==================================================
# 2. 하루 값 (구간 시간 누적)
# ==================================================

def _seconds(rows: list[dict[int, float]]) -> np.ndarray:
    """구간마다 {시간: 초} → (구간 수, 24) 배열 (없는 시간은 0)"""
    out = np.zeros((len(rows), 24))
    for i, row in enumerate(rows):
        for hour, value in row.items():
            out[i, hour] = value
    return out


def test_trip_uses_value_of_hour_when_bus_reaches_segment():
    """8:30 출발. 구간 1은 8시 값 20분, 8:50에 구간 2에 닿으면 8시 값 → 9:30에 구간 3이면 9시 값"""
    seconds = _seconds([{8: 1200, 9: 600}, {8: 2400, 9: 9999}, {8: 9999, 9: 1800}])
    daily = np.array([900.0, 900.0, 900.0])

    minutes, fill = trip_minutes(seconds, daily, 8 * 60 + 30)

    assert minutes == pytest.approx(20 + 40 + 30) and fill == 0


def test_trip_crosses_midnight():
    """23:30 출발, 구간 1이 40분 → 00:10에 닿는 구간 2는 0시 값"""
    seconds = _seconds([{23: 2400}, {23: 9999, 0: 600}])
    minutes, _ = trip_minutes(seconds, np.array([1.0, 1.0]), 23 * 60 + 30)
    assert minutes == pytest.approx(40 + 10)


def test_trip_fills_missing_hour_with_daily_mean_and_skips_no_departure():
    seconds = _seconds([{8: 600}, {}, {8: 600, 9: 600}])    # 구간 3에는 9:00에 닿음
    minutes, fill = trip_minutes(seconds, np.array([600.0, 1200.0, 600.0]), 8 * 60 + 30)
    assert minutes == pytest.approx(10 + 20 + 10) and fill == pytest.approx(1 / 3)
    # 첫 구간에 7시 기록이 없음 = 7시에 출발한 버스가 없음
    assert trip_minutes(seconds, np.array([600.0, 1200.0, 600.0]), 7 * 60 + 30) is None


def _raw(date: str, route: str, per_segment: list[float], seqs: list[int] | None = None) -> pd.DataFrame:
    seqs = seqs or list(range(1, len(per_segment) + 1))
    rows = []
    for seq, sec in zip(seqs, per_segment):
        row = {"기준_날짜": date, "노선명": route, "출발_정류장_순서": seq, "운행시간": sec}
        row.update({c: (sec if 4 <= h <= 22 else 0.0) for h, c in enumerate(HOUR_COLS)})
        rows.append(row)
    return pd.DataFrame(rows)


def test_daily_trips_fills_missing_segment_row_from_earlier_day_only():
    """구간 행이 빠진 날은 앞 날짜의 같은 구간 하루 평균으로 채움. 뒤 날짜 값은 쓰지 않음"""
    raw = pd.concat([
        _raw("20261005", "r1", [600.0] * 10),                              # 구간 10개 × 10분
        _raw("20261006", "r1", [600.0] * 9, seqs=[1, 2, 3, 4, 6, 7, 8, 9, 10]),   # 5번 구간 행 없음
        _raw("20261007", "r1", [6000.0] * 10),                             # 뒤 날짜 (쓰면 안 됨)
    ])
    trips = daily_trips(raw)
    day = trips[(trips["date"] == "2026-10-06") & (trips["hour"] == 8)].iloc[0]
    assert day["minutes"] == pytest.approx(100) and day["fill"] == pytest.approx(0.1)
    # 첫날은 앞 날짜가 없으니 빠진 구간이 있으면 그날을 뺌
    first = daily_trips(_raw("20261005", "r1", [600.0] * 9, seqs=[1, 2, 3, 4, 6, 7, 8, 9, 10]))
    assert first.empty


# ==================================================
# 3. 표와 예측 시점 규칙
# ==================================================

def _trips(start: str, days: int, minutes_of) -> pd.DataFrame:
    dates = pd.date_range(start, periods=days)
    return pd.DataFrame([{"date": d, "route": "r1", "hour": h, "minutes": minutes_of(d, h), "fill": 0.0}
                         for d in dates for h in range(5, 23)])


def test_table_uses_only_days_up_to_asof_and_lookup_lags_21_days():
    # 9월은 120분, 10월부터 200분
    trips = _trips("2026-08-01", 92, lambda d, h: 120.0 if d.month < 10 else 200.0)
    table = build_table(trips)

    sep = pd.Timestamp("2026-09-27")
    assert set(table[table["asof"] == sep]["minutes"]) == {120.0}
    # 10/20 계획: 쓸 수 있는 기준일은 9/29 이전의 일요일(9/27). 10월 값(미래)을 쓰지 않음
    asof = usable_asof(table, "2026-10-20")
    assert asof == sep and asof <= pd.Timestamp("2026-10-20") - pd.Timedelta(days=BUS_RUNTIME_LAG_DAYS)
    assert profile(table, "r1", "weekday", asof)[8] == 120.0
    # 기준일이 하나도 맞지 않으면 None
    assert usable_asof(table, "2026-08-10") is None


def test_table_drops_cells_with_too_few_days():
    trips = _trips("2026-10-01", 14, lambda d, h: 150.0)
    trips = trips[~((trips["hour"] == 22) & (trips["date"] > "2026-10-02"))]    # 22시는 이틀만
    table = build_table(trips)
    last = table[table["asof"] == table["asof"].max()]
    assert 22 not in set(last[last["daytype"] == "weekday"]["hour"]) and 8 in set(last["hour"])


# ==================================================
# 4. 서비스 연결 (합성 데이터)
# ==================================================

def _service_table(adapter, date: str, extra: float = 0.0) -> pd.DataFrame:
    """합성 데이터 노선의 고정 운행시간(+extra분)으로 모든 시간·요일유형을 채운 표 (기준일 = date − 30일)"""
    info = adapter.dataset.route_info
    asof = pd.Timestamp(date) - pd.Timedelta(days=30)
    rows = [{"asof": asof, "route": r["route"], "daytype": r["daytype"], "hour": h,
             "minutes": float(r["run_minutes"]) + extra, "days": 8}
            for _, r in info.iterrows() for h in range(24)]
    return pd.DataFrame(rows)


@pytest.fixture
def bus(client, monkeypatch):
    from app.core.registry import registry
    adapter = registry.get("bus")

    def use(table):
        monkeypatch.setattr(adapter.dataset, "runtime_table", table)
        monkeypatch.setattr(adapter.dataset, "_run_profiles", {})
        adapter.advisor.clear_cache()

    yield adapter, use
    adapter.advisor.clear_cache()


def test_sample_data_has_no_table_and_keeps_fixed_run(bus):
    adapter, _ = bus
    ds = adapter.dataset
    assert ds.runtime_table is None
    assert ds.run_profile(ds.routes[0], "weekday", "2026-10-20", 150) is None


def test_optimize_same_with_flat_table_and_more_buses_with_slow_table(client, bus):
    adapter, use = bus
    date = client.get("/api/bus/meta").json()["dates"]["max_date"]
    body = {"date": date, "hours": [7, 8, 9]}

    use(None)
    before = adapter.optimize(body)
    use(_service_table(adapter, date))
    flat = adapter.optimize(body)
    use(_service_table(adapter, date, extra=60))
    slow = adapter.optimize(body)

    for key in ("plan", "kpi", "binding"):
        assert flat[key] == before[key]
    need = lambda r: {(i["route"], i["hour"]): i["buses_needed"] for i in r["plan"]}  # noqa: E731
    assert all(need(slow)[k] >= v for k, v in need(before).items() if k in need(slow))
    assert any(need(slow)[k] > v for k, v in need(before).items() if k in need(slow))


def test_profile_fills_empty_hours_with_fixed_and_shows_in_rationale(client, bus):
    adapter, use = bus
    meta = client.get("/api/bus/meta").json()
    date, route = meta["dates"]["max_date"], meta["routes"][0]["route"]
    table = _service_table(adapter, date, extra=30)
    table = table[~((table["route"] == route) & (table["hour"] == 5))]
    use(table)

    daytype = adapter.advisor.analyze_day(route, date)["daytype"]
    fixed = float(adapter.dataset.route_info_for(route, daytype)["run_minutes"])
    run, _ = adapter.dataset.run_profile(route, daytype, date, fixed)
    assert run[5] == round(fixed) and run[8] == round(fixed + 30)

    advice = client.post("/api/bus/advise", json={"route": route, "date": date, "hour": 8, "use_llm": False}).json()
    assert any("한 바퀴 운행시간" in line for line in advice["rationale"])
