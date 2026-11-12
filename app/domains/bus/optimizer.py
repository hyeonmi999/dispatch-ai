# ==================================================
# 전 노선 배차 최적화  (F-13)
# ==================================================
# 날짜와 시간대를 고르면, 노선별 시간당 운행횟수를 가진 버스 대수 안에서 한꺼번에 정합니다.
#
# 정할 값
#   x[노선, 시간] = 시간당 운행횟수 (정수)
#
# 지킬 것
#   ① 배차간격: 60 ÷ 최대 배차간격 ≤ x ≤ 60 ÷ 최소 배차간격
#   ② 버스 대수: 어느 시각이든 "도로 위에 있는 버스" ≤ 인가대수
#      도로 위 버스 = 최근 왕복 운행시간 동안 출발한 버스 수 (출발한 버스는 한 바퀴 돌 때까지 못 씀).
#      운행횟수가 계속 x로 같으면 x × 운행시간 ÷ 60과 같은 값입니다.
#      시간대마다 따로 x × 운행시간 ÷ 60 ≤ 인가대수로 보면 실제 운행 기록의 약 15%가 위반으로 나오지만,
#      이 방식으로는 거의 나오지 않아 실제와 맞습니다 (docs/decisions.md).
#   현행 운행횟수는 항상 허용합니다. 첫차·막차 시간대처럼 현행이 ①②를 벗어나 있으면 그 값까지 범위를 넓힙니다.
#   그래서 "현행 유지"가 늘 가능한 답이고, 풀 수 없는 경우는 생기지 않습니다.
#
# 줄일 것
#   overload × 과밀량 + trips × 총 운행횟수 + change × 현행과 달라진 운행횟수
#   과밀량 = 최대 혼잡 구간의 승객 수 상한(P90)이 목표 혼잡도(0.7) × 정원 × x 를 넘는 만큼 (명)
#   기본 가중치는 change 0 (감차 허용): 붐비는 곳은 늘리고, 목표 혼잡도보다 한가한 곳은 줄입니다.
#   change를 1 이상으로 주면 늘리기만 합니다.
#
# 풀이
#   cpsat : OR-Tools CP-SAT 정수계획 (기본)
#   greedy: 과밀이 가장 큰 노선·시간부터 한 회씩 늘림 (비교용 기준선, 줄이지는 않음)
#
# 재차인원과 현행 운행횟수는 BusAdvisor.hour_state(), 승객 수 상한 비율은 calibration.ratio_for()를 그대로 씁니다.

import math
import time

import pandas as pd

from app.config import (
    BUS_CAPACITY, BUS_OPT_SEED, BUS_OPT_TIME_LIMIT_SEC, BUS_OPT_WEIGHTS, CONGESTION_HIGH, CONGESTION_TARGET,
)
from app.core.domain import InvalidRequest
from app.domains.bus.advisor import BusAdvisor, congestion_level
from app.domains.bus.calibration import ratio_for

HOURS_PER_DAY = 24
LOAD_SCALE = 100       # 승객 수를 정수로 다루려고 1/100명 단위로 바꿈
WEIGHT_SCALE = 10      # 가중치는 소수 첫째 자리까지
ASSUMPTION = "승객 수는 그대로라고 가정. 증차 판단은 승객 수 상한(10번 중 9번은 이보다 적게 탐) 기준"
CUT_NOTICE = ("운행횟수를 줄이는 권고가 포함되어 있습니다. 줄이면 배차간격이 길어져 승객 대기시간이 늘어납니다. "
              "사업계획 변경 절차 대상인지 규정을 확인하고 담당자 검토 후 진행하세요.")
LIMIT_INTERVAL = "최소 배차간격 한계"
LIMIT_FLEET = "인가대수 한계"


# ==================================================
# 1. 입력 모으기
# ==================================================

def _minutes_on_road(start_hour: int, run_minutes: int, at_minute: int) -> int:
    """start_hour 시에 고르게 출발한 버스 중 at_minute 시각에 아직 도는 버스의 출발 시간 폭(분)

    at_minute 직전 run_minutes 동안 출발한 버스가 도로 위에 있습니다.
    """
    return max(0, min(at_minute, (start_hour + 1) * 60) - max(at_minute - run_minutes, start_hour * 60))


