# ==================================================
# 물류(화물 차량) 배차 도메인 - 인터페이스만 정의 (2차 스프린트 예정)
# ==================================================
# 예측 대상 : 거점(허브)·권역·시간대별 출고 화물량(건/kg)
# 권고      : 차량 톤급별 필요 대수, 상차 시간대 조정
# 규정 근거 : 화물자동차 운수사업법, 차량 적재 기준 등 → docs/logistics/

import datetime as dt

from pydantic import BaseModel, Field

from app.core.domain import NotImplementedDomain


class LogisticsPredictRequest(BaseModel):
    depot_id: str = Field(..., description="출고 거점 ID", examples=["HUB-ICN-01"])
    date: dt.date
    region: str | None = Field(None, description="배송 권역", examples=["서울 강남권"])


class LogisticsAdviseRequest(LogisticsPredictRequest):
    vehicle_capacity_kg: float = Field(2500, gt=0, description="차량 1대 적재량 (kg)")
    shift: str = Field("am", description="상차 회차 (am/pm/night)")


class LogisticsAdapter(NotImplementedDomain):
    name = "logistics"
    title = "물류 배차"
    description = "거점·권역별 화물량 예측과 차량 배차 권고 (준비 중)"
    predict_schema = LogisticsPredictRequest
    advise_schema = LogisticsAdviseRequest
