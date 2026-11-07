# ==================================================
# 통합 배차 AI 플랫폼 (Dispatch AI) - FastAPI 서버 (Oh!gimbak)
# ==================================================
# 도메인 공통 아키텍처 + 1차 스프린트(버스 배차 최적화 도우미)
#
# 실행 (프로젝트 루트, .venv 활성화 후)
#   python -m uvicorn app.main:app --port 8000 --reload
#
# API 문서: http://localhost:8000/docs

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as common_router
from app.core.domain import DomainNotImplemented
from app.core.llm import LLMConfigError, get_device
from app.core.registry import registry
from app.db.database import close_db, init_db
from app.domains import register_all

# ==================================================
# 1. 도메인 등록
# ==================================================

register_all(registry)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("-" * 60)
    print(f"통합 배차 AI 플랫폼 시작 | 실행 장치: {get_device()}")
    print("-" * 60)
    for adapter in registry.all():
        adapter.startup()
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="통합 배차 AI 플랫폼 API",
    description=(
        "도메인 공통 구조 (수요 예측 + 규정 RAG + 배차 권고). "
        "1차 스프린트: 버스 배차 최적화 도우미 (서울 시내버스). "
        "물류·서비스 기사 출동 배차는 인터페이스만 제공합니다 (501). "
        "모든 권고는 담당자 검토(needs_review)가 필요합니다."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================================================
# 2. 공통 예외 → HTTP 응답
# ==================================================

@app.exception_handler(DomainNotImplemented)
async def not_implemented_handler(request: Request, exc: DomainNotImplemented):
    return JSONResponse(status_code=501, content={
        "status": "not_implemented",
        "domain": exc.domain,
        "feature": exc.feature,
        "detail": f"'{exc.domain}' 도메인은 아직 구현되지 않았습니다 (인터페이스·스키마만 정의됨, 2차 스프린트 예정).",
        "schema_url": f"/api/{exc.domain}/schema",
    })


@app.exception_handler(LLMConfigError)
async def llm_config_handler(request: Request, exc: LLMConfigError):
    return JSONResponse(status_code=400, content={"status": "llm_config_error", "detail": str(exc)})


# ==================================================
# 3. 라우터: 도메인 전용(지도 등) 먼저, 공통 라우터 나중
# ==================================================

for adapter in registry.all():
    extra = adapter.router()
    if extra is not None:
        app.include_router(extra)

app.include_router(common_router)
