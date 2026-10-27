from app.config import NOT_FOUND_ANSWER


def meta(client):
    return client.get("/api/bus/meta").json()


def test_domains_registry(client):
    names = {d["name"]: d["status"] for d in client.get("/api/domains").json()}
    assert names == {"bus": "ready", "logistics": "not_implemented", "field_service": "not_implemented"}


def test_predict(client):
    m = meta(client)
    route, date = m["routes"][0]["route"], m["dates"]["max_date"]
    r = client.post("/api/bus/predict", json={"route": route, "date": date, "hour": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["pred_total"] > 0 and body["stops"]


def test_predict_future_within_horizon(client):
    m = meta(client)
    r = client.post("/api/bus/predict", json={"route": m["routes"][0]["route"], "date": m["dates"]["max_forecast_date"]})
    assert r.status_code == 200 and r.json()["is_forecast"] is True


def test_ask_found_has_sources(client):
    r = client.post("/api/bus/ask", json={"question": "노선버스 운행횟수를 늘리는 사업계획 변경 절차는?", "use_llm": False})
    body = r.json()
    assert body["found"] is True
    assert all(s["document"] and s["article"] for s in body["sources"])


def test_ask_not_found(client):
    r = client.post("/api/bus/ask", json={"question": "오늘 점심 메뉴 추천해줘", "use_llm": False})
    body = r.json()
    assert body["found"] is False and body["answer"] == NOT_FOUND_ANSWER and body["sources"] == []


def test_advise_fields(client):
    m = meta(client)
    r = client.post("/api/bus/advise", json={"route": m["routes"][0]["route"], "date": m["dates"]["max_date"], "hour": 8, "use_llm": False})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["prediction"]["boarding_total"] is not None
    assert a["congestion"]["ratio"] is not None
    assert a["recommendation"]["recommended_interval_min"] > 0
    assert a["evidence"]["sources"]
    assert a["needs_review"] is True


def test_not_implemented_domains(client):
    for domain in ("logistics", "field_service"):
        for ep in ("predict", "advise"):
            r = client.post(f"/api/{domain}/{ep}", json={})
            assert r.status_code == 501 and r.json()["status"] == "not_implemented"


def test_openai_without_key_gives_clear_error(client, monkeypatch):
    import app.core.llm as llm
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    r = client.post("/api/bus/ask", json={"question": "노선버스 운행횟수를 늘리는 사업계획 변경 절차는?", "llm": "openai"})
    assert r.status_code == 400 and "OPENAI_API_KEY" in r.json()["detail"]
