# ==================================================
# 버스 도메인 요청 스키마
# ==================================================

import datetime as dt

from pydantic import BaseModel, Field


class BusPredictRequest(BaseModel):
    route: str = Field(..., description="노선번호", examples=["143"])
    date: dt.date = Field(..., description="예측 날짜 (실측 마지막 날 + 21일까지)", examples=["2026-10-14"])
    hour: int | None = Field(None, ge=0, le=23, description="지정하면 해당 시간대 정류장별 예측도 반환")


# ==================================================
# 길안내 (F-28)
# ==================================================

class BusGuideRequest(BaseModel):
    destination: str = Field(..., min_length=1, max_length=50, description="도착 정류장 이름 (일부만 적어도 됨)", examples=["이태원역"])
    origin: str | None = Field(None, max_length=50, description="출발 정류장 이름. 없으면 lat·lng(현재 위치)를 씀")
    lat: float | None = Field(None, ge=33, le=39, description="현재 위치 위도")
    lng: float | None = Field(None, ge=124, le=132, description="현재 위치 경도")
    date: dt.date = Field(..., examples=["2026-10-28"])
    hour: int = Field(..., ge=0, le=23, examples=[8])


class BusGuideStop(BaseModel):
    name: str
    seq: int
    lat: float
    lng: float


class BusGuideLeg(BaseModel):
    route: str
    board: BusGuideStop
    alight: BusGuideStop
    direction: str | None = None
    stops: int = Field(..., description="타고 가는 정류장 수")
    walk_m: int = Field(..., description="타는 정류장까지 걷는 거리(m)")
    walk_min: int = Field(..., description="걷는 시간(분): 타러 가는 길 + 내려서 도착지까지")
    exit_walk_m: int = Field(0, description="내린 정류장에서 도착지까지 걷는 거리(m)")
    interval_min: float = Field(..., description="이 시간대 배차간격(분)")
    wait_min: float = Field(..., description="평균 대기 시간 = 배차간격의 절반")
    ride_min: int = Field(..., description="탑승 시간(분). 정류장별 공식 시간표 기준 (없으면 운행시간으로 어림)")
    distance_km: float | None = Field(None, description="타고 가는 도로 거리(km). 거리 정보가 없으면 null")
    congestion: float | None = Field(None, description="타는 구간에서 가장 붐비는 곳의 예상 혼잡도. 예측하지 않는 노선은 null")
    level: str | None = None
    predicted: bool = Field(True, description="False면 혼잡도를 예측하지 않는 노선 (배차간격은 평일 기준 기본값)")
    path: list[list[float]] = Field(default_factory=list, description="타고 가는 정류장들의 [위도, 경도] (지도에 그림)")


class BusGuideOption(BaseModel):
    kind: str = Field(..., description="direct(직행) / transfer(1회 환승)")
    routes: list[str]
    legs: list[BusGuideLeg]
    total_min: int = Field(..., description="걷기 + 대기 + 탑승을 더한 대략의 시간(분)")
    max_congestion: float | None = None
    level: str | None = None
    fastest: bool
    least_crowded: bool
    recommended: bool


class BusGuidePlace(BaseModel):
    label: str | None = None
    stop: str
    lat: float
    lng: float
    walk_m: int = 0
    far: bool = Field(False, description="True면 걸어갈 거리 안에 정류장이 없어 가장 가까운 정류장으로 안내")


class BusGuideRecommendation(BaseModel):
    index: int = Field(..., description="options에서 추천하는 경로의 위치 (0부터)")
    reason: str = Field(..., description="fastest(가장 빠름) / less_crowded(조금 더 걸리지만 덜 붐빔)")
    slower_by_min: int = 0


class BusGuideResponse(BaseModel):
    date: str = Field(..., description="혼잡도·배차간격을 계산한 날짜")
    requested_date: str | None = Field(None, description="요청한 날짜가 예측 범위 밖이어서 date로 바꿨을 때만, 원래 요청한 날짜")
    hour: int
    is_forecast: bool
    origin: BusGuidePlace
    destination: BusGuidePlace
    options: list[BusGuideOption] = Field(..., description="빠른 순. 경로가 없으면 빈 목록")
    recommendation: BusGuideRecommendation | None = None
    assumption: str


class BusRouteInfoRequest(BaseModel):
    route: str = Field(..., examples=["160"])


class BusRouteInfoResponse(BaseModel):
    route: str
    route_type: str | None = None
    company: str | None = None
    start: str | None = Field(None, description="기점")
    end: str | None = Field(None, description="종점")
    first_bus: str | None = None
    last_bus: str | None = None
    interval_weekday: float | None = Field(None, description="평일 기준 배차간격(분)")
    min_interval: float | None = None
    max_interval: float | None = None
    run_minutes: float | None = Field(None, description="한 바퀴 운행시간(분)")
    authorized_buses: float | None = Field(None, description="인가대수")
    authorized_estimated: bool = Field(False, description="True면 원본에 없어 보완한 인가대수")
    distance_km: float | None = None
    stops_count: int = Field(..., description="정류장 수 (왕복)")
    key_stops: list[str] = Field(..., description="기점에서 회차 지점까지 고르게 뽑은 주요 정류장")
    predicted: bool = Field(True, description="False면 승객·혼잡도를 예측하지 않는 노선 (기본 정보만 있음)")


class BusStopSearchItem(BaseModel):
    name: str
    lat: float
    lng: float
    routes: list[str]


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
