# 실시간 사고·정체 알림 (이슈 #42 방법 2, Task P-9). 외부 HTTP 없이 가짜 응답으로만 돕니다 (conftest가 topis._get을 막음)
import datetime as dt
import json

import httpx
import pandas as pd
import pytest

import numpy as np

from app.config import TOPIS_LINK_SPEED, TOPIS_ROUTE_LINKS, TOPIS_ROUTE_SEGMENTS
from app.domains.bus import topis, traffic
from app.domains.bus.traffic import KST, TrafficAlerts, plan_from

SECRET = "TESTKEY-SHOULD-NOT-LEAK"
NOW = dt.datetime(2026, 11, 28, 14, 0, tzinfo=KST)        # 토요일 14시

# 동서로 곧은 노선 (약 3.5km)
GEO = {
    "route": "T",
    "path": [[37.55, 126.95 + i * 0.004] for i in range(11)],
    "stops": [{"seq": 1, "name": "서쪽 끝", "lat": 37.55, "lng": 126.95},
              {"seq": 2, "name": "가운데", "lat": 37.55, "lng": 126.97},
              {"seq": 3, "name": "동쪽 끝", "lat": 37.55, "lng": 126.99}],
}


def acc_row(acc_id, lat, lng, start="20261128|1300", end="20261128|180000", link="", kind="A01", dkind="01B01"):
    x, y = topis.wgs_to_tm(lat, lng)
    (sd, st), (ed, et) = start.split("|"), end.split("|")
    return {"acc_id": acc_id, "occr_date": sd, "occr_time": st, "exp_clr_date": ed, "exp_clr_time": et,
            "acc_type": kind, "acc_dtype": dkind, "link_id": link, "grs80tm_x": f"{x}", "grs80tm_y": f"{y}",
            "acc_info": f"시험 돌발 {acc_id}"}


ROWS = [
    acc_row("near", 37.5509, 126.97),                                  # 경로에서 약 100m
    acc_row("far", 37.556, 126.97),                                    # 약 670m
    acc_row("far_on_link", 37.556, 126.98, link="L2"),                 # 멀어도 노선 링크 위
    acc_row("cleared", 37.5502, 126.96, end="20261128|120000"),        # 이미 해제
    acc_row("long", 37.5501, 126.985, end="20270301|000000", kind="A04", dkind="04B01"),   # 장기 공사
]


# ==================================================
# 1. 좌표 · 응답 읽기 · 호출
# ==================================================

def test_tm_origin_and_round_trip():
    assert topis.wgs_to_tm(38.0, 127.0) == pytest.approx((200000.0, 500000.0), abs=1e-6)
    for lat, lng in [(37.5665, 126.9780), (37.65, 127.1), (37.45, 126.85)]:
        assert topis.tm_to_wgs(*topis.wgs_to_tm(lat, lng)) == pytest.approx((lat, lng), abs=1e-8)


def test_tm_matches_real_topis_points():
    # 2026-11-28 AccInfo 실제 좌표 → 봉은교·여의도·노원교 근처
    assert topis.tm_to_wgs(206076.71, 446245.70) == pytest.approx((37.5157, 127.0687), abs=2e-3)
    assert topis.tm_to_wgs(192689.50, 447653.04) == pytest.approx((37.5283, 126.9173), abs=2e-3)
    assert topis.tm_to_wgs(204508.48, 464102.41) == pytest.approx((37.6766, 127.0511), abs=2e-3)


def test_parse_ok_empty_and_error():
    ok = ("<AccInfo><list_total_count>1</list_total_count><RESULT><CODE>INFO-000</CODE><MESSAGE>정상</MESSAGE></RESULT>"
          "<row><acc_id>1</acc_id><acc_info> 공사 </acc_info></row></AccInfo>")
    assert topis.parse("AccInfo", ok) == ([{"acc_id": "1", "acc_info": "공사"}], 1)
    empty = "<TrafficInfo><RESULT><CODE>INFO-200</CODE><MESSAGE>해당하는 데이터가 없습니다.</MESSAGE></RESULT></TrafficInfo>"
    assert topis.parse("TrafficInfo", empty) == ([], 0)
    with pytest.raises(topis.TopisError) as e:
        topis.parse("AccInfo", "<response><RESULT><CODE>INFO-100</CODE><MESSAGE>인증키가 유효하지 않습니다</MESSAGE></RESULT></response>")
    assert e.value.status == "topis_key_invalid"
    with pytest.raises(topis.TopisError):
        topis.parse("AccInfo", "<html>에러가 발생했습니다")


