# ==================================================
# 버스 도메인 요청·응답 스키마
# ==================================================

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import BUS_OPT_WEIGHTS


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
    wait_min: float = Field(..., description="평균 대기 시간 = 배차간격의 절반. 첫차를 기다리는 경우는 첫차가 올 때까지의 분")
    first_bus_at: str | None = Field(None, description="첫차가 아직 오지 않아 첫차를 기다려 타는 경우, 첫차가 이 정류장에 오는 시각('HH:MM', 시간표 기준 어림). 그 밖에는 null")
    ride_min: int = Field(..., description="탑승 시간(분). 정류장별 공식 시간표 기준 (없으면 운행시간으로 어림)")
    distance_km: float | None = Field(None, description="타고 가는 도로 거리(km). 거리 정보가 없으면 null")
    congestion: float | None = Field(None, description="타는 구간에서 가장 붐비는 곳의 예상 혼잡도. 예측하지 않는 노선은 null")
    level: str | None = None
    predicted: bool = Field(True, description="False면 혼잡도를 예측하지 않는 노선 (배차간격은 평일 기준 기본값)")
    start_hour: int | None = Field(None, description="이 정류장에서 탈 버스가 기점을 떠난 시간대. 혼잡도·배차간격은 이 시간대 값. 예측하지 않는 노선은 null")
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
    ambiguous: bool = Field(False, description="True면 같은 이름의 정류장이 멀리 떨어진 여러 곳에 있어, 상대편(출발지·도착지)에서 가장 가까운 곳으로 고름")


class BusGuideWalkOnly(BaseModel):
    walk_m: int
    walk_min: int


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
    walk_only: BusGuideWalkOnly | None = Field(None, description="출발지가 도착지 바로 옆이면 걸어갈 거리. 이때 options는 빈 목록")
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


class BusFleetNeed(BaseModel):
    buses_needed_before: int = Field(..., description="지금 운행에서 도로 위 버스가 가장 많은 순간의 대수")
    buses_needed: int = Field(..., description="이 시간대만 바꿨을 때의 대수 (나머지 시간대는 지금대로)")
    authorized_buses: int | None = Field(None, description="인가대수. 모르면 null")
    authorized_estimated: bool = Field(False, description="True면 원본에 없어 보완한 인가대수")
    over: bool = Field(..., description="True면 바꾼 뒤 필요한 대수가 인가대수를 넘음")


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
    fleet: BusFleetNeed = Field(..., description="필요한 버스 대수와 인가대수")
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
    run_id: int | None = Field(None, description="이력 테이블 optimization_run의 id. DB가 꺼져 있으면 None")
    db_saved: bool = Field(False, description="이력 저장 여부. DB가 꺼져 있어도 최적화 결과는 그대로 나옴")


class BusOptimizeRun(BaseModel):
    id: int
    date: str
    hours: list[int]
    weights: dict[str, float]
    status: str
    solve_seconds: float | None = None
    overcrowded_before: int | None = None
    overcrowded_after: int | None = None
    extra_trips: int | None = None
    created_at: str = Field(..., description="저장 시각 (DB 서버 시각)")


class BusOptimizeRunList(BaseModel):
    db: dict = Field(..., description="DB 연결 상태 {available, error}")
    items: list[BusOptimizeRun] = Field(..., description="최근 실행부터. DB가 꺼져 있으면 빈 목록")


class BusBacktestWindow(BaseModel):
    start: str
    end: str
    days: int


class BusBacktestMethod(BaseModel):
    name: str = Field(..., description="현행 · 규칙 · 그리디 · 최적화 · 감차 허용")
    label: str
    weights: dict[str, float] | None = Field(None, description="최적화 가중치 (과밀·운행·변경). 최적화 계열만")
    reference: bool = Field(False, description="True면 미리 정한 순서로 고른 설정이 아닌 참고 행")
    why: str | None = None
    cells: int = Field(..., description="채점한 노선·시간 수")
    route_days: int
    overcrowded_cells: int
    overcrowded_pct: float = Field(..., description="붐비는 노선·시간 비율(%). 실제 승객 기준 혼잡도 ≥ crowded_threshold")
    total_trips: float
    trips_vs_current_pct: float = Field(..., description="총 운행횟수의 현행 대비 증감(%)")
    changed_cells: int = Field(..., description="현행과 운행횟수가 달라진 노선·시간 수")
    fleet_over_new_days: int = Field(..., description="현행에는 없던 인가대수 초과가 생긴 노선·일 수")


