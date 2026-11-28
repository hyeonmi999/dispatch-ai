# ==================================================
# MariaDB (SQLAlchemy async) - 예측·권고·질의 이력 저장
# ==================================================
# docker/maria_db.yml 로 띄운 MariaDB에 aiomysql 드라이버로 접속합니다.
# DB가 꺼져 있어도 API는 동작하고, 응답의 "db_saved": false 로 저장 실패를 알립니다.

from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import ASYNC_DB_URL


class Base(DeclarativeBase):
    pass


# Ground Rule : 모든 테이블명/컬럼명은 소문자

class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    target: Mapped[str] = mapped_column(String(50), index=True)       # 버스: 노선번호
    target_date: Mapped[datetime] = mapped_column(Date)
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AdvisoryLog(Base):
    __tablename__ = "advisory_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    target: Mapped[str] = mapped_column(String(50), index=True)
    target_date: Mapped[datetime] = mapped_column(Date)
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted: Mapped[float | None] = mapped_column(Float, nullable=True)
    congestion: Mapped[float | None] = mapped_column(Float, nullable=True)
    congestion_level: Mapped[str] = mapped_column(String(20), default="")
    action: Mapped[str] = mapped_column(String(30), default="")
    current_interval: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_interval: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_buses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")   # pending/approved/held/rejected
    reviewer: Mapped[str] = mapped_column(String(50), default="")
    reviewer_note: Mapped[str] = mapped_column(Text, default="")
    llm: Mapped[str] = mapped_column(String(20), default="")
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdvisoryBatch(Base):
    """'전체 노선 권고' 1회 실행 = 묶음 1건 (권고 이력의 대표 항목)"""
    __tablename__ = "advisory_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    target_date: Mapped[datetime] = mapped_column(Date)
    hour: Mapped[int] = mapped_column(Integer)
    llm: Mapped[str] = mapped_column(String(20), default="")
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)        # {"increase": 3, "keep": 2, ...}
    advisory_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)   # 이 묶음에 속한 advisory_log.id
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QALog(Base):
    __tablename__ = "qa_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    embedding: Mapped[str] = mapped_column(String(20), default="")
    elapsed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatLog(Base):
    """배차 상담 챗봇 대화 1건 (F-25). 어떤 기능을 불렀는지, 숫자 검증을 통과했는지 남김"""
    __tablename__ = "chat_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(20), index=True)
    slots: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # {"route": "160", "date": "...", "hour": 15}
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)       # [{"name": "predict", "args": {...}, "ok": true}]
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    numbers_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)   # 숫자를 대조하지 않는 답은 NULL
    llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_rejected: Mapped[bool] = mapped_column(Boolean, default=False)    # LLM 문장의 숫자가 달라 버렸으면 True
    elapsed_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OptimizationRun(Base):
    """전 노선 배차 최적화 1회 실행 (F-13). 결과 이력 보기·가중치만 바꾼 결과 비교에 사용"""
    __tablename__ = "optimization_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    target_date: Mapped[datetime] = mapped_column(Date, index=True)
    hours: Mapped[list | None] = mapped_column(JSON, nullable=True)       # [6, 7, 8, 9]
    weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # {"overload": 10, "trips": 1, "change": 0}
    method: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="")
    solve_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    overcrowded_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overcrowded_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_trips: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kpi: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)        # 노선·시간별 권고 운행횟수
    binding: Mapped[list | None] = mapped_column(JSON, nullable=True)     # 버스가 모자라 더 못 늘린 곳
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())



class RetrainReview(Base):
    """예측 오차 점검 결과(eval/results/drift/<check_id>.json)에 대한 담당자 결정 (요청 R-3).
    판정 숫자는 파일에 있고, 여기에는 사람의 결정만 남김. 같은 점검을 다시 결정하면 그 줄을 고침"""
    __tablename__ = "retrain_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(30), index=True)
    check_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)   # forward_2026-11-02_2026-11-08
    verdict: Mapped[str] = mapped_column(String(20), default="")                  # 결정 당시의 판정 (retrain / ok / insufficient)
    status: Mapped[str] = mapped_column(String(20), default="")                   # approved / held / rejected
    reviewer: Mapped[str] = mapped_column(String(50), default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ==================================================
# Engine / Session
# ==================================================

engine = create_async_engine(ASYNC_DB_URL, pool_pre_ping=True, pool_recycle=3600) if ASYNC_DB_URL else None
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession) if engine else None
state = {"available": False, "error": "ASYNC_DB_URL 미설정" if not ASYNC_DB_URL else None}


async def init_db() -> None:
    """서버 시작 시 테이블 생성 (없을 때만). 실패해도 서버는 계속 실행"""
    if engine is None:
        print("⚠️ ASYNC_DB_URL이 없어 DB 저장을 건너뜁니다.")
        return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        state.update(available=True, error=None)
        print("✅ MariaDB 연결 및 테이블 확인 완료")
    except Exception as error:
        state.update(available=False, error=str(error).splitlines()[0][:200])
        print(f"⚠️ MariaDB 연결 실패 (DB 저장 없이 실행): {state['error']}")


async def close_db() -> None:
    if engine is not None:
        await engine.dispose()
