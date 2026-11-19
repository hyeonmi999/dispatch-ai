# PR #4 독립 리뷰에서 나온 문제의 재현 테스트 (2026-11-19, 이슈 #13 · #14 · #15)
import datetime as dt

import pandas as pd
import pytest

from app.core import agent
from app.core.agent import classify, extract, extract_places

ROUTES = ["143", "272", "160", "150", "271"]
ALL_ROUTES = ROUTES + ["N13", "01A", "새벽A160", "6715B", "402"]
TODAY = dt.date(2026, 11, 19)
NOW = dt.datetime(2026, 11, 19, 10, 0)


@pytest.fixture()
def bus(client):
    from app.core.registry import registry
    return registry.get("bus")


def sample(client):
    m = client.get("/api/bus/meta").json()
    return m["routes"][0]["route"], m["dates"]["max_date"]


# ==================================================
# 1. 의도 분류: 부분 문자열로 잘못 걸리는 키워드
# ==================================================

def test_polite_ending_is_not_a_regulation_keyword():
    # "~인가요"의 '인가', "법원"의 '법'이 규정 키워드로 걸렸음
    assert classify("143번 첫차 몇 시인가요?", True) == "route_info"
    assert classify("143번 배차간격은 몇 분인가요?", True) == "route_info"
    assert classify("143번 인가대수 몇 대야?", True) == "route_info"
    assert classify("교대역에서 서울중앙지방법원 어떻게 가?", False) == "guide"
    # 진짜 규정 질문은 그대로
    assert classify("운행횟수를 늘리려면 인가를 받아야 하나요, 신고만 하면 되나요?", False) == "ask"
    assert classify("시내버스 배차간격을 단축하는 것은 인가 대상인가요, 신고 대상인가요?", False) == "ask"
    assert classify("여객자동차 운수사업법에서 정한 휴식시간은?", False) == "ask"
    assert classify("환승 할인 요금 규정이 어떻게 돼?", False) == "ask"


# ==================================================
# 2. 값 뽑기
# ==================================================

def test_interval_after_hour_is_kept():
    # "8시 10분 간격"의 10분이 시각의 분으로 지워졌음
    assert extract("143번 내일 8시 10분 간격으로 바꾸면?", ROUTES, TODAY)["interval"] == 10.0
    assert "interval" not in extract("2026-11-20 272번 3시 30분 승객", ROUTES, TODAY)      # 시각의 분은 간격이 아님


def test_night_hour_and_day_after_tomorrow():
    assert extract("143번 내일 밤 1시에 붐벼?", ROUTES, TODAY)["hour"] == 1               # 밤 1시는 13시가 아님
    assert extract("143번 내일 밤 10시에 붐벼?", ROUTES, TODAY)["hour"] == 22
    assert extract("143번 내일모레 8시 붐벼?", ROUTES, TODAY)["date"] == "2026-11-21"     # 내일이 아니라 모레


def test_route_names_that_are_not_plain_numbers():
    assert extract("N13번 노선 알려줘", ROUTES, TODAY, ALL_ROUTES)["route"] == "N13"
    assert extract("N13 노선 알려줘", ROUTES, TODAY, ALL_ROUTES)["route"] == "N13"
    assert extract("새벽A160번 노선 알려줘", ROUTES, TODAY, ALL_ROUTES)["route"] == "새벽A160"   # 160번으로 읽으면 안 됨
    assert extract("01A번은 어디 가?", ROUTES, TODAY, ALL_ROUTES)["route"] == "01A"
    assert extract("160번 노선 알려줘", ROUTES, TODAY, ALL_ROUTES)["route"] == "160"
    assert extract("402번 버스 노선알려줘", ROUTES, TODAY, ALL_ROUTES)["route"] == "402"
    assert "route" not in extract("11월 20일 15시 기준", ROUTES, TODAY, ALL_ROUTES)


