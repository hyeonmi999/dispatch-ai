# ==================================================
# 이력 저장·조회
# ==================================================
# 저장 실패는 서비스 응답을 막지 않습니다 (None 반환 → 응답에 db_saved: false).

import datetime as dt

from sqlalchemy import select

from app.config import LIST_LIMIT_MAX
from app.db import database as db
from app.db.database import AdvisoryBatch, AdvisoryLog, ChatLog, OptimizationRun, PredictionLog, QALog, RetrainReview


async def _add(row):
    if not db.state["available"]:
        return None
    try:
        async with db.SessionLocal() as session:
            session.add(row)
            await session.commit()
            return row.id
    except Exception as error:
        print(f"⚠️ DB 저장 실패: {error}")
        return None


async def save_prediction(domain: str, result: dict) -> int | None:
    s = result.get("summary", {})
    return await _add(PredictionLog(
        domain=domain,
        target=result.get("route", ""),
        target_date=dt.date.fromisoformat(result["date"]),
        hour=result.get("hour"),
        predicted=s.get("pred_total"),
        baseline=s.get("baseline_total"),
        actual=s.get("actual_total"),
        is_forecast=bool(result.get("is_forecast")),
        model_version=result.get("model_version", ""),
        detail={"hourly": result.get("hourly")},
    ))


async def save_advisory(domain: str, result: dict, llm: str) -> int | None:
    rec, cur, con = result["recommendation"], result["current"], result["congestion"]
    return await _add(AdvisoryLog(
        domain=domain,
        target=result.get("route", ""),
        target_date=dt.date.fromisoformat(result["date"]),
        hour=result.get("hour"),
        predicted=result["prediction"].get("boarding_total"),
        congestion=con.get("ratio"),
        congestion_level=con.get("level", ""),
        action=rec.get("action", ""),
        current_interval=cur.get("interval_min"),
        recommended_interval=rec.get("recommended_interval_min"),
        extra_buses=rec.get("extra_buses"),
        confidence=result.get("confidence"),
        needs_review=True,
        review_status="pending",
        llm=llm if result["evidence"].get("llm") else "",
        evidence=result.get("evidence"),
        payload=result,
    ))


async def save_batch(domain: str, date: str, hour: int, llm: str, summary: dict, advisory_ids: list[int]) -> int | None:
    return await _add(AdvisoryBatch(
        domain=domain, target_date=dt.date.fromisoformat(date), hour=hour, llm=llm,
        summary=summary, advisory_ids=advisory_ids,
    ))


async def _review_counts(session, ids: list[int]) -> dict:
    if not ids:
        return {}
    rows = (await session.execute(select(AdvisoryLog.review_status).where(AdvisoryLog.id.in_(ids)))).scalars().all()
    out: dict = {}
    for s in rows:
        out[s] = out.get(s, 0) + 1
    return out


async def list_batches(domain: str | None, limit: int = 20) -> list[dict]:
    """권고 이력 대표 항목: '{날짜} {시}시 전체 노선 권고' 단위"""
    if not db.state["available"]:
        return []
    async with db.SessionLocal() as session:
        q = select(AdvisoryBatch).order_by(AdvisoryBatch.id.desc()).limit(limit)
        if domain:
            q = q.where(AdvisoryBatch.domain == domain)
        rows = (await session.execute(q)).scalars().all()
        return [{
            "id": b.id, "domain": b.domain, "date": f"{b.target_date}", "hour": b.hour, "llm": b.llm,
            "summary": b.summary or {}, "count": len(b.advisory_ids or []),
            "reviews": await _review_counts(session, b.advisory_ids or []),
            "created_at": f"{b.created_at}",
        } for b in rows]


async def get_batch(batch_id: int) -> dict | None:
    """묶음 1건 + 노선별 저장된 권고 전체(현재 검토 상태 반영)"""
    if not db.state["available"]:
        return None
    async with db.SessionLocal() as session:
        b = await session.get(AdvisoryBatch, batch_id)
        if b is None:
            return None
        ids = b.advisory_ids or []
        rows = (await session.execute(select(AdvisoryLog).where(AdvisoryLog.id.in_(ids)))).scalars().all() if ids else []
    by_id = {r.id: r for r in rows}
    items = []
    for i in ids:                                   # 저장 순서(긴급도 순) 유지
        r = by_id.get(i)
        if r is None:
            continue
        p = dict(r.payload or {})
        p.update({"advisory_id": r.id, "review_status": r.review_status, "reviewer": r.reviewer,
                  "reviewer_note": r.reviewer_note, "db_saved": True, "status": "ok"})
        items.append(p)
    return {"batch_id": b.id, "date": f"{b.target_date}", "hour": b.hour, "llm": b.llm,
            "summary": b.summary or {}, "created_at": f"{b.created_at}", "items": items}


