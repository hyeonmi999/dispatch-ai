# ==================================================
# 이력 저장·조회
# ==================================================
# 저장 실패는 서비스 응답을 막지 않습니다 (None 반환 → 응답에 db_saved: false).

import datetime as dt

from sqlalchemy import select

from app.db import database as db
from app.db.database import AdvisoryBatch, AdvisoryLog, PredictionLog, QALog


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