class BusBacktestResponse(BaseModel):
    eval_window: BusBacktestWindow
    tune_window: BusBacktestWindow
    crowded_threshold: float
    methods: list[BusBacktestMethod]
    pick_order: list[str] = Field(..., description="가중치를 고를 때 본 순서")
    notes: list[str]
    caution: str


# ==================================================
# 예측 오차 점검 → 재학습 권고 (요청 R-3, docs/contracts.md 2-3)
# ==================================================

class BusDriftWindow(BaseModel):
    start: str
    end: str
    days: int = Field(..., description="실측이 있는 날 수")


class BusDriftMetrics(BaseModel):
    rows: int = Field(..., description="정류장·시간 행 수")
    stop_hour_mae: float | None = Field(None, description="정류장·시간 MAE(명)")
    baseline_mae: float | None = Field(None, description="같은 행의 작년 같은 요일 MAE(명)")
    model_mae_on_baseline_rows: float | None = Field(None, description="작년 값이 있는 행만의 모델 MAE (베이스라인과 같은 행 비교)")
    route_hour_cells: int = Field(0, description="노선·날짜·시간 합계 칸 수 (P90 적중률의 분모)")
    p90_coverage_pct: float | None = Field(None, description="실제 합계가 P90 상한 이하인 비율(%). 비율표가 없으면 null")
    mae_ratio: float | None = Field(None, description="stop_hour_mae ÷ 평가 MAE (전체 지표에만)")


class BusDriftCheckItem(BaseModel):
    name: str = Field(..., description="mae_ratio / vs_baseline / p90_coverage")
    label: str
    value: float | None
    limit: float | None
    failed: bool


class BusDriftReview(BaseModel):
    status: str = Field(..., description="approved / held / rejected")
    reviewer: str = ""
    note: str = ""
    reviewed_at: str | None = None


class BusDriftCheck(BaseModel):
    id: str = Field(..., description="<kind>_<첫날>_<마지막 날>")
    kind: str = Field(..., description="forward(실제 예측) / replay(평가 구간 다시 돌려 봄)")
    kind_label: str
    window: BusDriftWindow
    model_version: str
    reference_mae: float = Field(..., description="평가 구간 정류장·시간 MAE (metrics.json)")
    metrics: BusDriftMetrics
    by_daytype: dict[str, BusDriftMetrics]
    by_route: dict[str, BusDriftMetrics]
    checks: list[BusDriftCheckItem]
    verdict: str = Field(..., description="retrain(재학습 권고) / ok / insufficient(실측이 모자라 판정 보류)")
    recommend: bool
    reasons: list[str]
    next_steps: list[str] = Field(..., description="권고일 때 담당자가 실행할 순서. 서버는 학습을 돌리지 않음")
    caution: str
    needs_review: bool = True
    review: BusDriftReview | None = Field(None, description="담당자 결정. 아직 없거나 DB가 꺼져 있으면 null")


class BusDriftRules(BaseModel):
    mae_ratio: float
    p90_coverage_min: float
    min_days: int


class BusDriftList(BaseModel):
    db: dict
    rules: BusDriftRules
    items: list[BusDriftCheck]


class BusDriftReviewRequest(BaseModel):
    status: Literal["approved", "held", "rejected"] = Field(..., description="승인 / 보류 / 반려")
    reviewer: str = Field("", max_length=50)
    note: str = Field("", max_length=500)


# ==================================================
# 실시간 사고·정체 알림 (이슈 #42 방법 2, Task P-9)
# ==================================================

class BusTrafficStop(BaseModel):
    seq: int = Field(..., description="노선 정류장 순번")
    name: str


