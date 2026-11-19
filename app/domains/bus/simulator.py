# ==================================================
# 배차간격 변경 시뮬레이션  (F-12)
# ==================================================
# "이 노선을 몇 분마다 보내면 얼마나 덜 붐비나"를 계산합니다.
#
#   1) 운행횟수 = 60 ÷ 배차간격
#   2) 구간별 혼잡도 = 지금 혼잡도 × (지금 운행횟수 ÷ 바꾼 운행횟수)
#      재차인원 ÷ (바꾼 운행횟수 × 정원)과 같은 값입니다. (같은 간격을 넣으면 지금과 똑같은 결과가 나옵니다)
#   3) 지금과 바꾼 뒤의 최대 혼잡도 비교
#   4) 필요한 버스 대수: 이 시간대만 바꾸고 나머지는 지금대로 둘 때, 도로 위 버스가 가장 많은 순간
#      (최근 왕복 운행시간 동안 기점을 출발한 버스 수. 배차 최적화의 인가대수 제약과 같은 계산)
#      인가대수를 넘으면 막지는 않고 넘는다고 알려 줍니다.
#
# 시간은 버스가 기점을 출발하는 시각 기준이고, 운행횟수는 기점 출발 횟수입니다.
# 구간별 재차인원과 지금 운행횟수는 BusAdvisor(analyze_day)의 계산을 그대로 씁니다.
# 가정: 승객 수는 그대로. 배차가 잦아지면 승객이 늘어나는 효과는 근거 수치가 없어 넣지 않았습니다.

from app.core.domain import InvalidRequest
from app.domains.bus.advisor import BusAdvisor, congestion_level, departures_by_hour
from app.domains.bus.fleet import peak_on_road

ASSUMPTION = "그 시각에 기점을 출발하는 버스 기준. 승객 수는 그대로라고 가정 (배차가 잦아져 늘어나는 승객은 반영하지 않음)"
DAYTYPE_LABELS = {"weekday": "평일", "saturday": "토요일", "holiday": "공휴일"}


def fleet_need(state: dict, hour: int, new_trips: float) -> dict:
    """hour 시의 출발 횟수만 new_trips로 바꿨을 때 필요한 버스 대수와 인가대수"""
    info = state["info"]
    run_minutes = int(round(float(info.get("run_minutes") or 0)))
    authorized = int(info.get("buses") or 0)
    current = departures_by_hour(state["day"])
    changed = current.copy()
    changed[hour] = new_trips
    before, after = peak_on_road(current, run_minutes, hour), peak_on_road(changed, run_minutes, hour)
    known = authorized > 0 and run_minutes > 0
    return {
        "buses_needed_before": before,
        "buses_needed": after,
        "authorized_buses": authorized if known else None,
        "authorized_estimated": bool(info.get("buses_estimated", False)),
        "over": bool(known and after > authorized),
    }


def simulate(advisor: BusAdvisor, route: str, date, hour: int, interval: float) -> dict:
    """배차간격을 interval(분)로 바꿨을 때의 구간별 혼잡도

    interval이 노선 기본정보의 최소~최대 배차간격을 벗어나면 InvalidRequest,
    그 시간대에 운행 데이터가 없으면 ValueError를 올립니다.
    """
    state = advisor.hour_state(route, date, hour)
    day = state["day"]
    low, high = state["min_interval"], state["max_interval"]
    if not low <= interval <= high:
        daytype = DAYTYPE_LABELS.get(day["daytype"], day["daytype"])
        raise InvalidRequest(
            f"{route}번 {daytype} 배차간격은 {low:g}~{high:g}분 범위만 가능합니다 (요청 {interval:g}분)")

    cur_trips = state["cur_trips"]
    new_trips = 60.0 / interval
    scale = cur_trips / new_trips

    names = advisor.stop_names(route)
    segments = []
    for row in state["segments"].sort_values("stop_seq").itertuples():
        before = float(row.congestion)
        after = before * scale
        stop = names.get((row.stop_id, int(row.stop_seq)), {})
        segments.append({
            "seq": int(row.stop_seq),
            "from_name": stop.get("name", row.stop_id),
            "congestion_before": round(before, 3),
            "level_before": congestion_level(before),
            "congestion": round(after, 3),
            "level": congestion_level(after),
        })

    top = state["top"]
    max_before = float(top["congestion"])
    max_after = max_before * scale
    return {
        "route": route,
        "date": f"{day['date']:%Y-%m-%d}",
        "hour": hour,
        "daytype": day["daytype"],
        "is_forecast": day["is_future"],
        "current": {
            "interval": round(60.0 / cur_trips, 2),
            "trips_per_hour": round(cur_trips, 2),
            "max_congestion": round(max_before, 3),
            "level": congestion_level(max_before),
        },
        "simulated": {
            "interval": round(interval, 2),
            "trips_per_hour": round(new_trips, 2),
            "max_congestion": round(max_after, 3),
            "level": congestion_level(max_after),
        },
        "bounds": {"min_interval": low, "max_interval": high},
        "fleet": fleet_need(state, hour, new_trips),
        "peak_segment": {
            "seq": int(top["stop_seq"]),
            "from_name": names.get((top["stop_id"], int(top["stop_seq"])), {}).get("name", top["stop_id"]),
        },
        "segments": segments,
        "assumption": ASSUMPTION,
        "needs_review": True,
    }
