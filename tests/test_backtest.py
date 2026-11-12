# 배차 방식 성능 비교 테스트 (F-14)
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.config import BUS_OPT_WEIGHTS, CONGESTION_HIGH, MODELS_DIR, PROCESSED_DIR
from app.domains.bus.backtest import ALL_HOURS, METHOD_LABELS, actual_peaks, decide, fleet_check, score_cells, summarize

ROOT = Path(__file__).resolve().parents[1]
DATE = pd.Timestamp("2026-10-20")


def _inputs(current: dict[int, int], hours: list[int], fleet: int, run_minutes: int = 120) -> dict:
    trips = [current.get(h, 0) for h in range(24)]
    cells = {h: {"current": trips[h], "low": 1, "high": 15, "onboard": 100.0, "ratio": 1.0} for h in hours}
    route = {"current": trips, "cells": cells, "fleet": fleet, "fleet_estimated": False, "run_minutes": run_minutes}
    return {"routes": {"r1": route}, "skipped": [], "daytype": "weekday", "is_forecast": False, "date": DATE}


def test_more_trips_lower_actual_congestion_in_proportion():
    # 준비: 8시 현행 8회, 실제 혼잡도 1.0
    inputs = _inputs({8: 8}, [8], fleet=100)
    peaks = {("r1", 8): 1.0}

    # 실행: 10회로 늘린 계획을 채점
    row = score_cells(inputs, {("r1", 8): 10}, peaks)[0]

    # 확인: 혼잡도 = 1.0 × 8 ÷ 10
    assert row["congestion_now"] == 1.0 and row["congestion"] == pytest.approx(0.8)


def test_cells_without_actual_are_not_scored():
    inputs = _inputs({7: 8, 8: 8}, [7, 8], fleet=100)
    rows = score_cells(inputs, {("r1", 7): 8, ("r1", 8): 8}, peaks={("r1", 8): 0.5})
    assert [r["hour"] for r in rows] == [8]


def test_fleet_check_flags_only_plans_that_go_over():
    # 준비: 운행시간 2시간, 버스 16대 → 시간당 8회가 한계. 현행은 딱 8회
    inputs = _inputs({h: 8 for h in range(5, 12)}, [7, 8], fleet=16)
    keep = {("r1", 7): 8, ("r1", 8): 8}
    more = {("r1", 7): 10, ("r1", 8): 10}

    # 실행
    ok, over = fleet_check(inputs, keep)[0], fleet_check(inputs, more)[0]

    # 확인
    assert (ok["over"], ok["over_new"]) == (False, False) and ok["max_on_road"] == pytest.approx(16)
    assert (over["over"], over["over_new"]) == (True, True) and over["max_on_road"] == pytest.approx(20)


def test_current_already_over_is_not_counted_as_new():
    # 준비: 현행이 이미 인가대수(14대)를 넘음 (시간당 8회 × 2시간 = 16대)
    inputs = _inputs({h: 8 for h in range(5, 12)}, [8], fleet=14)

    # 실행: 현행 그대로
    row = fleet_check(inputs, {("r1", 8): 8})[0]

    # 확인: 초과이긴 하지만 방식이 새로 만든 초과는 아님
    assert row["over"] is True and row["over_new"] is False


def test_summary_metrics():
    # 준비: 4칸 중 1칸이 과밀, 1칸을 늘림
    cells = pd.DataFrame({
        "current": [8, 8, 8, 8], "trips": [8, 8, 8, 10],
        "congestion": [CONGESTION_HIGH, 0.5, 0.79, 0.4],
    })
    fleets = pd.DataFrame({"over": [False, True], "over_new": [False, False]})

    # 실행
    m = summarize(cells, fleets)

    # 확인
    assert m["cells"] == 4 and m["overcrowded_cells"] == 1 and m["overcrowded_pct"] == 25.0
    assert m["total_trips"] == 34 and m["trips_vs_current_pct"] == 6.25 and m["changed_cells"] == 1
    assert m["fleet_over_days"] == 1 and m["fleet_over_new_days"] == 0 and m["route_days"] == 2


def test_decisions_do_not_use_actual_passengers(sample_model):
    """누수 방지: 실제 승차를 5배로 바꿔도 네 방식이 정한 운행횟수는 그대로이고, 채점 값만 달라짐"""
    # 준비: 합성 데이터의 검증 구간 하루
    from app.domains.bus.advisor import BusAdvisor
    from app.domains.bus.calibration import load_ratios
    from app.domains.bus.data import BusDataset
    from app.domains.bus.forecaster import BusForecaster
    from app.domains.bus.optimizer import collect_inputs

    dataset = BusDataset("sample")
    forecaster = BusForecaster(dataset)
    assert forecaster.load()
    advisor = BusAdvisor(None, forecaster, dataset)
    table = load_ratios(MODELS_DIR / "bus/sample/p90.json")
    date = forecaster.store.loc[forecaster.store["split"] == "valid", "date"].max()

    # 실행: 원래 실제값으로 한 번, 실제값을 5배로 바꿔서 한 번
    inputs = collect_inputs(advisor, table, dataset.routes, date, ALL_HOURS)
    plans, peaks = decide(advisor, inputs, BUS_OPT_WEIGHTS), actual_peaks(advisor, inputs)

    forecaster.store.loc[forecaster.store["date"] == date, "boarding"] *= 5
    advisor.clear_cache()
    inputs_spiked = collect_inputs(advisor, table, dataset.routes, date, ALL_HOURS)
    plans_spiked, peaks_spiked = decide(advisor, inputs_spiked, BUS_OPT_WEIGHTS), actual_peaks(advisor, inputs_spiked)

    # 확인
    assert set(plans) == set(METHOD_LABELS)
    assert plans == plans_spiked
    assert peaks and all(peaks_spiked[key] == pytest.approx(value * 5) for key, value in peaks.items())


def test_script_scores_all_methods_on_sample(sample_model):
    # 실행: 합성 데이터로 스크립트 전체를 돌림
    subprocess.run([sys.executable, "scripts/backtest.py", "--mode", "sample"], cwd=ROOT, check=True)

    # 확인: 네 방식이 같은 노선·날짜·시간을 채점했고, 현행은 운행횟수가 그대로
    detail = pd.read_parquet(PROCESSED_DIR / "experiment/backtest/cells_sample.parquet")
    assert set(detail["method"]) == set(METHOD_LABELS) | {"cpsat_cut"}     # 네 방식 + 참고 행(감차 허용)
    assert detail.groupby("method").size().nunique() == 1
    current = detail[detail["method"] == "current"]
    assert (current["trips"] == current["current"]).all()
