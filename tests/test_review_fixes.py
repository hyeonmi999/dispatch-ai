# PR #4 독립 리뷰에서 나온 문제의 재현 테스트 (2026-11-19 ~ 20, 이슈 #13 ~ #17)
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


# ==================================================
# 6. LLM이 다시 쓴 문장의 검증 (이슈 #16)
# ==================================================

PREDICT_TEMPLATE = ("🚌 143번 2026-11-20 8시 예상 승차는 3,133명입니다. 작년 같은 요일·시간대는 3,065명이었습니다."
                    "\n🟠 가장 붐비는 구간의 혼잡도는 0.61(혼잡)입니다.")
ADVISE_TEMPLATE = ("⬆️ 143번 2026-11-20 8시 권고는 '배차간격 단축 (증차)'입니다."
                   "\n🚌 지금은 약 6.7분 간격(시간당 9회)이고,"
                   "\n🟠 가장 붐비는 구간(정릉 → 길음)의 혼잡도는 0.82(혼잡)입니다."
                   "\n👉 권고 배차간격은 6분이고, 그때 예상 혼잡도는 0.73입니다. 버스가 약 2대 더 필요합니다."
                   "\n📝 최종 결정은 담당자 검토가 필요합니다.")


def test_rewrite_numbers_must_keep_value_unit_order_and_label():
    from app.core.agent import same_numbers
    # 문장 틀 그대로, 그리고 뜻이 같은 자연스러운 문장은 통과
    assert same_numbers(PREDICT_TEMPLATE, PREDICT_TEMPLATE) and same_numbers(ADVISE_TEMPLATE, ADVISE_TEMPLATE)
    assert same_numbers("143번은 11월 20일 8시에 3,133명이 탈 것으로 예상되고, 작년에는 3,065명이었어요. 혼잡도는 0.61(혼잡)입니다.", PREDICT_TEMPLATE)
    assert same_numbers("지금은 6.7분 간격인데 6분으로 줄이는 것을 권고해요. 혼잡도는 0.82에서 0.73으로 내려갑니다.", ADVISE_TEMPLATE)
    # 이슈 #16에서 통과하던 틀린 답
    assert not same_numbers("혼잡도는 1입니다.", PREDICT_TEMPLATE)                                   # 0.61을 정수로
    assert not same_numbers("예상 승차는 20명입니다.", PREDICT_TEMPLATE)                              # 날짜의 20
    assert not same_numbers("예상 승차는 3,065명이고 작년에는 3,133명이었습니다.", PREDICT_TEMPLATE)   # 값을 서로 바꿈
    assert not same_numbers("작년 같은 요일은 3,133명이었습니다.", PREDICT_TEMPLATE)                  # 다른 값의 설명
    assert not same_numbers("지금은 약 6분 간격입니다.", ADVISE_TEMPLATE)                             # 6.7분을 6분으로
    assert not same_numbers("지금은 약 7분 간격입니다.", ADVISE_TEMPLATE)                             # 6.7분을 7분으로
    assert not same_numbers("권고 배차간격은 6.7분이고 지금은 6분입니다.", ADVISE_TEMPLATE)
    assert not same_numbers("혼잡도는 0.73에서 0.82로 바뀝니다.", ADVISE_TEMPLATE)                    # 순서가 바뀜
    assert not same_numbers("버스가 약 9대 더 필요합니다.", ADVISE_TEMPLATE)                          # 9회의 9를 다른 단위로
    assert not same_numbers("11월 21일 8시 예상 승차는 3,133명입니다.", PREDICT_TEMPLATE)             # 날짜가 다름


