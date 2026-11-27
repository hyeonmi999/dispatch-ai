# 예측 오차 점검 → 재학습 권고 (요청 R-3, docs/plans/park-next.md Task P-5)
import pandas as pd
import pytest

from app.domains.bus import drift

TABLE = {"route_default": {"A": 1.2, "B": 1.2}, "ratios": {}}


def _rows(days=7, err=1.0, base_err=3.0, start="2026-11-02"):
    """노선 2개 × 정류장 2곳 × 3시간. 예측은 실측보다 err명 많고, 작년 값은 base_err명 많음"""
    out = []
    for d in pd.date_range(start, periods=days):
        for route in ("A", "B"):
            for stop in ("s1", "s2"):
                for hour in (7, 8, 9):
                    actual = 20.0
                    out.append({"date": d, "route": route, "stop_id": stop, "hour": hour, "boarding": actual,
                                "pred": actual + err, "baseline": actual + base_err,
                                "daytype": "weekday" if d.day_of_week < 5 else "saturday"})
    return pd.DataFrame(out)


def test_judge_passes_when_error_is_like_evaluation():
    result = drift.judge(_rows(err=1.0), kind="forward", reference_mae=1.0, table=TABLE, model_version="m1")
    assert result["verdict"] == "ok" and result["recommend"] is False and result["next_steps"] == []
    assert result["id"] == "forward_2026-11-02_2026-11-08"
    assert result["metrics"]["stop_hour_mae"] == 1.0 and result["metrics"]["mae_ratio"] == 1.0
    assert result["metrics"]["p90_coverage_pct"] == 100.0           # 실제 20 ≤ 예측 21 × 1.2
    assert set(result["by_route"]) == {"A", "B"} and set(result["by_daytype"]) == {"weekday", "saturday"}
    assert result["needs_review"] is True


def test_judge_recommends_retrain_when_error_grows():
    result = drift.judge(_rows(err=1.3), kind="forward", reference_mae=1.0, table=TABLE)
    assert result["verdict"] == "retrain" and result["recommend"] is True
    failed = [c["name"] for c in result["checks"] if c["failed"]]
    assert failed == ["mae_ratio"]
    assert "1.30배" in result["reasons"][0] and result["next_steps"]        # 실행할 순서를 안내


def test_judge_recommends_when_worse_than_last_year():
    result = drift.judge(_rows(err=2.0, base_err=1.5), kind="forward", reference_mae=10.0, table=TABLE)
    assert [c["name"] for c in result["checks"] if c["failed"]] == ["vs_baseline"]
    assert result["recommend"] is True


def test_judge_recommends_when_upper_misses_actual():
    rows = _rows(err=-5.0)                         # 예측이 실측보다 5명 적음 → 상한 15 × 1.2 = 18 < 20
    result = drift.judge(rows, kind="forward", reference_mae=10.0, table=TABLE)
    assert result["metrics"]["p90_coverage_pct"] == 0.0
    assert "p90_coverage" in [c["name"] for c in result["checks"] if c["failed"]]


def test_judge_holds_when_too_few_days_and_skips_p90_without_table():
    result = drift.judge(_rows(days=2, err=5.0), kind="forward", reference_mae=1.0, table=None)
    assert result["verdict"] == "insufficient" and result["recommend"] is False
    assert result["metrics"]["p90_coverage_pct"] is None
    with pytest.raises(ValueError):
        drift.judge(_rows().assign(boarding=float("nan")), kind="forward", reference_mae=1.0, table=None)


def test_weeks_are_monday_to_sunday():
    dates = pd.date_range("2026-09-30", "2026-10-12")
    assert [(f"{a:%m-%d}", f"{b:%m-%d}") for a, b in drift.weeks(dates)] == [
        ("09-30", "10-04"), ("10-05", "10-11"), ("10-12", "10-12")]


def test_check_files_round_trip_and_reject_odd_ids(tmp_path):
    first = drift.judge(_rows(), kind="replay", reference_mae=1.0, table=TABLE)
    later = drift.judge(_rows(start="2026-11-09"), kind="forward", reference_mae=1.0, table=TABLE)
    for r in (first, later):
        drift.save_check(r, tmp_path)
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")       # 점검 결과가 아닌 파일은 무시
    assert [r["id"] for r in drift.list_checks(10, tmp_path)] == [later["id"], first["id"]]
    assert drift.load_check(first["id"], tmp_path)["metrics"] == first["metrics"]
    assert drift.load_check("../backtest", tmp_path) is None
    assert drift.load_check("forward_2026-01-01_2026-01-07", tmp_path) is None


# ==================================================
# API
# ==================================================

@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(drift, "RESULTS_DIR", tmp_path)
    result = drift.judge(_rows(err=1.3), kind="forward", reference_mae=1.0, table=TABLE)
    drift.save_check(result)
    return result


def test_drift_list_api(client, saved):
    body = client.get("/api/bus/drift").json()
    assert body["rules"] == {"mae_ratio": 1.2, "p90_coverage_min": 85.0, "min_days": 3}
    assert [i["id"] for i in body["items"]] == [saved["id"]]
    item = body["items"][0]
    assert item["recommend"] is True and item["review"] is None and item["needs_review"] is True
    assert client.get("/api/bus/drift", params={"limit": 0}).status_code == 422


def test_drift_review_api_without_db_and_unknown_id(client, saved):
    ok = client.post(f"/api/bus/drift/{saved['id']}/review", json={"status": "approved", "reviewer": "담당"})
    assert ok.status_code == 503                         # 테스트는 DB 없이 돎 → 결정을 못 남김
    assert client.post("/api/bus/drift/forward_2020-01-01_2020-01-07/review", json={"status": "approved"}).status_code == 404
    assert client.post(f"/api/bus/drift/{saved['id']}/review", json={"status": "maybe"}).status_code == 422


def test_drift_review_api_saves_decision(client, saved, monkeypatch):
    from app.db import repository as repo
    calls = []

    async def fake_save(domain, check_id, verdict, status, reviewer, note):
        calls.append((domain, check_id, verdict, status, reviewer, note))
        return {"status": status, "reviewer": reviewer, "note": note, "reviewed_at": "2026-11-26 15:00:00"}

    monkeypatch.setattr(repo, "save_retrain_review", fake_save)
    body = client.post(f"/api/bus/drift/{saved['id']}/review",
                       json={"status": "approved", "reviewer": "박성훈", "note": "재학습"}).json()
    assert calls == [("bus", saved["id"], "retrain", "approved", "박성훈", "재학습")]
    assert body["review"]["status"] == "approved" and body["next_steps"]
