# ==================================================
# 버스 길안내 (F-28)
# ==================================================
# 출발 위치(GPS 좌표 또는 정류장 이름)와 도착 정류장을 받아
# 이 서비스가 다루는 노선 안에서 직행 또는 1회 환승 경로를 찾습니다.
#
# 처리 흐름
#   출발·도착 후보 정류장 (걸어갈 수 있는 거리 안의 정류장 전부 / 이름이 맞는 정류장 전부)
#     ↓
#   직행: 같은 노선에서 출발 순번 < 도착 순번
#   환승: 노선 A로 환승 정류장까지 → 가까운 정류장(걸어서 이동)에서 노선 B로 도착지까지
#     ↓
#   경로마다 탈 구간의 예상 혼잡도(F-03) · 배차간격 · 대략의 소요 시간
#     ↓
#   가장 빠른 경로와 덜 붐비는 경로를 표시하고 하나를 추천
#
# 한계 (응답의 assumption에도 적음)
# - 이 서비스의 노선(실데이터 5개)만 압니다. 지하철·다른 버스는 안내하지 않습니다.
# - 소요 시간은 "왕복 운행시간 × 탄 정류장 수 비율"로 어림한 값입니다. 실시간 교통 정보가 아닙니다.
# - 환승 뒤 구간도 같은 시간대 혼잡도로 봅니다.

import math

import pandas as pd

from app.config import (
    GUIDE_CROWD_TRADEOFF_MIN,
    GUIDE_MAX_OPTIONS,
    GUIDE_NEAR_STOPS,
    GUIDE_TRANSFER_M,
    GUIDE_WALK_M,
    GUIDE_WALK_M_PER_MIN,
)
from app.domains.bus.advisor import BusAdvisor, _num, congestion_level

ASSUMPTION = (
    "이 서비스가 다루는 노선만 안내합니다. 소요 시간은 운행시간에서 어림한 값이고, "
    "혼잡도는 예측값입니다. 환승 뒤에도 같은 시간대로 계산했습니다."
)


PARTICLES = ("까지", "으로", "에서", "로", "에", "을", "를", "은", "는")


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


