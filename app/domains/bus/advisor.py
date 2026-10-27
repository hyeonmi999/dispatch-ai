# ==================================================
# 버스 배차 권고
# ==================================================
# 혼잡도 계산
#   1) 예측한 노선 전체 승차(시간대) × 구간 환산 비율 = 구간별 예상 재차인원(시간 합계)
#   2) ÷ 그 시간대 운행횟수 = 버스 1대당 평균 재차인원
#   3) ÷ 차량 정원 = 혼잡도 (0.45 여유 / 0.8 보통 / 1.0 혼잡 / 그 이상 매우혼잡)
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
    CONGESTION_HIGH,
    CONGESTION_LEVELS,
    CONGESTION_LOW,
    CONGESTION_TARGET,
)
from app.core.advisor import Advisor
from app.domains.bus.data import BusDataset
from app.domains.bus.forecaster import BusForecaster

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

        # 시간대별 노선 합계
        hourly = frame.groupby("hour").agg(
            actual=("boarding", lambda s: s.sum(min_count=1)),
            pred=("pred", "sum"),
            baseline=("baseline", lambda s: s.sum(min_count=1)),
            trips=("trips", "median"),
        ).reset_index()

        # 구간별 재차인원·혼잡도
        prof = self.fc.profile_for(route, daytype)
        seg = prof.merge(hourly[["hour", "pred", "actual"]], on="hour")
        seg = seg.merge(frame[["stop_id", "stop_seq", "hour", "trips"]], on=["stop_id", "stop_seq", "hour"], how="left")
        # 일부 정류장은 운행횟수가 1~2회로 잡히는 경우가 있어(단축 운행·집계 누락) 혼잡도가 과대 계산됨
        # → 그 시간대 노선 중앙값의 절반 미만이면 중앙값으로 대체
        median_trips = seg["hour"].map(hourly.set_index("hour")["trips"])
        seg["trips"] = seg["trips"].where(seg["trips"] >= 0.5 * median_trips, median_trips)
        seg["onboard"] = seg["ratio"] * seg["pred"]
        seg["onboard_actual"] = seg["ratio"] * seg["actual"]
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
                  "frame": frame, "hourly": hourly, "segments": seg}
        self._day_cache[key] = result
        return result

    # ==================================================
    # 2. 시간대 권고 수치
    # ==================================================

    def analyze_hour(self, route: str, date, hour: int) -> dict:
        day = self.analyze_day(route, date)
        seg = day["segments"][day["segments"]["hour"] == hour].dropna(subset=["congestion"])
        hrow = day["hourly"][day["hourly"]["hour"] == hour]
        if seg.empty or hrow.empty:
            raise ValueError(f"{route}번 {hour}시는 운행 데이터가 없는 시간대입니다")
        hrow = hrow.iloc[0]

        names = self.stop_names(route)
        top = seg.loc[seg["congestion"].idxmax()]
        c_max = float(top["congestion"])
        cur_trips = float(top["trips"]) if top["trips"] > 0 else float(hrow["trips"])
        cur_interval = 60.0 / cur_trips

        info = self.ds.route_info_for(route, day["daytype"])
        min_iv = float(info.get("min_interval") or 3)
        max_iv = float(info.get("max_interval") or 20)
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
            f"현재 {cur_trips:.0f}회/시간 (약 {cur_interval:.1f}분 간격), 노선 기준 배차 {min_iv:.0f}~{max_iv:.0f}분",
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