class BusTrafficIncident(BaseModel):
    acc_id: str
    type: str = Field(..., description="돌발 종류 코드 (A01 교통사고, A02 차량고장, A04 공사 …)")
    type_name: str
    dtype_name: str = Field(..., description="세부 종류 이름 (추돌사고 등). 이름을 모르면 코드")
    info: str = Field(..., description="TOPIS 돌발 내용")
    start: str | None = Field(None, description="발생 시각 (KST ISO)")
    expected_clear: str | None = Field(None, description="해제 예정 시각 (KST ISO)")
    lat: float
    lng: float
    distance_m: int | None = Field(None, description="노선 경로까지 거리(m)")
    near_stop: BusTrafficStop | None = None
    long_term: bool = Field(..., description="해제 예정이 TOPIS_LONG_TERM_DAYS보다 먼 돌발 (장기 공사 등)")


class BusTrafficRun(BaseModel):
    hour: int = Field(..., description="받은 시각의 시간대 (평소 속도를 고른 시간대)")
    links_total: int = Field(..., description="노선 링크 수")
    links_used: int = Field(..., description="지금·평소 값을 모두 받은 링크 수")
    coverage: float = Field(..., description="받은 링크 길이 ÷ 노선 링크 길이")
    live_minutes: float | None = Field(None, description="받은 링크의 지금 차량 통행시간 합(분)")
    usual_minutes: float | None = Field(None, description="같은 링크의 평소 차량 통행시간 합(분)")
    extra_minutes: float | None = Field(None, description="한 바퀴 추가 시간(분, 0 이상). 받은 비율이 낮으면 null")
    usual_run: int | None = Field(None, description="평소 한 바퀴(분): P-7 시간대별 표(지금 계획이 가정한 값) 또는 고정 운행시간")
    est_run: int | None = Field(None, description="평소 + 추가 시간(분)")
    buses_usual: int | None = Field(None, description="평소 한 바퀴로 센 도로 위 버스 대수")
    buses_est: int | None = Field(None, description="추가 시간을 더해 센 도로 위 버스 대수")
    authorized: int | None = Field(None, description="인가대수")
    fleet_short: bool = Field(False, description="True면 '인가대수 부족 가능'")
    basis: str | None = Field(None, description="운행횟수·운행시간을 어디서 가져왔는지")


class BusTrafficRoute(BaseModel):
    route: str
    incidents: list[BusTrafficIncident] = Field(..., description="경로 위의 진행 중인 돌발 (가까운 순)")
    long_term: list[BusTrafficIncident] = Field(..., description="경로 위의 장기 공사 등 (평소 운행시간에 이미 들어 있음)")
    traffic: BusTrafficRun | None = Field(None, description="정체 추정. 링크·평소 속도 표가 없으면 null")
    messages: list[str] = Field(..., description="화면에 보여 줄 알림 문장. 알릴 것이 없으면 빈 목록")


class BusTrafficReady(BaseModel):
    route_links: bool = Field(..., description="노선 ↔ 링크 표가 있음")
    link_speed: bool = Field(..., description="평소 링크 속도 표가 있음")
    api_key: bool = Field(..., description="SEOUL_OPEN_API_KEY가 있음 (값은 보내지 않음)")


class BusTrafficResponse(BaseModel):
    source: Literal["live", "replay"] = Field(..., description="live 실시간 / replay 받아 둔 기록")
    fetched_at: str = Field(..., description="TOPIS에서 받은 시각 (KST ISO). replay면 기록의 시각")
    snapshot: str | None = Field(None, description="기록 이름 (YYYYMMDD_HHMMSS)")
    daytype: str
    routes: list[BusTrafficRoute]
    warnings: list[str]
    ready: BusTrafficReady
    needs_review: bool = Field(True, description="배차는 자동으로 바꾸지 않음. 항상 true")


class BusTrafficSnapshot(BaseModel):
    name: str
    fetched_at: str | None
    incidents: int = Field(..., description="서울 전체 돌발 수")
    links: int = Field(..., description="소통 정보를 받은 링크 수")


class BusTrafficSnapshotList(BaseModel):
    items: list[BusTrafficSnapshot]
