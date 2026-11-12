# ==================================================
# 버스 도메인 어댑터 (1차 스프린트)
# ==================================================
# 공통 API(/api/bus/predict·ask·advise) + 지도 대시보드용 부가 API
#   GET /api/bus/meta                         데이터 기간·노선 목록·평가 지표
#   GET /api/bus/routes/{route}/geometry      노선 경로·정류소·구간 좌표
#   GET /api/bus/routes/{route}/timeseries    24시간 실제/예측/베이스라인 + 시간대별 최대 혼잡도
#   GET /api/bus/overview?date=&hour=         전체 노선 구간별 혼잡도 (지도 색칠)
#   POST /api/bus/simulate                    배차간격을 바꿨을 때의 구간별 혼잡도
#   POST /api/bus/optimize                    전 노선 운행횟수를 인가대수 안에서 한꺼번에 권고

import datetime as dt
import subprocess
import sys

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.core.domain import DomainAdapter
from app.domains.bus.advisor import BusAdvisor, _num, congestion_level
from app.domains.bus.data import BusDataset
from app.domains.bus.forecaster import BusForecaster
from app.db import repository as repo
from app.domains.bus.calibration import load_ratios
from app.domains.bus.optimizer import optimize
from app.domains.bus.schemas import (
    BusAdviseAllRequest, BusAdviseRequest, BusOptimizeRequest, BusOptimizeResponse, BusPredictRequest,
    BusSimulateRequest, BusSimulateResponse,
)
from app.domains.bus.simulator import simulate


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

        @r.get("/meta", summary="데이터 기간·노선·평가 지표")
        def meta():
            self._require_model()
            routes = []
            for route in self.dataset.routes:
                info = self.dataset.route_info_for(route, "weekday")
                routes.append({
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
                })
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
            self._require_model()
            self._check_route(req.route)
            try:
                return simulate(self.advisor, req.route, req.date, req.hour, req.interval)
            except ValueError as e:
                raise HTTPException(422, str(e))

        @r.post("/optimize", response_model=BusOptimizeResponse, summary="전 노선 배차 최적화 (needs_review)")
        def optimize_dispatch(req: BusOptimizeRequest):
            self._require_model()
            table = self._require_p90()
            try:
                return optimize(self.advisor, table, self.dataset.routes, req.date, req.hours, req.weights.model_dump())
            except ValueError as e:
                raise HTTPException(422, str(e))

        @r.get("/overview", summary="전체 노선 구간별 혼잡도 (지도)")
        def overview(date: dt.date = Query(...), hour: int = Query(..., ge=0, le=23)):
            self._require_model()
            try:
                return {"date": f"{date:%Y-%m-%d}", **self.advisor.overview(self.dataset.routes, date, hour)}
            except ValueError as e:
                raise HTTPException(422, str(e))

        return r
