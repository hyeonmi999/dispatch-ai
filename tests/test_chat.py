# 배차 상담 챗봇 (F-25): 맞는 기능을 고르는지, 숫자가 기능 결과와 같은지
import datetime as dt

from app.core.agent import classify, extract, verify_numbers

ROUTES = ["143", "272", "160", "150", "271"]
TODAY = dt.date(2026, 11, 16)


def meta(client):
    m = client.get("/api/bus/meta").json()
    return m["routes"][0]["route"], m["dates"]["max_date"]


def chat(client, message, **extra):
    r = client.post("/api/bus/chat", json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ==================================================
# 1. API: 의도별 기능 호출
# ==================================================

def test_chat_predict_uses_numbers_from_predict(client):
    route, date = meta(client)
    body = chat(client, f"{route}번 {date} 8시 몇 명 타?")
    assert body["intent"] == "predict"
    assert body["tools"] == [{"name": "predict", "args": {"route": route, "date": date, "hour": 8}, "ok": True}]
    assert body["numbers_verified"] is True and body["needs_review"] is True

    predicted = client.post("/api/bus/predict", json={"route": route, "date": date, "hour": 8}).json()
    pred = next(h["pred"] for h in predicted["hourly"] if h["hour"] == 8)
    assert f"{int(round(pred)):,}명" in body["answer"]
    assert predicted["congestion"]["level"] in body["answer"]


def test_chat_advise_has_sources(client):
    route, date = meta(client)
    body = chat(client, f"{route}번 {date} 8시 증차해야 해?")
    assert body["intent"] == "advise" and body["tools"][0]["name"] == "advise"
    assert body["sources"] and body["numbers_verified"] is True


def test_chat_regulation_question_goes_to_ask(client):
    body = chat(client, "노선버스 운행횟수를 늘리는 사업계획 변경 절차는?")
    assert body["intent"] == "ask" and body["tools"][0]["name"] == "ask"
    assert all(s["document"] and s["article"] for s in body["sources"])
    assert body["llm"] is None      # use_llm 기본값 False: 근거 조항만


def test_chat_asks_back_when_value_missing(client):
    route, _ = meta(client)
    body = chat(client, f"{route}번 몇 명 타?")
    assert body["intent"] == "clarify" and body["tools"] == []
    assert "날짜" in body["answer"]


def test_chat_follow_up_keeps_route_and_date(client):
    route, date = meta(client)
    history = [{"role": "user", "content": f"{route}번 {date} 8시 몇 명 타?"},
               {"role": "assistant", "content": "..."}]
    body = chat(client, "그럼 9시는?", history=history)
    assert body["intent"] == "predict"
    assert body["tools"][0]["args"] == {"route": route, "date": date, "hour": 9}


def test_chat_follow_up_can_switch_to_advise(client):
    # 재현: 노선 없이 "증차해야 해?"라고 이어 물으면 규정 질의로 잘못 분류됐음 (docs/lessons.md)
    route, date = meta(client)
    history = [{"role": "user", "content": f"{route}번 {date} 15시에 혼잡해?"}]
    body = chat(client, "그럼 8시에 증차해야 해?", history=history)
    assert body["intent"] == "advise"
    assert body["tools"][0]["args"] == {"route": route, "date": date, "hour": 8}


def test_chat_out_of_scope_calls_nothing(client):
    body = chat(client, "오늘 점심 메뉴 추천해줘")
    assert body["intent"] == "out_of_scope" and body["tools"] == [] and body["sources"] == []


# ==================================================
# 2. API: 오류
# ==================================================

def test_chat_unknown_route_answers_with_reason(client):
    _, date = meta(client)
    body = chat(client, f"9999번 {date} 8시 몇 명 타?")
    assert body["tools"][0]["ok"] is False
    assert "9999" in body["answer"]


def test_chat_rejects_empty_and_too_long_message(client):
    assert client.post("/api/bus/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/bus/chat", json={"message": "가" * 501}).status_code == 422


def test_chat_not_implemented_domain(client):
    r = client.post("/api/logistics/chat", json={"message": "내일 물량 얼마나 돼?"})
    assert r.status_code == 501 and r.json()["status"] == "not_implemented"


# ==================================================
# 3. 규칙: 의도 분류 · 값 뽑기 · 숫자 검증
# ==================================================

def test_classify_keywords():
    cases = [
        ("160번 내일 오후 3시 붐벼?", "predict"),
        ("143번 내일 8시 증차해야 해?", "advise"),
        ("버스 늘리려면 뭐 해야 돼?", "ask"),
        ("서울시 버스 혼잡도는 어떻게 구분하나요?", "ask"),
        ("150번 내일 8시 간격을 5분으로 바꾸면?", "simulate"),
        ("내일 출근 시간 전체 노선 최적화 해줘", "optimize"),
        ("그럼 4시는?", None),
    ]
    for message, expected in cases:
        has_route = "route" in extract(message, ROUTES, TODAY)
        assert classify(message, has_route) == expected, message


def test_extract_route_date_hour_interval():
    assert extract("160번 내일 오후 3시 붐벼?", ROUTES, TODAY) == {"route": "160", "date": "2026-11-17", "hour": 15}
    assert extract("271 모레 몇 명 타?", ROUTES, TODAY) == {"route": "271", "date": "2026-11-18"}
    assert extract("10월 28일 143번 8시", ROUTES, TODAY) == {"route": "143", "date": "2026-10-28", "hour": 8}
    # "3시 30분"의 분은 배차간격이 아님
    assert extract("2026-11-20 272번 3시 30분 승객", ROUTES, TODAY) == {"route": "272", "date": "2026-11-20", "hour": 3}
    assert extract("150번 간격을 5분으로 바꾸면?", ROUTES, TODAY) == {"route": "150", "interval": 5.0}
    assert extract("퇴근 시간에 붐벼?", ROUTES, TODAY) == {"hour": 18, "hours": [17, 18, 19]}
    # 시간 범위 (전 노선 최적화용)
    assert extract("내일 7시부터 9시 전체 노선 최적화", ROUTES, TODAY)["hours"] == [7, 8, 9]
    assert extract("내일 오후 5~7시 전체 노선 최적화", ROUTES, TODAY)["hours"] == [17, 18, 19]
    assert "hours" not in extract("160번 내일 15시 붐벼?", ROUTES, TODAY)
    # 날짜·시간의 숫자를 노선으로 잘못 읽지 않음
    assert "route" not in extract("11월 20일 15시 기준", ROUTES + ["15", "20"], TODAY)


def test_verify_numbers_rejects_invented_values():
    facts = {"route": "160", "date": "2026-11-17", "hour": 15, "pred": 1234.0, "congestion": 0.618}
    assert verify_numbers("160번 2026-11-17 15시 예상 승차는 1,234명, 혼잡도 0.61입니다.", facts)
    assert not verify_numbers("160번 15시 예상 승차는 1,300명입니다.", facts)
    assert not verify_numbers("혼잡도는 0.75입니다.", facts)


def test_llm_rewrite_must_keep_recommendation_and_level():
    # 재현: 권고가 '현행 유지'인데 LLM이 "감차해도 돼요"로 바꿈. 숫자는 같아서 숫자 검증만으로는 통과했음
    from app.core.agent import keeps_meaning
    keep = {"action": "keep", "level": "보통"}
    assert keeps_meaning("271번 9시 권고는 현행 유지입니다. 혼잡도는 보통입니다.", keep)
    assert not keeps_meaning("271번 9시 감차해도 돼요.", keep)                       # 결론이 뒤집힘
    assert not keeps_meaning("271번 9시는 현행 유지이고 매우혼잡입니다.", keep)       # 혼잡 단계가 다름
    increase = {"action": "increase", "level": "혼잡"}
    assert keeps_meaning("배차간격을 단축해 증차하는 것을 권고합니다. 지금은 혼잡입니다.", increase)
    assert not keeps_meaning("증차하지 않아도 됩니다.", increase)
    assert keeps_meaning("160번 15시에는 2,194명이 탈 것으로 보이며 매우혼잡입니다.", {"level": "매우혼잡"})


# ==================================================
# 4. 시뮬레이션 · 전 노선 최적화 연결 (F-12 · F-13)
# ==================================================

def test_chat_simulate_uses_numbers_from_simulate(client):
    m = client.get("/api/bus/meta").json()
    info = m["routes"][0]
    route, date = info["route"], m["dates"]["max_date"]
    interval = info["min_interval"]
    body = chat(client, f"{route}번 {date} 8시 간격을 {interval:g}분으로 바꾸면?")
    assert body["intent"] == "simulate", body
    assert body["tools"] == [{"name": "simulate", "args": {"route": route, "date": date, "hour": 8, "interval": interval}, "ok": True}]
    assert body["numbers_verified"] is True and body["needs_review"] is True
    direct = client.post("/api/bus/simulate", json={"route": route, "date": date, "hour": 8, "interval": interval}).json()
    assert f"{direct['fleet']['buses_needed']}대" in body["answer"] and direct["simulated"]["level"] in body["answer"]


def test_chat_simulate_interval_out_of_range_answers_with_reason(client):
    m = client.get("/api/bus/meta").json()
    info = m["routes"][0]
    body = chat(client, f"{info['route']}번 {m['dates']['max_date']} 8시 간격을 {info['max_interval'] + 30:g}분으로 바꾸면?")
    assert body["intent"] == "simulate" and body["tools"][0]["ok"] is False
    assert body["answer"].startswith("⚠️")


def test_chat_optimize_all_routes(client):
    date = client.get("/api/bus/meta").json()["dates"]["max_date"]
    body = chat(client, f"{date} 7시부터 9시 전체 노선 최적화 해줘")
    assert body["intent"] == "optimize", body
    assert body["tools"] == [{"name": "optimize", "args": {"date": date, "hours": [7, 8, 9]}, "ok": True}]
    assert body["numbers_verified"] is True
    direct = client.post("/api/bus/optimize", json={"date": date, "hours": [7, 8, 9]}).json()
    assert f"{direct['kpi']['total_trips_after']}회" in body["answer"]


def test_chat_optimize_asks_for_hours(client):
    body = chat(client, "전체 노선 최적화 해줘")
    assert body["intent"] == "clarify" and "시간대" in body["answer"]


# ==================================================
# 5. 문서 열람 역할 전달 (F-37)
# ==================================================

def test_chat_passes_role_to_regulation_search(client, monkeypatch):
    from app.core.registry import registry
    seen = {}
    knowledge = registry.get("bus").knowledge_base
    original = knowledge.ask

    def spy(*args, **kwargs):
        seen["role"] = kwargs.get("role")
        return original(*args, **kwargs)
    monkeypatch.setattr(knowledge, "ask", spy)

    chat(client, "노선버스 운행횟수를 늘리는 사업계획 변경 절차는?", role="staff")
    assert seen["role"] == "staff"
    chat(client, "노선버스 운행횟수를 늘리는 사업계획 변경 절차는?")
    assert seen["role"] == "citizen"                          # 생략하면 시민
    assert client.post("/api/bus/chat", json={"message": "절차 알려줘", "role": "root"}).status_code == 422