def test_rewrite_meaning_rejects_negation_and_contradiction():
    from app.core.agent import keeps_meaning
    from app.domains.bus.advisor import ACTION_LABELS
    keep, decrease = {"action": "keep", "level": "보통"}, {"action": "decrease", "level": "여유"}
    # 문장 틀 답변은 권고마다 통과해야 함
    assert keeps_meaning(ADVISE_TEMPLATE, {"action": "increase", "level": "혼잡"})
    assert keeps_meaning(f"권고는 '{ACTION_LABELS['keep']}'입니다. 혼잡도는 0.5(보통)입니다.\n지금 배차간격을 유지해도 됩니다.", keep)
    assert keeps_meaning(f"권고는 '{ACTION_LABELS['decrease']}'입니다. 가장 붐비는 구간의 혼잡도는 0.3(여유)입니다.", decrease)
    assert keeps_meaning(f"권고는 '{ACTION_LABELS['increase_limited']}'입니다. 혼잡도는 1.1(매우혼잡)입니다.",
                         {"action": "increase_limited", "level": "매우혼잡"})
    # 이슈 #16에서 통과하던 틀린 답
    assert not keeps_meaning("감차하지 않아도 됩니다. 지금은 여유입니다.", decrease)
    assert not keeps_meaning("유지보다는 증차를 권합니다. 혼잡도는 보통입니다.", keep)
    assert not keeps_meaning("배차간격 단축(증차)을 권고합니다. 혼잡하지 않습니다.", {"action": "increase", "level": "혼잡"})
    assert not keeps_meaning("감차를 검토하세요. 상당히 붐빕니다.", decrease)                 # 단계를 적지 않고 반대로 말함
    assert not keeps_meaning("감차를 검토하세요. 여유이지만 꽤 붐빕니다.", decrease)
    assert not keeps_meaning("증차를 권고합니다. 혼잡이지만 한산한 편입니다.", {"action": "increase", "level": "혼잡"})
    # 길안내: 추천 경로의 번호
    assert keeps_meaning("추천은 1안입니다. 2안보다 3분 빠릅니다.".replace("2안보다 3분 빠릅니다", "가장 빨리 도착합니다"), {"recommended_no": 1})
    assert not keeps_meaning("추천은 2안입니다.", {"recommended_no": 1})


# ==================================================
# 7. 길안내·검색 정리 (이슈 #17)
# ==================================================

def test_guide_from_location_next_to_destination_says_walk(client, bus):
    """현재 위치가 도착지 바로 옆이면 한 정류장 가는 버스가 아니라 걸어갈 거리를 알림"""
    route, date = sample(client)
    dest = client.get(f"/api/bus/routes/{route}/geometry").json()["stops"][10]
    body = client.post("/api/bus/guide", json={
        "lat": dest["lat"] + 0.0005, "lng": dest["lng"], "destination": dest["name"], "date": date, "hour": 8}).json()   # 약 55m
    assert body["options"] == [] and body["recommendation"] is None
    assert 0 < body["walk_only"]["walk_m"] <= 300 and body["walk_only"]["walk_min"] >= 1
    out = agent.run(bus, f"여기서 {dest['name'].replace(' ', '')} 어떻게 가? {date} 8시",
                    location={"lat": dest["lat"] + 0.0005, "lng": dest["lng"]}, now=NOW)
    assert out["intent"] == "guide" and "걸어서" in out["answer"] and "찾지 못했습니다" not in out["answer"]
    assert out["numbers_verified"] is True
    # 멀리서 출발하면 그대로 버스 경로를 안내
    far = client.post("/api/bus/guide", json={
        "origin": client.get(f"/api/bus/routes/{route}/geometry").json()["stops"][2]["name"],
        "destination": dest["name"], "date": date, "hour": 8}).json()
    assert far["walk_only"] is None and far["options"]


def _line(route, names, lat, lng0, step=0.004):
    """동서로 뻗은 가상의 노선 (정류장 간격 약 350m)"""
    return [{"route": route, "seq": i + 1, "station_id": f"{route}-{i}", "name": name, "lat": lat, "lng": lng0 + i * step,
             "direction": ""} for i, name in enumerate(names)]


