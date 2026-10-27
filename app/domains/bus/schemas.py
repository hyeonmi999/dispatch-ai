# ==================================================
# 버스 도메인 요청 스키마
# ==================================================

import datetime as dt

from pydantic import BaseModel, Field


class BusPredictRequest(BaseModel):
    route: str = Field(..., description="노선번호", examples=["143"])
    date: dt.date = Field(..., description="예측 날짜 (데이터 기간 ~ 마지막 날 +7일)", examples=["2026-10-14"])
    hour: int | None = Field(None, ge=0, le=23, description="지정하면 해당 시간대 정류장별 예측도 반환")


class BusAdviseRequest(BaseModel):
    route: str = Field(..., examples=["143"])
    date: dt.date = Field(..., examples=["2026-10-14"])
    hour: int = Field(..., ge=0, le=23, examples=[8])
