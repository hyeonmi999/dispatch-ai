# 배차간격 변경 시뮬레이션 테스트 (F-12)
import pytest

HOUR = 8


def _route(client) -> dict:
    """합성 데이터의 첫 노선과 실측 마지막 날"""
    meta = client.get("/api/bus/meta").json()
    return {**meta["routes"][0], "date": meta["dates"]["max_date"]}


def _simulate(client, info: dict, interval: float):
    return client.post("/api/bus/simulate", json={
        "route": info["route"], "date": info["date"], "hour": HOUR, "interval": interval})


def test_shorter_interval_lowers_congestion(client):
    # 준비
    info = _route(client)

    # 실행: 노선이 허용하는 가장 짧은 간격과 가장 긴 간격
    short = _simulate(client, info, info["min_interval"])
    long = _simulate(client, info, info["max_interval"])

    # 확인: 자주 보낼수록 덜 붐비고, 모든 결과는 검토 대상
    assert short.status_code == 200 and long.status_code == 200, short.text
    short, long = short.json(), long.json()
    assert short["simulated"]["max_congestion"] < long["simulated"]["max_congestion"]
    assert short["simulated"]["trips_per_hour"] == pytest.approx(60 / info["min_interval"], abs=0.01)
    assert short["segments"] and short["needs_review"] is True and short["assumption"]
    assert all(s["level"] and s["level_before"] for s in short["segments"])
    assert max(s["congestion"] for s in short["segments"]) == pytest.approx(
        short["simulated"]["max_congestion"], abs=0.002)


def test_same_interval_as_now_changes_nothing(client):
    # 준비: 지금 간격을 먼저 읽음
    info = _route(client)
    current = _simulate(client, info, info["min_interval"]).json()["current"]
    low, high = info["min_interval"], info["max_interval"]
    if not low <= current["interval"] <= high:
        pytest.skip("지금 간격이 노선 허용 범위 밖이라 같은 값으로 시뮬레이션할 수 없음")

    # 실행
    body = _simulate(client, info, current["interval"]).json()

    # 확인: 전후가 같음 (간격은 소수 둘째 자리로 반올림해 주고받으므로 1% 이내)
    assert body["simulated"]["max_congestion"] == pytest.approx(current["max_congestion"], rel=0.01)
    assert all(s["congestion"] == pytest.approx(s["congestion_before"], rel=0.01, abs=0.002) for s in body["segments"])


def test_congestion_scales_with_interval(client):
    # 준비
    info = _route(client)
    interval = (info["min_interval"] + info["max_interval"]) / 2

    # 실행
    body = _simulate(client, info, interval).json()

    # 확인: 바꾼 뒤 최대 혼잡도 = 지금 × (바꾼 간격 ÷ 지금 간격)
    expected = body["current"]["max_congestion"] * interval / body["current"]["interval"]
    assert body["simulated"]["max_congestion"] == pytest.approx(expected, rel=0.01)


@pytest.mark.parametrize("offset", [-1.0, 1.0])
def test_interval_outside_route_limits_is_rejected(client, offset):
    # 준비: 최소보다 1분 짧게 / 최대보다 1분 길게
    info = _route(client)
    interval = info["min_interval"] + offset if offset < 0 else info["max_interval"] + offset

    # 실행
    r = _simulate(client, info, interval)

    # 확인: 400 + 공통 에러 형식
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["status"] == "invalid_request" and "범위" in body["detail"]


def test_unknown_route_and_bad_input(client):
    info = _route(client)

    # 없는 노선 → 404
    r = client.post("/api/bus/simulate", json={"route": "0000", "date": info["date"], "hour": HOUR, "interval": 6})
    assert r.status_code == 404

    # 간격이 0 이하이거나 시간이 범위 밖 → 422 (입력 검증)
    for bad in ({"interval": 0}, {"hour": 24}):
        r = client.post("/api/bus/simulate", json={
            "route": info["route"], "date": info["date"], "hour": HOUR, "interval": 6, **bad})
        assert r.status_code == 422


def test_fleet_need_grows_with_shorter_interval(client):
    """간격을 줄이면 필요한 버스가 늘고, 인가대수를 넘는지 알려 줌"""
    # 준비
    info = _route(client)

    # 실행
    short = _simulate(client, info, info["min_interval"]).json()["fleet"]
    long = _simulate(client, info, info["max_interval"]).json()["fleet"]

    # 확인: 지금 값은 같고, 자주 보낼수록 더 많이 필요하며, over는 인가대수와의 비교 그대로
    assert short["buses_needed_before"] == long["buses_needed_before"] > 0
    assert short["buses_needed"] >= long["buses_needed"]
    assert short["over"] == (short["authorized_buses"] is not None and short["buses_needed"] > short["authorized_buses"])


def test_fleet_need_counts_departures_in_last_run_time():
    """왕복 120분, 매시간 10회 출발 → 도로 위 20대. 8시만 20회로 늘리면 30대"""
    import pandas as pd
    from app.domains.bus.simulator import fleet_need
    hourly = pd.DataFrame({"hour": range(24), "trips": [10.0] * 24})
    state = {"info": {"run_minutes": 120, "buses": 25}, "day": {"hourly": hourly}}

    same = fleet_need(state, 8, 10.0)
    more = fleet_need(state, 8, 20.0)

    assert same["buses_needed_before"] == same["buses_needed"] == 20 and same["over"] is False
    assert more["buses_needed"] == 30 and more["over"] is True and more["authorized_buses"] == 25
