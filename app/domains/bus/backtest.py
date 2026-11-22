# ==================================================
# 배차 방식 성능 비교  (F-14)
# ==================================================
# 지난 날짜를 하루씩 다시 돌려, 방식마다 운행횟수를 정하고 실제 승객으로 채점합니다.
#
#   현행    그날 실제 운행횟수 그대로
#   규칙    노선·시간마다 따로 판단 (F-04: 혼잡도 0.8 이상 증차, 0.25 미만 감차)
#   그리디  과밀이 가장 큰 곳부터 한 회씩 증차 (F-13 기준선)
#   최적화  정수계획 (F-13)
#
# 운행횟수를 정할 때는 예측값(승객 수 상한 포함)만 쓰고, 채점할 때만 실제 승객을 씁니다.
# 실제값이 결정에 섞이면 점수가 부풀려지므로, 결정 입력(collect_inputs)과 채점 입력(actual_peaks)을 따로 만듭니다.
#
# 채점 지표
#   과밀 비율      실제 승객 기준 최대 혼잡도가 0.8 이상인 노선·시간 ÷ 전체 노선·시간
#   총 운행        운행횟수 합계 (현행 대비 %)
#   변경 건수      현행과 운행횟수가 다른 노선·시간 수
#   인가대수 초과  도로 위 버스가 인가대수를 넘는 순간이 있는 노선·일 수
#                 (새로 생긴 초과 = 현행에는 없던 초과. 현행이 이미 넘는 순간은 현행 값까지 허용)

import json
from pathlib import Path

import pandas as pd

from app.config import BACKTEST_RESULT_PATH, CONGESTION_HIGH
from app.domains.bus.advisor import BusAdvisor
from app.domains.bus.optimizer import HOURS_PER_DAY, _checkpoints, _fleet_limits, _on_road, solve_cpsat, solve_greedy

ALL_HOURS = list(range(HOURS_PER_DAY))
METHOD_LABELS = {"current": "현행", "rule": "규칙", "greedy": "그리디", "cpsat": "최적화"}
EPS = 1e-6


# ==================================================
# 1. 방식별 운행횟수 결정 (예측값만 사용)
# ==================================================

def current_plan(inputs: dict) -> dict:
    return {(route, hour): cell["current"] for route, r in inputs["routes"].items() for hour, cell in r["cells"].items()}


def rule_plan(advisor: BusAdvisor, inputs: dict) -> dict:
    """노선·시간마다 따로 내는 기존 권고(F-04)의 운행횟수. 권고가 배차간격이라 정수가 아닐 수 있습니다."""
    plan = {}
    for route, r in inputs["routes"].items():
        for hour in r["cells"]:
            advice = advisor.analyze_hour(route, inputs["date"], hour)
            plan[route, hour] = float(advice["recommendation"]["recommended_trips_per_hour"])
    return plan


def decide(advisor: BusAdvisor, inputs: dict, weights: dict) -> dict[str, dict]:
    """방식 이름 → {(노선, 시간): 운행횟수}"""
    return {
        "current": current_plan(inputs),
        "rule": rule_plan(advisor, inputs),
        "greedy": solve_greedy(inputs)[0],
        "cpsat": solve_cpsat(inputs, weights)[0],
    }


# ==================================================
# 2. 채점 (실제 승객 사용)
# ==================================================

def actual_peaks(advisor: BusAdvisor, inputs: dict) -> dict:
    """{(노선, 시간): 현행 운행횟수일 때 실제 승객 기준 최대 혼잡도}. 실제값이 없으면 넣지 않습니다."""
    peaks = {}
    for route, r in inputs["routes"].items():
        for hour in r["cells"]:
            actual = advisor.hour_state(route, inputs["date"], hour)["segments"]["congestion_actual"].max()
            if pd.notna(actual):
                peaks[route, hour] = float(actual)
    return peaks