def collect_inputs(advisor: BusAdvisor, table: dict, routes: list[str], date, hours: list[int]) -> dict:
    """노선별 현행 운행횟수(24시간)와, 최적화 대상 시간대의 승객 수 상한·범위"""
    out = {"routes": {}, "skipped": [], "daytype": None, "is_forecast": False, "date": pd.Timestamp(date).normalize()}
    for route in routes:
        current = [0] * HOURS_PER_DAY
        cells, info = {}, None
        for hour in range(HOURS_PER_DAY):
            try:
                state = advisor.hour_state(route, date, hour)
            except ValueError:
                continue                              # 운행이 없는 시간대
            current[hour] = max(1, round(state["cur_trips"]))
            info = state["info"]
            out["daytype"], out["is_forecast"] = state["day"]["daytype"], state["day"]["is_future"]
            if hour not in hours:
                continue
            top = state["top"]
            ratio = ratio_for(table, route, hour)
            low = math.ceil(60.0 / state["max_interval"] - 1e-9)
            high = math.floor(60.0 / state["min_interval"] + 1e-9)
            cells[hour] = {
                "current": current[hour],
                "low": min(low, current[hour]), "high": max(high, current[hour]),
                # 현행 혼잡도가 그대로 나오도록, 최대 혼잡 구간의 재차인원(명/시간)을 현행 운행횟수 기준으로 환산
                "onboard": float(top["congestion"]) * BUS_CAPACITY * current[hour],
                "ratio": ratio,
            }
        if info is None:
            continue
        for hour in hours:
            if hour not in cells:
                out["skipped"].append({"route": route, "hour": hour, "reason": "운행 데이터가 없는 시간대"})
        out["routes"][route] = {
            "current": current, "cells": cells,
            "fleet": int(info.get("buses") or 0),
            "fleet_estimated": bool(info.get("buses_estimated", False)),
            "run_minutes": int(round(float(info.get("run_minutes") or 0))),
        }
    return out


def _checkpoints(run_minutes: int, hours: list[int]) -> list[int]:
    """버스 대수를 확인할 시각(분). 도로 위 버스 수는 정시와 '정시 + 운행시간'에서만 꺾이므로 그 시각만 보면 됩니다."""
    first, last = min(hours) * 60, (max(hours) + 1) * 60 + run_minutes
    points = {m for m in range(0, last + 60, 60)} | {h * 60 + run_minutes for h in range(HOURS_PER_DAY)}
    return sorted(m for m in points if first < m <= last)


def _on_road(trips: list[int], run_minutes: int, at_minute: int) -> float:
    """at_minute 시각에 도로 위에 있는 버스 수"""
    return sum(trips[h] * _minutes_on_road(h, run_minutes, at_minute) for h in range(HOURS_PER_DAY)) / 60.0


def _fleet_limits(route: dict, hours: list[int]) -> dict[int, float]:
    """확인 시각별 버스 대수 한도. 현행이 이미 인가대수를 넘는 시각은 현행 값을 한도로 둡니다."""
    return {m: max(float(route["fleet"]), _on_road(route["current"], route["run_minutes"], m))
            for m in _checkpoints(route["run_minutes"], hours)}


def _overload(cell: dict, trips: int) -> float:
    """목표 혼잡도를 넘는 승객 수 (명/시간, 상한 기준)"""
    return max(0.0, cell["onboard"] * cell["ratio"] - CONGESTION_TARGET * BUS_CAPACITY * trips)


# ==================================================
# 2. 풀이
# ==================================================

