# ==================================================
# 버스 배차 권고
# ==================================================
# 혼잡도 계산 (시간 기준 = 버스가 기점을 출발한 시각)
#   1) N시부터 3시간 동안의 예측 승차 합 × 구간 환산 비율 = N시 출발 버스들의 구간별 예상 재차인원 합
#   2) ÷ N시 기점 출발 횟수 = 버스 1대당 평균 재차인원
#   3) ÷ 차량 정원 = 혼잡도 (0.45 여유 / 0.8 보통 / 1.0 혼잡 / 그 이상 매우혼잡)
#   실제 혼잡도는 원본 구간 재차인원이 있는 날이면 그 값을 그대로 쓰고(재차인원 ÷ 출발 횟수 ÷ 정원),
#   없는 날은 실제 승차로 같은 계산을 합니다.
#   원본 재차인원의 "N시"가 기점 출발 시각 기준이라, 정류장을 지난 시각 기준인 운행횟수로 나누면
#   새벽·심야 혼잡도가 크게 어긋납니다 (app/config.py, docs/decisions.md).
#
# 권고 규칙 (최대 혼잡 구간 기준)
#   혼잡도 ≥ 0.8  → 배차간격 단축(증차): 목표 혼잡도 0.7이 되는 운행횟수
#   혼잡도 < 0.25 → 감차 검토
#   그 외         → 현행 유지
#   권고 간격은 노선 기본정보의 최소·최대 배차간격 안으로 제한
#
# 근거: 규정 지식베이스(/ask) 질의 결과를 첨부, needs_review=True

import math

import numpy as np
import pandas as pd

from app.config import (
    BUS_CAPACITY,
    BUS_DEPARTURE_STOPS,
    CONGESTION_HIGH,
    CONGESTION_LEVELS,
    CONGESTION_LOW,
    CONGESTION_TARGET,
)
from app.core.advisor import Advisor
from app.domains.bus.data import BusDataset
from app.domains.bus.forecaster import BusForecaster, forward_window

ACTION_LABELS = {
    "increase": "배차간격 단축 (증차)",
    "increase_limited": "최소 배차간격 도달 - 별도 대책 검토",
    "decrease": "감차 검토",
    "keep": "현행 유지",
}

EVIDENCE_QUESTIONS = {
    "increase": "혼잡한 시간대에 시내버스 운행횟수를 늘리거나 배차간격을 줄이려면 어떤 기준과 절차(사업계획 변경 등)를 따라야 하나요?",
    "increase_limited": "버스 차내 혼잡이 심한 구간과 시간대에 배차간격을 줄이는 것 외에 어떤 혼잡 완화 대책이 있나요?",
    "decrease": "시내버스 운행횟수를 줄이거나 배차간격을 늘리는 사업계획 변경은 어떤 기준과 절차를 따르나요?",
    "keep": "시내버스 노선의 운행횟수와 배차간격을 조정할 때 지켜야 할 기준은 무엇인가요?",
}


def congestion_level(c: float) -> str:
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return "정보없음"
    for upper, label in CONGESTION_LEVELS:
        if c < upper:
            return label
    return CONGESTION_LEVELS[-1][1]


def departures(frame: pd.DataFrame) -> pd.Series:
    """시간별 기점 출발 횟수: 정류장 순서가 가장 앞인 몇 곳의 운행횟수 중앙값 (한 곳만 보면 집계 누락에 흔들림)"""
    first = sorted(frame["stop_seq"].unique())[:BUS_DEPARTURE_STOPS]
    return frame[frame["stop_seq"].isin(first)].groupby("hour")["trips"].median()


def _num(x, digits=1):
    return None if x is None or pd.isna(x) else round(float(x), digits)