def score_cells(inputs: dict, plan: dict, peaks: dict) -> list[dict]:
    """노선·시간별 채점 행. 운행횟수를 바꾸면 혼잡도는 (현행 ÷ 바꾼 운행횟수)배가 됩니다."""
    rows = []
    for (route, hour), actual in peaks.items():
        current = inputs["routes"][route]["cells"][hour]["current"]
        trips = plan[route, hour]
        rows.append({
            "date": inputs["date"], "route": route, "hour": hour,
            "current": current, "trips": trips,
            "congestion_now": actual, "congestion": actual * current / trips,
        })
    return rows


def fleet_check(inputs: dict, plan: dict) -> list[dict]:
    """노선별 하루 중 도로 위 버스가 가장 많은 순간과 인가대수 초과 여부"""
    rows = []
    for route, r in inputs["routes"].items():
        hours = sorted(r["cells"])
        if not hours:
            continue
        trips = [float(t) for t in r["current"]]
        for hour in hours:
            trips[hour] = plan[route, hour]
        limits = _fleet_limits(r, hours)          # 현행이 이미 넘는 순간은 현행 값까지 허용한 한도
        on_road = {m: _on_road(trips, r["run_minutes"], m) for m in _checkpoints(r["run_minutes"], hours)}
        rows.append({
            "date": inputs["date"], "route": route, "fleet": r["fleet"],
            "max_on_road": max(on_road.values()),
            "over": any(v > r["fleet"] + EPS for v in on_road.values()),
            "over_new": any(v > limits[m] + EPS for m, v in on_road.items()),
        })
    return rows


def summarize(cells: pd.DataFrame, fleets: pd.DataFrame) -> dict:
    """채점 행을 지표로 모음"""
    if cells.empty:
        return {"cells": 0}
    total_now, total = float(cells["current"].sum()), float(cells["trips"].sum())
    crowded = int((cells["congestion"] >= CONGESTION_HIGH).sum())
    return {
        "cells": int(len(cells)),
        "route_days": int(len(fleets)),
        "overcrowded_cells": crowded,
        "overcrowded_pct": round(crowded / len(cells) * 100, 2),
        "total_trips": round(total, 1),
        "trips_vs_current_pct": round((total / total_now - 1) * 100, 2),
        "changed_cells": int(((cells["trips"] - cells["current"]).abs() > EPS).sum()),
        "fleet_over_days": int(fleets["over"].sum()),
        "fleet_over_new_days": int(fleets["over_new"].sum()),
    }


# ==================================================
# 결과 파일 읽기 (대시보드 GET /api/bus/backtest)
# ==================================================
# scripts/backtest.py가 만든 eval/results/backtest.json을 화면용 표로 정리합니다. 다시 계산하지 않습니다.

METHODS = tuple(METHOD_LABELS.values())       # 표에 보일 순서 (backtest.json의 eval 키)
METRIC_KEYS = ("cells", "route_days", "overcrowded_cells", "overcrowded_pct", "total_trips",
               "trips_vs_current_pct", "changed_cells", "fleet_over_new_days")
CAUTION = ("평가 구간은 설정을 고른 뒤에도 여러 번 채점했습니다(감차 허용 행 포함). "
           "새 데이터로 다시 채점하기 전까지는 참고 수치로 봐 주세요.")


def backtest_summary(path: Path = BACKTEST_RESULT_PATH) -> dict:
    """FileNotFoundError: 결과 파일 없음 / ValueError: 파일 형식이 다름"""
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        evals = raw["eval"]
        rows = [{"name": name, "label": name, "weights": raw.get("weights_chosen") if name == "최적화" else None,
                 "reference": False, **{k: evals[name][k] for k in METRIC_KEYS}}
                for name in METHODS if name in evals]
        ref = raw.get("eval_reference")
        if ref:
            rows.append({"name": "감차 허용", "label": ref.get("label", "감차 허용"), "weights": ref.get("weights"),
                         "reference": True, "why": ref.get("why"), **{k: ref["metrics"][k] for k in METRIC_KEYS}})
        return {
            "eval_window": raw["eval_window"], "tune_window": raw["tune_window"],
            "crowded_threshold": raw["crowded_threshold"], "methods": rows,
            "pick_order": raw.get("pick_order", []), "notes": raw.get("notes", []), "caution": CAUTION,
        }
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"성능 비교 결과 파일의 형식이 예상과 다릅니다: {e}") from e