def test_client_without_key_does_not_call():
    with pytest.raises(topis.TopisError) as e:
        topis.TopisClient(key="").call("AccInfo", 1, 5)
    assert e.value.status == "topis_key_missing"


def test_timeout_becomes_topis_error_without_key_in_message(monkeypatch, caplog):
    def slow(url, timeout):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(topis, "_get", slow)
    with pytest.raises(topis.TopisError) as e:
        topis.TopisClient(key=SECRET).call("AccInfo", 1, 5)
    assert e.value.status == "topis_timeout"
    assert SECRET not in e.value.detail and SECRET not in caplog.text
    assert topis.mask(f"http://openapi.seoul.go.kr:8088/{SECRET}/xml/AccInfo/1/5/") == "http://openapi.seoul.go.kr:8088/***/xml/AccInfo/1/5/"


def test_httpx_request_log_is_silenced_even_when_script_logs_info(caplog):
    # 재현: build_topis_links.py가 logging.basicConfig(INFO)를 쓰자 httpx가 "HTTP Request: GET …/{키}/xml/…"을 찍음 (2026-11-28)
    import logging
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info("HTTP Request: GET http://openapi.seoul.go.kr:8088/%s/xml/AccInfo/1/5/", SECRET)
    assert SECRET not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING


# ==================================================
# 2. 돌발 · 정체 · 버스 대수
# ==================================================

def test_route_incidents_near_link_active_and_long_term():
    incidents = traffic.clean_incidents(ROWS, {"A01": "교통사고", "01B01": "추돌사고", "A04": "공사"})
    xy = traffic.path_xy(GEO["path"])
    now_items, long_items = traffic.route_incidents(incidents, GEO, xy, {"L1", "L2"}, NOW)
    assert [i["acc_id"] for i in now_items] == ["near", "far_on_link"]      # 가까운 순, far·cleared 빠짐
    assert now_items[0]["distance_m"] == pytest.approx(100, abs=10)
    assert now_items[0]["near_stop"] == {"seq": 2, "name": "가운데"}
    assert now_items[0]["dtype_name"] == "추돌사고"
    assert [i["acc_id"] for i in long_items] == ["long"] and long_items[0]["long_term"]


def test_incident_time_formats():
    # occr_time이 4자리(HHMM)·6자리(HHMMSS) 둘 다 옴
    a, b = traffic.clean_incidents([acc_row("a", 37.55, 126.97, start="20261128|1300"),
                                    acc_row("b", 37.55, 126.97, start="20261128|130000")])
    assert a["start"] == b["start"] == "2026-11-28T13:00:00+09:00"


LINKS = pd.DataFrame({"link_id": ["L1", "L2"], "length_m": [1000.0, 1000.0]})
USUAL = {"L1": 20.0, "L2": 20.0}          # 20km/h → 1,000m에 180초


def test_extra_minutes_and_low_coverage():
    r = traffic.extra_minutes(LINKS, {"L1": 600, "L2": 600}, USUAL)
    assert r["extra_minutes"] == pytest.approx((1200 - 360) / 60, abs=0.1) and r["coverage"] == 1.0
    low = traffic.extra_minutes(LINKS, {"L1": 600}, USUAL)                  # 절반만 받음 (< 70%)
    assert low["extra_minutes"] is None and low["links_used"] == 1
    faster = traffic.extra_minutes(LINKS, {"L1": 100, "L2": 100}, USUAL)   # 평소보다 빠르면 0
    assert faster["extra_minutes"] == 0


def test_fleet_check_warns_when_extra_exceeds_authorized():
    trips, run = [6.0] * 24, [120] * 24
    usual = traffic.fleet_check(trips, run, 14, None, 12)
    assert usual["buses_usual"] == usual["buses_est"] == 12 and not usual["fleet_short"]
    late = traffic.fleet_check(trips, run, 14, 14.0, 12)
    assert late["est_run"] == 134 and late["buses_est"] > 12 and late["fleet_short"]
    assert traffic.delayed_run(run, 14, 14.0)[11] == 120          # 지금 도로 위에 없는 시간대는 그대로