class BusAdvisor(Advisor):
    def __init__(self, knowledge_base, forecaster: BusForecaster, dataset: BusDataset):
        super().__init__(knowledge_base)
        self.fc = forecaster
        self.ds = dataset
        self._day_cache: dict[tuple, dict] = {}
        self._stop_names: dict[str, dict] = {}

    def clear_cache(self):
        self._day_cache.clear()

    def stop_names(self, route: str) -> dict:
        if route not in self._stop_names:
            geo = self.ds.geo(route) or {"stops": []}
            self._stop_names[route] = {(s["station_id"], s["seq"]): s for s in geo["stops"]}
        return self._stop_names[route]

    # ==================================================
    # 1. 하루 단위 분석 (지도·차트·권고 공용, 캐시)
    # ==================================================

    def analyze_day(self, route: str, date) -> dict:
        date = pd.Timestamp(date).normalize()
        key = (route, date)
        if key in self._day_cache:
            return self._day_cache[key]

        frame, is_future = self.fc.day_frame(date, route)
        if frame.empty:
            raise ValueError(f"{route}번 {date:%Y-%m-%d} 데이터가 없습니다 (원천 결측일일 수 있음)")
        daytype = frame["daytype"].iloc[0]

        # 시간대별 노선 합계. trips = 기점 출발 횟수 (출발이 없는 시간대는 비어 있음)
        hourly = frame.groupby("hour").agg(
            actual=("boarding", lambda s: s.sum(min_count=1)),
            pred=("pred", "sum"),
            baseline=("baseline", lambda s: s.sum(min_count=1)),
        )
        hourly["trips"] = departures(frame)
        hourly.loc[~(hourly["trips"] >= 1), "trips"] = np.nan
        # N시 출발 버스가 태우는 승객 = N시부터 몇 시간 동안의 승차 합
        hourly["pred_window"] = forward_window(hourly[["pred"]].T).iloc[0]
        hourly["actual_window"] = forward_window(hourly[["actual"]].T).iloc[0]
        hourly = hourly.reset_index()

        # 구간별 재차인원·혼잡도
        prof = self.fc.profile_for(route, daytype)
        seg = prof.merge(hourly[["hour", "pred", "actual", "pred_window", "actual_window", "trips"]], on="hour")
        seg["onboard"] = seg["ratio"] * seg["pred_window"]
        seg["onboard_actual"] = seg["ratio"] * seg["actual_window"]
        # 원본 구간 재차인원이 있는 날은 추정값 대신 그 값을 실제로 씀
        observed = self.ds.section_long
        observed = observed[(observed["date"] == date) & (observed["route"] == route)]
        if not observed.empty:
            keys = ["stop_id", "stop_seq", "to_stop_id", "hour"]
            seg = seg.merge(observed[keys + ["onboard"]].rename(columns={"onboard": "onboard_observed"}), on=keys, how="left")
            seg["onboard_actual"] = seg["onboard_observed"].where(seg["actual"].notna())
            seg = seg.drop(columns="onboard_observed")
        seg["per_bus"] = seg["onboard"] / seg["trips"]
        seg["congestion"] = seg["per_bus"] / BUS_CAPACITY
        seg["congestion_actual"] = seg["onboard_actual"] / seg["trips"] / BUS_CAPACITY
        seg = seg.replace([np.inf, -np.inf], np.nan)

        valid = seg.dropna(subset=["congestion"])
        peak = valid.loc[valid.groupby("hour")["congestion"].idxmax()]
        hourly = hourly.merge(
            peak[["hour", "congestion", "congestion_actual"]].rename(
                columns={"congestion": "max_congestion", "congestion_actual": "max_congestion_actual"}),
            on="hour", how="left")

        result = {"route": route, "date": date, "daytype": daytype, "is_future": is_future,
                  "frame": frame, "hourly": hourly, "segments": seg,
                  "actual_source": "observed" if not observed.empty else "estimated"}
        self._day_cache[key] = result
        return result

    # ==================================================
    # 2. 시간대 권고 수치
    # ==================================================

    def hour_state(self, route: str, date, hour: int) -> dict:
        """시간대의 구간별 혼잡도와 현재 운행 상태 (권고·시뮬레이션 공용)

        시간은 기점 출발 시각 기준이고, 현재 운행횟수는 그 시각의 기점 출발 횟수입니다.
        출발이 없는 시간대(심야 등)는 ValueError.
        """
        day = self.analyze_day(route, date)
        seg = day["segments"][day["segments"]["hour"] == hour].dropna(subset=["congestion"])
        hrow = day["hourly"][day["hourly"]["hour"] == hour]
        if seg.empty or hrow.empty:
            raise ValueError(f"{route}번 {hour}시는 기점에서 출발하는 버스가 없는 시간대입니다")
        hrow = hrow.iloc[0]

        top = seg.loc[seg["congestion"].idxmax()]
        cur_trips = float(hrow["trips"])
        info = self.ds.route_info_for(route, day["daytype"])
        return {
            "day": day, "segments": seg, "hourly": hrow, "top": top, "info": info,
            "cur_trips": cur_trips,
            "min_interval": float(info.get("min_interval") or 3),
            "max_interval": float(info.get("max_interval") or 20),
        }

    def analyze_hour(self, route: str, date, hour: int) -> dict:
        state = self.hour_state(route, date, hour)
        day, hrow, top, info = state["day"], state["hourly"], state["top"], state["info"]

        names = self.stop_names(route)
        c_max = float(top["congestion"])
        cur_trips = state["cur_trips"]
        cur_interval = 60.0 / cur_trips

        min_iv, max_iv = state["min_interval"], state["max_interval"]
        run_minutes = float(info.get("run_minutes") or 180)

        # 목표 혼잡도가 되려면 필요한 운행횟수
        needed = math.ceil(float(top["onboard"]) / (BUS_CAPACITY * CONGESTION_TARGET)) if top["onboard"] > 0 else 1

        if c_max >= CONGESTION_HIGH:
            rec_trips = max(needed, cur_trips + 1)
            rec_interval = min(max(60.0 / rec_trips, min_iv), max_iv)
            action = "increase" if rec_interval < cur_interval - 0.05 else "increase_limited"
        elif c_max < CONGESTION_LOW and cur_interval < max_iv:
            rec_trips = max(needed, 60.0 / max_iv)
            rec_interval = min(max(60.0 / rec_trips, min_iv), max_iv)
            action = "decrease" if rec_interval > cur_interval + 0.05 else "keep"
        else:
            rec_interval, action = cur_interval, "keep"
        if action in {"keep", "increase_limited"}:
            rec_interval = cur_interval if action == "keep" else min(cur_interval, min_iv)

        rec_trips = 60.0 / rec_interval
        extra_buses = math.ceil(run_minutes / rec_interval) - math.ceil(run_minutes / cur_interval)
        expected_c = c_max * cur_trips / rec_trips

        stop_from = names.get((top["stop_id"], int(top["stop_seq"])), {})
        to_candidates = [s for (sid, _), s in names.items() if sid == top["to_stop_id"]]
        stop_to = to_candidates[0] if to_candidates else {}
        peak_segment = {
            "seq": int(top["stop_seq"]),
            "from_stop_id": top["stop_id"],
            "from_name": stop_from.get("name", top["stop_id"]),
            "to_stop_id": top["to_stop_id"],
            "to_name": stop_to.get("name", top["to_stop_id"]),
            "lat": stop_from.get("lat"),
            "lng": stop_from.get("lng"),
        }

        level = congestion_level(c_max)
        rationale = [
            f"예측 승차 {hrow['pred']:.0f}명/시간 (작년 같은 요일·시간 "
            + (f"{hrow['baseline']:.0f}명" if pd.notna(hrow["baseline"]) else "-")
            + (f", 실제 {hrow['actual']:.0f}명)" if pd.notna(hrow["actual"]) else ")"),
            f"최대 혼잡 구간 {peak_segment['from_name']} → {peak_segment['to_name']}: "
            f"버스 1대당 약 {top['per_bus']:.0f}명 / 정원 {BUS_CAPACITY}명 → 혼잡도 {c_max:.2f} ({level})",
            f"현재 기점 출발 {cur_trips:.0f}회/시간 (약 {cur_interval:.1f}분 간격), 노선 기준 배차 {min_iv:.0f}~{max_iv:.0f}분",
            f"목표 혼잡도 {CONGESTION_TARGET} 기준 필요 운행 {needed}회/시간",
        ]
        if action == "increase_limited":
            rationale.append("이미 최소 배차간격 수준이라 간격 단축만으로는 한계 → 구간 맞춤버스(다람쥐버스) 등 검토")

        return {
            "route": route,
            "date": f"{day['date']:%Y-%m-%d}",
            "hour": hour,
            "daytype": day["daytype"],
            "is_forecast": day["is_future"],
            "prediction": {
                "boarding_total": _num(hrow["pred"], 0),
                "baseline_total": _num(hrow["baseline"], 0),
                "actual_total": _num(hrow["actual"], 0),
                "max_onboard_per_bus": _num(top["per_bus"], 1),
                "peak_segment": peak_segment,
            },
            "congestion": {
                "ratio": round(c_max, 3),
                "level": level,
                "capacity": BUS_CAPACITY,
                "actual_ratio": _num(top["congestion_actual"], 3),
                "thresholds": {"increase": CONGESTION_HIGH, "decrease": CONGESTION_LOW, "target": CONGESTION_TARGET},
            },
            "current": {
                "trips_per_hour": round(cur_trips, 1),
                "interval_min": round(cur_interval, 1),
                "scheduled_interval_min": _num(info.get("interval")),
                "authorized_buses": _num(info.get("buses"), 0),
            },
            "recommendation": {
                "action": action,
                "label": ACTION_LABELS[action],
                "recommended_interval_min": round(rec_interval, 1),
                "recommended_trips_per_hour": round(rec_trips, 1),
                "extra_buses": int(extra_buses),
                "expected_congestion": round(expected_c, 3),
                "bounds_min": [min_iv, max_iv],
            },
            "rationale": rationale,
        }

    # ==================================================
    # 3. 권고 (수치 + 규정 근거)
    # ==================================================

    def advise(self, route: str, date, hour: int, llm: str, embedding: str, use_llm: bool) -> dict:
        advice = self.analyze_hour(route, date, hour)
        question = EVIDENCE_QUESTIONS[advice["recommendation"]["action"]]
        evidence = self.evidence(question, llm=llm, embedding=embedding, use_llm=use_llm)

        by_route = self.fc.metrics().get("by_route", {}).get(route, {})
        if by_route.get("mean_actual"):
            base_conf = 1 - by_route["model_mae"] / by_route["mean_actual"]
        else:
            base_conf = 0.6
        if advice["is_forecast"]:
            base_conf -= 0.05

        advice["model_version"] = self.fc.model_version
        return self.finalize(advice, evidence, base_conf)

    def advise_all(self, routes: list[str], date, hour: int, llm: str, embedding: str, use_llm: bool) -> list[dict]:
        """선택한 시간대의 전체 노선 권고 (증차 필요 → 감차 가능 → 유지 순)

        규정 근거는 권고 유형별로 한 번만 조회해 노선끼리 공유 (LLM 호출 최소화)
        """
        evidence_cache: dict[str, dict] = {}
        by_route = self.fc.metrics().get("by_route", {})
        items = []
        for route in routes:
            try:
                advice = self.analyze_hour(route, date, hour)
            except ValueError as e:
                items.append({"route": route, "date": f"{pd.Timestamp(date):%Y-%m-%d}", "hour": hour,
                              "status": "no_service", "detail": str(e)})
                continue
            action = advice["recommendation"]["action"]
            if action not in evidence_cache:
                evidence_cache[action] = self.evidence(EVIDENCE_QUESTIONS[action], llm=llm, embedding=embedding, use_llm=use_llm)
            m = by_route.get(route, {})
            base_conf = 1 - m["model_mae"] / m["mean_actual"] if m.get("mean_actual") else 0.6
            if advice["is_forecast"]:
                base_conf -= 0.05
            advice["model_version"] = self.fc.model_version
            advice["status"] = "ok"
            items.append(self.finalize(advice, evidence_cache[action], base_conf))

        priority = {"increase": 0, "increase_limited": 1, "decrease": 2, "keep": 3}
        return sorted(items, key=lambda a: (
            a.get("status") != "ok",
            priority.get(a.get("recommendation", {}).get("action"), 9),
            -(a.get("congestion", {}).get("ratio") or 0),
        ))

    # ==================================================
    # 4. 지도용 개요
    # ==================================================

    def overview(self, routes: list[str], date, hour: int) -> dict:
        out = []
        for route in routes:
            try:
                day = self.analyze_day(route, date)
            except ValueError:
                continue
            seg = day["segments"][day["segments"]["hour"] == hour]
            names = self.stop_names(route)
            segments, hotspots = [], []
            for row in seg.itertuples():
                c = None if pd.isna(row.congestion) else round(float(row.congestion), 3)
                segments.append({"seq": int(row.stop_seq), "from_station": row.stop_id,
                                 "congestion": c, "level": congestion_level(c),
                                 "per_bus": _num(row.per_bus)})
                if c is not None and c >= CONGESTION_HIGH:
                    s = names.get((row.stop_id, int(row.stop_seq)), {})
                    hotspots.append({"route": route, "seq": int(row.stop_seq), "name": s.get("name", row.stop_id),
                                     "lat": s.get("lat"), "lng": s.get("lng"), "congestion": c})
            max_c = seg["congestion"].max()
            hrow = day["hourly"][day["hourly"]["hour"] == hour]
            out.append({
                "route": route,
                "max_congestion": _num(max_c, 3),
                "level": congestion_level(max_c) if pd.notna(max_c) else "운행없음",
                "pred_total": _num(hrow["pred"].iloc[0], 0) if not hrow.empty else None,
                "segments": segments,
                "hotspots": sorted(hotspots, key=lambda h: -h["congestion"])[:5],
            })
        return {"hour": hour, "routes": out}
