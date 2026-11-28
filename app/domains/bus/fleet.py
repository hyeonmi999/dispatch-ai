# ==================================================
# 도로 위 버스 대수  (배차 최적화·시뮬레이션·권고 공용)
# ==================================================
# 어느 시각이든 "도로 위에 있는 버스" = 최근 왕복 운행시간 동안 기점을 출발한 버스 수.
# 출발한 버스는 한 바퀴 돌 때까지 다시 쓸 수 없으므로 이 값이 인가대수를 넘으면 운행할 수 없습니다.
#
# 운행시간(run_minutes)은 둘 중 하나입니다.
#   정수          노선당 고정 운행시간 (노선 기본정보, 지금까지의 방식)
#   24칸 목록     기점 출발 시각(0~23시)별 한 바퀴 운행시간 (이슈 #42, runtime.py의 표)
#                 h시에 출발한 버스는 run_minutes[h]분 동안 도로 위에 있습니다. 출퇴근 정체로 한 바퀴가 길어지는 것을 반영
# 같은 값 24칸 목록과 그 정수는 결과가 같습니다.

import math
from collections.abc import Sequence

HOURS_PER_DAY = 24

Run = int | Sequence[int]


def run_at(run_minutes: Run, hour: int) -> int:
    """hour 시에 출발한 버스의 한 바퀴 운행시간(분)"""
    return run_minutes if isinstance(run_minutes, (int, float)) else run_minutes[hour]


def minutes_on_road(start_hour: int, run_minutes: Run, at_minute: int) -> int:
    """start_hour 시에 고르게 출발한 버스 중 at_minute 시각에 아직 도는 버스의 출발 시간 폭(분)

    at_minute 직전 run_at(run_minutes, start_hour) 동안 출발한 버스가 도로 위에 있습니다.
    """
    span = run_at(run_minutes, start_hour)
    return max(0, min(at_minute, (start_hour + 1) * 60) - max(at_minute - span, start_hour * 60))


def checkpoints(run_minutes: Run, hours: list[int]) -> list[int]:
    """버스 대수를 확인할 시각(분). 도로 위 버스 수는 정시와 'h시 + 운행시간'·'h+1시 + 운행시간'에서만 꺾이므로 그 시각만 보면 됩니다.

    hours 시에 출발한 버스가 영향을 주는 구간(첫 시간대 정시 ~ 마지막 버스가 돌아오는 시각)만 돌려줍니다.
    """
    first = min(hours) * 60
    last = max((h + 1) * 60 + run_at(run_minutes, h) for h in hours)
    bends = {h * 60 + run_at(run_minutes, h) for h in range(HOURS_PER_DAY)}
    bends |= {(h + 1) * 60 + run_at(run_minutes, h) for h in range(HOURS_PER_DAY)}
    points = set(range(0, last + 60, 60)) | bends
    return sorted(m for m in points if first < m <= last)


def on_road(trips: list[int], run_minutes: Run, at_minute: int) -> float:
    """at_minute 시각에 도로 위에 있는 버스 수"""
    return sum(trips[h] * minutes_on_road(h, run_minutes, at_minute) for h in range(HOURS_PER_DAY)) / 60.0


def peak_on_road(trips: list[float], run_minutes: Run, hour: int) -> int:
    """hour 시의 출발이 영향을 주는 동안(그 시각 ~ 그 시각 + 운행시간) 도로 위 버스가 가장 많은 순간의 대수"""
    if run_at(run_minutes, hour) <= 0:
        return 0
    return math.ceil(max((on_road(trips, run_minutes, m) for m in checkpoints(run_minutes, [hour])), default=0.0) - 1e-6)
