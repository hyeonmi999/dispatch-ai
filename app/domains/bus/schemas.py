# ==================================================
# 버스 도메인 요청 스키마
# ==================================================

import datetime as dt

from pydantic import BaseModel, Field


class BusPredictRequest(BaseModel):
    route: str = Field(..., description="노선번호", examples=["143"])
    date: dt.date = Field(..., description="예측 날짜 (실측 마지막 날 + 21일까지)", examples=["2026-10-14"])
    hour: int | None = Field(None, ge=0, le=23, description="지정하면 해당 시간대 정류장별 예측도 반환")


class BusAdviseAllRequest(BaseModel):
    date: dt.date = Field(..., examples=["2026-10-28"])
    hour: int = Field(..., ge=0, le=23, examples=[8])
    llm: str = Field("qwen")
    embedding: str = Field("bge")
    use_llm: bool = Field(False, description="True면 권고 유형별 규정 요약을 LLM으로 생성 (유형당 10~30초)")


class BusAdviseRequest(BaseModel):
    route: str = Field(..., examples=["143"])
    date: dt.date = Field(..., examples=["2026-10-14"])
    hour: int = Field(..., ge=0, le=23, examples=[8])