def test_places_ignore_time_words_and_function_words():
    assert extract_places("서울역에서 종로3가역 8시에 어떻게 가?") == {"origin": "서울역", "destination": "종로3가역"}
    assert extract_places("서울역에서 종로3가역 내일은 어떻게 가?") == {"origin": "서울역", "destination": "종로3가역"}
    assert extract_places("여기서 서울역 가는 버스 뭐 타?") == {"from_here": True, "destination": "서울역"}
    assert extract_places("여기서 강남역 가는 버스 몇 번 타?") == {"from_here": True, "destination": "강남역"}
    # '~서'로 끝나는 도착지는 출발지가 아님
    assert extract_places("강남경찰서 어떻게 가?") == {"destination": "강남경찰서"}
    assert extract_places("여기서 종로소방서 어떻게 가?") == {"from_here": True, "destination": "종로소방서"}
    # 정류장 이름 속 '환승'에서 문장을 자르지 않음
    assert extract_places("서울역에서 서울역버스환승센터 어떻게 가?") == {"origin": "서울역", "destination": "서울역버스환승센터"}


# ==================================================
# 3. 이전 대화 이어 쓰기
# ==================================================

def test_unrelated_question_is_out_of_scope_even_with_history(client, bus):
    route, date = sample(client)
    history = [{"role": "user", "content": f"{route}번 {date} 8시 붐벼?"}]
    for message in ("점심 메뉴 추천해줘", "오늘 날씨 어때?"):
        out = agent.run(bus, message, history=history, now=NOW)
        assert out["intent"] == "out_of_scope" and out["tools"] == [], message
    # 진짜 이어 묻기는 그대로 동작
    out = agent.run(bus, "그럼 9시는?", history=history, now=NOW)
    assert out["intent"] == "predict" and out["tools"][0]["args"]["hour"] == 9
    out = agent.run(bus, "증차해야 해?", history=history, now=NOW)
    assert out["intent"] == "advise" and out["tools"][0]["args"]["route"] == route


def test_small_talk_after_guide_is_not_answered_as_guide(client, bus):
    route, date = sample(client)
    stops = client.get(f"/api/bus/routes/{route}/geometry").json()["stops"]
    first = f"{stops[2]['name'].replace(' ', '')}에서 {stops[10]['name'].replace(' ', '')} 어떻게 가? {date} 8시"
    history = [{"role": "user", "content": first}]
    for message in ("고마워", "점심 뭐 먹지?"):
        out = agent.run(bus, message, history=history, now=NOW)
        assert out["intent"] == "out_of_scope" and out["tools"] == [], message


def test_place_name_with_bus_word_answers_guide_clarification(client, bus):
    # 되묻기에 "고속버스터미널"이라고 답하면 '버스' 때문에 규정 검색으로 빠졌음
    route, date = sample(client)
    stops = client.get(f"/api/bus/routes/{route}/geometry").json()["stops"]
    history = [{"role": "user", "content": f"{stops[2]['name'].replace(' ', '')}에서 어떻게 가?"}]
    out = agent.run(bus, "고속버스터미널", history=history, now=NOW)
    assert out["intent"] in ("guide", "clarify") and out["slots"].get("destination") == "고속버스터미널"


def test_guide_does_not_reuse_date_and_hour_from_other_questions(client, bus):
    route, date = sample(client)
    stops = client.get(f"/api/bus/routes/{route}/geometry").json()["stops"]
    history = [{"role": "user", "content": f"{route}번 {date} 8시 붐벼?"}]
    out = agent.run(bus, f"{stops[2]['name'].replace(' ', '')}에서 {stops[10]['name'].replace(' ', '')} 어떻게 가?",
                    history=history, now=NOW)
    assert out["slots"]["date"] == NOW.date().isoformat() and out["slots"]["hour"] == NOW.hour


# ==================================================
# 4. 오류 처리 · 개인정보
# ==================================================

def test_invalid_value_from_feature_is_answered_not_raised(client, bus):
    route, date = sample(client)
    out = agent.run(bus, f"{route}번 {date} 8시에 배차간격을 0분으로 바꾸면?", now=NOW)      # 예전에는 ValidationError가 밖으로 나감
    assert out["intent"] == "simulate" and out["tools"][0]["ok"] is False and out["answer"].startswith("⚠️")


