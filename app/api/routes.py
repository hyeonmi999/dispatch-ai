# ==================================================
# 공통 API 라우터 (도메인 무관)
# ==================================================
# 도메인은 경로 파라미터로 구분하고, 처리는 레지스트리에 등록된 어댑터에 위임합니다.
#
#   GET  /api/domains                  등록된 도메인 목록·상태
#   GET  /api/{domain}/schema          도메인별 요청 스키마
#   POST /api/{domain}/predict         수요 예측
#   POST /api/{domain}/ask             규정 질의응답 (RAG)
#   POST /api/{domain}/advise          배차 권고 (예측 + 규정 근거, needs_review)
#   GET  /api/advisories               권고 이력 (MariaDB)
#   POST /api/advisories/{id}/review   담당자 검토 결과 기록

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.core.domain import DomainAdapter, DomainNotImplemented
from app.core.llm import llm_status
from app.core.registry import registry
from app.core.schemas import AskRequest, LLMOptions, ReviewRequest
from app.db import database as db
from app.db import repository as repo

router = APIRouter(prefix="/api")


def get_domain(name: str) -> DomainAdapter:
    adapter = registry.get(name)
    if adapter is None:
        names = ", ".join(a.name for a in registry.all())
        raise HTTPException(404, f"등록되지 않은 도메인입니다: {name} (등록: {names})")
    return adapter


def validation_error(e: ValidationError) -> HTTPException:
    return HTTPException(422, e.errors(include_url=False, include_context=False))


# ==================================================
# 1. 상태
# ==================================================

@router.get("/health", tags=["상태"])
def health():
    return {
        "status": "ok",
        "llm": llm_status(),
        "db": db.state,
        "domains": [a.info() for a in registry.all()],
    }


@router.get("/domains", tags=["상태"])
def domains():
    return [a.info() for a in registry.all()]


@router.get("/{domain}/schema", tags=["공통"])
def schema(domain: str):
    adapter = get_domain(domain)
    return {**adapter.info(), "schemas": adapter.schemas()}


# ==================================================
# 2. 예측
# ==================================================

@router.post("/{domain}/predict", tags=["공통"], summary="수요 예측")
async def predict(domain: str, payload: dict = Body(..., examples=[{"route": "143", "date": "2026-10-14", "hour": 8}])):
    adapter = get_domain(domain)
    try:
        result = await run_in_threadpool(adapter.predict, payload)
    except ValidationError as e:
        raise validation_error(e)
    saved_id = await repo.save_prediction(domain, result)
    return {**result, "db_saved": saved_id is not None, "prediction_id": saved_id}


# ==================================================
# 3. 규정 질의응답 (RAG)
# ==================================================

@router.post("/{domain}/ask", tags=["공통"], summary="규정 질의응답 (근거 문서·조항 포함)")
async def ask(domain: str, req: AskRequest):
    adapter = get_domain(domain)
    if not adapter.implemented and not adapter.knowledge_base.has_docs():
        raise DomainNotImplemented(domain, "ask")
    result = await run_in_threadpool(
        adapter.knowledge_base.ask, req.question, req.llm, req.embedding, req.top_k, req.use_llm,
    )
    saved_id = await repo.save_qa(domain, result)
    return {**result, "domain": domain, "db_saved": saved_id is not None}


# ==================================================
# 4. 배차 권고
# ==================================================

@router.post("/{domain}/advise", tags=["공통"], summary="배차 권고 (needs_review)")
async def advise(domain: str, payload: dict = Body(..., examples=[{"route": "143", "date": "2026-10-14", "hour": 8, "llm": "qwen", "embedding": "bge", "use_llm": True}])):
    adapter = get_domain(domain)
    options = LLMOptions(**{k: payload.pop(k) for k in ("llm", "embedding", "use_llm") if k in payload})
    try:
        result = await run_in_threadpool(adapter.advise, payload, options.llm, options.embedding, options.use_llm)
    except ValidationError as e:
        raise validation_error(e)
    advisory_id = await repo.save_advisory(domain, result, options.llm)
    return {**result, "advisory_id": advisory_id, "db_saved": advisory_id is not None}


# ==================================================
# 5. 권고 이력 / 담당자 검토
# ==================================================

@router.get("/advisories", tags=["이력"])
async def advisories(domain: str | None = None, limit: int = 20):
    return {"db": db.state, "items": await repo.list_advisories(domain, limit)}


@router.get("/advisories/{advisory_id}", tags=["이력"])
async def advisory_detail(advisory_id: int):
    if not db.state["available"]:
        raise HTTPException(503, f"DB에 연결되지 않았습니다: {db.state['error']}")
    item = await repo.get_advisory(advisory_id)
    if item is None:
        raise HTTPException(404, f"권고 이력을 찾을 수 없습니다: {advisory_id}")
    return item


@router.post("/advisories/{advisory_id}/review", tags=["이력"])
async def review(advisory_id: int, req: ReviewRequest):
    if not db.state["available"]:
        raise HTTPException(503, f"DB에 연결되지 않아 검토 결과를 저장할 수 없습니다: {db.state['error']}")
    ok = await repo.review_advisory(advisory_id, req.status, req.reviewer, req.note)
    if not ok:
        raise HTTPException(404, f"권고 이력을 찾을 수 없습니다: {advisory_id}")
    return {"id": advisory_id, "review_status": req.status}
