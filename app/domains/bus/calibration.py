# ==================================================
# 승객 수 상한(P90) 보정  (F-11)
# ==================================================
# 예측값(pred)은 평균 수준입니다. 버스가 모자라면 승객이 못 타지만 조금 남는 것은 괜찮으므로,
# 증차 판단은 "10번 중 9번은 이보다 적게 탄다"는 넉넉한 값(P90)으로 합니다.
# 검증 구간의 (실제 ÷ 예측) 비율에서 90% 분위수를 구해 pred에 곱합니다. 재학습은 하지 않습니다.
#
#   1) 노선·날짜·시간 단위로 실제 합계와 예측 합계를 냄 (정류장 단위 비율은 값이 작아 크게 흔들림)
#   2) 노선·시간별 비율의 90% 분위수. 표본이 적은 시간대는 그 노선 전체 값으로 대체
#   3) 상한 = 예측 × 비율
#
# 비율표는 검증 구간 앞쪽(튜닝 구간)으로만 만들고 뒤쪽(평가 구간)에서는 채점만 합니다.
# 평가 구간의 실제값이 비율표에 섞이면 배차 방식 성능 비교(F-14) 점수가 부풀려집니다.

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import BUS_P90_MIN_SAMPLES, BUS_P90_QUANTILE

logger = logging.getLogger(__name__)

KEYS = ["route", "date", "hour"]


# ==================================================
# 1. 집계
# ==================================================

def route_hour_totals(store: pd.DataFrame) -> pd.DataFrame:
    """검증 구간의 노선·날짜·시간별 실제 합계와 예측 합계

    승차 값이 결측인 행(원천 집계 누락일)은 실제·예측 양쪽에서 함께 뺍니다. 0으로 채우지 않습니다.
    """
    valid = store[(store["split"] == "valid") & store["boarding"].notna()]
    return valid.groupby(KEYS, observed=True)[["boarding", "pred"]].sum().reset_index()


def split_by_date(totals: pd.DataFrame, tune_end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(튜닝 구간, 평가 구간). tune_end 당일까지가 튜닝 구간입니다."""
    tune = totals[totals["date"] <= tune_end]
    evaluation = totals[totals["date"] > tune_end]
    if len(tune) and len(evaluation):
        assert tune["date"].max() < evaluation["date"].min()
    return tune, evaluation


# ==================================================
# 2. 비율표
# ==================================================

def fit_ratios(totals: pd.DataFrame, quantile: float = BUS_P90_QUANTILE,
               min_samples: int = BUS_P90_MIN_SAMPLES) -> dict:
    """노선·시간별 (실제 ÷ 예측) 비율의 분위수

    반환: {"route_default": {노선: 비율}, "ratios": {노선: {시간: 비율}}}
    표본(날짜 수)이 min_samples 미만인 시간대는 ratios에 넣지 않고, 조회할 때 route_default를 씁니다.
    """
    usable = totals[totals["pred"] > 0]            # 예측 합계가 0이면 비율을 구할 수 없음
    ratio = usable["boarding"] / usable["pred"]

    by_route = ratio.groupby(usable["route"]).quantile(quantile)
    cells = ratio.groupby([usable["route"], usable["hour"]])
    by_cell = cells.quantile(quantile)[cells.size() >= min_samples]

    ratios: dict[str, dict[str, float]] = {str(route): {} for route in by_route.index}
    for (route, hour), value in by_cell.items():
        ratios[str(route)][str(int(hour))] = round(float(value), 4)
    return {
        "route_default": {str(route): round(float(value), 4) for route, value in by_route.items()},
        "ratios": ratios,
    }


def ratio_for(table: dict, route: str, hour: int) -> float:
    """노선·시간의 상한 비율. 그 시간대 값이 없으면 노선 전체 값"""
    if route not in table["route_default"]:
        raise KeyError(f"상한 비율표에 없는 노선입니다: {route}")
    return table["ratios"].get(route, {}).get(str(int(hour)), table["route_default"][route])


def load_ratios(path: Path) -> dict | None:
    """저장된 비율표(scripts/calibrate_p90.py 결과). 파일이 없거나 읽을 수 없으면 None"""
    if not path.exists():
        return None
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("상한 비율표를 읽지 못했습니다 (%s): %s", path, e)
        return None
    if not {"route_default", "ratios"} <= set(table):
        logger.warning("상한 비율표 형식이 다릅니다 (%s)", path)
        return None
    return table


def upper_bound(totals: pd.DataFrame, table: dict) -> pd.Series:
    """상한 = 예측 × 비율"""
    ratios = [ratio_for(table, route, hour) for route, hour in zip(totals["route"], totals["hour"])]
    return totals["pred"] * np.asarray(ratios, dtype="float64")


# ==================================================
# 3. 채점
# ==================================================

def coverage(totals: pd.DataFrame, upper: pd.Series) -> dict:
    """상한이 얼마나 맞는지

    covered_pct: 실제 승객이 상한 이하였던 노선·날짜·시간의 비율 (목표 90% 안팎)
    margin_pct : 상한이 예측보다 얼마나 큰지 (전체 합계 기준). 작을수록 버스를 덜 남김
    """
    if not len(totals):
        return {"rows": 0, "covered_pct": None, "margin_pct": None}
    return {
        "rows": int(len(totals)),
        "covered_pct": round(float((totals["boarding"] <= upper).mean() * 100), 2),
        "margin_pct": round(float((upper.sum() / totals["pred"].sum() - 1) * 100), 2),
    }