def test_location_is_not_stored_in_chat_log():
    from app.db.repository import without_location
    result = {"slots": {"destination": "서울역", "lat": 37.5, "lng": 127.0, "hour": 8},
              "tools": [{"name": "guide", "args": {"destination": "서울역", "lat": 37.5, "lng": 127.0}, "ok": True}]}
    slots, tools = without_location(result)
    assert slots == {"destination": "서울역", "hour": 8, "location_used": True}
    assert tools == [{"name": "guide", "args": {"destination": "서울역"}, "ok": True}]
    assert result["slots"]["lat"] == 37.5                      # 응답 자체는 건드리지 않음


def test_route_info_for_route_with_missing_fields(client, bus):
    # 심야 노선은 노선 기본정보에 유형이 비어 있어(NaN) 응답 검증과 문장 만들기가 터졌음
    from app.domains.bus.guide import BusGuide
    from app.domains.bus.schemas import BusRouteInfoResponse
    rows = [{"route": "N13", "seq": i + 1, "station_id": f"N-{i}", "name": f"심야정류장{i}", "lat": 37.5 + i * 0.01, "lng": 127.0,
             "direction": ""} for i in range(6)]
    info = pd.DataFrame([{"route": "N13", "route_type": float("nan"), "company": float("nan"), "start": "차고지", "end": "종점",
                          "interval": 30.0, "run_minutes": float("nan"), "first_bus": "23:30", "last_bus": "03:30"}])
    bus.__dict__["guide_engine"] = BusGuide(bus.advisor, bus.dataset.routes, network=(pd.DataFrame(rows), info))
    try:
        body = BusRouteInfoResponse(**bus.route_info({"route": "N13"}))
        assert body.route_type is None and body.company is None and body.predicted is False
        out = agent.run(bus, "N13번 노선 알려줘", now=NOW)
        assert out["intent"] == "route_info" and out["tools"][0]["ok"] is True and "N13번" in out["answer"]
    finally:
        bus.__dict__.pop("guide_engine", None)


# ==================================================
# 5. 길안내 추천
# ==================================================

def test_option_with_unpredicted_leg_is_not_called_less_crowded(client, bus):
    """예측 없는 노선을 낀 환승은 혼잡도를 모르는 구간이 있으므로 '덜 붐빔'으로 추천하지 않음"""
    from app.domains.bus.guide import BusGuide
    route, date = sample(client)
    stops = client.get(f"/api/bus/routes/{route}/geometry").json()["stops"]
    rows = [{"route": route, "seq": s["seq"], "station_id": s["station_id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"],
             "direction": ""} for s in stops]
    # 가상의 노선 X1: 출발 정류장에서 직행 노선의 중간 정류장까지만 감 → 거기서 같은 직행 노선으로 갈아타는 환승이 생김
    a, mid = stops[2], stops[6]
    rows += [{"route": "X1", "seq": 1, "station_id": a["station_id"], "name": a["name"], "lat": a["lat"], "lng": a["lng"], "direction": ""},
             {"route": "X1", "seq": 2, "station_id": mid["station_id"], "name": mid["name"], "lat": mid["lat"], "lng": mid["lng"], "direction": ""}]
    info = pd.DataFrame([{"route": "X1", "route_type": "지선", "company": "", "start": "", "end": "", "interval": 4.0,
                          "run_minutes": 10.0, "first_bus": "04:00", "last_bus": "23:59"}])
    guide = BusGuide(bus.advisor, bus.dataset.routes, network=(pd.DataFrame(rows), info))
    result = guide.find(dt.date.fromisoformat(date), 8, stops[12]["name"], origin=a["name"])
    options = result["options"]
    assert options[0]["kind"] == "direct" and options[0]["recommended"] is True
    assert result["recommendation"]["reason"] == "fastest"
    # 이미 직행으로 가는 노선으로 갈아타는 환승은 보여 주지 않음
    assert all(route not in o["routes"] for o in options if o["kind"] == "transfer")
    assert not any(o["least_crowded"] for o in options if any(not leg["predicted"] for leg in o["legs"]))
