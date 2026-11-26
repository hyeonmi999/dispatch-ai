# 버스 길안내 (F-28): 직행·환승 경로, 현재 위치, 챗봇 연결
import datetime as dt

import pytest

from app.core import agent
from app.core.agent import classify, extract_places
from app.domains.bus.guide import distance_m


def meta(client):
    m = client.get("/api/bus/meta").json()
    return [r["route"] for r in m["routes"]], m["dates"]["max_date"]


def stops_of(client, route):
    return client.get(f"/api/bus/routes/{route}/geometry").json()["stops"]


# ==================================================
# 1. API: /api/bus/guide
# ==================================================

def test_guide_direct_route(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    body = client.post("/api/bus/guide", json={
        "origin": stops[2]["name"], "destination": stops[10]["name"], "date": date, "hour": 8}).json()
    best = body["options"][0]
    assert best["kind"] == "direct" and best["routes"] == [routes[0]]
    assert best["legs"][0]["stops"] == 8
    assert best["legs"][0]["interval_min"] > 0 and best["total_min"] > 0
    assert best["fastest"] is True
    assert body["recommendation"]["index"] == [o["recommended"] for o in body["options"]].index(True)


def test_guide_from_current_location_picks_near_stop(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    here, dest = stops[3], stops[12]
    body = client.post("/api/bus/guide", json={
        "lat": here["lat"], "lng": here["lng"], "destination": dest["name"], "date": date, "hour": 8}).json()
    assert body["origin"]["far"] is False and body["origin"]["label"] == "현재 위치"
    assert body["options"] and body["options"][0]["legs"][0]["walk_m"] <= 500


def test_guide_far_location_is_flagged_and_walk_not_counted(client):
    routes, date = meta(client)
    dest = stops_of(client, routes[0])[12]
    body = client.post("/api/bus/guide", json={
        "lat": 35.1, "lng": 129.0, "destination": dest["name"], "date": date, "hour": 8}).json()   # 부산
    assert body["origin"]["far"] is True and body["origin"]["walk_m"] > 100_000
    assert all(leg["walk_min"] == 0 for o in body["options"] for leg in o["legs"][:1])


def test_guide_transfer_between_routes(client):
    routes, date = meta(client)
    a, b = stops_of(client, routes[0]), stops_of(client, routes[1])
    # 두 노선이 걸어서 갈아탈 만큼 가까운 정류장이 있을 때만 확인 (합성 좌표에 따라 없을 수 있음)
    near = [(x, y) for x in a for y in b if distance_m(x["lat"], x["lng"], y["lat"], y["lng"]) <= 150]
    pair = next(((x, y) for x, y in near if x["seq"] > 1 and y["seq"] < b[-1]["seq"]), None)
    if pair is None:
        pytest.skip("합성 노선끼리 갈아탈 정류장이 없음")
    x, y = pair
    body = client.post("/api/bus/guide", json={
        "origin": a[0]["name"], "destination": b[-1]["name"], "date": date, "hour": 8}).json()
    transfer = next(o for o in body["options"] if o["kind"] == "transfer")
    assert transfer["routes"] == [routes[0], routes[1]] and len(transfer["legs"]) == 2


def test_guide_unknown_stop_and_missing_origin(client):
    routes, date = meta(client)
    dest = stops_of(client, routes[0])[5]["name"]
    r = client.post("/api/bus/guide", json={"origin": "없는정류장이름", "destination": dest, "date": date, "hour": 8})
    assert r.status_code == 422 and "찾을 수 없습니다" in str(r.json())
    r = client.post("/api/bus/guide", json={"destination": dest, "date": date, "hour": 8})
    assert r.status_code == 422


def test_stop_search_and_limit(client):
    routes, _ = meta(client)
    name = stops_of(client, routes[0])[5]["name"]
    found = client.get("/api/bus/stops/search", params={"q": name}).json()
    assert found[0]["name"] == name and routes[0] in found[0]["routes"]
    assert client.get("/api/bus/stops/search", params={"q": "zzz없는이름"}).json() == []
    assert client.get("/api/bus/stops/search", params={"q": name, "limit": 999}).status_code == 422


# ==================================================
# 2. 챗봇 연결
# ==================================================

def test_chat_guide_with_location(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    here, dest = stops[3], stops[12]["name"].replace(" ", "")
    r = client.post("/api/bus/chat", json={
        "message": f"여기서 {dest} 어떻게 가? {date} 8시", "location": {"lat": here["lat"], "lng": here["lng"]}})
    body = r.json()
    assert r.status_code == 200 and body["intent"] == "guide", body
    assert body["tools"][0]["name"] == "guide" and body["tools"][0]["ok"] is True
    assert body["numbers_verified"] is True
    assert body["data"]["options"] and routes[0] + "번" in body["answer"]


def test_chat_guide_from_here_without_location_flags_it(client):
    # 재현: "여기서 ○○ 어떻게 가?"인데 위치가 꺼져 있으면 되묻기만 했음 → 화면이 위치를 얻어 다시 묻도록 표시
    routes, _ = meta(client)
    dest = stops_of(client, routes[0])[12]["name"].replace(" ", "")
    body = client.post("/api/bus/chat", json={"message": f"여기서 {dest} 어떻게가"}).json()
    assert body["intent"] == "clarify" and body["slots"].get("from_here") is True and body["tools"] == []


def test_chat_guide_asks_for_origin_without_location(client):
    routes, _ = meta(client)
    dest = stops_of(client, routes[0])[12]["name"].replace(" ", "")
    body = client.post("/api/bus/chat", json={"message": f"{dest} 어떻게 가?"}).json()
    assert body["intent"] == "clarify" and body["tools"] == [] and "출발" in body["answer"]


def test_guide_keywords_and_places():
    assert classify("정릉우체국앞에서 종로3가역 어떻게 가?", False) == "guide"
    assert classify("환승 할인 요금 규정이 어떻게 돼?", False) == "ask"        # 요금·규정은 길안내가 아님
    assert classify("종로3가역 가는 법 알려줘", False) == "guide"               # '가는 법'의 법은 규정이 아님
    assert extract_places("정릉우체국앞에서 종로3가역 어떻게 가?") == {"origin": "정릉우체국앞", "destination": "종로3가역"}
    assert extract_places("여기서 신동중학교까지 가는 법") == {"from_here": True, "destination": "신동중학교까지"}
    assert extract_places("내일 8시 종로3가역으로 가려면?") == {"destination": "종로3가역으로"}
    assert extract_places("어떻게 가?") == {}


# ==================================================
# 3. LLM 문장: 숫자가 달라지면 버림
# ==================================================

class FakeAdapter:
    """예측 결과만 돌려주는 가짜 어댑터 (LLM 문장 검증 확인용)"""
    def targets(self):
        return ["160"]

    def route_names(self):
        return ["160"]

    def predict(self, payload):
        return {"route": "160", "date": "2026-11-17", "is_forecast": True, "hour": 15,
                "summary": {"pred_total": 20000.0, "baseline_total": 19000.0, "actual_total": None},
                "hourly": [{"hour": 15, "pred": 1234.0, "baseline": 1200.0, "actual": None, "trips": 6.0}],
                "congestion": {"ratio": 0.618, "level": "보통"}}


NOW = dt.datetime(2026, 11, 16, 9, 0)


def test_llm_rewrite_is_used_when_numbers_match(monkeypatch):
    monkeypatch.setattr(agent.llm_module, "generate", lambda *a, **k: "160번은 2026-11-17 15시에 1,234명이 탈 것으로 보여요.")
    out = agent.run(FakeAdapter(), "160번 내일 15시 몇 명 타?", use_llm=True, now=NOW)
    assert out["llm"] == "qwen" and out["llm_rejected"] is False and out["numbers_verified"] is True
    assert "보여요" in out["answer"]


def test_llm_rewrite_with_invented_number_is_rejected(monkeypatch):
    monkeypatch.setattr(agent.llm_module, "generate", lambda *a, **k: "160번은 15시에 약 1,500명이 탈 것으로 보여요.")
    out = agent.run(FakeAdapter(), "160번 내일 15시 몇 명 타?", use_llm=True, now=NOW)
    assert out["llm"] is None and out["llm_rejected"] is True
    assert "1,234명" in out["answer"] and "1,500" not in out["answer"]      # 문장 틀 답변으로 대체
    assert out["numbers_verified"] is True


def test_llm_failure_falls_back_to_template(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("모델 로드 실패")
    monkeypatch.setattr(agent.llm_module, "generate", boom)
    out = agent.run(FakeAdapter(), "160번 내일 15시 몇 명 타?", use_llm=True, now=NOW)
    assert "1,234명" in out["answer"] and out["llm"] is None and out["llm_rejected"] is True


# ==================================================
# 4. 예측 범위 밖 날짜 · 노선 정보
# ==================================================

def test_guide_date_out_of_range_falls_back_to_same_weekday(client):
    routes, _ = meta(client)
    latest = dt.date.fromisoformat(client.get("/api/bus/meta").json()["dates"]["max_forecast_date"])
    asked = latest + dt.timedelta(days=3)
    stops = stops_of(client, routes[0])
    r = client.post("/api/bus/guide", json={
        "origin": stops[2]["name"], "destination": stops[10]["name"], "date": asked.isoformat(), "hour": 8})
    body = r.json()
    assert r.status_code == 200, r.text
    used = dt.date.fromisoformat(body["date"])
    assert body["requested_date"] == asked.isoformat()
    assert used <= latest and (asked - used).days % 7 == 0 and body["options"]


def test_route_info_api(client):
    routes, _ = meta(client)
    body = client.get(f"/api/bus/routes/{routes[0]}/info").json()
    assert body["route"] == routes[0] and body["stops_count"] > 0
    assert body["start"] and body["end"] and body["key_stops"]
    assert client.get("/api/bus/routes/9999/info").status_code == 404      # 어느 파일에도 없는 노선


def test_chat_route_info(client):
    routes, _ = meta(client)
    for message in (f"{routes[0]}번 버스 노선알려줘", f"{routes[0]}번 첫차 몇 시야?", f"{routes[0]}번 어디서 어디까지 가?"):
        body = client.post("/api/bus/chat", json={"message": message}).json()
        assert body["intent"] == "route_info", message
        assert body["tools"] == [{"name": "route_info", "args": {"route": routes[0]}, "ok": True}]
        assert body["numbers_verified"] is True and "첫차" in body["answer"]


def test_route_info_does_not_steal_other_intents():
    assert classify("143번 노선 변경 절차 알려줘", True) == "ask"            # 절차는 규정
    assert classify("160번 10월 21일 정류장별 승객 알려줘", True) == "predict"
    assert classify("160번 버스 노선알려줘", True) == "route_info"


# ==================================================
# 5. 출발지 우선순위 (이어 묻기)
# ==================================================

def test_chat_guide_here_overrides_origin_from_history(client):
    # 재현: 앞에서 "A에서 B 어떻게 가?"를 물은 뒤 "지금 내위치에서 C 어떻게 가냐고" 해도 A에서 출발하는 경로를 답했음
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    first = f"{stops[2]['name'].replace(' ', '')}에서 {stops[10]['name'].replace(' ', '')} 어떻게 가? {date} 8시"
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": "..."}]
    dest = stops[20]["name"].replace(" ", "")
    here = {"lat": stops[14]["lat"], "lng": stops[14]["lng"]}

    # 위치가 있으면 그 위치에서 출발
    body = client.post("/api/bus/chat", json={"message": f"아니 지금 내위치에서 {dest} 어떻게 가냐고", "history": history, "location": here}).json()
    assert body["intent"] == "guide" and "origin" not in body["slots"] and body["slots"]["lat"] == here["lat"]

    # 위치가 없으면 예전 출발지를 쓰지 않고 위치를 요청
    body = client.post("/api/bus/chat", json={"message": f"아니 지금 내위치에서 {dest} 어떻게 가냐고", "history": history}).json()
    assert body["intent"] == "clarify" and body["slots"].get("from_here") is True and body["tools"] == []

    # 출발지를 말하지 않았고 현재 위치가 켜져 있으면 현재 위치가 우선
    body = client.post("/api/bus/chat", json={"message": f"{dest} 어떻게가", "history": history, "location": here}).json()
    assert "origin" not in body["slots"] and body["slots"]["lat"] == here["lat"]

    # 출발지도 위치도 없으면 앞에서 말한 출발지를 쓰고, 그 사실을 답변에 적음
    body = client.post("/api/bus/chat", json={"message": f"{dest} 어떻게가", "history": history}).json()
    assert body["slots"].get("origin_from_history") is True and "앞에서 말씀하신" in body["answer"]


# ==================================================
# 6. 전체 노선 (혼잡도를 예측하지 않는 노선 포함)
# ==================================================

def test_guide_uses_routes_without_prediction(client):
    """예측하는 노선 A의 정류장에서 예측하지 않는 노선 X로 갈아타 X만 가는 정류장에 도착"""
    import pandas as pd
    from app.core.registry import registry
    from app.domains.bus.guide import BusGuide

    adapter = registry.get("bus")
    routes, date = meta(client)
    a = stops_of(client, routes[0])
    rows = [{"route": r, "seq": s["seq"], "station_id": s["station_id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"],
             "direction": s.get("direction")} for r in routes for s in stops_of(client, r)]
    # 가상의 노선 X9: A의 10번째 정류장(같은 정류장ID)을 지나 A가 가지 않는 "가상시장"으로 감
    hub = a[10]
    rows += [
        {"route": "X9", "seq": 1, "station_id": "X-1", "name": "가상차고지", "lat": hub["lat"] + 0.02, "lng": hub["lng"], "direction": ""},
        {"route": "X9", "seq": 2, "station_id": hub["station_id"], "name": hub["name"], "lat": hub["lat"], "lng": hub["lng"], "direction": ""},
        {"route": "X9", "seq": 3, "station_id": "X-3", "name": "가상시장.가상구청", "lat": hub["lat"] - 0.02, "lng": hub["lng"], "direction": ""},
    ]
    info = pd.DataFrame([{"route": "X9", "route_type": "지선", "company": "", "start": "가상차고지", "end": "가상시장",
                          "interval": 12.0, "run_minutes": 40.0, "first_bus": "05:00", "last_bus": "23:00"}])
    guide = BusGuide(adapter.advisor, adapter.dataset.routes, network=(pd.DataFrame(rows), info))

    result = guide.find(dt.date.fromisoformat(date), 8, "가상시장", origin=a[2]["name"])
    best = result["options"][0]
    assert best["kind"] == "transfer" and best["routes"] == [routes[0], "X9"]
    first, second = best["legs"]
    assert first["predicted"] is True and second["predicted"] is False
    assert second["congestion"] is None and second["interval_min"] == 12.0 and second["path"]
    assert "가상시장" in result["destination"]["stop"]

    # 운행하지 않는 시간대의 노선은 경로에서 빠짐
    assert guide.find(dt.date.fromisoformat(date), 2, "가상시장", origin=a[2]["name"])["options"] == []


def test_stop_name_matching_rules(client):
    from app.core.registry import registry
    guide = registry.get("bus").guide_engine
    guide._build()
    name = next(iter(guide.names))
    assert {s["name"] for s in guide.by_name(name)} >= {name}
    assert {s["name"] for s in guide.by_name(name + "으로")} >= {name}       # 조사가 붙어도 찾음


# ==================================================
# 7. 탑승 시간: 시간표 기준
# ==================================================

def _timed_guide(client, extra_rows=()):
    import pandas as pd
    from app.core.registry import registry
    from app.domains.bus.guide import BusGuide
    adapter = registry.get("bus")
    rows = [
        # 가상의 노선 T1: 정류장 사이 600m, 시간표는 첫차 2분·막차 3분 간격
        {"route": "T1", "seq": i + 1, "station_id": f"T-{i}", "name": f"시간표정류장{i}", "lat": 37.5 + i * 0.005, "lng": 127.0,
         "direction": "", "cum_m": i * 600.0, "t_first": i * 2.0, "t_last": i * 3.0}
        for i in range(12)
    ] + list(extra_rows)
    info = pd.DataFrame([{"route": "T1", "route_type": "간선", "company": "", "start": "", "end": "",
                          "interval": 10.0, "run_minutes": 200.0, "first_bus": "04:00", "last_bus": "23:30"}])
    guide = BusGuide(adapter.advisor, adapter.dataset.routes, network=(pd.DataFrame(rows), info))
    guide._build()
    return guide


def test_ride_time_follows_timetable_by_hour(client):
    guide = _timed_guide(client)
    line = guide.by_route["T1"]
    a, b = line[1], line[9]                                  # 8구간, 4.8km
    assert guide.ride_minutes(a, b, 14) == (24.0, 4.8)       # 낮: 막차 시간표 3분 × 8
    assert guide.ride_minutes(a, b, 4) == (16.0, 4.8)        # 새벽: 첫차 시간표 2분 × 8
    peak, _ = guide.ride_minutes(a, b, 8)
    assert round(peak, 1) == 27.6                            # 출근 시간: 24분 × 1.15
    # 예전 방식(한 바퀴 200분 ÷ 정류장 수)이면 145분쯤 나오던 구간
    _, date = meta(client)
    option = guide.find(dt.date.fromisoformat(date), 14, "시간표정류장9", origin="시간표정류장1")["options"][0]
    assert option["legs"][0]["ride_min"] == 24 and option["legs"][0]["distance_km"] == 4.8


def test_ride_time_ignores_broken_timetable(client):
    guide = _timed_guide(client)
    line = guide.by_route["T1"]
    # 짧은 구간(시간표 3분 미만 차이)은 거리 ÷ 노선 평균 속도
    short, km = guide.ride_minutes(line[1], line[1 + 0] | {"cum_m": line[1]["cum_m"] + 600, "t_last": line[1]["t_last"] + 1}, 14)
    assert km == 0.6 and 2.5 < short < 3.5                   # 600m ÷ 12km/h = 3분 (시간표의 1분이 아님)
    # 시간표가 말이 안 되는 구간(600m에 300분)은 버리고 거리로 계산
    broken, _ = guide.ride_minutes(line[1], line[1] | {"cum_m": line[1]["cum_m"] + 600, "t_last": line[1]["t_last"] + 300}, 14)
    assert 2.5 < broken < 3.5
    # 거리 정보가 없으면 (None, None) → 예전 방식으로 어림
    assert guide.ride_minutes({"route": "T1"}, {"route": "T1"}, 14) == (None, None)


# ==================================================
# 8. 혼잡도 시간 기준: 탈 정류장에 오는 버스의 기점 출발 시간대 (park-next P-3)
# ==================================================

def test_departure_hour_goes_back_by_time_from_terminal(client):
    from app.core.registry import registry
    advisor = registry.get("bus").advisor
    route = registry.get("bus").dataset.routes[0]
    clock = 8 * 60 + 30

    assert advisor.departure_hour(route, "weekday", 1, clock, 0) == 8          # 기점에서 타면 그 시간대
    assert advisor.departure_hour(route, "weekday", 30, clock, 75) == 7        # 75분 전에 기점을 떠난 버스
    assert advisor.departure_hour(route, "weekday", 30, clock, 600) == 0       # 전날 출발이면 0시로 묶음
    assert advisor.departure_hour(route, "weekday", 30, 24 * 60 + 40, 10) == 23   # 다음 날로 넘어가면 23시


def test_departure_hour_estimates_from_run_minutes_without_timetable(client):
    from app.core.registry import registry
    adapter = registry.get("bus")
    route = adapter.dataset.routes[0]
    run = float(adapter.dataset.route_info_for(route, "weekday")["run_minutes"])
    last = max(seq for _, seq in adapter.advisor.stop_names(route))
    clock = 12 * 60 + 30

    # 마지막 정류장은 한 바퀴 운행시간만큼 전에 기점을 떠난 버스
    assert adapter.advisor.departure_hour(route, "weekday", last, clock) == int((clock - run) // 60)
    assert adapter.advisor.departure_hour(route, "weekday", 1, clock) == 12


def test_guide_reads_congestion_of_bus_that_left_terminal_earlier(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    near_start = client.post("/api/bus/guide", json={
        "origin": stops[1]["name"], "destination": stops[4]["name"], "date": date, "hour": 12}).json()
    near_end = client.post("/api/bus/guide", json={
        "origin": stops[-6]["name"], "destination": stops[-2]["name"], "date": date, "hour": 12}).json()

    first = near_start["options"][0]["legs"][0]
    late = near_end["options"][0]["legs"][0]
    assert first["start_hour"] == 12                       # 기점 근처: 같은 시간대
    assert late["start_hour"] < 12                          # 종점 근처: 한두 시간 전에 떠난 버스
    assert "기점을 떠난 시간대" in near_end["assumption"]


def test_chat_guide_marks_start_hour_when_bus_left_terminal_earlier(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    origin, dest = stops[-6]["name"].replace(" ", ""), stops[-2]["name"].replace(" ", "")
    body = client.post("/api/bus/chat", json={"message": f"{origin}에서 {dest} 어떻게 가? {date} 12시"}).json()
    assert body["intent"] == "guide", body
    hour = body["data"]["options"][0]["legs"][0]["start_hour"]
    assert hour is not None and hour != 12
    assert "기점 " in body["answer"] and "시대 출발 버스 기준" in body["answer"]
    assert f"기점 {hour}시대 출발 버스 기준" in body["answer"]
    assert body["numbers_verified"] is True


def test_chat_guide_no_start_hour_note_near_terminal(client):
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    origin, dest = stops[1]["name"].replace(" ", ""), stops[4]["name"].replace(" ", "")
    body = client.post("/api/bus/chat", json={"message": f"{origin}에서 {dest} 어떻게 가? {date} 12시"}).json()
    assert body["intent"] == "guide", body
    assert body["data"]["options"][0]["legs"][0]["start_hour"] == 12
    assert "출발 버스 기준" not in body["answer"]


@pytest.mark.parametrize("mode", ["none", "missing"])
def test_chat_guide_start_hour_none_has_no_note(client, monkeypatch, mode):
    from app.core.registry import registry
    adapter = registry.get("bus")
    routes, date = meta(client)
    stops = stops_of(client, routes[0])
    original = adapter.guide

    def patched(args):
        result = original(args)
        for option in result["options"]:
            for leg in option["legs"]:
                if mode == "none":
                    leg["start_hour"] = None
                else:
                    leg.pop("start_hour", None)
        return result

    monkeypatch.setattr(adapter, "guide", patched)
    slots = {"origin": stops[-6]["name"], "destination": stops[-2]["name"], "date": date, "hour": 12}
    _, answer, _, _ = agent._guide(adapter, slots)
    assert "출발 버스 기준" not in answer


def test_guide_second_leg_is_boarded_later(client, monkeypatch):
    from app.core.registry import registry
    guide = registry.get("bus").guide_engine
    seen = []

    def fake_leg(leg, date, clock_minute):
        seen.append(clock_minute)
        return {"wait_min": 5.0, "ride_min": 20}

    monkeypatch.setattr(guide, "_leg", fake_leg)
    legs = [{"board": {"walk_m": 140}}, {"board": {"walk_m": 70}}]

    guide._legs(legs, None, 8)

    # 8시 30분 + 걷기 2분 → 탐. 그 뒤 대기 5분 + 탑승 20분 + 갈아타며 걷기 1분
    assert seen == [8 * 60 + 30 + 2, 8 * 60 + 30 + 2 + 25 + 1]


# ==================================================
# 9. 자정을 넘는 환승 · 첫차를 기다리는 정류장 (PR #25 리뷰 LOW 2 · 3)
# ==================================================

def test_leg_after_midnight_reads_next_day(client, monkeypatch):
    import pandas as pd
    from app.core.registry import registry
    from app.domains.bus.guide import GuideError
    adapter = registry.get("bus")
    guide = adapter.guide_engine
    guide._build()
    route = adapter.dataset.routes[0]
    board, alight = guide.by_route[route][0], guide.by_route[route][3]
    date = client.get("/api/bus/meta").json()["dates"]["max_forecast_date"]     # 예측 범위 마지막 날
    day_before = dt.date.fromisoformat(date) - dt.timedelta(days=1)
    seen = []
    original = adapter.advisor.analyze_day

    def spy(r, d):
        seen.append(pd.Timestamp(d).date())
        return original(r, d)

    monkeypatch.setattr(adapter.advisor, "analyze_day", spy)
    # 기점에서 0시 40분에 떠나는 버스 → 다음 날 0시대 값 (예전에는 그날 23시로 묶음)
    try:
        guide._leg({"board": board, "alight": alight}, day_before, 24 * 60 + 40)
    except GuideError:
        pass                                   # 0시대에 출발이 없으면 그 구간은 빠짐
    assert seen[-1] == dt.date.fromisoformat(date)
    # 다음 날이 예측 범위 밖이면 500이 아니라 그 경로만 빠짐
    with pytest.raises(GuideError):
        guide._leg({"board": board, "alight": alight}, dt.date.fromisoformat(date), 24 * 60 + 40)


def test_guide_waits_for_first_bus_when_it_comes_soon(client):
    guide = _timed_guide(client)
    _, date = meta(client)
    day = dt.date.fromisoformat(date)
    # T1 첫차는 기점 04:00, 네 번째 정류장에는 첫차 시간표로 6분 뒤 → 3시 30분에 오면 36분 기다려 04:06 첫차를 탐
    option = guide.find(day, 3, "시간표정류장9", origin="시간표정류장3")["options"][0]
    leg = option["legs"][0]
    assert leg["first_bus_at"] == "04:06"
    assert leg["wait_min"] == 36
    assert option["total_min"] == leg["walk_min"] + 36 + leg["ride_min"]
    # 첫차가 한 시간보다 더 뒤면 예전처럼 그 노선은 빠짐
    assert guide.find(day, 2, "시간표정류장9", origin="시간표정류장3")["options"] == []
    # 첫차가 이미 지나간 시각이면 평소처럼 배차간격의 절반을 기다림
    normal = guide.find(day, 14, "시간표정류장9", origin="시간표정류장3")["options"][0]["legs"][0]
    assert normal["first_bus_at"] is None and normal["wait_min"] == 5.0


def test_first_bus_counted_in_next_hour_is_not_dropped(client, monkeypatch):
    from app.core.registry import registry
    adapter = registry.get("bus")
    guide = adapter.guide_engine
    guide._build()
    route = adapter.dataset.routes[0]
    board, alight = guide.by_route[route][0], guide.by_route[route][3]
    _, date = meta(client)
    hourly = adapter.advisor.analyze_day(route, date)["hourly"]
    first_hour = int(hourly.loc[hourly["trips"] > 0, "hour"].min())
    # 첫차가 기점을 (첫 출발 시간대 - 1)시 50분에 떠나는데, 자료에는 그 출발이 다음 시간대로 잡힌 경우
    monkeypatch.setitem(guide.route_meta, route, {"first_bus": f"{first_hour - 1:02d}:50", "last_bus": "23:00"})
    leg = guide._leg({"board": board, "alight": alight}, date, (first_hour - 1) * 60 + 55)
    assert leg["start_hour"] == first_hour and leg["first_bus_at"] is None
