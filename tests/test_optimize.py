# 전 노선 배차 최적화 테스트 (F-13)
import pandas as pd
import pytest

from app.config import BUS_CAPACITY, BUS_OPT_WEIGHTS, CONGESTION_TARGET
from app.domains.bus.optimizer import (
    LIMIT_FLEET, LIMIT_INTERVAL, _checkpoints, _on_road, _overload, build_result, solve_cpsat, solve_greedy,
)

PER_BUS = CONGESTION_TARGET * BUS_CAPACITY      # 버스 1대가 목표 혼잡도로 태우는 승객 수


def _route(current: dict[int, int], onboard: dict[int, float], fleet: int, run_minutes: int = 120,
           low: int = 4, high: int = 15) -> dict:
    """시간별 현행 운행횟수와, 최적화 대상 시간대의 최대 혼잡 구간 재차인원으로 노선 하나를 만듦"""
    trips = [current.get(h, 0) for h in range(24)]
    cells = {h: {"current": trips[h], "low": min(low, trips[h]), "high": max(high, trips[h]),
                 "onboard": float(load), "ratio": 1.0} for h, load in onboard.items()}
    return {"current": trips, "cells": cells, "fleet": fleet, "fleet_estimated": False, "run_minutes": run_minutes}


def _inputs(**routes) -> dict:
    return {"routes": routes, "skipped": [], "daytype": "weekday", "is_forecast": False,
            "date": pd.Timestamp("2026-10-20")}


def _cost(inputs: dict, plan: dict, weights: dict) -> float:
    total = 0.0
    for (route, hour), trips in plan.items():
        cell = inputs["routes"][route]["cells"][hour]
        total += (weights["overload"] * _overload(cell, trips) + weights["trips"] * trips
                  + weights["change"] * abs(trips - cell["current"]))
    return total


def _max_on_road(route: dict, plan: dict, name: str) -> float:
    trips = route["current"].copy()
    for hour in route["cells"]:
        trips[hour] = plan[name, hour]
    return max(_on_road(trips, route["run_minutes"], m) for m in _checkpoints(route["run_minutes"], sorted(route["cells"])))


def test_on_road_equals_trips_times_run_time_when_steady():
    """운행횟수가 계속 같으면 도로 위 버스 = 운행횟수 × 운행시간 ÷ 60"""
    trips = [6] * 24
    assert _on_road(trips, run_minutes=150, at_minute=12 * 60) == pytest.approx(6 * 150 / 60)


@pytest.mark.parametrize("solver", [solve_cpsat, solve_greedy])
def test_adds_trips_until_overload_is_gone(solver):
    # 준비: 8시에 6회 운행, 최대 혼잡 구간에 10대 분량의 승객. 버스는 넉넉함
    inputs = _inputs(r1=_route({7: 6, 8: 6, 9: 6}, {8: PER_BUS * 10}, fleet=100))

    # 실행
    plan, _ = solver(inputs, BUS_OPT_WEIGHTS)

    # 확인: 딱 필요한 만큼(10회)만 늘림
    assert plan["r1", 8] == 10
    assert _overload(inputs["routes"]["r1"]["cells"][8], plan["r1", 8]) == 0


@pytest.mark.parametrize("solver", [solve_cpsat, solve_greedy])
def test_never_exceeds_fleet(solver):
    # 준비: 운행시간 2시간, 버스 20대. 7~9시 모두 승객이 넘쳐 12회씩 필요하지만 그러면 24대가 필요함
    current = {h: 8 for h in range(5, 12)}
    inputs = _inputs(r1=_route(current, {h: PER_BUS * 12 for h in (7, 8, 9)}, fleet=20))

    # 실행
    plan, _ = solver(inputs, BUS_OPT_WEIGHTS)

    # 확인: 늘리긴 하되 도로 위 버스는 인가대수 이하
    route = inputs["routes"]["r1"]
    assert sum(plan["r1", h] for h in (7, 8, 9)) > 24
    assert _max_on_road(route, plan, "r1") <= route["fleet"] + 1e-6


def test_respects_interval_limits_and_reports_reason():
    # 준비: 최소 배차간격 5분(시간당 12회)이 한계인데 승객은 15대 분량
    inputs = _inputs(r1=_route({8: 8}, {8: PER_BUS * 15}, fleet=100, high=12))

    # 실행
    plan, status = solve_cpsat(inputs, BUS_OPT_WEIGHTS)
    result = build_result(inputs, plan, BUS_OPT_WEIGHTS, "cpsat", status, 0.0)

    # 확인: 12회에서 멈추고, 못 늘린 이유를 알려 줌
    assert plan["r1", 8] == 12
    assert result["plan"][0]["limited_by"] == LIMIT_INTERVAL
    assert result["binding"] == [f"r1번 8시: {LIMIT_INTERVAL}"]
    assert result["plan"][0]["action"] == "increase" and result["cut_notice"] is None
    assert result["needs_review"] is True


def test_reports_fleet_limit():
    # 준비: 버스 16대, 운행시간 2시간 → 시간당 8회가 한계. 지금 8회인데 승객은 더 많음
    inputs = _inputs(r1=_route({h: 8 for h in range(5, 12)}, {8: PER_BUS * 11}, fleet=16))

    # 실행
    plan, status = solve_cpsat(inputs, BUS_OPT_WEIGHTS)
    result = build_result(inputs, plan, BUS_OPT_WEIGHTS, "cpsat", status, 0.0)

    # 확인
    assert plan["r1", 8] == 8
    assert result["plan"][0]["limited_by"] == LIMIT_FLEET
    assert result["kpi"]["extra_trips"] == 0


