# 배차 방식 성능 비교 결과 API (F-14 → 대시보드, park-next P-4)
import json

import pytest

from app.domains.bus import backtest


def _metrics(pct):
    return {"cells": 100, "route_days": 10, "overcrowded_cells": int(pct), "overcrowded_pct": pct, "total_trips": 1000.0,
            "trips_vs_current_pct": 0.0, "changed_cells": 0, "fleet_over_days": 0, "fleet_over_new_days": 0}


@pytest.fixture
def result_file(tmp_path, monkeypatch):
    path = tmp_path / "backtest.json"
    monkeypatch.setattr(backtest.backtest_summary, "__defaults__", (path,))
    return path


def test_backtest_api_returns_methods_in_order(client, result_file):
    # 준비
    result_file.write_text(json.dumps({
        "tune_window": {"start": "2026-09-01", "end": "2026-09-28", "days": 28},
        "eval_window": {"start": "2026-09-30", "end": "2026-11-01", "days": 32},
        "crowded_threshold": 0.8,
        "weights_chosen": {"overload": 10, "trips": 1, "change": 1},
        "pick_order": ["새로 생긴 인가대수 초과 0건"],
        "eval": {"최적화": _metrics(8.3), "현행": _metrics(20.7), "그리디": _metrics(8.7), "규칙": _metrics(12.1)},
        "eval_reference": {"label": "최적화 (감차 허용, 참고)", "weights": {"overload": 10, "trips": 1, "change": 0},
                           "metrics": _metrics(8.6), "why": "참고 값"},
        "notes": ["채점에만 실제 승객 사용"],
    }, ensure_ascii=False), encoding="utf-8")

    # 실행
    r = client.get("/api/bus/backtest")

    # 확인
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["name"] for m in body["methods"]] == ["현행", "규칙", "그리디", "최적화", "감차 허용"]
    assert body["methods"][3]["weights"] == {"overload": 10, "trips": 1, "change": 1}
    assert body["methods"][4]["reference"] is True and body["methods"][0]["weights"] is None
    assert body["methods"][0]["overcrowded_pct"] == 20.7 and body["caution"]


def test_backtest_api_without_result_file_says_how_to_make_it(client, result_file):
    r = client.get("/api/bus/backtest")
    assert r.status_code == 404
    assert "scripts/backtest.py" in r.json()["detail"]


def test_backtest_api_rejects_broken_file(client, result_file):
    result_file.write_text('{"eval": {}}', encoding="utf-8")
    r = client.get("/api/bus/backtest")
    assert r.status_code == 500 and "형식" in r.json()["detail"]


def test_committed_backtest_result_matches_schema():
    # 저장소에 있는 실제 결과 파일도 화면이 읽을 수 있는 형식인지
    body = backtest.backtest_summary()
    assert [m["name"] for m in body["methods"]][:4] == list(backtest.METHODS)