def test_same_name_stops_far_apart_use_the_one_near_the_other_end(client, bus):
    """같은 이름의 정류장이 멀리 떨어진 두 곳에 있으면, 출발지에서 가까운 쪽으로 안내하고 그 사실을 답변에 적음"""
    from app.domains.bus.guide import BusGuide
    _, date = sample(client)
    north = _line("N1", ["북쪽출발", "북쪽중간", "농수산물센터"], 37.60, 127.00)
    south = _line("S1", ["남쪽출발", "남쪽중간", "농수산물센터"], 37.40, 127.00)      # 약 22km 남쪽
    info = pd.DataFrame([{"route": r, "route_type": "지선", "company": "", "start": "", "end": "", "interval": 5.0,
                          "run_minutes": 20.0, "first_bus": "04:00", "last_bus": "23:59"} for r in ("N1", "S1")])
    guide = BusGuide(bus.advisor, bus.dataset.routes, network=(pd.DataFrame(north + south), info))
    day = dt.date.fromisoformat(date)

    for start, route, lat in (("북쪽출발", "N1", 37.60), ("남쪽출발", "S1", 37.40)):
        result = guide.find(day, 10, "농수산물센터", origin=start)
        assert result["destination"]["ambiguous"] is True and result["origin"]["ambiguous"] is False
        assert abs(result["destination"]["lat"] - lat) < 0.01                    # 지도에 찍히는 도착지도 가까운 쪽
        assert [o["routes"] for o in result["options"]] == [[route]]
    # 현재 위치에서 출발해도 같음
    result = guide.find(day, 10, "농수산물센터", lat=37.4001, lng=127.0)
    assert result["destination"]["ambiguous"] and result["options"][0]["routes"] == ["S1"]
    # 이름이 하나뿐인 곳은 표시하지 않음
    assert guide.find(day, 10, "북쪽중간", origin="북쪽출발")["destination"]["ambiguous"] is False

    bus.__dict__["guide_engine"] = guide
    try:
        out = agent.run(bus, f"남쪽출발에서 농수산물센터 어떻게 가? {date} 10시", now=NOW)
        assert out["intent"] == "guide" and "여러 곳" in out["answer"] and "S1번" in out["answer"]
    finally:
        bus.__dict__.pop("guide_engine", None)


def test_same_name_falls_back_to_a_place_that_has_a_route(client, bus):
    """가장 가까운 곳으로는 갈 수 있는 버스가 없으면, 그다음 가까운 곳에서 경로를 찾음"""
    from app.domains.bus.guide import BusGuide
    _, date = sample(client)
    stop = lambda route, seq, name, lat: {"route": route, "seq": seq, "station_id": f"{route}-{seq}", "name": name,
                                          "lat": lat, "lng": 127.0, "direction": ""}
    rows = [stop("A1", 1, "먼출발", 37.50), stop("A1", 2, "가는길", 37.40), stop("A1", 3, "농수산물센터", 37.30),   # 약 22km
            stop("Z9", 1, "외딴곳", 37.46), stop("Z9", 2, "농수산물센터", 37.45)]                                  # 약 5.5km, 이어지는 노선 없음
    info = pd.DataFrame([{"route": r, "route_type": "지선", "company": "", "start": "", "end": "", "interval": 5.0,
                          "run_minutes": 20.0, "first_bus": "04:00", "last_bus": "23:59"} for r in ("A1", "Z9")])
    guide = BusGuide(bus.advisor, bus.dataset.routes, network=(pd.DataFrame(rows), info))
    result = guide.find(dt.date.fromisoformat(date), 10, "농수산물센터", origin="먼출발")
    assert result["destination"]["ambiguous"] is True
    assert [o["routes"] for o in result["options"]] == [["A1"]] and abs(result["destination"]["lat"] - 37.30) < 0.01


def test_role_search_embeds_the_question_once(tmp_path, monkeypatch):
    """허용된 조각이 모자라 후보를 늘려 다시 찾아도 질문은 한 번만 임베딩"""
    from app.core import rag
    from tests.test_bus_documents import GradedEmbeddings, document_client

    calls = []

    class Counting(GradedEmbeddings):
        def embed_query(self, text):
            calls.append(text)
            return super().embed_query(text)

    client, _, knowledge = document_client(tmp_path, monkeypatch)
    monkeypatch.setattr(rag, "get_embedder", lambda _: Counting())
    endpoint = "/api/bus/documents"
    assert client.post(endpoint, data={"title": "공개 절차", "type": "법령", "grade": "공개"},
                       files={"file": ("pub.txt", "증차 절차 공개본", "text/plain")}).status_code == 201
    for i in range(20):
        assert client.post(endpoint, data={"title": f"제한 {i}", "type": "내부자료", "grade": "제한"},
                           files={"file": (f"sec{i:02d}.txt", f"증차 절차 내부 검토 {i}", "text/plain")}).status_code == 201
    knowledge.search("증차 절차", "bge", 4, role="admin")       # 색인 생성 (문서 임베딩은 세지 않음)
    calls.clear()
    hits = knowledge.search("증차 절차", "bge", 4, role="citizen")   # 제한 문서 20건 뒤의 공개 문서 → 후보를 여러 번 늘림
    assert [h["document"] for h in hits] == ["공개 절차"]
    assert calls == ["증차 절차"]
