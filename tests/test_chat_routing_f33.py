"""F-33에서 발견한 업무 표현과 범위 밖 추천의 재현·회귀 검사."""
import pytest

from app.core.agent import classify


@pytest.mark.parametrize("message, expected", [
    ("143번 출근 시간에 차를 더 넣어야 할까?", "advise"),
    ("272번 버스를 더 넣는 게 좋을까?", "advise"),
    ("160번 차량을 좀 빼야 할까?", "advise"),
    ("271번 8분마다 운행하면 어떻게 달라져?", "simulate"),
    ("150번 6분 간격으로 운행하면 혼잡도는?", "simulate"),
    ("143번 8분마다 운행하려면 신고해야 해?", "ask"),
    ("143번 차를 더 넣으려면 어떤 절차가 필요해?", "ask"),
    ("143번 배차 권고 추천해줘", "advise"),
    ("143번 첫차 몇 시인가요?", "route_info"),
    ("160번 몇 명 타?", "predict"),
])
def test_dispatch_expressions_keep_regulation_priority(message, expected):
    assert classify(message, True) == expected


def test_chat_understands_additional_vehicle_advice(client):
    meta = client.get("/api/bus/meta").json()
    route, date = meta["routes"][0]["route"], meta["dates"]["max_date"]
    response = client.post("/api/bus/chat", json={"message": f"{route}번 {date} 출근 시간에 차를 더 넣어야 할까?"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "advise"
    assert body["tools"][0]["name"] == "advise"
    assert body["tools"][0]["args"] == {"route": route, "date": date, "hour": 8}
    assert body["tools"][0]["ok"] is True


def test_chat_understands_every_n_minutes_simulation(client):
    meta = client.get("/api/bus/meta").json()
    info, date = meta["routes"][0], meta["dates"]["max_date"]
    interval = (info["min_interval"] + info["max_interval"]) / 2
    response = client.post("/api/bus/chat", json={"message": f"{info['route']}번 {date} 8시에 {interval}분마다 운행하면 어떻게 달라져?"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "simulate"
    assert body["tools"][0]["args"]["interval"] == interval
    assert body["tools"][0]["ok"] is True


@pytest.mark.parametrize("with_history", [False, True])
@pytest.mark.parametrize("topic", ["점심 도시락 메뉴", "여행 숙소", "선물"])
def test_route_number_does_not_make_unrelated_recommendation_dispatch(client, with_history, topic):
    meta = client.get("/api/bus/meta").json()
    route, date = meta["routes"][0]["route"], meta["dates"]["max_date"]
    history = [{"role": "user", "content": f"{route}번 {date} 8시 승객 예측해줘"}] if with_history else []
    body = client.post("/api/bus/chat", json={"message": f"{route}번 담당자에게 줄 {topic} 추천해줘", "history": history}).json()
    assert body["intent"] == "out_of_scope"
    assert body["tools"] == [] and body["sources"] == []


# ==================================================
# PR #31 리뷰에서 나온 분류 회귀 (오정훈)
# ==================================================

@pytest.mark.parametrize("message, expected", [
    # '차 + 넣/빼'가 다른 낱말 안의 '차'와 '빼곡'에 걸리지 않음
    ("143번 첫차 빼고 몇 시에 와?", "route_info"),
    ("160번 막차 빼면 마지막 버스는?", "route_info"),
    ("150번 주차 빼고 정류장 몇 개야?", "route_info"),
    ("143번 버스 빼곡해?", None),
    # 의도한 표현은 그대로 배차 권고
    ("143번 출근 시간에 차를 더 넣어야 할까?", "advise"),
    ("272번 시내버스를 더 넣는 게 좋을까?", "advise"),
    ("143번 차 몇 대 빼도 돼?", "advise"),
    # 노선과 시간을 대고 '추천'만 해도 배차 권고
    ("150번 내일 8시 추천해줘", "advise"),
    ("272번 출근 시간 대책 추천", "advise"),
    ("160번 내일 저녁 운영 추천해줘", "advise"),
    # 시간이 없는 업무 밖 추천은 그대로 분류 없음
    ("143번 담당자에게 줄 점심 도시락 메뉴 추천해줘", None),
    ("143번 담당자에게 줄 선물 추천해줘", None),
])
def test_review_regressions_in_dispatch_expressions(message, expected):
    assert classify(message, True) == expected


@pytest.mark.parametrize("message, expected", [
    # PR #32 리뷰: '차' 앞이 한글이면 제외하도록 고치면서 '배차'의 '차'까지 막혀 분류 없음이 됐던 문장
    ("배차를 더 넣어야 해 143번 8시", "advise"),
    ("143번 배차 더 넣어", "advise"),
    ("143번 8시 배차 좀 빼도 돼?", "advise"),
    # '배차'를 살려도 다른 낱말 안의 '차'는 그대로 제외
    ("143번 첫차 빼고 몇 시에 와?", "route_info"),
    ("150번 주차 빼고 정류장 몇 개야?", "route_info"),
    ("143번 버스 빼곡해?", None),
    # 시간 낱말 '새벽'도 아침·저녁과 같게 봄 (PR #32 리뷰 LOW 1)
    ("143번 새벽 추천해줘", "advise"),
    ("143번 담당자에게 줄 점심 도시락 메뉴 추천해줘", None),
])
def test_dispatch_word_keeps_vehicle_expression(message, expected):
    assert classify(message, True) == expected


def test_recommend_without_route_is_not_dispatch():
    assert classify("2026-10-31 부산 여행 숙소 추천해줘", False) is None
    assert classify("내일 8시 맛집 추천해줘", False) is None