def test_slow_warning_is_15_percent_of_usual_run():
    # 평소 233분(143번)이면 35분부터. 10분 고정이던 때는 토요일 저녁 +24분에도 경고였음
    base = {"usual_run": 233, "usual_minutes": 207.0, "fleet_short": False}
    assert not traffic.is_slow({**base, "extra_minutes": 24.0})
    assert traffic.is_slow({**base, "extra_minutes": 35.0})
    assert not any("평소보다" in m for m in traffic.messages_for("143", [], {**base, "extra_minutes": 24.0}))
    assert any("약 35분 더" in m for m in traffic.messages_for("143", [], {**base, "extra_minutes": 35.0}))
    # 버스 한 바퀴를 모르면 같은 링크의 평소 차량 통행시간 기준
    assert traffic.is_slow({"usual_run": None, "usual_minutes": 100.0, "extra_minutes": 15.0})


# ==================================================
# 3. 실시간 → 기록 → 다시 보기
# ==================================================

class FakeClient(topis.TopisClient):
    def __init__(self, link_seconds):
        super().__init__(key=SECRET)
        self.link_seconds = link_seconds
        self.calls = []

    def call(self, service, start, end, *args):
        self.calls.append(service)
        if service == "AccInfo":
            return ROWS, len(ROWS)
        if service == "AccMainCode":
            return [{"acc_type": "A01", "acc_type_nm": "교통사고"}], 1
        if service == "AccSubCode":
            return [{"acc_dtype": "01B01", "acc_dtype_nm": "추돌사고"}], 1
        if service == "TrafficInfo":
            sec = self.link_seconds.get(args[0])
            if sec is None:
                raise topis.TopisError("topis_timeout", "시간 초과")
            return [{"link_id": args[0], "prcs_spd": "10", "prcs_trv_time": str(sec)}], 1
        raise AssertionError(service)


@pytest.fixture
def alerts(tmp_path):
    LINKS.assign(route="T").to_parquet(tmp_path / TOPIS_ROUTE_LINKS, index=False)
    pd.DataFrame({"link_id": ["L1", "L2"], "daytype": "saturday", "hour": 14, "speed_kmh": 20.0, "days": 8}) \
        .to_parquet(tmp_path / TOPIS_LINK_SPEED, index=False)
    # 지도 소통 띠용 링크 ↔ 정류장 구간 표: L1은 1구간, L2는 1·2구간에 반씩
    pd.DataFrame({"route": "T", "link_id": ["L1", "L2", "L2"], "seq": [1, 1, 2], "share": [1.0, 0.5, 0.5]}) \
        .to_parquet(tmp_path / TOPIS_ROUTE_SEGMENTS, index=False)
    plan = plan_from([6.0] * 24, 120, 12, "시험 계획")
    a = TrafficAlerts(tmp_path, lambda route: GEO if route == "T" else None, lambda *_: plan,
                      client=FakeClient({"L1": 600, "L2": 600}), clock=lambda: NOW)
    return a


def test_live_builds_alert_saves_snapshot_and_replays(alerts):
    live = alerts.live(["T"])
    route = live["routes"][0]
    assert live["source"] == "live" and live["needs_review"] is True
    assert [i["acc_id"] for i in route["incidents"]] == ["near", "far_on_link"]
    assert route["traffic"]["extra_minutes"] == pytest.approx(14.0, abs=0.1)
    assert route["traffic"]["fleet_short"] is True
    assert any("인가대수 부족 가능" in m for m in route["messages"])
    assert route["messages"][-1].startswith("배차는 바꾸지 않았습니다")
    # 1,000m 링크를 평소 180초 → 지금 600초: 평소 속도의 30% → 구간 속도 비율 0.3, 두 링크 모두 많이 느린 도로
    assert route["segments"] == [{"seq": 1, "ratio": 0.3, "length_m": 1500}, {"seq": 2, "ratio": 0.3, "length_m": 500}]
    assert [s["link_id"] for s in route["slow_links"]] == ["L1", "L2"] and route["slow_links"][0]["extra_seconds"] == 420
    assert live["ready"]["route_segments"] is True

    files = list(alerts.snapshot_dir.glob("*.json"))
    assert len(files) == 1 and SECRET not in files[0].read_text(encoding="utf-8")

    again = alerts.replay(["T"], None)
    assert again["source"] == "replay" and again["snapshot"] == live["snapshot"]
    assert again["routes"] == live["routes"]


