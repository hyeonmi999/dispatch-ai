# ==================================================
# 버스 길안내 (F-28)
# ==================================================
# 출발 위치(GPS 좌표 또는 정류장 이름)와 도착 정류장을 받아 직행 또는 1회 환승 경로를 찾습니다.
#
# 아는 노선
#   - 전체 노선 정류장 파일이 있으면(data/interim/network, scripts/fetch_network_stops.py) 서울 버스 전체
#   - 없으면 이 서비스가 예측하는 노선만 (합성 데이터·테스트 포함)
#   혼잡도 예측은 이 서비스가 예측하는 노선(self.routes)에만 있습니다. 나머지 노선은 경로·배차간격·대략의 시간만 안내합니다.
#
# 처리 흐름
#   출발·도착 후보 정류장 (걸어갈 수 있는 거리 안의 정류장 / 이름이 맞는 정류장 전부)
#     ↓
#   직행: 같은 노선에서 출발 순번 < 도착 순번
#   환승: 출발 노선을 타고 가며 지나는 정류장(과 걸어서 옮길 수 있는 가까운 정류장)에서
#         도착지를 지나는 노선으로 갈아탈 수 있는지 확인 (정류장 → 지나는 노선 색인 사용)
#     ↓
#   경로마다 탈 구간의 예상 혼잡도(F-03) · 배차간격 · 대략의 소요 시간
#     ↓
#   가장 빠른 경로와 덜 붐비는 경로를 표시하고 하나를 추천
#
# 한계 (응답의 assumption에도 적음)
# - 버스만 안내합니다. 지하철은 다루지 않습니다. 환승은 한 번까지입니다.
# - 소요 시간은 "한 바퀴 운행시간 × 탄 정류장 수 비율"로 어림한 값입니다. 실시간 교통 정보가 아닙니다.
# - 혼잡도·배차간격은 기점 출발 시간대 기준 값이라, 탈 정류장에 닿는 시각에서 기점 → 그 정류장 소요 시간을 빼서
#   그 버스가 기점을 떠난 시간대의 값을 읽습니다. 환승 뒤 구간은 앞 구간의 걷기·대기·탑승 시간만큼 지난 시각으로 계산합니다.

import math
import threading
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from app.config import (
    GUIDE_CANDIDATES,
    GUIDE_CLOCK_MINUTE,
    GUIDE_CROWD_MIN_GAP,
    GUIDE_CROWD_TRADEOFF_MIN,
    GUIDE_DEFAULT_KMH,
    GUIDE_DEFAULT_MIN_PER_STOP,
    GUIDE_EARLY_HOUR,
    GUIDE_MAX_OPTIONS,
    GUIDE_NEAR_STOPS,
    GUIDE_PEAK_FACTOR,
    GUIDE_PEAK_HOURS,
    GUIDE_PLACE_M,
    GUIDE_PLACE_TRIES,
    GUIDE_SAME_PLACE_M,
    GUIDE_SCAN_FACTOR,
    GUIDE_SHORT_LEG_MIN,
    GUIDE_SPEED_RANGE_KMH,
    GUIDE_TRANSFER_M,
    GUIDE_WALK_M,
    GUIDE_WALK_M_PER_MIN,
    INTERIM_DIR,
)
from app.domains.bus.advisor import BusAdvisor, _num, congestion_level

NETWORK_DIR = INTERIM_DIR / "network"      # scripts/fetch_network_stops.py 결과

PARTICLES = ("까지", "으로", "에서", "로", "에", "을", "를", "은", "는")
# "서울역"이라고 하면 "서울역버스환승센터", "서울역앞"도 같은 곳으로 봄 ("서울역사박물관"은 아님)
NAME_SUFFIXES = ("", "앞", "입구", "역", "버스환승센터", "환승센터", "정류장", "광장", "사거리", "교차로")
GRID_LAT, GRID_LNG = 0.0015, 0.0019        # 약 165m 격자 (가까운 정류장을 빨리 찾기 위한 칸)


