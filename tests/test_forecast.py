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


def test_lags_only_use_past_week_or_older():
    hist = pd.DataFrame({
        "route": "r", "stop_id": "s", "stop_seq": 1, "hour": 8,
        "date": pd.date_range("2026-01-01", periods=30), "boarding": range(30),
    })
    target = hist[hist["date"] == "2026-01-29"].drop(columns="boarding")
    out = add_lags(target, hist).iloc[0]
    # 1/29 기준 lag7 = 1/22 값(21), lag28 = 1/1 값(0) → 1주 이내 값은 쓰지 않음
    assert out["lag7"] == 21 and out["lag14"] == 14 and out["lag28"] == 0


def test_metrics_saved_with_baseline_and_improvement():
    m = json.loads((MODELS_DIR / "bus/sample/metrics.json").read_text())
    for key in ("stop_hour", "route_hour"):
        assert {"model_mae", "baseline_mae", "improvement_pct"} <= set(m[key])
    assert m["stop_hour"]["model_mae"] < m["stop_hour"]["baseline_mae"]