def solve_cpsat(inputs: dict, weights: dict) -> tuple[dict, str]:
    """정수계획. 반환: ({(노선, 시간): 운행횟수}, 상태)"""
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    w = {k: int(round(float(weights[k]) * WEIGHT_SCALE)) for k in ("overload", "trips", "change")}
    per_bus = int(round(CONGESTION_TARGET * BUS_CAPACITY * LOAD_SCALE))
    x, cost = {}, []

    for route, r in inputs["routes"].items():
        hours = sorted(r["cells"])
        if not hours:
            continue
        for hour in hours:
            cell = r["cells"][hour]
            var = model.NewIntVar(cell["low"], cell["high"], f"x_{route}_{hour}")
            model.AddHint(var, cell["current"])
            x[route, hour] = var

            load = int(round(cell["onboard"] * cell["ratio"] * LOAD_SCALE))
            over = model.NewIntVar(0, max(load, 0), f"over_{route}_{hour}")
            model.Add(over >= load - per_bus * var)

            change = model.NewIntVar(0, cell["high"] - cell["low"], f"change_{route}_{hour}")
            model.Add(change >= var - cell["current"])
            model.Add(change >= cell["current"] - var)

            # 비용이 같으면 현행 유지 쪽을 고르도록 change에 아주 작은 값을 더함
            cost += [w["overload"] * over, w["trips"] * LOAD_SCALE * var, (w["change"] * LOAD_SCALE + 1) * change]

        for minute, limit in _fleet_limits(r, hours).items():
            fixed = sum(r["current"][h] * _minutes_on_road(h, r["run_minutes"], minute)
                        for h in range(HOURS_PER_DAY) if h not in r["cells"])
            terms = [(_minutes_on_road(h, r["run_minutes"], minute), x[route, h]) for h in hours]
            terms = [(coef, var) for coef, var in terms if coef > 0]
            if terms:
                model.Add(sum(coef * var for coef, var in terms) <= math.floor(limit * 60 + 1e-6) - fixed)

    if not x:
        return {}, "empty"
    model.Minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = BUS_OPT_TIME_LIMIT_SEC
    solver.parameters.random_seed = BUS_OPT_SEED
    solver.parameters.num_workers = 1                 # 같은 입력이면 같은 답
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"배차 최적화 풀이 실패: {solver.StatusName(status)}")
    plan = {key: int(solver.Value(var)) for key, var in x.items()}
    return plan, "optimal" if status == cp_model.OPTIMAL else "time_limit"


def _can_add(route: dict, trips: list[int], hour: int, limits: dict[int, float]) -> str | None:
    """hour 시에 한 회 더 보낼 수 없으면 그 이유"""
    if trips[hour] + 1 > route["cells"][hour]["high"]:
        return LIMIT_INTERVAL
    trial = trips.copy()
    trial[hour] += 1
    if any(_on_road(trial, route["run_minutes"], m) > limit + 1e-6 for m, limit in limits.items()):
        return LIMIT_FLEET
    return None


def solve_greedy(inputs: dict, weights: dict | None = None) -> tuple[dict, str]:
    """기준선: 과밀량이 가장 큰 노선·시간부터 한 회씩 늘림 (가중치는 쓰지 않음)"""
    trips = {route: r["current"].copy() for route, r in inputs["routes"].items()}
    limits = {route: _fleet_limits(r, sorted(r["cells"])) for route, r in inputs["routes"].items() if r["cells"]}
    blocked: set[tuple[str, int]] = set()
    while True:
        open_cells = [(_overload(r["cells"][h], trips[route][h]), route, h)
                      for route, r in inputs["routes"].items() for h in r["cells"] if (route, h) not in blocked]
        open_cells = [c for c in open_cells if c[0] > 0]
        if not open_cells:
            break
        _, route, hour = max(open_cells)
        if _can_add(inputs["routes"][route], trips[route], hour, limits[route]):
            blocked.add((route, hour))
        else:
            trips[route][hour] += 1
    return {(route, h): trips[route][h] for route, r in inputs["routes"].items() for h in r["cells"]}, "greedy"


SOLVERS = {"cpsat": solve_cpsat, "greedy": solve_greedy}


# ==================================================
# 3. 결과 정리
# ==================================================

def _congestion(cell: dict, trips: int, upper: bool) -> float:
    load = cell["onboard"] * (cell["ratio"] if upper else 1.0)
    return load / (BUS_CAPACITY * trips)


