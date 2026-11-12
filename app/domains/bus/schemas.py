# ==================================================
# 버스 도메인 요청·응답 스키마
# ==================================================

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.config import BUS_OPT_WEIGHTS


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


# ==================================================
# 배차간격 변경 시뮬레이션 (F-12)
# ==================================================

class BusSimulateRequest(BaseModel):
    route: str = Field(..., examples=["143"])
    date: dt.date = Field(..., examples=["2026-10-28"])
    hour: int = Field(..., ge=0, le=23, examples=[8])
    interval: float = Field(..., gt=0, description="바꿔 볼 배차간격(분). 노선의 최소~최대 배차간격 안이어야 함", examples=[5])


class BusIntervalState(BaseModel):
    interval: float = Field(..., description="배차간격(분)")
    trips_per_hour: float = Field(..., description="시간당 운행횟수 = 60 ÷ 배차간격")
    max_congestion: float = Field(..., description="가장 붐비는 구간의 혼잡도")
    level: str = Field(..., description="여유 / 보통 / 혼잡 / 매우혼잡")


class BusIntervalBounds(BaseModel):
    min_interval: float
    max_interval: float


class BusPeakSegment(BaseModel):
    seq: int
    from_name: str


class BusSimulatedSegment(BaseModel):
    seq: int = Field(..., description="정류장 순번 (이 정류장에서 다음 정류장까지의 구간)")
    from_name: str
    congestion_before: float = Field(..., description="지금 혼잡도")
    level_before: str = Field(..., description="지금 혼잡 단계")
    congestion: float = Field(..., description="바꾼 뒤 혼잡도")
    level: str = Field(..., description="바꾼 뒤 혼잡 단계")


class BusSimulateResponse(BaseModel):
    route: str
    date: str
    hour: int
    daytype: str
    is_forecast: bool = Field(..., description="True면 실측이 아직 없는 날짜")
    current: BusIntervalState
    simulated: BusIntervalState
    bounds: BusIntervalBounds = Field(..., description="이 노선·요일유형에서 허용되는 배차간격 범위")
    peak_segment: BusPeakSegment = Field(..., description="지금 가장 붐비는 구간")
    segments: list[BusSimulatedSegment]
    assumption: str = Field(..., description="계산에 쓴 가정")
    needs_review: bool = Field(True, description="항상 True. 담당자 검토 필요")


# ==================================================
# 전 노선 배차 최적화 (F-13)
# ==================================================

class BusOptimizeWeights(BaseModel):
    overload: float = Field(BUS_OPT_WEIGHTS["overload"], ge=0, le=1000, description="과밀량(명) 1에 매기는 벌점")
    trips: float = Field(BUS_OPT_WEIGHTS["trips"], ge=0, le=1000, description="운행 1회에 매기는 비용")
    change: float = Field(BUS_OPT_WEIGHTS["change"], ge=0, le=1000,
                          description="현행과 달라진 운행 1회에 매기는 비용. 0이면 한가한 시간대는 줄임(감차 허용), 1 이상이면 늘리기만 함")


class BusOptimizeRequest(BaseModel):
    date: dt.date = Field(..., examples=["2026-10-28"])
    hours: list[int] = Field(..., min_length=1, max_length=24, description="최적화할 시간대", examples=[[6, 7, 8, 9]])
    weights: BusOptimizeWeights = Field(default_factory=BusOptimizeWeights)

    @field_validator("hours")
    @classmethod
    def hours_in_range(cls, hours: list[int]) -> list[int]:
        if any(h < 0 or h > 23 for h in hours):
            raise ValueError("시간대는 0~23 사이여야 합니다")
        return sorted(set(hours))


class BusOptimizePlanItem(BaseModel):
    route: str
    hour: int
    current_trips: int = Field(..., description="현행 시간당 운행횟수")
    recommended_trips: int = Field(..., description="권고 시간당 운행횟수")
    change: int = Field(..., description="권고 − 현행")
    action: str = Field(..., description="increase(증차) / decrease(감차) / keep(유지)")
    interval: float = Field(..., description="권고 배차간격(분) = 60 ÷ 권고 운행횟수")
    buses_needed: int = Field(..., description="권고대로 운행할 때 이 시간대에 도로 위에 있는 버스 수")
    fleet: int = Field(..., description="인가대수")
    fleet_estimated: bool = Field(..., description="True면 인가대수가 원본에 없어 따로 채운 값")
    max_congestion_before: float = Field(..., description="현행 최대 혼잡도 (예측 승객 기준, 대시보드와 같은 값)")
    max_congestion_after: float = Field(..., description="권고 후 최대 혼잡도 (예측 승객 기준)")
    upper_congestion_before: float = Field(..., description="현행 최대 혼잡도 (승객 수 상한 기준)")
    upper_congestion_after: float = Field(..., description="권고 후 최대 혼잡도 (승객 수 상한 기준)")
    level_after: str = Field(..., description="권고 후 혼잡 단계 (승객 수 상한 기준)")
    limited_by: str | None = Field(None, description="과밀이 남았는데 더 늘리지 못한 이유")


class BusOptimizeKpi(BaseModel):
    overcrowded_before: int = Field(..., description="혼잡도 0.8 이상인 노선·시간 수 (현행, 승객 수 상한 기준)")
    overcrowded_after: int = Field(..., description="혼잡도 0.8 이상인 노선·시간 수 (권고 후)")
    extra_trips: int = Field(..., description="늘어난 운행횟수 합계 (줄면 음수)")
    changed: int = Field(..., description="현행과 달라진 노선·시간 수")
    increased: int = Field(..., description="증차 권고인 노선·시간 수")
    decreased: int = Field(..., description="감차 권고인 노선·시간 수")
    total_trips_before: int
    total_trips_after: int


class BusOptimizeSkipped(BaseModel):
    route: str
    hour: int
    reason: str


class BusOptimizeResponse(BaseModel):
    date: str
    daytype: str
    is_forecast: bool
    method: str = Field(..., description="cpsat(정수계획)")
    status: str = Field(..., description="optimal: 최적해 / time_limit: 시간 제한 안에서 찾은 해")
    solve_seconds: float
    weights: BusOptimizeWeights
    plan: list[BusOptimizePlanItem]
    kpi: BusOptimizeKpi
    binding: list[str] = Field(..., description="과밀이 남았는데 제약에 걸려 더 늘리지 못한 곳")
    skipped: list[BusOptimizeSkipped] = Field(..., description="운행 데이터가 없어 뺀 노선·시간")
    assumption: str
    cut_notice: str | None = Field(None, description="감차 권고가 하나라도 있을 때의 안내")
    needs_review: bool = Field(True, description="항상 True. 담당자 검토 필요")
