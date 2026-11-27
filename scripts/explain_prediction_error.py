# ==================================================
# 예측 오차를 배차 담당자의 말로 (요청 R-2)
# ==================================================
# 평가 구간의 노선·시간대마다 "가장 붐비는 구간"의 혼잡도를 예측값과 실제값으로 구해 비교합니다.
#   버스 한 대 기준 오차(명) = |예측 혼잡도 − 실제 혼잡도| × 정원(BUS_CAPACITY)
#   혼잡 단계가 틀린 비율     = 예측으로 매긴 단계(여유·보통·혼잡·매우혼잡) ≠ 실제로 매긴 단계
#   붐빔 판단                 = 혼잡도 CONGESTION_HIGH(0.8) 이상인지를 예측이 맞혔는지 (놓침 / 헛경보)
# 예측값은 승객 수 상한(P90)을 곱하지 않은 평균 예측입니다(대시보드·권고 화면의 숫자).
# 최적화는 상한(평균 예측 × 노선·시간별 P90 비율)으로 판단하므로, 상한 기준의 붐빔 판단도 함께 셉니다.
# 실제값은 구간 재차인원 원본이 있는 날은 그 값, 없으면 실제 승차로 같은 방식으로 계산한 값입니다.
#
#   python scripts/explain_prediction_error.py [--name 1]
#   → eval/results/prediction_error_<name>.json
#
# API 서버를 끄고 돌립니다 (실데이터를 같이 올리면 메모리가 모자람).

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import BUS_CAPACITY, BUS_TUNE_END, CONGESTION_HIGH, EVAL_RESULTS_DIR, GUIDE_PEAK_HOURS, MODELS_DIR  # noqa: E402
from app.domains.bus.advisor import BusAdvisor, congestion_level  # noqa: E402
from app.domains.bus.calibration import load_ratios, ratio_for  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.forecaster import BusForecaster  # noqa: E402

PEAK_HOURS = GUIDE_PEAK_HOURS          # 출퇴근 시간대 (길안내와 같은 구분, app/config.py)
LEVEL_ORDER = {"여유": 0, "보통": 1, "혼잡": 2, "매우혼잡": 3}


def cells(advisor: BusAdvisor, table: dict, routes: list[str], date) -> list[dict]:
    """노선·시간대별 가장 붐비는 구간의 예측·실제 혼잡도 (운행이 있고 실제값이 있는 시간대만)"""
    out = []
    for route in routes:
        try:
            day = advisor.analyze_day(route, date)
        except ValueError:          # 원천 결측일
            continue
        seg = day["segments"]
        for hour, group in seg.groupby("hour"):
            pred, actual = group["congestion"].max(), group["congestion_actual"].max()
            if pd.isna(pred) or pd.isna(actual):
                continue
            out.append({"date": f"{pd.Timestamp(date):%Y-%m-%d}", "route": route, "hour": int(hour),
                        "daytype": day["daytype"], "pred": float(pred), "actual": float(actual),
                        "upper": float(pred) * ratio_for(table, route, int(hour))})
    return out


def summarize(df: pd.DataFrame) -> dict:
    per_bus = (df["pred"] - df["actual"]).abs() * BUS_CAPACITY
    level_pred, level_actual = df["pred"].map(congestion_level), df["actual"].map(congestion_level)
    step = (level_pred.map(LEVEL_ORDER) - level_actual.map(LEVEL_ORDER)).abs()
    crowded_pred, crowded_actual = df["pred"] >= CONGESTION_HIGH, df["actual"] >= CONGESTION_HIGH
    crowded_upper = df["upper"] >= CONGESTION_HIGH
    return {
        "cells": len(df),
        "per_bus_mae": round(float(per_bus.mean()), 1),
        "per_bus_median": round(float(per_bus.median()), 1),
        "per_bus_p90": round(float(per_bus.quantile(0.9)), 1),
        "bias_per_bus": round(float(((df["pred"] - df["actual"]) * BUS_CAPACITY).mean()), 1),   # +면 많게 예측
        "level_mismatch_pct": round(float((step > 0).mean() * 100), 1),
        "level_off_by_2_pct": round(float((step >= 2).mean() * 100), 1),
        "crowded_actual": int(crowded_actual.sum()),
        "crowded_missed": int((crowded_actual & ~crowded_pred).sum()),          # 실제로는 붐볐는데 예측은 아님
        "crowded_false_alarm": int((~crowded_actual & crowded_pred).sum()),     # 예측은 붐빔인데 실제는 아님
        "upper_crowded_missed": int((crowded_actual & ~crowded_upper).sum()),   # 상한(P90) 기준으로 판단했을 때
        "upper_crowded_false_alarm": int((~crowded_actual & crowded_upper).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="1")
    args = ap.parse_args()

    dataset = BusDataset("real")
    forecaster = BusForecaster(dataset)
    if not forecaster.load():
        raise SystemExit("학습된 모델이 없습니다. 먼저 실행: python scripts/train_bus.py --mode real")
    advisor = BusAdvisor(None, forecaster, dataset)
    table = load_ratios(MODELS_DIR / "bus" / "real" / "p90.json")
    if table is None:
        raise SystemExit("승객 수 상한 비율표가 없습니다. 먼저 실행: python scripts/calibrate_p90.py")
    store = forecaster.store
    tune_end = pd.Timestamp(BUS_TUNE_END)
    dates = [d for d in sorted(store.loc[store["split"] == "valid", "date"].unique()) if pd.Timestamp(d) > tune_end]

    rows = []
    for date in dates:
        rows += cells(advisor, table, dataset.routes, date)
        advisor.clear_cache()
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("평가 구간에 실제값이 있는 시간대가 없습니다.")

    result = {
        "eval_window": {"start": df["date"].min(), "end": df["date"].max(), "days": int(df["date"].nunique())},
        "capacity": BUS_CAPACITY, "crowded_threshold": CONGESTION_HIGH,
        "all": summarize(df),
        "peak_hours": summarize(df[df["hour"].isin(PEAK_HOURS)]),
        "by_daytype": {k: summarize(g) for k, g in df.groupby("daytype")},
        "by_route": {k: summarize(g) for k, g in df.groupby("route")},
    }
    out = EVAL_RESULTS_DIR / f"prediction_error_{args.name}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "by_route"}, ensure_ascii=False, indent=1))
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
