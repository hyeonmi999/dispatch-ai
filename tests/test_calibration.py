# 승객 수 상한(P90) 보정 테스트 (F-11)
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domains.bus.calibration import coverage, fit_ratios, ratio_for, route_hour_totals, split_by_date, upper_bound

ROOT = Path(__file__).resolve().parents[1]


def _totals(route: str, hour: int, ratios: list[float], start: str = "2026-09-01") -> pd.DataFrame:
    """예측 100명에 실제가 ratio배인 날을 하루씩 만든 노선·날짜·시간 합계"""
    return pd.DataFrame({
        "route": route, "hour": hour, "date": pd.date_range(start, periods=len(ratios)),
        "pred": 100.0, "boarding": [100.0 * r for r in ratios],
    })


def test_ratio_is_90th_percentile_per_route_hour():
    # 준비: 비율이 0.80, 0.82 … 1.18로 고르게 퍼진 20일
    spread = list(np.linspace(0.80, 1.18, 20))
    totals = _totals("143", 8, spread)

    # 실행
    table = fit_ratios(totals, quantile=0.90, min_samples=10)

    # 확인: 90% 분위수, 그리고 그 비율을 곱하면 20일 중 18일(90%)이 상한 이하
    assert table["ratios"]["143"]["8"] == pytest.approx(np.quantile(spread, 0.90), abs=1e-4)
    score = coverage(totals, upper_bound(totals, table))
    assert score["covered_pct"] == 90.0 and score["margin_pct"] > 0


def test_small_sample_hour_falls_back_to_route_value():
    # 준비: 8시는 20일, 3시(심야)는 4일뿐
    totals = pd.concat([_totals("143", 8, [1.0] * 18 + [1.2, 1.3]), _totals("143", 3, [3.0] * 4)])

    # 실행
    table = fit_ratios(totals, quantile=0.90, min_samples=10)

    # 확인: 3시는 자체 값(3.0)을 쓰지 않고 노선 전체 값으로 대체
    assert "3" not in table["ratios"]["143"]
    assert ratio_for(table, "143", 3) == table["route_default"]["143"]
    assert ratio_for(table, "143", 8) == table["ratios"]["143"]["8"]


def test_unknown_route_raises():
    table = fit_ratios(_totals("143", 8, [1.0] * 12))
    with pytest.raises(KeyError):
        ratio_for(table, "999", 8)


def test_missing_boarding_is_dropped_not_counted_as_zero():
    # 준비: 정류장 2곳 중 한 곳의 승차가 결측(집계 누락)인 검증 구간 하루
    store = pd.DataFrame({
        "split": ["valid", "valid", "train"], "route": "143", "date": pd.Timestamp("2026-09-01"), "hour": 8,
        "boarding": [50.0, np.nan, 999.0], "pred": [40.0, 60.0, 999.0],
    })

    # 실행
    totals = route_hour_totals(store)

    # 확인: 결측 행은 실제·예측 양쪽에서 빠지고, 학습 구간 행은 쓰지 않음
    assert len(totals) == 1
    assert totals.iloc[0]["boarding"] == 50.0 and totals.iloc[0]["pred"] == 40.0


def test_ratios_do_not_see_evaluation_window():
    """누수 방지: 평가 구간의 실제값을 바꿔도 튜닝 구간으로 만든 비율표는 그대로"""
    # 준비: 앞 14일은 튜닝, 뒤 14일은 평가
    totals = _totals("143", 8, [1.0] * 28)
    tune_end = pd.Timestamp("2026-09-14")
    spiked = totals.copy()
    spiked.loc[spiked["date"] > tune_end, "boarding"] *= 5

    # 실행
    tune, evaluation = split_by_date(totals, tune_end)
    tune_spiked, _ = split_by_date(spiked, tune_end)

    # 확인
    assert tune["date"].max() < evaluation["date"].min()
    assert fit_ratios(tune) == fit_ratios(tune_spiked)


def test_script_writes_table_for_every_sample_route(sample_model):
    # 실행: 합성 데이터로 스크립트 전체를 돌림
    subprocess.run([sys.executable, "scripts/calibrate_p90.py", "--mode", "sample"], cwd=ROOT, check=True)

    # 확인: 모든 합성 노선에 비율이 있고, 튜닝 구간이 평가 구간보다 앞
    from app.config import BUS_SAMPLE_ROUTES, MODELS_DIR
    saved = json.loads((MODELS_DIR / "bus/sample/p90.json").read_text(encoding="utf-8"))
    assert set(saved["route_default"]) == set(BUS_SAMPLE_ROUTES)
    assert all(value > 0 for value in saved["route_default"].values())
    assert saved["fit_window"]["end"] < saved["eval_window"]["start"]


def test_default_tune_end_real_uses_config_and_sample_uses_first_half():
    # 준비: 검증 구간 20일
    from app.config import BUS_TUNE_END
    from app.domains.bus.calibration import default_tune_end
    dates = pd.date_range("2026-09-28", periods=20)

    # 확인: 실데이터는 설정값, 합성 데이터는 앞쪽 절반의 마지막 날 (10번째 날)
    assert default_tune_end(dates, "real") == pd.Timestamp(BUS_TUNE_END)
    assert default_tune_end(dates, "sample") == dates[9]