def build_result(inputs: dict, plan: dict, weights: dict, method: str, status: str, seconds: float) -> dict:
    items, binding = [], []
    for route, r in inputs["routes"].items():
        hours = sorted(r["cells"])
        if not hours:
            continue
        after = r["current"].copy()
        for hour in hours:
            after[hour] = plan[route, hour]
        limits = _fleet_limits(r, hours)

        for hour in hours:
            cell, cur, rec = r["cells"][hour], r["current"][hour], plan[route, hour]
            # 이 시간대(정시 ~ 다음 정시)에 도로 위 버스가 가장 많은 순간
            in_hour = [m for m in limits if hour * 60 < m <= (hour + 1) * 60]
            buses = max((_on_road(after, r["run_minutes"], m) for m in in_hour), default=0.0)
            limited_by = _can_add(r, after, hour, limits) if _overload(cell, rec) > 0 else None
            if limited_by:
                binding.append(f"{route}번 {hour}시: {limited_by}")
            upper_after = _congestion(cell, rec, upper=True)
            items.append({
                "route": route, "hour": hour,
                "current_trips": cur, "recommended_trips": rec, "change": rec - cur,
                "action": "increase" if rec > cur else "decrease" if rec < cur else "keep",
                "interval": round(60.0 / rec, 1),
                "buses_needed": math.ceil(buses - 1e-6), "fleet": r["fleet"], "fleet_estimated": r["fleet_estimated"],
                "max_congestion_before": round(_congestion(cell, cur, upper=False), 3),
                "max_congestion_after": round(_congestion(cell, rec, upper=False), 3),
                "upper_congestion_before": round(_congestion(cell, cur, upper=True), 3),
                "upper_congestion_after": round(upper_after, 3),
                "level_after": congestion_level(upper_after),
                "limited_by": limited_by,
            })

    def crowded(key: str) -> int:
        return sum(1 for item in items if item[key] >= CONGESTION_HIGH)

    return {
        "date": f"{inputs['date']:%Y-%m-%d}",
        "daytype": inputs["daytype"],
        "is_forecast": inputs["is_forecast"],
        "method": method,
        "status": status,
        "solve_seconds": round(seconds, 3),
        "weights": {k: float(weights[k]) for k in ("overload", "trips", "change")},
        "plan": items,
        "kpi": {
            "overcrowded_before": crowded("upper_congestion_before"),
            "overcrowded_after": crowded("upper_congestion_after"),
            "extra_trips": sum(item["change"] for item in items),
            "changed": sum(1 for item in items if item["change"] != 0),
            "increased": sum(1 for item in items if item["change"] > 0),
            "decreased": sum(1 for item in items if item["change"] < 0),
            "total_trips_before": sum(item["current_trips"] for item in items),
            "total_trips_after": sum(item["recommended_trips"] for item in items),
        },
        "binding": binding,
        "skipped": inputs["skipped"],
        "assumption": ASSUMPTION,
        "cut_notice": CUT_NOTICE if any(item["change"] < 0 for item in items) else None,
        "needs_review": True,
    }


def optimize(advisor: BusAdvisor, table: dict, routes: list[str], date, hours: list[int],
             weights: dict | None = None, method: str = "cpsat") -> dict:
    """선택한 날짜·시간대의 전 노선 운행횟수 권고"""
    if method not in SOLVERS:
        raise InvalidRequest(f"지원하지 않는 풀이 방식입니다: {method} (가능: {', '.join(SOLVERS)})")
    weights = {**BUS_OPT_WEIGHTS, **(weights or {})}
    hours = sorted(set(hours))
    inputs = collect_inputs(advisor, table, routes, date, hours)
    if not any(r["cells"] for r in inputs["routes"].values()):
        raise ValueError(f"{pd.Timestamp(date):%Y-%m-%d} 선택한 시간대에 운행 데이터가 있는 노선이 없습니다")
    started = time.perf_counter()
    plan, status = SOLVERS[method](inputs, weights)
    return build_result(inputs, plan, weights, method, status, time.perf_counter() - started)
