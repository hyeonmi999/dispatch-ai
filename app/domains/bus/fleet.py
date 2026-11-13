# ==================================================
# 도로 위 버스 대수  (배차 최적화·시뮬레이션·권고 공용)
# ==================================================
# 어느 시각이든 "도로 위에 있는 버스" = 최근 왕복 운행시간 동안 기점을 출발한 버스 수.
# 출발한 버스는 한 바퀴 돌 때까지 다시 쓸 수 없으므로 이 값이 인가대수를 넘으면 운행할 수 없습니다.

import math

HOURS_PER_DAY = 24


def minutes_on_road(start_hour: int, run_minutes: int, at_minute: int) -> int:
    """start_hour 시에 고르게 출발한 버스 중 at_minute 시각에 아직 도는 버스의 출발 시간 폭(분)

    at_minute 직전 run_minutes 동안 출발한 버스가 도로 위에 있습니다.
    """
    return max(0, min(at_minute, (start_hour + 1) * 60) - max(at_minute - run_minutes, start_hour * 60))


def checkpoints(run_minutes: int, hours: list[int]) -> list[int]:
    """버스 대수를 확인할 시각(분). 도로 위 버스 수는 정시와 '정시 + 운행시간'에서만 꺾이므로 그 시각만 보면 됩니다."""
    first, last = min(hours) * 60, (max(hours) + 1) * 60 + run_minutes
    points = {m for m in range(0, last + 60, 60)} | {h * 60 + run_minutes for h in range(HOURS_PER_DAY)}
    return sorted(m for m in points if first < m <= last)


def on_road(trips: list[int], run_minutes: int, at_minute: int) -> float:
    """at_minute 시각에 도로 위에 있는 버스 수"""
    return sum(trips[h] * minutes_on_road(h, run_minutes, at_minute) for h in range(HOURS_PER_DAY)) / 60.0


def peak_on_road(trips: list[float], run_minutes: int, hour: int) -> int:
    """hour 시의 출발이 영향을 주는 동안(그 시각 ~ 그 시각 + 운행시간) 도로 위 버스가 가장 많은 순간의 대수"""
    if run_minutes <= 0:
        return 0
    return math.ceil(max((on_road(trips, run_minutes, m) for m in checkpoints(run_minutes, [hour])), default=0.0) - 1e-6)