async def save_qa(domain: str, result: dict) -> int | None:
    return await _add(QALog(
        domain=domain,
        question=result["question"],
        answer=result.get("answer"),
        found=bool(result.get("found")),
        sources=result.get("sources"),
        llm=result.get("llm"),
        embedding=result.get("embedding", ""),
        elapsed_sec=result.get("elapsed_sec"),
    ))


LOCATION_KEYS = ("lat", "lng")


def without_location(result: dict) -> tuple[dict, list]:
    """대화 기록에 남길 slots·tools에서 현재 위치 좌표를 뺌 (썼다는 사실만 남김). 응답 자체는 바꾸지 않음"""
    slots = {k: v for k, v in (result.get("slots") or {}).items() if k not in LOCATION_KEYS}
    if any(k in (result.get("slots") or {}) for k in LOCATION_KEYS):
        slots["location_used"] = True
    tools = [{**t, "args": {k: v for k, v in (t.get("args") or {}).items() if k not in LOCATION_KEYS}}
             for t in result.get("tools") or []]
    return slots, tools


async def save_chat(domain: str, question: str, result: dict) -> int | None:
    slots, tools = without_location(result)
    return await _add(ChatLog(
        domain=domain,
        question=question,
        answer=result["answer"],
        intent=result["intent"],
        slots=slots,
        tools=tools,
        sources=result.get("sources"),
        numbers_verified=result.get("numbers_verified"),
        llm=result.get("llm"),
        llm_rejected=bool(result.get("llm_rejected")),
        elapsed_sec=result.get("elapsed_sec"),
    ))


def same_optimization_run(last: dict, result: dict, hours: list[int]) -> bool:
    """직전 실행(list_optimization_runs의 한 줄)과 날짜·시간대·가중치·결과 요약이 모두 같으면 True"""
    kpi = result.get("kpi") or {}
    weights = {k: float(v) for k, v in (result.get("weights") or {}).items()}
    return (last.get("date") == result.get("date")
            and list(last.get("hours") or []) == hours
            and {k: float(v) for k, v in (last.get("weights") or {}).items()} == weights
            and last.get("status") == result.get("status")
            and all(last.get(k) == kpi.get(k) for k in ("overcrowded_before", "overcrowded_after", "extra_trips")))


async def save_optimization_run(domain: str, result: dict, hours: list[int] | None = None) -> int | None:
    """result: POST /api/bus/optimize 응답 (docs/contracts.md 2번)
    hours: 요청한 시간대. 운행이 없어 plan에서 빠진 시간대도 남기려고 따로 받음 (없으면 plan에서 뽑음)
    직전 실행과 같은 조건·같은 결과면 새 줄을 만들지 않고 직전 id를 돌려줌 (화면을 오가며 같은 최적화를 다시 불러도 이력이 쌓이지 않게)"""
    kpi = result.get("kpi") or {}
    hours = sorted(set(hours)) if hours else sorted({item["hour"] for item in result.get("plan") or []})
    try:
        last = (await list_optimization_runs(domain, 1) or [None])[0]
    except Exception as error:          # 직전 기록을 못 읽어도 저장은 시도 (실패하면 _add가 None)
        print(f"⚠️ 직전 최적화 기록 조회 실패: {error}")
        last = None
    if last and same_optimization_run(last, result, hours):
        return last["id"]
    return await _add(OptimizationRun(
        domain=domain,
        target_date=dt.date.fromisoformat(result["date"]),
        hours=hours,
        weights=result.get("weights"),
        method=result.get("method", ""),
        status=result.get("status", ""),
        solve_seconds=result.get("solve_seconds"),
        overcrowded_before=kpi.get("overcrowded_before"),
        overcrowded_after=kpi.get("overcrowded_after"),
        extra_trips=kpi.get("extra_trips"),
        kpi=kpi,
        plan=result.get("plan"),
        binding=result.get("binding"),
    ))


