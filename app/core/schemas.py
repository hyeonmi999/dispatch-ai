# ==================================================
# 공통 요청·응답 스키마
# ==================================================

from typing import Literal

from pydantic import BaseModel, Field

from app.config import (
    CHAT_HISTORY_ITEM_MAX,
    CHAT_HISTORY_MAX,
    CHAT_MESSAGE_MAX,
    DEFAULT_EMBEDDING,
    DEFAULT_LLM,
    RAG_TOP_K,
)

LLMChoice = Literal["qwen", "openai"]
EmbeddingChoice = Literal["bge", "openai"]


class LLMOptions(BaseModel):
    llm: LLMChoice = Field(DEFAULT_LLM, description="qwen(로컬 Qwen3-1.7B) | openai(gpt-4o-mini)")
    embedding: EmbeddingChoice = Field(DEFAULT_EMBEDDING, description="bge(BAAI/bge-m3) | openai(text-embedding-3-small)")
    use_llm: bool = Field(True, description="False면 LLM 요약 없이 근거 조항만 반환 (빠름)")


class AskRequest(LLMOptions):
    question: str = Field(..., min_length=2, examples=["혼잡 시간대에 버스 운행횟수를 늘리려면 어떤 절차가 필요한가요?"])
    top_k: int = Field(RAG_TOP_K, ge=1, le=10)


class ReviewRequest(BaseModel):
    status: Literal["approved", "held", "rejected"] = Field(..., description="담당자 검토 결과")
    reviewer: str = Field("", max_length=50)
    note: str = Field("", max_length=500)


# ==================================================
# 배차 상담 챗봇 (F-25)
# ==================================================

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=CHAT_HISTORY_ITEM_MAX)


class ChatLocation(BaseModel):
    lat: float = Field(..., ge=33, le=39)
    lng: float = Field(..., ge=124, le=132)


class ChatRequest(LLMOptions):
    message: str = Field(..., min_length=2, max_length=CHAT_MESSAGE_MAX, examples=["160번 10월 28일 오후 3시에 혼잡해?"])
    history: list[ChatTurn] = Field(default_factory=list, max_length=CHAT_HISTORY_MAX,
                                    description="이전 대화 (오래된 것부터). 이어 묻기에 사용")
    use_llm: bool = Field(False, description="False면 정해진 문장 틀로만 답함 (빠름, 숫자 오류 없음)")
    role: str = Field("public", max_length=20, description="열람 등급 (F-37). 값 목록은 docs/contracts.md")
    location: ChatLocation | None = Field(None, description="현재 위치 (길안내에서 출발지로 사용). 서버에 저장하지 않음")


class ChatToolCall(BaseModel):
    name: str = Field(..., description="predict / advise / ask / guide / route_info / simulate / optimize")
    args: dict = Field(default_factory=dict)
    ok: bool = Field(True, description="False면 기능이 오류를 돌려줌 (answer에 이유)")


class ChatSource(BaseModel):
    id: int
    document: str | None = None
    article: str | None = None
    page: int | None = None
    url: str | None = None
    score: float | None = None
    snippet: str | None = None


class ChatResponse(BaseModel):
    domain: str
    answer: str
    intent: str = Field(..., description="predict / advise / ask / guide / route_info / simulate / optimize / clarify / out_of_scope")
    slots: dict = Field(default_factory=dict, description="질문에서 뽑은 값 (route, date, hour, interval, origin, destination)")
    tools: list[ChatToolCall] = Field(default_factory=list, description="부른 기능 목록")
    sources: list[ChatSource] = Field(default_factory=list, description="근거 조항")
    numbers_verified: bool | None = Field(None, description="답변 속 숫자가 모두 기능 결과에 있으면 True. 숫자를 대조하지 않는 답(규정·되묻기)은 null")
    llm: str | None = Field(None, description="문장을 쓴 LLM. 문장 틀로만 답했으면 null")
    llm_rejected: bool = Field(False, description="True면 LLM이 쓴 문장의 숫자가 기능 결과와 달라 버리고 문장 틀로 답함")
    data: dict | None = Field(None, description="길안내 결과 전체 (/api/bus/guide 응답과 같은 형식). 화면이 지도에 그림")
    elapsed_sec: float
    needs_review: bool = Field(True, description="항상 True. 담당자 검토 필요")
    chat_id: int | None = None
    db_saved: bool = False
