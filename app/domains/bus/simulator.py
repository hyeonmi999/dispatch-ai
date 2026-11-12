# ==================================================
# 배차간격 변경 시뮬레이션  (F-12)
# ==================================================
# "이 노선을 몇 분마다 보내면 얼마나 덜 붐비나"를 계산합니다.
#
#   1) 운행횟수 = 60 ÷ 배차간격
#   2) 구간별 혼잡도 = 지금 혼잡도 × (지금 운행횟수 ÷ 바꾼 운행횟수)
#      최대 혼잡 구간에서는 재차인원 ÷ (바꾼 운행횟수 × 정원)과 같은 값입니다.
#      구간마다 지나가는 버스 수가 조금씩 달라서, 그 차이는 그대로 두고 전체를 같은 비율로 늘리거나 줄입니다.
#      (같은 간격을 넣으면 지금과 똑같은 결과가 나옵니다)
#   3) 지금과 바꾼 뒤의 최대 혼잡도 비교
#
# 구간별 재차인원과 지금 운행횟수는 BusAdvisor(analyze_day)의 계산을 그대로 씁니다.
# 가정: 승객 수는 그대로. 배차가 잦아지면 승객이 늘어나는 효과는 근거 수치가 없어 넣지 않았습니다.

from app.core.domain import InvalidRequest
from app.domains.bus.advisor import BusAdvisor, congestion_level

ASSUMPTION = "승객 수는 그대로라고 가정 (배차가 잦아져 늘어나는 승객은 반영하지 않음)"
DAYTYPE_LABELS = {"weekday": "평일", "saturday": "토요일", "holiday": "공휴일"}


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
        "peak_segment": {
            "seq": int(top["stop_seq"]),
            "from_name": names.get((top["stop_id"], int(top["stop_seq"])), {}).get("name", top["stop_id"]),
        },
        "segments": segments,
        "assumption": ASSUMPTION,
        "needs_review": True,
    }