async def list_optimization_runs(domain: str | None, limit: int = 20) -> list[dict]:
    if not db.state["available"]:
        return []
    async with db.SessionLocal() as session:
        query = select(OptimizationRun).order_by(OptimizationRun.id.desc()).limit(min(max(limit, 1), LIST_LIMIT_MAX))
        if domain:
            query = query.where(OptimizationRun.domain == domain)
        rows = (await session.execute(query)).scalars().all()
    return [{"id": r.id, "date": f"{r.target_date}", "hours": r.hours or [], "weights": r.weights or {},
             "status": r.status, "solve_seconds": r.solve_seconds, "overcrowded_before": r.overcrowded_before,
             "overcrowded_after": r.overcrowded_after, "extra_trips": r.extra_trips,
             "created_at": f"{r.created_at}"} for r in rows]


async def list_advisories(domain: str | None, limit: int = 20) -> list[dict]:
    if not db.state["available"]:
        return []
    async with db.SessionLocal() as session:
        q = select(AdvisoryLog).order_by(AdvisoryLog.id.desc()).limit(limit)
        if domain:
            q = q.where(AdvisoryLog.domain == domain)
        rows = (await session.execute(q)).scalars().all()
    return [{
        "id": r.id, "domain": r.domain, "target": r.target, "date": f"{r.target_date}", "hour": r.hour,
        "predicted": r.predicted, "congestion": r.congestion, "level": r.congestion_level, "action": r.action,
        "current_interval": r.current_interval, "recommended_interval": r.recommended_interval,
        "extra_buses": r.extra_buses, "confidence": r.confidence, "needs_review": r.needs_review,
        "review_status": r.review_status, "reviewer": r.reviewer, "reviewer_note": r.reviewer_note,
        "created_at": f"{r.created_at}", "reviewed_at": f"{r.reviewed_at}" if r.reviewed_at else None,
    } for r in rows]


async def get_advisory(advisory_id: int) -> dict | None:
    """저장된 권고 1건 (생성 당시 응답 전체 + 현재 검토 상태)"""
    if not db.state["available"]:
        return None
    async with db.SessionLocal() as session:
        r = await session.get(AdvisoryLog, advisory_id)
    if r is None:
        return None
    payload = dict(r.payload or {})
    payload.update({
        "advisory_id": r.id, "review_status": r.review_status, "reviewer": r.reviewer,
        "reviewer_note": r.reviewer_note, "created_at": f"{r.created_at}",
        "reviewed_at": f"{r.reviewed_at}" if r.reviewed_at else None, "db_saved": True,
    })
    return payload


async def review_advisory(advisory_id: int, status: str, reviewer: str, note: str) -> bool:
    if not db.state["available"]:
        return False
    async with db.SessionLocal() as session:
        row = await session.get(AdvisoryLog, advisory_id)
        if row is None:
            return False
        row.review_status, row.reviewer, row.reviewer_note = status, reviewer, note
        row.reviewed_at = dt.datetime.now()
        await session.commit()
        return True


# ==================================================
# 재학습 권고에 대한 담당자 결정 (요청 R-3)
# ==================================================

def _review_dict(r: RetrainReview) -> dict:
    return {"status": r.status, "reviewer": r.reviewer, "note": r.note or "",
            "reviewed_at": f"{r.reviewed_at:%Y-%m-%d %H:%M:%S}" if r.reviewed_at else None}


async def get_retrain_reviews(domain: str, check_ids: list[str]) -> dict[str, dict]:
    """점검 id → 결정. DB가 꺼져 있으면 빈 dict (서버가 뜬 뒤에 꺼졌으면 예외를 그대로 올림)"""
    if not db.state["available"] or not check_ids:
        return {}
    async with db.SessionLocal() as session:
        query = select(RetrainReview).where(RetrainReview.domain == domain, RetrainReview.check_id.in_(check_ids))
        rows = (await session.execute(query)).scalars().all()
    return {r.check_id: _review_dict(r) for r in rows}


async def save_retrain_review(domain: str, check_id: str, verdict: str, status: str, reviewer: str, note: str) -> dict | None:
    """결정을 남김 (같은 점검이면 고침). DB가 꺼져 있으면 None"""
    if not db.state["available"]:
        return None
    async with db.SessionLocal() as session:
        query = select(RetrainReview).where(RetrainReview.domain == domain, RetrainReview.check_id == check_id)
        row = (await session.execute(query)).scalars().first()
        if row is None:
            row = RetrainReview(domain=domain, check_id=check_id)
            session.add(row)
        row.verdict, row.status, row.reviewer, row.note = verdict, status, reviewer, note
        row.reviewed_at = dt.datetime.now()
        await session.commit()
        return _review_dict(row)
