# ==================================================
# 버스 도메인 어댑터 (1차 스프린트)
# ==================================================
# 공통 API(/api/bus/predict·ask·advise) + 지도 대시보드용 부가 API
#   GET /api/bus/meta                         데이터 기간·노선 목록·평가 지표
#   GET /api/bus/routes/{route}/geometry      노선 경로·정류소·구간 좌표
#   GET /api/bus/routes/{route}/timeseries    24시간 실제/예측/베이스라인 + 시간대별 최대 혼잡도
#   GET /api/bus/overview?date=&hour=         전체 노선 구간별 혼잡도 (지도 색칠)
#   POST /api/bus/simulate                    배차간격을 바꿨을 때의 구간별 혼잡도
#   POST /api/bus/optimize                    전 노선 운행횟수를 인가대수 안에서 한꺼번에 권고 (실행마다 이력 저장)
#   GET /api/bus/optimize/runs                최적화 실행 이력
#   GET /api/bus/backtest                     배차 방식 성능 비교 (eval/results/backtest.json)

import datetime as dt
import logging
import subprocess
import sys
from functools import cached_property

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from app.core.schemas import DocumentRole
from fastapi.concurrency import run_in_threadpool

from app.config import (
    DOCUMENT_LIST_LIMIT, DOCUMENT_LIST_MAX_LIMIT, MAX_DOCUMENT_BYTES,
    DRIFT_LIST_LIMIT, DRIFT_MAE_RATIO, DRIFT_MIN_DAYS, DRIFT_P90_COVERAGE_MIN,
    GUIDE_DATE_FALLBACK_WEEKS, GUIDE_SEARCH_LIMIT_MAX, LIST_LIMIT_MAX, OPTIMIZE_RUNS_LIMIT, ROUTE_INFO_KEY_STOPS,
)
from app.core.domain import DomainAdapter
from app.domains.bus.advisor import BusAdvisor, _num, congestion_level
from app.domains.bus.data import BusDataset
from app.core.documents import (
    Documents, DocumentFields, DocumentInfo, DocumentList, DocumentUpdate, require_document_admin,
)
from app.domains.bus.forecaster import BusForecaster
from app.domains.bus.guide import BusGuide, GuideError
from app.db import database as db
from app.db import repository as repo
from app.domains.bus.calibration import load_ratios
from app.domains.bus.backtest import backtest_summary
from app.domains.bus import drift
from app.domains.bus.optimizer import optimize
from app.domains.bus.schemas import (
    BusAdviseAllRequest, BusAdviseRequest, BusBacktestResponse, BusDriftCheck, BusDriftList, BusDriftReviewRequest, BusGuideRequest, BusGuideResponse, BusOptimizeRequest, BusOptimizeResponse, BusOptimizeRunList,
    BusPredictRequest, BusRouteInfoRequest, BusRouteInfoResponse, BusSimulateRequest, BusSimulateResponse, BusStopSearchItem,
)
from app.domains.bus.simulator import simulate

logger = logging.getLogger(__name__)


