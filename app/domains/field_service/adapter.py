# ==================================================
# 서비스 기사 출동 배차 도메인 - 인터페이스만 정의 (2차 스프린트 예정)
# ==================================================
# 예측 대상 : 권역·시간대·서비스 유형별 출동 요청 건수
# 권고      : 권역별 대기 기사 수, 출동 우선순위
# 규정 근거 : 서비스 수준 협약(SLA), 근로시간 규정 등 → docs/field_service/

import datetime as dt

from pydantic import BaseModel, Field

from app.core.domain import NotImplementedDomain


class FieldServicePredictRequest(BaseModel):
    service_area: str = Field(..., description="서비스 권역", examples=["서울 마포구"])
    date: dt.date
    hour: int | None = Field(None, ge=0, le=23)
    skill_type: str = Field("general", description="서비스 유형 (가전 수리, 설치 등)")


class FieldServiceAdviseRequest(FieldServicePredictRequest):
    technicians_on_duty: int = Field(..., ge=0, description="현재 대기 기사 수")


class FieldServiceAdapter(NotImplementedDomain):
    name = "field_service"
    title = "서비스 출동 배차"
    description = "권역별 출동 요청 예측과 기사 배치 권고 (준비 중)"
    predict_schema = FieldServicePredictRequest
    advise_schema = FieldServiceAdviseRequest
