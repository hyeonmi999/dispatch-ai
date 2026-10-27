# ==================================================
# 이력 저장·조회
# ==================================================
# 저장 실패는 서비스 응답을 막지 않습니다 (None 반환 → 응답에 db_saved: false).

import datetime as dt

from sqlalchemy import select

from app.db import database as db
from app.db.database import AdvisoryLog, PredictionLog, QALog


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