def test_live_reuses_cache(alerts):
    alerts.live(["T"])
    n = len(alerts.client.calls)
    alerts.live(["T"])
    assert len(alerts.client.calls) == n


def test_failed_links_warn_and_skip_estimate(alerts):
    alerts.client.link_seconds = {"L1": 600}         # L2 실패 → 받은 비율 50%
    result = alerts.live(["T"])
    assert result["routes"][0]["traffic"]["extra_minutes"] is None
    assert any("2개 중 1개" in w for w in result["warnings"])


def test_without_tables_only_incidents(tmp_path):
    a = TrafficAlerts(tmp_path, lambda route: GEO, lambda *_: None, client=FakeClient({}), clock=lambda: NOW)
    result = a.live(["T"])
    assert result["routes"][0]["traffic"] is None and result["routes"][0]["incidents"]
    assert any("링크 표가 없어" in w for w in result["warnings"])


def test_without_segment_table_warns_and_skips_band(alerts):
    (alerts.base / TOPIS_ROUTE_SEGMENTS).unlink()
    result = alerts.live(["T"])
    assert result["routes"][0]["segments"] == [] and result["routes"][0]["traffic"]["extra_minutes"] is not None
    assert any("정류장 구간 표가 없어" in w for w in result["warnings"]) and result["ready"]["route_segments"] is False


def test_replay_rejects_bad_names(alerts):
    assert alerts.replay(["T"], "../../etc/passwd") is None
    assert alerts.replay(["T"], "20990101_0000") is None


# ==================================================
# 4. API
# ==================================================

@pytest.fixture
def bus_adapter(client, tmp_path, monkeypatch):
    from app.core.registry import registry
    adapter = registry.get("bus")
    a = TrafficAlerts(tmp_path, adapter.dataset.geo, adapter._traffic_plan, client=topis.TopisClient(key=""))
    monkeypatch.setitem(adapter.__dict__, "traffic", a)
    return adapter


def test_api_live_without_key_is_503(client, bus_adapter):
    r = client.get("/api/bus/traffic/alerts")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable" and "SEOUL_OPEN_API_KEY" in r.json()["detail"]