class BusGuide:
    def __init__(self, advisor: BusAdvisor, routes: list[str]):
        self.advisor = advisor
        self.routes = routes
        self._stops: list[dict] | None = None
        self._transfers: dict[tuple[str, str], list[tuple[dict, dict, float]]] = {}

    # ==================================================
    # 1. 정류장
    # ==================================================

    @property
    def stops(self) -> list[dict]:
        if self._stops is None:
            out = []
            for route in self.routes:
                geo = self.advisor.ds.geo(route) or {"stops": []}
                for s in geo["stops"]:
                    if s.get("lat") is None or s.get("lng") is None:
                        continue
                    out.append({"route": route, "seq": int(s["seq"]), "station_id": s["station_id"], "name": s["name"],
                                "lat": float(s["lat"]), "lng": float(s["lng"]), "direction": s.get("direction")})
            self._stops = out
        return self._stops

    def last_seq(self, route: str) -> int:
        return max(s["seq"] for s in self.stops if s["route"] == route)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """이름에 검색어가 들어간 정류장 (같은 이름은 한 줄로, 지나는 노선 목록 포함)"""
        q = _key(query)
        if not q:
            return []
        found: dict[str, dict] = {}
        for s in self.stops:
            if q in _key(s["name"]):
                item = found.setdefault(s["name"], {"name": s["name"], "lat": s["lat"], "lng": s["lng"], "routes": []})
                if s["route"] not in item["routes"]:
                    item["routes"].append(s["route"])
        # 검색어와 똑같은 이름 → 검색어로 시작하는 이름 → 짧은 이름 순
        ranked = sorted(found.values(), key=lambda x: (_key(x["name"]) != q, not _key(x["name"]).startswith(q), len(x["name"])))
        return ranked[:limit]

    def by_name(self, name: str) -> list[dict]:
        """이름으로 정류장 찾기: 똑같은 이름이 있으면 그것만, 없으면 가장 잘 맞는 이름 하나"""
        # "종로3가역으로"처럼 조사가 붙은 말도 받음: 그대로 찾아보고, 없으면 조사를 떼고 다시 찾음
        tries = [name] + [name[: -len(p)] for p in PARTICLES if name.endswith(p) and len(name) > len(p) + 1]
        best = []
        for candidate in tries:
            q = _key(candidate)
            exact = [s for s in self.stops if _key(s["name"]) == q]
            if exact:
                return exact
            best = best or self.search(candidate, 1)
        if not best:
            raise GuideError(f"'{name}' 정류장을 찾을 수 없습니다. 이 서비스의 노선({', '.join(self.routes)}번)이 지나는 정류장만 안내합니다.")
        return [s for s in self.stops if s["name"] == best[0]["name"]]

    def near(self, lat: float, lng: float) -> tuple[list[dict], bool]:
        """걸어갈 수 있는 거리 안의 정류장 (walk_m 포함). 없으면 가장 가까운 정류장들을 돌려주고 far=True"""
        ranked = sorted(({**s, "walk_m": distance_m(lat, lng, s["lat"], s["lng"])} for s in self.stops), key=lambda s: s["walk_m"])
        if not ranked:
            raise GuideError("정류장 좌표가 없습니다. python scripts/fetch_route_geo.py 를 먼저 실행하세요.")
        within = [s for s in ranked if s["walk_m"] <= GUIDE_WALK_M]
        if within:
            return within, False
        # 너무 멀면 가장 가까운 정류장과 같은 이름의 정류장들로 안내하고, 멀다는 사실을 알림
        nearest = ranked[0]
        return [s for s in ranked[:GUIDE_NEAR_STOPS * 4] if s["name"] == nearest["name"]], True

    def transfers(self, a: str, b: str) -> list[tuple[dict, dict, float]]:
        """노선 a에서 내려 노선 b로 갈아탈 수 있는 정류장 쌍 (걸어서 GUIDE_TRANSFER_M 이내)"""
        if (a, b) not in self._transfers:
            sa = [s for s in self.stops if s["route"] == a]
            sb = [s for s in self.stops if s["route"] == b]
            pairs = []
            for x in sa:
                for y in sb:
                    d = distance_m(x["lat"], x["lng"], y["lat"], y["lng"])
                    if d <= GUIDE_TRANSFER_M:
                        pairs.append((x, y, d))
            self._transfers[(a, b)] = pairs
        return self._transfers[(a, b)]

    # ==================================================
    # 2. 경로 찾기
    # ==================================================

    def _candidates(self, origins: list[dict], dests: list[dict]) -> list[list[dict]]:
        """경로 후보: [탈 구간, ...]. 노선 조합마다 정류장 수가 가장 적은 것 하나"""
        best: dict[tuple, tuple[float, list[dict]]] = {}

        def keep(key, cost, legs):
            if key not in best or cost < best[key][0]:
                best[key] = (cost, legs)

        # 걷는 거리도 비용에 넣음 (정류장 하나 = 약 400m로 환산)
        walk_cost = lambda s: s.get("walk_m", 0) / 400

        for o in origins:
            for d in dests:
                if o["route"] == d["route"] and o["seq"] < d["seq"]:
                    keep((o["route"],), d["seq"] - o["seq"] + walk_cost(o) + walk_cost(d),
                         [{"board": o, "alight": d}])
        for o in origins:
            for d in dests:
                if o["route"] == d["route"]:
                    continue
                for x, y, walk in self.transfers(o["route"], d["route"]):
                    if x["seq"] > o["seq"] and y["seq"] < d["seq"]:
                        cost = (x["seq"] - o["seq"]) + (d["seq"] - y["seq"]) + walk_cost(o) + walk_cost(d) + walk / 400
                        keep((o["route"], d["route"]), cost,
                             [{"board": o, "alight": x}, {"board": {**y, "walk_m": walk}, "alight": d}])
        # 같은 노선으로 한 번에 갈 수 있으면 그 노선에서 시작하는 환승 경로는 뺌
        direct = {key[0] for key in best if len(key) == 1}
        kept = [v for key, v in best.items() if len(key) == 1 or key[0] not in direct]
        return [legs for _, legs in sorted(kept, key=lambda v: v[0])]

    def _leg(self, leg: dict, date, hour: int) -> dict:
        """탈 구간 하나의 혼잡도 · 배차간격 · 대략의 탑승 시간"""
        board, alight = leg["board"], leg["alight"]
        route = board["route"]
        day = self.advisor.analyze_day(route, date)
        hrow = day["hourly"][day["hourly"]["hour"] == hour]
        if hrow.empty or not (hrow["trips"].iloc[0] > 0):
            raise GuideError(f"{route}번은 {hour}시에 운행 데이터가 없습니다")
        trips = float(hrow["trips"].iloc[0])
        interval = 60.0 / trips

        seg = day["segments"]
        ridden = seg[(seg["hour"] == hour) & (seg["stop_seq"] >= board["seq"]) & (seg["stop_seq"] < alight["seq"])]
        c = ridden["congestion"].max() if not ridden.empty else None
        has_c = c is not None and pd.notna(c)

        info = self.advisor.ds.route_info_for(route, day["daytype"])
        run_minutes = float(info.get("run_minutes") or 180)
        stops = alight["seq"] - board["seq"]
        ride = run_minutes * stops / max(self.last_seq(route) - 1, 1)
        walk_m = float(board.get("walk_m", 0))
        return {
            "route": route,
            "board": {"name": board["name"], "seq": board["seq"], "lat": board["lat"], "lng": board["lng"]},
            "alight": {"name": alight["name"], "seq": alight["seq"], "lat": alight["lat"], "lng": alight["lng"]},
            "direction": board.get("direction"),
            "stops": stops,
            "walk_m": round(walk_m),
            "walk_min": math.ceil(walk_m / GUIDE_WALK_M_PER_MIN),
            "interval_min": round(interval, 1),
            "wait_min": round(interval / 2, 1),
            "ride_min": round(ride),
            "congestion": _num(c, 3) if has_c else None,
            "level": congestion_level(c) if has_c else None,
        }

    def find(self, date, hour: int, destination: str, origin: str | None = None,
             lat: float | None = None, lng: float | None = None) -> dict:
        if origin:
            origins, far, origin_label = self.by_name(origin), False, None
        elif lat is not None and lng is not None:
            origins, far = self.near(lat, lng)
            origin_label = "현재 위치"
        else:
            raise GuideError("출발 정류장 이름이나 현재 위치(lat, lng)가 필요합니다")
        dests = self.by_name(destination)

        # 정류장이 걸어갈 거리 밖이면 걷는 시간은 계산에서 빼고, 거리만 따로 알림
        origin_walk_m = round(origins[0].get("walk_m", 0))
        if far:
            origins = [{**s, "walk_m": 0} for s in origins]

        origin_name, dest_name = origins[0]["name"], dests[0]["name"]
        if not origin_label:
            origin_label = origin_name
        if _key(origin_name) == _key(dest_name):
            raise GuideError("출발 정류장과 도착 정류장이 같습니다")

        options = []
        for legs in self._candidates(origins, dests)[:GUIDE_MAX_OPTIONS * 2]:
            try:
                detail = [self._leg(leg, date, hour) for leg in legs]
            except GuideError:
                continue                      # 그 시간대에 안 다니는 노선은 뺌
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
        options = options[:GUIDE_MAX_OPTIONS]

        day = self.advisor.analyze_day(self.routes[0], date)
        result = {
            "date": f"{day['date']:%Y-%m-%d}", "hour": hour, "is_forecast": day["is_future"],
            "origin": {"label": origin_label, "stop": origin_name, "lat": origins[0]["lat"], "lng": origins[0]["lng"],
                       "walk_m": origin_walk_m, "far": far},
            "destination": {"stop": dest_name, "lat": dests[0]["lat"], "lng": dests[0]["lng"]},
            "options": options, "recommendation": None, "assumption": ASSUMPTION,
        }
        if not options:
            return result

        fastest = options[0]
        fastest["fastest"] = True
        rated = [o for o in options if o["max_congestion"] is not None]
        calm = min(rated, key=lambda o: (o["max_congestion"], o["total_min"])) if rated else fastest
        calm["least_crowded"] = True

        # 덜 붐비는 경로가 조금만 더 걸리면 그쪽을 추천, 아니면 가장 빠른 경로
        slower_by = calm["total_min"] - fastest["total_min"]
        pick = calm if calm is not fastest and slower_by <= GUIDE_CROWD_TRADEOFF_MIN and \
            (fastest["max_congestion"] or 0) > (calm["max_congestion"] or 0) else fastest
        pick["recommended"] = True
        reason = "less_crowded" if pick is not fastest else "fastest"
        result["recommendation"] = {"index": options.index(pick), "reason": reason, "slower_by_min": max(slower_by, 0) if pick is not fastest else 0}
        return result