class GuideError(ValueError):
    """안내할 수 없는 요청 (없는 정류장 이름 등). API에서 422로 바꿈"""


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m). 서울 안에서는 평면 근사로 충분"""
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _key(name: str) -> str:
    """정류장 이름 비교용: 공백·점 제거 ("이태원역.보광동입구" = "이태원역 보광동입구")"""
    return "".join(ch for ch in name if ch not in " .·,()").lower()


def _parts(name: str) -> list[str]:
    """정류장 이름을 점으로 나눈 조각 ("종로3가역.탑골공원" → 종로3가역, 탑골공원)"""
    return [_key(p) for p in name.replace("·", ".").replace(",", ".").split(".") if _key(p)]


def _hour_of(clock: str | None) -> int | None:
    try:
        return int(str(clock).split(":")[0])
    except (TypeError, ValueError):
        return None


class BusGuide:
    def __init__(self, advisor: BusAdvisor, routes: list[str], network: tuple[pd.DataFrame, pd.DataFrame] | None = None):
        """routes: 혼잡도를 예측하는 노선. network: (정류장 표, 노선 표). 없으면 파일에서 읽고, 파일도 없으면 routes만"""
        self.advisor = advisor
        self.routes = routes
        self._network = network
        self._built = False
        self._build_lock = threading.Lock()     # 요청이 겹쳐도 색인은 한 번만, 다 만든 뒤에 보이게

    # ==================================================
    # 1. 정류장·노선 색인 (처음 쓸 때 한 번 만듦)
    # ==================================================

    def _load_network(self) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        if self._network is not None:
            return self._network
        stops_file, routes_file = NETWORK_DIR / "stops.parquet", NETWORK_DIR / "routes.parquet"
        if self.advisor.ds.mode == "real" and stops_file.exists() and routes_file.exists():
            return pd.read_parquet(stops_file), pd.read_parquet(routes_file)
        return None

    def _build(self) -> None:
        if self._built:
            return
        with self._build_lock:
            if not self._built:
                self._build_index()

    def _build_index(self) -> None:
        stops: list[dict] = []
        self.route_meta: dict[str, dict] = {}
        network = self._load_network()
        if network is not None:
            table, meta = network
            num = lambda v: None if v is None or pd.isna(v) else float(v)
            for r in table.itertuples():
                stops.append({"route": str(r.route), "seq": int(r.seq), "station_id": str(r.station_id), "name": r.name,
                              "lat": float(r.lat), "lng": float(r.lng), "direction": getattr(r, "direction", None),
                              # 소요 시간 계산용 (없는 파일도 있음): 누적 도로 거리(m), 시간표 누적 분(첫차·막차)
                              "cum_m": num(getattr(r, "cum_m", None)), "t_first": num(getattr(r, "t_first", None)),
                              "t_last": num(getattr(r, "t_last", None))})
            self.route_meta = {str(m["route"]): m for m in meta.to_dict("records")}
        have = {s["route"] for s in stops}
        for route in self.routes:      # 예측하는 노선은 항상 포함 (전체 노선 파일에 없더라도)
            if route in have:
                continue
            for s in (self.advisor.ds.geo(route) or {"stops": []})["stops"]:
                if s.get("lat") is None or s.get("lng") is None:
                    continue
                stops.append({"route": route, "seq": int(s["seq"]), "station_id": str(s["station_id"]), "name": s["name"],
                              "lat": float(s["lat"]), "lng": float(s["lng"]), "direction": s.get("direction")})

        self.stops = stops
        self.by_route: dict[str, list[dict]] = defaultdict(list)
        self.by_station: dict[str, list[dict]] = defaultdict(list)
        self.stations: dict[str, dict] = {}
        self.grid: dict[tuple[int, int], list[str]] = defaultdict(list)
        self.names: dict[str, dict] = {}
        for s in stops:
            self.by_route[s["route"]].append(s)
            self.by_station[s["station_id"]].append(s)
            if s["station_id"] not in self.stations:
                self.stations[s["station_id"]] = s
                self.grid[self._cell(s["lat"], s["lng"])].append(s["station_id"])
            item = self.names.setdefault(s["name"], {"name": s["name"], "lat": s["lat"], "lng": s["lng"], "routes": [],
                                                    "key": _key(s["name"]), "parts": _parts(s["name"])})
            if s["route"] not in item["routes"]:
                item["routes"].append(s["route"])
        for line in self.by_route.values():
            line.sort(key=lambda s: s["seq"])
        self._lat = np.array([s["lat"] for s in stops])
        self._lng = np.array([s["lng"] for s in stops])
        self._near_stations: dict[str, list[tuple[str, float]]] = {}
        self.whole_network = network is not None
        self._built = True

    @staticmethod
    def _cell(lat: float, lng: float) -> tuple[int, int]:
        return int(lat / GRID_LAT), int(lng / GRID_LNG)

    def near_stations(self, station_id: str) -> list[tuple[str, float]]:
        """걸어서 갈아탈 수 있는 정류장: 자기 자신(0m) + GUIDE_TRANSFER_M 이내의 다른 정류장"""
        if station_id not in self._near_stations:
            me = self.stations[station_id]
            cy, cx = self._cell(me["lat"], me["lng"])
            out = [(station_id, 0.0)]
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for other in self.grid.get((cy + dy, cx + dx), ()):
                        if other == station_id:
                            continue
                        o = self.stations[other]
                        d = distance_m(me["lat"], me["lng"], o["lat"], o["lng"])
                        if d <= GUIDE_TRANSFER_M:
                            out.append((other, d))
            self._near_stations[station_id] = out
        return self._near_stations[station_id]

    def last_seq(self, route: str) -> int:
        return self.by_route[route][-1]["seq"]

    # ==================================================
    # 탑승 시간: 정류장별 공식 시간표(첫차·막차)와 도로 거리로 계산
    # ==================================================

    def _timetable_speed(self, route: str, table: str) -> float | None:
        """노선 전체의 시간표 평균 속도(km/h, 정차 포함). 시간표가 이상하면 None"""
        cache = self.__dict__.setdefault("_speed_cache", {})
        if (route, table) not in cache:
            timed = [s for s in self.by_route[route] if s.get(table) is not None and s.get("cum_m") is not None]
            speed = None
            if len(timed) >= 2:
                minutes, km = timed[-1][table] - timed[0][table], (timed[-1]["cum_m"] - timed[0]["cum_m"]) / 1000
                if minutes > 0 and GUIDE_SPEED_RANGE_KMH[0] <= km / (minutes / 60) <= GUIDE_SPEED_RANGE_KMH[1]:
                    speed = km / (minutes / 60)
            cache[(route, table)] = speed
        return cache[(route, table)]

    def ride_minutes(self, board: dict, alight: dict, hour: int) -> tuple[float | None, float | None]:
        """(탑승 시간 분, 거리 km). 시간표·거리가 없으면 (None, None) → 부르는 쪽이 예전 방식으로 어림

        새벽은 첫차 시간표, 그 밖은 막차 시간표를 기준으로 하고 출퇴근 시간에는 GUIDE_PEAK_FACTOR를 곱함.
        시간표가 분 단위라 짧은 구간이나, 시간표 속도가 말이 안 되는 구간은 거리 ÷ 노선 평균 속도로 계산.
        """
        if board.get("cum_m") is None or alight.get("cum_m") is None:
            return None, None
        km = (alight["cum_m"] - board["cum_m"]) / 1000
        if km <= 0:
            return None, None
        # 쓰려는 시간표가 비었거나 이상하면 다른 쪽 시간표를 씀 (두 시간표의 소요 시간은 노선 중앙값 기준으로 같음)
        tables = ("t_first", "t_last") if hour <= GUIDE_EARLY_HOUR else ("t_last", "t_first")
        minutes = None
        for table in tables:
            if board.get(table) is None or alight.get(table) is None:
                continue
            timed = alight[table] - board[table]
            if timed >= GUIDE_SHORT_LEG_MIN and GUIDE_SPEED_RANGE_KMH[0] <= km / (timed / 60) <= GUIDE_SPEED_RANGE_KMH[1]:
                minutes = timed
                break
        if minutes is None:
            route = board["route"]
            speed = self._timetable_speed(route, tables[0]) or self._timetable_speed(route, tables[1]) or GUIDE_DEFAULT_KMH
            minutes = km / speed * 60
        if hour in GUIDE_PEAK_HOURS:
            minutes *= GUIDE_PEAK_FACTOR
        return minutes, km

    # ==================================================
    # 2. 정류장 찾기
    # ==================================================

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """이름에 검색어가 들어간 정류장 (같은 이름은 한 줄로, 지나는 노선 목록 포함)"""
        self._build()
        q = _key(query)
        if not q:
            return []
        found = [n for n in self.names.values() if q in n["key"]]
        # 검색어와 똑같은 이름 → 검색어로 시작하는 이름 → 짧은 이름 순
        found.sort(key=lambda x: (x["key"] != q, not x["key"].startswith(q), len(x["name"])))
        return [{"name": n["name"], "lat": n["lat"], "lng": n["lng"], "routes": n["routes"]} for n in found[:limit]]

    def by_name(self, name: str) -> list[dict]:
        """이름으로 정류장 찾기.

        "서울역" → 이름(또는 점으로 나눈 조각)이 서울역·서울역앞·서울역버스환승센터인 정류장 전부.
        그런 정류장이 없으면 이름에 검색어가 들어간 정류장 중 가장 잘 맞는 이름 하나.
        """
        self._build()
        # "종로3가역으로"처럼 조사가 붙은 말도 받음: 그대로 찾아보고, 없으면 조사를 떼고 다시 찾음
        tries = [name] + [name[: -len(p)] for p in PARTICLES if name.endswith(p) and len(name) > len(p) + 1]
        best = []
        for candidate in tries:
            q = _key(candidate)
            if not q:
                continue
            wanted = {q + suffix for suffix in NAME_SUFFIXES}
            matched = {n["name"] for n in self.names.values() if n["key"] in wanted or any(p in wanted for p in n["parts"])}
            if matched:
                return [s for s in self.stops if s["name"] in matched]
            best = best or self.search(candidate, 1)
        if not best:
            scope = "서울 버스 정류장" if self.whole_network else f"이 서비스의 노선({', '.join(self.routes)}번)이 지나는 정류장"
            raise GuideError(f"'{name}' 정류장을 찾을 수 없습니다. {scope} 이름으로 알려 주세요.")
        return [s for s in self.stops if s["name"] == best[0]["name"]]

    @staticmethod
    def places(matched: list[dict]) -> list[list[dict]]:
        """이름으로 찾은 정류장을 '곳'으로 묶음: GUIDE_SAME_PLACE_M 안에서 이어지는 정류장끼리 한 곳.

        "서울역"·"서울역버스환승센터"는 한 곳, 서로 수 km 떨어진 "농수산물센터" 3곳은 세 곳.
        """
        groups: list[list[dict]] = []
        for stop in matched:
            near = [g for g in groups
                    if any(distance_m(stop["lat"], stop["lng"], o["lat"], o["lng"]) <= GUIDE_SAME_PLACE_M for o in g)]
            for g in near:
                groups.remove(g)
            groups.append([s for g in near for s in g] + [stop])
        return groups

    @staticmethod
    def gap_m(a: list[dict], b: list[dict]) -> float:
        """두 묶음에서 가장 가까운 정류장 사이의 직선거리"""
        return min(distance_m(x["lat"], x["lng"], y["lat"], y["lng"]) for x in a for y in b)

    def around(self, matched: list[dict]) -> list[dict]:
        """이름으로 찾은 정류장 + 그 주변(GUIDE_PLACE_M)의 정류장. 길 건너편이나 다른 출구에서 타는 노선을 놓치지 않기 위함.

        이름이 맞은 정류장은 walk_m 0, 주변 정류장은 가장 가까운 이름 맞은 정류장까지의 거리.
        """
        walk: dict[str, float] = {s["station_id"]: 0.0 for s in matched}
        reach = math.ceil(GUIDE_PLACE_M / 160)
        for station_id in list(walk):
            me = self.stations[station_id]
            cy, cx = self._cell(me["lat"], me["lng"])
            for dy in range(-reach, reach + 1):
                for dx in range(-reach, reach + 1):
                    for other in self.grid.get((cy + dy, cx + dx), ()):
                        if walk.get(other) == 0.0:
                            continue
                        o = self.stations[other]
                        d = distance_m(me["lat"], me["lng"], o["lat"], o["lng"])
                        if d <= GUIDE_PLACE_M and d < walk.get(other, float("inf")):
                            walk[other] = d
        return [{**s, "walk_m": walk[station_id]} for station_id in walk for s in self.by_station[station_id]]

    def near(self, lat: float, lng: float) -> tuple[list[dict], bool]:
        """걸어갈 수 있는 거리 안의 정류장 (walk_m 포함). 없으면 가장 가까운 정류장들을 돌려주고 far=True"""
        self._build()
        if not self.stops:
            raise GuideError("정류장 좌표가 없습니다. python scripts/fetch_route_geo.py 를 먼저 실행하세요.")
        dy = (self._lat - lat) * 111_320
        dx = (self._lng - lng) * 111_320 * math.cos(math.radians(lat))
        dist = np.hypot(dx, dy)
        order = np.argsort(dist)
        within = [{**self.stops[i], "walk_m": float(dist[i])} for i in order[: 400] if dist[i] <= GUIDE_WALK_M]
        if within:
            return within, False
        # 너무 멀면 가장 가까운 정류장과 같은 이름의 정류장들로 안내하고, 멀다는 사실을 알림
        nearest = self.stops[order[0]]
        close = [{**self.stops[i], "walk_m": float(dist[i])} for i in order[: GUIDE_NEAR_STOPS * 8]]
        return [s for s in close if s["name"] == nearest["name"]], True

    # ==================================================
    # 3. 경로 찾기
    # ==================================================

    def _candidates(self, origins: list[dict], dests: list[dict]) -> list[list[dict]]:
        """경로 후보: [탈 구간, ...]. 노선 조합마다 비용(정류장 수 + 걷기)이 가장 작은 것 하나"""
        best: dict[tuple, tuple[float, list[dict]]] = {}

        def keep(key, cost, legs):
            if key not in best or cost < best[key][0]:
                best[key] = (cost, legs)

        walk_cost = lambda s: s.get("walk_m", 0) / 400      # 걷는 거리도 비용에 (정류장 하나 = 약 400m)

        # 노선마다 가까운 출발 정류장 몇 개만 (양방향 정류장을 모두 보려고 2개 이상)
        by_origin_route: dict[str, list[dict]] = defaultdict(list)
        for o in sorted(origins, key=walk_cost):
            if len(by_origin_route[o["route"]]) < 4:
                by_origin_route[o["route"]].append(o)
        dest_by_route: dict[str, list[dict]] = defaultdict(list)
        for d in dests:
            dest_by_route[d["route"]].append(d)

        for route, starts in by_origin_route.items():
            # 직행
            for o in starts:
                for d in dest_by_route.get(route, ()):
                    if o["seq"] < d["seq"]:
                        keep((route,), d["seq"] - o["seq"] + walk_cost(o) + walk_cost(d), [{"board": o, "alight": d}])
            # 환승: 이 노선을 타고 가며 지나는 정류장에서 도착지를 지나는 노선으로 갈아탐
            for o in starts:
                for x in self.by_route[route]:
                    if x["seq"] <= o["seq"]:
                        continue
                    for station, walk in self.near_stations(x["station_id"]):
                        for y in self.by_station[station]:
                            other = y["route"]
                            if other == route or other not in dest_by_route:
                                continue
                            for d in dest_by_route[other]:
                                if y["seq"] < d["seq"]:
                                    cost = (x["seq"] - o["seq"]) + (d["seq"] - y["seq"]) + walk_cost(o) + walk_cost(d) + walk / 400 + 3
                                    keep((route, other), cost, [{"board": o, "alight": x}, {"board": {**y, "walk_m": walk}, "alight": d}])
        # 한 번에 가는 노선이 있으면 그 노선을 타는 환승 경로는 뺌 (그 노선으로 시작하든, 중간에 갈아타든)
        direct = {key[0] for key in best if len(key) == 1}
        kept = [v for key, v in best.items() if len(key) == 1 or not (set(key) & direct)]
        return [legs for _, legs in sorted(kept, key=lambda v: v[0])]

    def minutes_from_start(self, board: dict, hour: int) -> float | None:
        """기점(첫 정류장) → 탈 정류장 소요 분. 시간표·거리가 없으면 None (advisor가 운행시간 비율로 어림)"""
        first = self.by_route[board["route"]][0]
        if board["seq"] <= first["seq"]:
            return 0.0
        return self.ride_minutes(first, board, hour)[0]

    def _leg(self, leg: dict, date, clock_minute: float) -> dict:
        """탈 구간 하나의 혼잡도 · 배차간격 · 대략의 탑승 시간

        clock_minute: 탈 정류장에 닿는 시각 (그날 0시부터 분). 예측 노선은 그 시각에 오는 버스의 기점 출발 시간대 값을 읽음
        """
        board, alight = leg["board"], leg["alight"]
        route = board["route"]
        hour = int(clock_minute // 60) % 24          # 승객이 타는 시계 시각의 시간대
        start_hour = None
        stops = alight["seq"] - board["seq"]
        span = max(self.last_seq(route) - 1, 1)

        if route in self.routes:
            # 이 서비스가 예측하는 노선: 그 시간대 운행횟수와 구간 혼잡도
            day = self.advisor.analyze_day(route, date)
            start_hour = self.advisor.departure_hour(route, day["daytype"], board["seq"], clock_minute,
                                                     self.minutes_from_start(board, hour))
            hrow = day["hourly"][day["hourly"]["hour"] == start_hour]
            if hrow.empty or not (hrow["trips"].iloc[0] > 0):
                raise GuideError(f"{route}번은 {hour}시에 이 정류장을 지나는 버스의 운행 데이터가 없습니다")
            interval = 60.0 / float(hrow["trips"].iloc[0])
            seg = day["segments"]
            ridden = seg[(seg["hour"] == start_hour) & (seg["stop_seq"] >= board["seq"]) & (seg["stop_seq"] < alight["seq"])]
            c = ridden["congestion"].max() if not ridden.empty else None
            has_c = c is not None and pd.notna(c)
            run_minutes = float(self.advisor.ds.route_info_for(route, day["daytype"]).get("run_minutes") or 0)
        else:
            # 그 밖의 노선: 노선 기본정보의 평일 배차간격·운행시간만 (혼잡도 예측 없음)
            meta = self.route_meta.get(route, {})
            first, last = _hour_of(meta.get("first_bus")), _hour_of(meta.get("last_bus"))
            if first is not None and last is not None:
                running = first <= hour <= last if first <= last else (hour >= first or hour <= last)
                if not running:
                    raise GuideError(f"{route}번은 {hour}시에 운행하지 않습니다")
            interval = meta.get("interval")
            if interval is None or pd.isna(interval) or interval <= 0:
                raise GuideError(f"{route}번은 배차간격 정보가 없습니다")
            interval = float(interval)
            c, has_c = None, False
            run = meta.get("run_minutes")
            run_minutes = float(run) if run is not None and pd.notna(run) else 0.0

        ride, km = self.ride_minutes(board, alight, hour)
        if ride is None:      # 시간표·거리 정보가 없는 노선(합성 데이터 등): 한 바퀴 운행시간을 정류장 수로 나눔
            ride = run_minutes * stops / span if run_minutes > 0 else stops * GUIDE_DEFAULT_MIN_PER_STOP
        walk_m = float(board.get("walk_m", 0))
        exit_walk_m = float(alight.get("walk_m", 0))      # 내린 정류장에서 도착지까지 (이름으로 찾은 곳의 주변 정류장일 때)
        line = self.by_route[route]
        return {
            "route": route,
            "board": {"name": board["name"], "seq": board["seq"], "lat": board["lat"], "lng": board["lng"]},
            "alight": {"name": alight["name"], "seq": alight["seq"], "lat": alight["lat"], "lng": alight["lng"]},
            "direction": board.get("direction"),
            "stops": stops,
            "walk_m": round(walk_m),
            "walk_min": math.ceil(walk_m / GUIDE_WALK_M_PER_MIN) + math.ceil(exit_walk_m / GUIDE_WALK_M_PER_MIN),
            "exit_walk_m": round(exit_walk_m),
            "interval_min": round(interval, 1),
            "wait_min": round(interval / 2, 1),
            "ride_min": max(round(ride), 1),
            "distance_km": round(km, 1) if km is not None else None,
            "congestion": _num(c, 3) if has_c else None,
            "level": congestion_level(c) if has_c else None,
            "predicted": route in self.routes,
            "start_hour": start_hour,
            "path": [[s["lat"], s["lng"]] for s in line if board["seq"] <= s["seq"] <= alight["seq"]],
        }

    def _legs(self, legs: list[dict], date, hour: int) -> list[dict]:
        """구간을 차례로 계산: 다음 구간은 앞 구간의 걷기·대기·탑승 시간만큼 지난 시각에 탐"""
        clock = hour * 60 + GUIDE_CLOCK_MINUTE
        detail = []
        for leg in legs:
            clock += math.ceil(float(leg["board"].get("walk_m", 0)) / GUIDE_WALK_M_PER_MIN)
            d = self._leg(leg, date, clock)
            detail.append(d)
            clock += d["wait_min"] + d["ride_min"]
        return detail

    CROWD_TIMING = ("혼잡도는 탈 정류장에 그 시각에 오는 버스가 기점을 떠난 시간대의 예측값이고, "
                    "갈아탄 뒤 구간은 앞 구간의 걷기·대기·탑승 시간만큼 지난 시각으로 계산했습니다.")

    def assumption(self) -> str:
        if self.whole_network:
            return (f"버스만 안내합니다(환승 한 번까지, 지하철 제외). 혼잡도 예측은 {', '.join(self.routes)}번만 있고, "
                    "나머지 노선은 평일 기준 배차간격만 안내합니다. 탑승 시간은 정류장별 공식 시간표로 계산했고 실시간 교통 상황은 반영하지 않습니다. "
                    + self.CROWD_TIMING)
        return "이 서비스가 다루는 노선만 안내합니다. 소요 시간은 운행시간에서 어림한 값이고, 혼잡도는 예측값입니다. " + self.CROWD_TIMING

    def _plan(self, date, hour: int, origin_place: list[dict] | None, named_dests: list[dict],
              lat: float | None, lng: float | None) -> dict:
        """출발지 한 곳과 도착지 한 곳 사이의 경로 후보를 계산 (origin_place가 없으면 현재 위치에서 출발)"""
        dest_name = Counter(s["name"] for s in named_dests).most_common(1)[0][0]
        if origin_place:
            origin_name = Counter(s["name"] for s in origin_place).most_common(1)[0][0]
            origins, far, origin_label = self.around(origin_place), False, origin_name
        else:
            origins, far = self.near(lat, lng)
            origin_label = "현재 위치"
            origin_name = min(origins, key=lambda s: s.get("walk_m", 0))["name"]
        dests = self.around(named_dests)
        # 출발지가 도착지 바로 옆이면 버스를 탈 필요가 없음 → 경로 대신 걸어갈 거리만 알림
        gap = self.gap_m(origin_place or [{"lat": lat, "lng": lng}], named_dests)
        walk_only = {"walk_m": round(gap), "walk_min": max(1, math.ceil(gap / GUIDE_WALK_M_PER_MIN))} if gap <= GUIDE_PLACE_M else None

        # 정류장이 걸어갈 거리 밖이면 걷는 시간은 계산에서 빼고, 거리만 따로 알림
        origin_walk_m = round(min(s.get("walk_m", 0) for s in origins))
        if far:
            origins = [{**s, "walk_m": 0} for s in origins]

        options = []
        # 정류장 수가 적은 후보부터 계산. 그 시간대에 안 다니는 노선(심야버스 등)은 빼고, 쓸 수 있는 후보가 찰 때까지 더 봄
        for legs in ([] if walk_only else self._candidates(origins, dests)[:GUIDE_CANDIDATES * GUIDE_SCAN_FACTOR]):
            if len(options) >= GUIDE_CANDIDATES:
                break
            try:
                detail = self._legs(legs, date, hour)
            except GuideError:
                continue
            levels = [l["congestion"] for l in detail if l["congestion"] is not None]
            worst = max(levels) if levels else None
            options.append({
                "kind": "direct" if len(detail) == 1 else "transfer",
                "routes": [l["route"] for l in detail],
                "legs": detail,
                "total_min": round(sum(l["walk_min"] + l["wait_min"] + l["ride_min"] for l in detail)),
                "max_congestion": worst,
                "level": congestion_level(worst) if worst is not None else None,
                "fastest": False, "least_crowded": False, "recommended": False,
            })
        options.sort(key=lambda o: o["total_min"])
        return {"origins": origins, "named_dests": named_dests, "options": options[:GUIDE_MAX_OPTIONS], "walk_only": walk_only,
                "origin_name": origin_name, "origin_label": origin_label, "dest_name": dest_name, "far": far,
                "origin_walk_m": origin_walk_m}

    def find(self, date, hour: int, destination: str, origin: str | None = None,
             lat: float | None = None, lng: float | None = None) -> dict:
        self._build()
        if not origin and (lat is None or lng is None):
            raise GuideError("출발 정류장 이름이나 현재 위치(lat, lng)가 필요합니다")
        named_dests = self.by_name(destination)
        named = self.by_name(origin) if origin else []
        if origin and {s["station_id"] for s in named} & {s["station_id"] for s in named_dests}:
            raise GuideError("출발 정류장과 도착 정류장이 같습니다")

        # 같은 이름의 정류장이 멀리 떨어진 여러 곳에 있으면, 출발지와 도착지가 서로 가까운 곳부터 찾아보고
        # 경로가 있는(또는 걸어갈 수 있는) 첫 번째 곳으로 안내. 어디에도 경로가 없으면 가장 가까운 곳 기준으로 답함
        here = [{"lat": lat, "lng": lng}]
        origin_places, dest_places = (self.places(named) if origin else [here]), self.places(named_dests)
        pairs = sorted(((o, d) for o in origin_places for d in dest_places), key=lambda pair: self.gap_m(*pair))
        plan = None
        for origin_place, dest_place in pairs[:GUIDE_PLACE_TRIES]:
            tried = self._plan(date, hour, origin_place if origin else None, dest_place, lat, lng)
            plan = plan or tried
            if tried["walk_only"] or tried["options"]:
                plan = tried
                break
        origins, named_dests, options, walk_only = plan["origins"], plan["named_dests"], plan["options"], plan["walk_only"]
        origin_name, origin_label, dest_name, far, origin_walk_m = (
            plan["origin_name"], plan["origin_label"], plan["dest_name"], plan["far"], plan["origin_walk_m"])

        day = self.advisor.analyze_day(self.routes[0], date)
        first_origin = min(origins, key=lambda s: s.get("walk_m", 0))
        near_dest = min(named_dests, key=lambda s: distance_m(first_origin["lat"], first_origin["lng"], s["lat"], s["lng"]))
        result = {
            "date": f"{day['date']:%Y-%m-%d}", "hour": hour, "is_forecast": day["is_future"],
            "origin": {"label": origin_label, "stop": origin_name, "lat": first_origin["lat"], "lng": first_origin["lng"],
                       "walk_m": origin_walk_m, "far": far, "ambiguous": len(origin_places) > 1},
            "destination": {"stop": dest_name, "lat": near_dest["lat"], "lng": near_dest["lng"], "ambiguous": len(dest_places) > 1},
            "options": options, "recommendation": None, "walk_only": walk_only, "assumption": self.assumption(),
        }
        if not options:
            return result

        fastest = options[0]
        fastest["fastest"] = True
        # 혼잡도를 비교할 수 있는 경로: 모든 구간에 예측이 있는 것만 (예측 없는 구간을 낀 경로는 얼마나 붐빌지 모름)
        rated = [o for o in options if o["max_congestion"] is not None and all(leg["predicted"] for leg in o["legs"])]
        calm = min(rated, key=lambda o: (o["max_congestion"], o["total_min"])) if rated else None
        # 가장 빠른 경로보다 뚜렷하게 덜 붐빌 때만 '덜 붐빔' (가장 빠른 경로의 혼잡도를 모르면 비교하지 않음)
        clearly_calmer = (
            calm is not None and calm is not fastest and fastest in rated
            and fastest["max_congestion"] - calm["max_congestion"] >= GUIDE_CROWD_MIN_GAP
        )
        if clearly_calmer:
            calm["least_crowded"] = True
        elif calm is fastest:
            fastest["least_crowded"] = True      # 가장 빠른 경로가 가장 덜 붐비기도 함

        # 덜 붐비는 경로가 조금만 더 걸리면 그쪽을 추천, 아니면 가장 빠른 경로
        slower_by = (calm["total_min"] - fastest["total_min"]) if clearly_calmer else 0
        pick = calm if clearly_calmer and slower_by <= GUIDE_CROWD_TRADEOFF_MIN else fastest
        pick["recommended"] = True
        reason = "less_crowded" if pick is not fastest else "fastest"
        result["recommendation"] = {"index": options.index(pick), "reason": reason, "slower_by_min": max(slower_by, 0) if pick is not fastest else 0}
        return result