def test_keeps_current_when_not_crowded():
    # 준비: 승객이 적음 (현행 8회, 필요는 3대 분량)
    inputs = _inputs(r1=_route({8: 8}, {8: PER_BUS * 3}, fleet=100))

    # 실행: 바꾸는 비용을 둔 설정(증차만)과 기본 설정(감차 허용)
    keep, _ = solve_cpsat(inputs, {"overload": 10, "trips": 1, "change": 1})
    cut, status = solve_cpsat(inputs, BUS_OPT_WEIGHTS)
    result = build_result(inputs, cut, BUS_OPT_WEIGHTS, "cpsat", status, 0.0)

    # 확인: 증차만 설정은 현행 유지, 기본 설정은 배차간격 하한(시간당 4회)까지 줄이고 감차 안내를 붙임
    assert BUS_OPT_WEIGHTS["change"] == 0
    assert keep["r1", 8] == 8
    assert cut["r1", 8] == 4
    assert result["plan"][0]["action"] == "decrease" and result["kpi"]["decreased"] == 1
    assert result["cut_notice"] and result["needs_review"] is True


def test_cut_never_makes_a_cell_crowded():
    # 준비: 현행 12회, 승객은 6.4대 분량 → 7회면 목표 혼잡도 안, 6회면 넘음
    inputs = _inputs(r1=_route({8: 12}, {8: PER_BUS * 6.4}, fleet=100))

    # 실행
    plan, _ = solve_cpsat(inputs, BUS_OPT_WEIGHTS)

    # 확인: 과밀이 생기지 않는 선까지만 줄임
    assert plan["r1", 8] == 7
    assert _overload(inputs["routes"]["r1"]["cells"][8], plan["r1", 8]) == 0


def test_scarce_buses_go_to_the_more_crowded_hour():
    # 준비: 버스 17대(지금 16대 사용)라 한 회만 더 보낼 수 있고, 8시가 7시보다 훨씬 붐빔
    current = {h: 8 for h in range(5, 12)}
    inputs = _inputs(r1=_route(current, {7: PER_BUS * 8.5, 8: PER_BUS * 11}, fleet=17))

    # 실행
    plan, _ = solve_cpsat(inputs, BUS_OPT_WEIGHTS)

    # 확인
    assert plan["r1", 8] > plan["r1", 7] and plan["r1", 7] == 8


def test_cpsat_is_never_worse_than_greedy():
    # 준비: 노선 2개, 버스가 빠듯한 아침 시간대
    current = {h: 7 for h in range(5, 12)}
    inputs = _inputs(
        r1=_route(current, {6: PER_BUS * 9, 7: PER_BUS * 12, 8: PER_BUS * 10}, fleet=17),
        r2=_route(current, {6: PER_BUS * 5, 7: PER_BUS * 9, 8: PER_BUS * 13}, fleet=18, run_minutes=150),
    )

    # 실행
    best, status = solve_cpsat(inputs, BUS_OPT_WEIGHTS)
    greedy, _ = solve_greedy(inputs, BUS_OPT_WEIGHTS)

    # 확인
    assert status == "optimal"
    assert _cost(inputs, best, BUS_OPT_WEIGHTS) <= _cost(inputs, greedy, BUS_OPT_WEIGHTS) + 1e-6


# --------------------------------------------------
# API
# --------------------------------------------------

def _meta(client) -> dict:
    return client.get("/api/bus/meta").json()


def test_optimize_api(client):
    # 준비
    meta = _meta(client)
    hours = [7, 8, 9]

    # 실행
    r = client.post("/api/bus/optimize", json={"date": meta["dates"]["max_date"], "hours": hours})

    # 확인
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["needs_review"] is True and body["status"] == "optimal" and body["assumption"]
    assert {i["route"] for i in body["plan"]} == {x["route"] for x in meta["routes"]}
    assert {i["hour"] for i in body["plan"]} <= set(hours)
    assert body["kpi"]["overcrowded_after"] <= body["kpi"]["overcrowded_before"]
    assert body["kpi"]["extra_trips"] == sum(i["change"] for i in body["plan"])
    assert body["kpi"]["increased"] + body["kpi"]["decreased"] == body["kpi"]["changed"]
    assert body["weights"]["change"] == 0                   # 기본은 감차 허용
    assert (body["cut_notice"] is not None) == (body["kpi"]["decreased"] > 0)
    for item in body["plan"]:
        assert item["recommended_trips"] >= 1
        if item["change"] > 0:                      # 늘린 곳은 인가대수 안이어야 함
            assert item["buses_needed"] <= item["fleet"]
            assert item["upper_congestion_after"] < item["upper_congestion_before"]


@pytest.mark.parametrize("payload", [
    {"hours": [24]},                                  # 없는 시간대
    {"hours": []},                                    # 시간대를 고르지 않음
    {"weights": {"overload": -1}},                    # 음수 가중치
    {"date": "2031-01-01"},                           # 예측 범위 밖
])
def test_optimize_api_rejects_bad_input(client, payload):
    body = {"date": _meta(client)["dates"]["max_date"], "hours": [8], **payload}
    r = client.post("/api/bus/optimize", json=body)
    assert r.status_code == 422, r.text
