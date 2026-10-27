# ==================================================
# 공통 요청·응답 스키마
# ==================================================

from typing import Literal

from pydantic import BaseModel, Field

from app.config import DEFAULT_EMBEDDING, DEFAULT_LLM, RAG_TOP_K

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