class BusAdapter(DomainAdapter):
    name = "bus"
    title = "버스 배차"
    description = "노선·정류장·시간대별 승객 수 예측과 혼잡 구간 배차간격 권고 (서울 시내버스)"
    predict_schema = BusPredictRequest
    advise_schema = BusAdviseRequest

    def __init__(self):
        super().__init__()
        self.dataset = BusDataset()
        self.forecaster = BusForecaster(self.dataset)
        self.advisor = BusAdvisor(self.knowledge_base, self.forecaster, self.dataset)
        self.p90_path = self.forecaster.model_dir / "p90.json"
        self.p90: dict | None = None      # 승객 수 상한 비율표 (scripts/calibrate_p90.py 결과)

    # ------------------------------------------------
    # 시작: 모델 로드 (합성 데이터 모드에서 모델이 없으면 바로 학습 - 수 초)
    # ------------------------------------------------

    def startup(self) -> None:
        print(f"🚌 버스 도메인 데이터 모드: {self.dataset.mode} ({self.dataset.dir})")
        self.p90 = load_ratios(self.p90_path)
        if self.p90 is None:
            print("ℹ️ 승객 수 상한 비율표가 없습니다. 배차 최적화를 쓰려면 python scripts/calibrate_p90.py 를 실행하세요.")
        if self.forecaster.load():
            return
        if self.dataset.mode == "sample" and self.dataset.exists():
            # 학습(XGBoost)은 torch와 OpenMP 충돌을 피하려고 별도 프로세스에서 실행
            print("ℹ️ 학습된 샘플 모델이 없어 바로 학습합니다.")
            subprocess.run([sys.executable, "scripts/train_bus.py", "--mode", "sample"], check=True)
            self.forecaster.load()
        else:
            print("⚠️ 학습된 모델이 없습니다. python scripts/train_bus.py 실행 후 서버를 다시 시작하세요.")

    def info(self) -> dict:
        out = super().info()
        out["data_mode"] = self.dataset.mode
        out["model_ready"] = self.forecaster.ready()
        return out

    def targets(self) -> list[str]:
        return list(self.dataset.routes)

    def route_names(self) -> list[str]:
        guide = self.guide_engine
        guide._build()
        return list(guide.by_route)

    # ------------------------------------------------
    # 시뮬레이션 (F-12) · 전 노선 최적화 (F-13): API와 챗봇이 같은 메서드를 부름
    # ------------------------------------------------

    def simulate(self, payload: dict) -> dict:
        req = BusSimulateRequest(**payload)
        self._require_model()
        self._check_route(req.route)
        try:
            return simulate(self.advisor, req.route, req.date, req.hour, req.interval)
        except ValueError as e:
            raise HTTPException(422, str(e))

    def optimize(self, payload: dict) -> dict:
        req = BusOptimizeRequest(**payload)
        self._require_model()
        table = self._require_p90()
        try:
            return optimize(self.advisor, table, self.dataset.routes, req.date, req.hours, req.weights.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))

    # ------------------------------------------------
    # 길안내 (F-28): 현재 위치·정류장 이름 → 직행·1회 환승 경로
    # ------------------------------------------------

    @cached_property
    def guide_engine(self) -> BusGuide:
        return BusGuide(self.advisor, self.dataset.routes)

    def guide(self, payload: dict) -> dict:
        req = BusGuideRequest(**payload)
        self._require_model()
        # 길은 날짜와 상관없이 같으므로, 요청한 날이 예측 범위 밖이면 범위 안의 가장 가까운 같은 요일로 계산
        latest = dt.date.fromisoformat(self.forecaster.date_range()["max_forecast_date"])
        date = req.date
        for _ in range(GUIDE_DATE_FALLBACK_WEEKS):
            if date <= latest:
                break
            date -= dt.timedelta(days=7)
        try:
            result = self.guide_engine.find(date, req.hour, req.destination, req.origin, req.lat, req.lng)
        except ValueError as e:      # GuideError 포함 (없는 정류장 등)
            raise HTTPException(422, str(e))
        if date != req.date:
            result["requested_date"] = req.date.isoformat()
        return result

    # ------------------------------------------------
    # 노선 기본 정보 (지도 화면의 meta와 챗봇이 같이 씀)
    # ------------------------------------------------

    def _route_row(self, route: str) -> dict:
        info = self.dataset.route_info_for(route, "weekday")
        return {
            "route": route,
            "route_type": info.get("route_type"),
            "start": info.get("start"),
            "end": info.get("end"),
            "company": info.get("company"),
            "authorized_buses": _num(info.get("buses"), 0),
            "interval_weekday": _num(info.get("interval")),
            "min_interval": _num(info.get("min_interval")),
            "max_interval": _num(info.get("max_interval")),
            "run_minutes": _num(info.get("run_minutes"), 0),
            "first_bus": info.get("first_bus"),
            "last_bus": info.get("last_bus"),
        }

    def route_info(self, payload: dict) -> dict:
        req = BusRouteInfoRequest(**payload)
        if req.route not in self.dataset.routes:
            return self._other_route_info(req.route)
        info = self.dataset.route_info_for(req.route, "weekday")
        stops = (self.dataset.geo(req.route) or {}).get("stops", [])
        # 기점에서 회차 지점까지(한 방향)에서 고르게 뽑음
        turn = next((s["seq"] for s in stops if s.get("is_turn")), len(stops))
        outbound = [s["name"] for s in stops if s["seq"] <= turn]
        step = max(1, (len(outbound) - 1) // max(ROUTE_INFO_KEY_STOPS - 1, 1))
        key_stops = outbound[::step][:ROUTE_INFO_KEY_STOPS - 1] + outbound[-1:] if outbound else []
        return {
            **self._route_row(req.route),
            "authorized_estimated": bool(info.get("buses_estimated")),
            "distance_km": _num(info.get("distance_km")),
            "stops_count": len(stops),
            "key_stops": list(dict.fromkeys(key_stops)),
            "predicted": True,
        }

    def _other_route_info(self, route: str) -> dict:
        """승객을 예측하지 않는 노선: 전체 노선 정류장 파일과 노선 기본정보에 있는 값만"""
        guide = self.guide_engine
        guide._build()
        line, meta = guide.by_route.get(route, []), guide.route_meta.get(route, {})
        if not line and not meta:
            raise HTTPException(404, f"{route}번 노선 정보가 없습니다. 서울 버스 노선번호를 확인해 주세요.")
        # 노선 기본정보에 비어 있는 칸(NaN)이 있음 (심야 노선의 유형 등) → None으로
        text = lambda key: None if meta.get(key) is None or pd.isna(meta.get(key)) or not str(meta.get(key)).strip() else str(meta.get(key))
        outbound = [s["name"] for s in line[: max(len(line) // 2, 1)]]      # 앞쪽 절반 = 한 방향
        step = max(1, (len(outbound) - 1) // max(ROUTE_INFO_KEY_STOPS - 1, 1))
        key_stops = outbound[::step][:ROUTE_INFO_KEY_STOPS - 1] + outbound[-1:] if outbound else []
        return {
            "route": route, "route_type": text("route_type"), "company": text("company"),
            "start": text("start"), "end": text("end"),
            "first_bus": text("first_bus"), "last_bus": text("last_bus"),
            "interval_weekday": _num(meta.get("interval")), "min_interval": None, "max_interval": None,
            "run_minutes": _num(meta.get("run_minutes"), 0), "authorized_buses": None, "authorized_estimated": False,
            "distance_km": None, "stops_count": len(line), "key_stops": list(dict.fromkeys(key_stops)), "predicted": False,
        }

    def _require_model(self):
        if not self.forecaster.ready():
            raise HTTPException(503, "버스 예측 모델이 준비되지 않았습니다. python scripts/train_bus.py 를 먼저 실행하세요.")

    def _require_p90(self) -> dict:
        if self.p90 is None:
            self.p90 = load_ratios(self.p90_path)     # 서버를 띄운 뒤에 만들었을 수 있음
        if self.p90 is None:
            raise HTTPException(503, "승객 수 상한 비율표가 없습니다. python scripts/calibrate_p90.py 를 먼저 실행하세요.")
        return self.p90

    def _check_route(self, route: str):
        if route not in self.dataset.routes:
            raise HTTPException(404, f"지원하지 않는 노선입니다: {route} (가능: {', '.join(self.dataset.routes)})")

    # ==================================================
    # 1. 예측
    # ==================================================

    def predict(self, payload: dict) -> dict:
        req = BusPredictRequest(**payload)
        self._require_model()
        self._check_route(req.route)
        try:
            day = self.advisor.analyze_day(req.route, req.date)
        except ValueError as e:
            raise HTTPException(422, str(e))

        hourly = [
            {"hour": int(r.hour), "pred": _num(r.pred, 0), "baseline": _num(r.baseline, 0),
             "actual": _num(r.actual, 0), "trips": _num(r.trips, 1)}
            for r in day["hourly"].itertuples()
        ]
        h = day["hourly"]
        out = {
            "domain": "bus",
            "route": req.route,
            "date": f"{day['date']:%Y-%m-%d}",
            "daytype": day["daytype"],
            "is_forecast": day["is_future"],
            "model_version": self.forecaster.model_version,
            "summary": {
                "pred_total": _num(h["pred"].sum(), 0),
                "baseline_total": _num(h["baseline"].sum(min_count=1), 0),
                "actual_total": _num(h["actual"].sum(min_count=1), 0),
            },
            "hourly": hourly,
            "metrics": self.forecaster.metrics().get("by_route", {}).get(req.route),
        }

        if req.hour is not None:
            frame = day["frame"]
            fh = frame[frame["hour"] == req.hour].sort_values("stop_seq")
            names = self.advisor.stop_names(req.route)
            out["hour"] = req.hour
            # 이 시간대 가장 붐비는 구간의 혼잡도 (챗봇·화면이 승차 예측과 함께 보여 줌)
            hrow = day["hourly"][day["hourly"]["hour"] == req.hour]
            c = hrow["max_congestion"].iloc[0] if not hrow.empty else None
            has_c = c is not None and pd.notna(c)
            out["congestion"] = {"ratio": _num(c, 3) if has_c else None, "level": congestion_level(c) if has_c else None}
            out["stops"] = [
                {"seq": int(r.stop_seq), "stop_id": r.stop_id,
                 "name": names.get((r.stop_id, int(r.stop_seq)), {}).get("name", r.stop_id),
                 "pred": _num(r.pred), "baseline": _num(r.baseline), "actual": _num(r.boarding), "trips": _num(r.trips, 0)}
                for r in fh.itertuples()
            ]
        return out

    # ==================================================
    # 2. 권고
    # ==================================================

    def advise(self, payload: dict, llm: str, embedding: str, use_llm: bool) -> dict:
        req = BusAdviseRequest(**payload)
        self._require_model()
        self._check_route(req.route)
        try:
            result = self.advisor.advise(req.route, req.date, req.hour, llm=llm, embedding=embedding, use_llm=use_llm)
        except ValueError as e:
            raise HTTPException(422, str(e))
        result["domain"] = "bus"
        return result

    # ==================================================
    # 3. 지도 대시보드용 부가 API
    # ==================================================

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/api/bus", tags=["버스 지도·차트"])

        documents = Documents(self.knowledge_base.docs_dir)

        @r.get("/documents", response_model=DocumentList, summary="법규 문서 목록")
        def list_documents(role: DocumentRole = "citizen",
                           limit: int = Query(DOCUMENT_LIST_LIMIT, ge=1, le=DOCUMENT_LIST_MAX_LIMIT)):
            return documents.list(role, limit)

        @r.post("/documents", response_model=DocumentInfo, status_code=201, summary="법규 문서 추가")
        def create_document(file: UploadFile = File(...), title: str = Form(...),
                            type: str = Form(...), grade: str = Form(...), role: DocumentRole = "citizen"):
            require_document_admin(role)
            try:
                fields = DocumentFields(title=title, type=type, grade=grade)
            except ValidationError as exc:
                raise HTTPException(422, "제목, 종류, 등급을 확인해 주세요.") from exc
            return documents.create(file.filename or "", fields, file.file.read(MAX_DOCUMENT_BYTES + 1))

        @r.put("/documents/{file}", response_model=DocumentInfo, summary="법규 문서 수정")
        def update_document(file: str, fields: DocumentUpdate, role: DocumentRole = "citizen"):
            require_document_admin(role)
            return documents.update(file, fields)

        @r.delete("/documents/{file}", status_code=204, response_class=Response, summary="법규 문서 삭제")
        def delete_document(file: str, role: DocumentRole = "citizen"):
            require_document_admin(role)
            documents.delete(file)

        @r.get("/meta", summary="데이터 기간·노선·평가 지표")
        def meta():
            self._require_model()
            routes = [self._route_row(route) for route in self.dataset.routes]
            return {
                "data_mode": self.dataset.mode,
                "model_version": self.forecaster.model_version,
                "dates": self.forecaster.date_range(),
                "routes": routes,
                "metrics": self.forecaster.metrics(),
            }

        @r.get("/routes/{route}/geometry", summary="노선 경로·정류소·구간 좌표")
        def geometry(route: str):
            self._check_route(route)
            geo = self.dataset.geo(route)
            if geo is None:
                raise HTTPException(404, f"{route}번 좌표가 없습니다. python scripts/fetch_route_geo.py 실행 필요")
            return geo

        @r.get("/routes/{route}/timeseries", summary="24시간 실제·예측·베이스라인")
        def timeseries(route: str, date: dt.date = Query(...)):
            self._require_model()
            self._check_route(route)
            try:
                day = self.advisor.analyze_day(route, date)
            except ValueError as e:
                raise HTTPException(422, str(e))
            return {
                "route": route,
                "date": f"{day['date']:%Y-%m-%d}",
                "daytype": day["daytype"],
                "is_forecast": day["is_future"],
                "hours": [
                    {"hour": int(r.hour), "actual": _num(r.actual, 0), "pred": _num(r.pred, 0),
                     "baseline": _num(r.baseline, 0), "trips": _num(r.trips, 1),
                     "max_congestion": _num(r.max_congestion, 3),
                     "level": congestion_level(r.max_congestion) if pd.notna(r.max_congestion) else None}
                    for r in day["hourly"].itertuples()
                ],
            }

        @r.get("/routes/{route}/stops", summary="시간대 정류장별 승차·구간 혼잡도")
        def stops(route: str, date: dt.date = Query(...), hour: int = Query(..., ge=0, le=23)):
            self._require_model()
            self._check_route(route)
            try:
                day = self.advisor.analyze_day(route, date)
            except ValueError as e:
                raise HTTPException(422, str(e))
            names = self.advisor.stop_names(route)
            seg = day["segments"][day["segments"]["hour"] == hour].set_index(["stop_id", "stop_seq"])
            fh = day["frame"][day["frame"]["hour"] == hour].sort_values("stop_seq")
            rows = []
            for r in fh.itertuples():
                key = (r.stop_id, int(r.stop_seq))
                c = seg["congestion"].get(key) if key in seg.index else None
                geo_stop = names.get(key, {})
                rows.append({
                    "seq": int(r.stop_seq), "stop_id": r.stop_id, "name": geo_stop.get("name", r.stop_id),
                    "direction": geo_stop.get("direction"), "is_turn": bool(geo_stop.get("is_turn")),
                    "pred": _num(r.pred), "actual": _num(r.boarding), "baseline": _num(r.baseline),
                    "congestion": _num(c, 3), "level": congestion_level(c) if c is not None and pd.notna(c) else None,
                })
            return {"route": route, "date": f"{day['date']:%Y-%m-%d}", "hour": hour, "stops": rows}

        @r.post("/advise/all", summary="선택 시간대 전체 노선 배차 권고 (needs_review)")
        async def advise_all(req: BusAdviseAllRequest):
            self._require_model()
            items = await run_in_threadpool(
                self.advisor.advise_all, self.dataset.routes, req.date, req.hour, req.llm, req.embedding, req.use_llm,
            )
            for item in items:
                if item.get("status") == "ok":
                    item["domain"] = "bus"
                    item["advisory_id"] = await repo.save_advisory("bus", item, req.llm)
                    item["db_saved"] = item["advisory_id"] is not None
            ok = [i for i in items if i.get("status") == "ok"]
            summary = {a: sum(1 for i in ok if i["recommendation"]["action"] == a)
                       for a in ("increase", "increase_limited", "decrease", "keep")}
            # 권고 이력에는 '이 시간대 전체 노선 권고' 1건으로 묶어서 표시
            ids = [i["advisory_id"] for i in ok if i.get("advisory_id")]
            batch_id = await repo.save_batch("bus", f"{req.date:%Y-%m-%d}", req.hour, req.llm, summary, ids) if ids else None
            return {"batch_id": batch_id, "date": f"{req.date:%Y-%m-%d}", "hour": req.hour, "summary": summary, "items": items}

        @r.post("/simulate", response_model=BusSimulateResponse, summary="배차간격 변경 시뮬레이션 (needs_review)")
        def simulate_interval(req: BusSimulateRequest):
            return self.simulate(req.model_dump())

        @r.post("/optimize", response_model=BusOptimizeResponse, summary="전 노선 배차 최적화 (needs_review)")
        async def optimize_dispatch(req: BusOptimizeRequest):
            # 풀이가 수 초 걸려 다른 요청을 막지 않게 스레드에서 돌림. 저장 실패는 결과를 막지 않음 (db_saved: false)
            result = await run_in_threadpool(self.optimize, req.model_dump())
            run_id = await repo.save_optimization_run("bus", result, req.hours)
            return {**result, "run_id": run_id, "db_saved": run_id is not None}

        @r.get("/backtest", response_model=BusBacktestResponse, summary="배차 방식 성능 비교 (F-14 결과 파일)")
        def backtest():
            try:
                return backtest_summary()
            except FileNotFoundError:
                raise HTTPException(404, "성능 비교 결과가 없습니다. 실데이터로 python scripts/backtest.py를 먼저 실행하세요")
            except ValueError as e:
                raise HTTPException(500, str(e))

        @r.get("/optimize/runs", response_model=BusOptimizeRunList, summary="최적화 실행 이력 (최근부터)")
        async def optimize_runs(limit: int = Query(OPTIMIZE_RUNS_LIMIT, ge=1, le=LIST_LIMIT_MAX)):
            try:
                items = await repo.list_optimization_runs("bus", limit)
            except Exception as e:      # 서버가 뜬 뒤에 DB가 꺼진 경우 (시작할 때 꺼져 있었으면 빈 목록)
                logger.warning("최적화 이력 조회 실패: %s", e)
                raise HTTPException(503, "DB에 연결할 수 없어 실행 기록을 불러오지 못했습니다")
            return {"db": db.state, "items": items}

        @r.get("/drift", response_model=BusDriftList, summary="예측 오차 점검과 재학습 권고 (요청 R-3, 최근 기간부터)")
        async def drift_checks(limit: int = Query(DRIFT_LIST_LIMIT, ge=1, le=LIST_LIMIT_MAX)):
            items = drift.list_checks(limit)
            try:
                reviews = await repo.get_retrain_reviews("bus", [item["id"] for item in items])
            except Exception as e:      # 서버가 뜬 뒤에 DB가 꺼진 경우: 판정은 보여 주고 결정만 비움
                logger.warning("재학습 결정 조회 실패: %s", e)
                reviews = {}
            rules = {"mae_ratio": DRIFT_MAE_RATIO, "p90_coverage_min": DRIFT_P90_COVERAGE_MIN, "min_days": DRIFT_MIN_DAYS}
            return {"db": db.state, "rules": rules,
                    "items": [{**item, "review": reviews.get(item["id"])} for item in items]}

        @r.post("/drift/{check_id}/review", response_model=BusDriftCheck, summary="재학습 권고에 대한 담당자 결정 (승인·보류·반려)")
        async def review_drift(check_id: str, req: BusDriftReviewRequest):
            item = drift.load_check(check_id)
            if item is None:
                raise HTTPException(404, f"점검 결과가 없습니다: {check_id}")
            try:
                review = await repo.save_retrain_review("bus", check_id, item["verdict"], req.status, req.reviewer, req.note)
            except Exception as e:
                logger.warning("재학습 결정 저장 실패: %s", e)
                review = None
            if review is None:
                raise HTTPException(503, "DB에 연결할 수 없어 결정을 남기지 못했습니다. MariaDB를 켠 뒤 다시 시도하세요")
            return {**item, "review": review}

        @r.get("/overview", summary="전체 노선 구간별 혼잡도 (지도)")
        def overview(date: dt.date = Query(...), hour: int = Query(..., ge=0, le=23)):
            self._require_model()
            try:
                return {"date": f"{date:%Y-%m-%d}", **self.advisor.overview(self.dataset.routes, date, hour)}
            except ValueError as e:
                raise HTTPException(422, str(e))

        @r.get("/stops/search", response_model=list[BusStopSearchItem], summary="정류장 이름 검색 (길안내 입력용)")
        def stop_search(q: str = Query(..., min_length=1, max_length=50), limit: int = Query(10, ge=1, le=GUIDE_SEARCH_LIMIT_MAX)):
            return self.guide_engine.search(q, limit)

        @r.get("/routes/{route}/info", response_model=BusRouteInfoResponse, summary="노선 기본 정보 (기점·종점·첫차·막차·배차간격·주요 정류장)")
        def route_info_api(route: str):
            return self.route_info({"route": route})

        @r.post("/guide", response_model=BusGuideResponse, summary="버스 길안내 (직행·1회 환승, 혼잡도 반영)")
        def guide_route(req: BusGuideRequest):
            return self.guide(req.model_dump())

        return r
