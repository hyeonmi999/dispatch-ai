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
    assert client.get("/api/bus/routes/9999/info").status_code == 404


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