def test_api_replay_snapshot_and_errors(client, bus_adapter):
    assert client.get("/api/bus/traffic/alerts", params={"mode": "replay"}).status_code == 404
    route = bus_adapter.dataset.routes[0]
    geo = bus_adapter.dataset.geo(route)
    lat, lng = geo["path"][len(geo["path"]) // 2]
    x, y = topis.wgs_to_tm(lat, lng)
    snap = {"fetched_at": NOW.isoformat(), "link_seconds": {}, "warnings": [],
            "incidents": traffic.clean_incidents([{**acc_row("s1", lat, lng), "grs80tm_x": str(x), "grs80tm_y": str(y)}])}
    topis.save_snapshot(bus_adapter.traffic.snapshot_dir, "20261128_140000", snap)

    r = client.get("/api/bus/traffic/alerts", params={"mode": "replay", "routes": route})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "replay" and body["needs_review"] is True and body["snapshot"] == "20261128_140000"
    assert [i["acc_id"] for i in body["routes"][0]["incidents"]] == ["s1"]
    assert body["ready"] == {"route_links": False, "link_speed": False, "route_segments": False, "api_key": False}
    assert body["routes"][0]["segments"] == [] and body["routes"][0]["slow_links"] == []

    items = client.get("/api/bus/traffic/snapshots").json()["items"]
    assert items == [{"name": "20261128_140000", "fetched_at": NOW.isoformat(), "incidents": 1, "links": 0}]

    assert client.get("/api/bus/traffic/alerts", params={"mode": "replay", "snapshot": "20200101_0000"}).status_code == 404
    assert client.get("/api/bus/traffic/alerts", params={"mode": "nope"}).status_code == 422
    assert client.get("/api/bus/traffic/alerts", params={"mode": "replay", "routes": "999999"}).status_code == 404
    assert json.dumps(body).find(SECRET) == -1


# ==================================================
# 5. 노선 ↔ 링크 대응 (scripts/build_topis_links.py, 키 없이 계산만)
# ==================================================

def test_match_route_keeps_same_direction_links_only():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("build_topis_links", Path(__file__).resolve().parents[1] / "scripts/build_topis_links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    xy = traffic.path_xy(GEO["path"])                     # 서쪽 → 동쪽
    (x0, y0), (x1, _) = xy[2], xy[4]
    rows = []
    for link_id, pts in {"same": [(x0, y0 + 5), (x1, y0 + 5)],          # 같은 방향, 5m 옆
                         "opposite": [(x1, y0 - 5), (x0, y0 - 5)],      # 반대 방향
                         "far": [(x0, y0 + 200), (x1, y0 + 200)]}.items():  # 200m 떨어짐
        rows += [{"link_id": link_id, "ver_seq": i + 1, "x": px, "y": py} for i, (px, py) in enumerate(pts)]
    links = pd.DataFrame({"link_id": ["same", "opposite", "far"], "length_m": [700.0] * 3, "road_name": ["시험로"] * 3})
    out = mod.match_route(xy, pd.DataFrame(rows), links)
    assert out["link_id"].tolist() == ["same"] and out["length_m"].tolist() == [700.0]


# ==================================================
# 6. 지도 소통 띠: 링크 → 정류장 구간, 구간 속도 비율, 많이 느린 도로
# ==================================================

def test_assign_segments_splits_links_and_keeps_direction():
    segments = [(1, np.array([[0.0, 0.0], [100.0, 0.0]])), (2, np.array([[100.0, 0.0], [200.0, 0.0]]))]
    links = {
        "inside": np.array([[10.0, 5.0], [50.0, 5.0], [90.0, 5.0]]),                 # 1구간 안, 같은 방향
        "across": np.array([[60.0, 5.0], [100.0, 5.0], [140.0, 5.0], [180.0, 5.0]]),  # 1·2구간에 걸침
        "reverse": np.array([[90.0, 5.0], [10.0, 5.0]]),                              # 반대 방향 차로
        "far": np.array([[10.0, 200.0], [90.0, 200.0]]),                              # 200m 떨어짐
    }
    out = traffic.assign_segments(segments, links)
    shares = {(r.link_id, r.seq): r.share for r in out.itertuples()}
    assert shares[("inside", 1)] == 1.0
    assert shares[("across", 1)] + shares[("across", 2)] == pytest.approx(1.0) and shares[("across", 2)] > shares[("across", 1)]
    assert not {"reverse", "far"} & set(out["link_id"])


def test_segment_speeds_weights_by_length_and_skips_missing():
    seg_links = pd.DataFrame({"link_id": ["L1", "L2", "L2", "L3", "L4"], "seq": [1, 1, 2, 3, 4], "share": [1.0, 0.5, 0.5, 1.0, 1.0]})
    lengths = {"L1": 1000.0, "L2": 1000.0, "L3": 1000.0, "L4": 40.0}
    usual = {"L1": 20.0, "L2": 20.0, "L4": 20.0}                 # L3는 평소 속도 없음
    live = {"L1": 360.0, "L2": 180.0, "L3": 100.0, "L4": 7.2}    # L1은 평소의 절반 속도
    out = traffic.segment_speeds(seg_links, lengths, live, usual)
    # 1구간: 평소 180+90=270초 / 지금 360+90=450초 = 0.6, 2구간: 평소대로 1.0, 3구간(평소 없음)·4구간(40m) 빠짐
    assert out == [{"seq": 1, "ratio": 0.6, "length_m": 1500}, {"seq": 2, "ratio": 1.0, "length_m": 500}]


def test_slow_links_only_below_two_thirds_and_most_delay_first():
    links = pd.DataFrame({"link_id": ["a", "b", "c", "d"], "length_m": [1000.0, 500.0, 1000.0, 1000.0],
                          "road_name": ["가로", "나로", "다로", "라로"]})
    usual = {"a": 20.0, "b": 20.0, "c": 20.0}
    live = {"a": 600.0, "b": 300.0, "c": 240.0, "d": 900.0}      # a 6km/h, b 6km/h, c 15km/h(75%, 제외), d 평소 없음
    out = traffic.slow_links(links, live, usual)
    assert [(s["link_id"], s["road_name"], s["extra_seconds"]) for s in out] == [("a", "가로", 420), ("b", "나로", 210)]
    assert out[0]["live_kmh"] == 6.0 and out[0]["usual_kmh"] == 20.0
