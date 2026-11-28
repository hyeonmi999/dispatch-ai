# ==================================================
# 실시간 사고·정체 알림 (이슈 #42 방법 2, docs/plans/park-next.md Task P-9)
# ==================================================
# 3주 앞 계획은 그날의 사고·정체를 알 수 없으므로 계획 알고리즘에는 넣지 않고, 관제 화면에 알려서 담당자가 판단합니다.
# 배차·최적화·권고 값은 바꾸지 않습니다 (응답은 항상 needs_review: true).
#
#   돌발   TOPIS AccInfo 중 지금 진행 중이고 노선 경로에서 TOPIS_ACC_NEAR_M 안(또는 노선 링크 위)인 것
#          해제 예정이 TOPIS_LONG_TERM_DAYS보다 먼 것(장기 공사)은 평소 운행시간에 이미 들어 있어 따로 묶음
#   정체   노선 링크마다 지금 통행시간 − 평소 통행시간(같은 요일유형·시간대 속도 중앙값)을 더한 값 = 한 바퀴 추가 시간
#          링크 길이의 TOPIS_MIN_COVERAGE 이상에서 두 값을 모두 받았을 때만 계산 (받은 링크만 더함, 늘려 잡지 않음)
#          평소 한 바퀴의 TOPIS_EXTRA_WARN_RATIO(15%) 이상이면 "평소보다 한 바퀴 약 N분 더"
#   구간   지도 소통 띠용: 정류장 구간마다 지금 속도 ÷ 평소 속도 (그 구간에 걸친 링크 길이로 가중). 링크 ↔ 구간 표는
#          scripts/build_topis_links.py가 미리 만듦 (route_link_segments.parquet)
#   버스   평소 한 바퀴 = P-7 표의 그 출발 시각 값(지금 계획이 가정한 값). 지금 도로 위에 있는 버스(최근 한 바퀴 동안
#          출발한 시간대)의 한 바퀴에 추가 시간을 더해 도로 위 버스 대수를 다시 셈 → 인가대수를 넘으면 "인가대수 부족 가능"
#
# HTTP는 topis.py만 합니다. 이 파일은 받은 값(또는 기록)으로 계산만 합니다.

import datetime as dt
import logging
import math
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.config import (
    TOPIS_ACC_NEAR_M, TOPIS_CACHE_SECONDS, TOPIS_EXTRA_WARN_RATIO, TOPIS_LINK_NEAR_M, TOPIS_LINK_SPEED,
    TOPIS_LONG_TERM_DAYS, TOPIS_MAX_CONCURRENCY, TOPIS_MIN_COVERAGE, TOPIS_ROUTE_LINKS, TOPIS_ROUTE_SEGMENTS,
    TOPIS_SEGMENT_MIN_M, TOPIS_SLOW_LINKS_TOP, TOPIS_SLOW_RATIO, TOPIS_SNAPSHOT_DIR,
)
from app.domains.bus import topis
from app.domains.bus.features import daytype_of
from app.domains.bus.fleet import HOURS_PER_DAY, peak_on_road

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
LIVE, REPLAY = "live", "replay"


# ==================================================
# 1. 경로와 거리 (TM 미터 평면)
# ==================================================

def path_xy(path: list[list[float]]) -> np.ndarray:
    """geo json의 [[lat, lng], ...] → TM (x, y) 배열"""
    return np.array([topis.wgs_to_tm(lat, lng) for lat, lng in path], dtype=float)


def distance_to_path(px: float, py: float, xy: np.ndarray) -> tuple[float, int]:
    """점에서 꺾은선까지 가장 가까운 거리(m)와 그 선분의 앞 꼭짓점 번호"""
    if len(xy) == 0:
        return math.inf, -1
    if len(xy) == 1:
        return float(math.hypot(px - xy[0, 0], py - xy[0, 1])), 0
    a, b = xy[:-1], xy[1:]
    ab = b - a
    ap = np.array([px, py]) - a
    denom = (ab ** 2).sum(axis=1)
    t = np.clip(np.where(denom > 0, (ap * ab).sum(axis=1) / np.where(denom > 0, denom, 1), 0), 0, 1)
    nearest = a + ab * t[:, None]
    d = np.hypot(nearest[:, 0] - px, nearest[:, 1] - py)
    i = int(d.argmin())
    return float(d[i]), i


def nearest_stop(geo: dict, lat: float, lng: float) -> dict | None:
    """돌발 지점에서 가장 가까운 정류장 (순번·이름)"""
    stops = geo.get("stops") or []
    if not stops:
        return None
    x, y = topis.wgs_to_tm(lat, lng)
    best = min(stops, key=lambda s: math.hypot(*(np.subtract(topis.wgs_to_tm(s["lat"], s["lng"]), (x, y)))))
    return {"seq": int(best["seq"]), "name": best.get("name", "")}


# ==================================================
# 2. 돌발
# ==================================================

def _when(date: str, clock: str) -> dt.datetime | None:
    """'20261128' + '1357'/'135700' → KST 시각. 읽지 못하면 None"""
    date, clock = (date or "").strip(), (clock or "").strip()
    if len(date) != 8 or not date.isdigit():
        return None
    clock = clock.ljust(6, "0")[:6] if clock.isdigit() else "000000"
    try:
        return dt.datetime.strptime(date + clock, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


def clean_incidents(rows: list[dict], codes: dict[str, str] | None = None) -> list[dict]:
    """AccInfo 행 → 계산에 쓰는 형태 (좌표는 WGS84로 바꿔 둠). 좌표를 못 읽는 행은 버림"""
    codes = codes or {}
    out = []
    for r in rows:
        try:
            x, y = float(r.get("grs80tm_x", "")), float(r.get("grs80tm_y", ""))
        except ValueError:
            continue
        lat, lng = topis.tm_to_wgs(x, y)
        start = _when(r.get("occr_date", ""), r.get("occr_time", ""))
        end = _when(r.get("exp_clr_date", ""), r.get("exp_clr_time", ""))
        out.append({
            "acc_id": r.get("acc_id", ""),
            "type": r.get("acc_type", ""),
            "type_name": codes.get(r.get("acc_type", ""), r.get("acc_type", "")),
            "dtype_name": codes.get(r.get("acc_dtype", ""), r.get("acc_dtype", "")),
            "info": " ".join((r.get("acc_info") or "").split()),
            "link_id": r.get("link_id", ""),
            "start": start.isoformat() if start else None,
            "expected_clear": end.isoformat() if end else None,
            "x": x, "y": y, "lat": round(lat, 6), "lng": round(lng, 6),
        })
    return out


def is_active(item: dict, now: dt.datetime) -> bool:
    start = dt.datetime.fromisoformat(item["start"]) if item.get("start") else None
    end = dt.datetime.fromisoformat(item["expected_clear"]) if item.get("expected_clear") else None
    return (start is None or start <= now) and (end is None or now <= end)


def is_long_term(item: dict, now: dt.datetime, days: int = TOPIS_LONG_TERM_DAYS) -> bool:
    end = dt.datetime.fromisoformat(item["expected_clear"]) if item.get("expected_clear") else None
    return end is not None and end - now > dt.timedelta(days=days)


def route_incidents(incidents: list[dict], geo: dict, xy: np.ndarray, link_ids: set[str], now: dt.datetime,
                    near_m: float = TOPIS_ACC_NEAR_M) -> tuple[list[dict], list[dict]]:
    """노선 경로 위의 진행 중인 돌발 → (지금 알릴 것, 장기 공사)"""
    now_items, long_items = [], []
    for item in incidents:
        if not is_active(item, now):
            continue
        dist, _ = distance_to_path(item["x"], item["y"], xy)
        if dist > near_m and item.get("link_id") not in link_ids:
            continue
        out = {k: item[k] for k in ("acc_id", "type", "type_name", "dtype_name", "info", "start", "expected_clear", "lat", "lng")}
        out["distance_m"] = round(dist) if math.isfinite(dist) else None
        out["near_stop"] = nearest_stop(geo, item["lat"], item["lng"])
        out["long_term"] = is_long_term(item, now)
        (long_items if out["long_term"] else now_items).append(out)
    now_items.sort(key=lambda i: i["distance_m"] if i["distance_m"] is not None else math.inf)
    return now_items, long_items


# ==================================================
# 3. 정체 → 한 바퀴 추가 시간
# ==================================================

def extra_minutes(links: pd.DataFrame, live_seconds: dict[str, float], usual_kmh: dict[str, float],
                  min_coverage: float = TOPIS_MIN_COVERAGE) -> dict:
    """노선 링크(link_id, length_m)에서 지금 − 평소 통행시간의 합(분). 받은 비율이 낮으면 extra_minutes = None"""
    total = float(links["length_m"].sum()) if not links.empty else 0.0
    used_len, live_sum, usual_sum, used = 0.0, 0.0, 0.0, 0
    for r in links.itertuples():
        sec, kmh = live_seconds.get(str(r.link_id)), usual_kmh.get(str(r.link_id))
        if sec is None or not kmh or kmh <= 0 or sec <= 0:
            continue
        used += 1
        used_len += float(r.length_m)
        live_sum += float(sec)
        usual_sum += float(r.length_m) / (kmh / 3.6)
    coverage = used_len / total if total > 0 else 0.0
    extra = max(0.0, (live_sum - usual_sum) / 60.0) if coverage >= min_coverage else None
    return {"links_total": int(len(links)), "links_used": used, "coverage": round(coverage, 3),
            "live_minutes": round(live_sum / 60.0, 1) if used else None,
            "usual_minutes": round(usual_sum / 60.0, 1) if used else None,
            "extra_minutes": round(extra, 1) if extra is not None else None}


def _bearing(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360


def assign_segments(segments: list[tuple[int, np.ndarray]], links: dict[str, np.ndarray],
                    near_m: float = TOPIS_LINK_NEAR_M, max_angle: float = 60) -> pd.DataFrame:
    """링크 꼭짓점마다 가장 가까운(방향이 맞는) 정류장 구간을 골라, 링크를 구간에 나눠 줌
    segments: [(구간 seq, TM 꼭짓점 배열)], links: {link_id: TM 꼭짓점 배열(순서대로)}
    → link_id, seq, share (그 링크 꼭짓점 중 그 구간에 붙은 비율, 링크마다 합 1)"""
    prepared = []
    for seq, xy in segments:
        if len(xy) < 2:
            continue
        a, b = xy[:-1], xy[1:]
        ab = b - a
        denom = (ab ** 2).sum(axis=1)
        prepared.append((seq, a, ab, np.where(denom > 0, denom, 1), np.array([_bearing(*d) for d in ab])))
    rows = []
    for link_id, pts in links.items():
        if len(pts) < 2:
            continue
        link_b = _bearing(*(pts[-1] - pts[0]))
        votes: dict[int, int] = {}
        for p in pts:
            best, best_d = None, near_m
            for seq, a, ab, denom, seg_b in prepared:
                t = np.clip(((p - a) * ab).sum(axis=1) / denom, 0, 1)
                d = np.hypot(*(a + ab * t[:, None] - p).T)
                i = int(d.argmin())
                if d[i] <= best_d and abs((seg_b[i] - link_b + 180) % 360 - 180) <= max_angle:
                    best, best_d = seq, float(d[i])
            if best is not None:
                votes[best] = votes.get(best, 0) + 1
        total = sum(votes.values())
        rows += [{"link_id": link_id, "seq": seq, "share": round(v / total, 3)} for seq, v in votes.items()]
    return pd.DataFrame(rows, columns=["link_id", "seq", "share"])


def segment_speeds(seg_links: pd.DataFrame, lengths: dict[str, float], live_seconds: dict[str, float],
                   usual_kmh: dict[str, float], min_m: float = TOPIS_SEGMENT_MIN_M) -> list[dict]:
    """정류장 구간마다 지금 속도 ÷ 평소 속도 = Σ평소 통행시간 ÷ Σ지금 통행시간 (링크 길이 × 구간 비율로 가중)
    지금·평소 값을 모두 받은 링크만 씀. 그런 링크 길이가 min_m 미만인 구간은 빼고 보냄"""
    acc: dict[int, list[float]] = {}
    for r in seg_links.itertuples():
        link_id = str(r.link_id)
        sec, kmh, length = live_seconds.get(link_id), usual_kmh.get(link_id), lengths.get(link_id)
        if not sec or sec <= 0 or not kmh or kmh <= 0 or not length:
            continue
        a = acc.setdefault(int(r.seq), [0.0, 0.0, 0.0])
        a[0] += length / (kmh / 3.6) * r.share
        a[1] += float(sec) * r.share
        a[2] += length * r.share
    return [{"seq": seq, "ratio": round(u / live, 3), "length_m": round(m)}
            for seq, (u, live, m) in sorted(acc.items()) if m >= min_m and live > 0]


def slow_links(links: pd.DataFrame, live_seconds: dict[str, float], usual_kmh: dict[str, float],
               ratio: float = TOPIS_SLOW_RATIO, top: int = TOPIS_SLOW_LINKS_TOP) -> list[dict]:
    """지금 속도가 평소의 ratio 아래인 링크를 더 늦어지는 시간이 큰 순서로 (패널 '많이 느린 도로')"""
    out = []
    for r in links.itertuples():
        link_id = str(r.link_id)
        sec, kmh = live_seconds.get(link_id), usual_kmh.get(link_id)
        length = float(r.length_m) if r.length_m == r.length_m else 0.0
        if not sec or sec <= 0 or not kmh or kmh <= 0 or length <= 0:
            continue
        live_kmh = length / float(sec) * 3.6
        if live_kmh >= kmh * ratio:
            continue
        out.append({"link_id": link_id, "road_name": str(getattr(r, "road_name", "") or ""), "length_m": round(length),
                    "live_kmh": round(live_kmh, 1), "usual_kmh": round(kmh, 1),
                    "extra_seconds": round(float(sec) - length / (kmh / 3.6))})
    return sorted(out, key=lambda x: -x["extra_seconds"])[:top]


def delayed_run(run: list[int], hour: int, extra: float) -> list[int]:
    """지금 도로 위에 있는 버스(최근 한 바퀴 동안 출발한 시간대 ~ 지금 시간대)의 한 바퀴에 extra분을 더한 24칸"""
    back = math.ceil(run[hour] / 60) if run[hour] > 0 else 0
    out = list(run)
    for h in range(max(0, hour - back), hour + 1):
        if out[h] > 0:
            out[h] = int(round(out[h] + extra))
    return out


def fleet_check(trips: list[float], run: list[int], hour: int, extra: float | None, authorized: int) -> dict:
    """평소와 추가 시간을 더했을 때 도로 위 버스 대수, 인가대수 부족 여부"""
    buses_usual = peak_on_road(trips, run, hour)
    est_run = delayed_run(run, hour, extra) if extra else list(run)
    buses_est = peak_on_road(trips, est_run, hour)
    return {"usual_run": int(run[hour]), "est_run": int(est_run[hour]),
            "buses_usual": int(buses_usual), "buses_est": int(buses_est), "authorized": int(authorized),
            "fleet_short": bool(authorized > 0 and buses_est > authorized and buses_est > buses_usual)}


def is_slow(traffic: dict, ratio: float = TOPIS_EXTRA_WARN_RATIO) -> bool:
    """한 바퀴 추가 시간이 평소 한 바퀴(버스, 없으면 같은 링크의 평소 차량 통행시간)의 ratio 이상인지"""
    base = traffic.get("usual_run") or traffic.get("usual_minutes")
    return bool(base) and traffic["extra_minutes"] >= base * ratio


def messages_for(route: str, incidents: list[dict], traffic: dict | None) -> list[str]:
    out = []
    for i in incidents[:3]:
        where = f", {i['near_stop']['seq']}번째 정류장({i['near_stop']['name']}) 근처" if i.get("near_stop") else ""
        out.append(f"{route}번 경로에 {i['dtype_name'] or i['type_name']}{where}")
    if traffic and traffic.get("extra_minutes") is not None:
        if is_slow(traffic):
            out.append(f"평소보다 한 바퀴 약 {traffic['extra_minutes']:.0f}분 더 걸릴 수 있음 (차량 소통 기준)")
        if traffic.get("fleet_short"):
            out.append(f"도로 위 버스 {traffic['buses_est']}대 필요 > 인가대수 {traffic['authorized']}대 → 인가대수 부족 가능")
    if out:
        out.append("배차는 바꾸지 않았습니다. 담당자 검토 필요")
    return out


# ==================================================
# 4. 노선별 알림 묶기 (실시간 / 기록 다시 보기)
# ==================================================

class TrafficAlerts:
    """노선 경로·링크 표·평소 속도 표를 읽어 두고, 받은 값(또는 기록)으로 노선별 알림을 만듦"""

    def __init__(self, base_dir: Path, geo_of, plan_of, client: topis.TopisClient | None = None, clock=None):
        """geo_of(route) → geo json, plan_of(route, date, hour) → {"trips": 24칸, "run": 24칸, "authorized", "basis"} | None
        clock() → 지금 시각 (KST, 테스트에서 바꿈)"""
        self.base = Path(base_dir)
        self.clock = clock or (lambda: dt.datetime.now(KST))
        self.geo_of = geo_of
        self.plan_of = plan_of
        self.client = client or topis.TopisClient()
        self._cache: tuple[float, dict] | None = None
        self._codes: dict[str, str] | None = None
        self._xy: dict[str, np.ndarray] = {}

    @property
    def snapshot_dir(self) -> Path:
        return self.base / TOPIS_SNAPSHOT_DIR

    # ---------------- 준비된 표 ----------------

    def _read(self, name: str) -> pd.DataFrame | None:
        path = self.base / name
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as e:
            logger.warning("TOPIS 표를 읽지 못했습니다 (%s): %s", path, e)
            return None

    @property
    def route_links(self) -> pd.DataFrame | None:
        table = self._read(TOPIS_ROUTE_LINKS)
        if table is not None:
            table["link_id"] = table["link_id"].astype(str)
        return table

    @property
    def route_segments(self) -> pd.DataFrame | None:
        table = self._read(TOPIS_ROUTE_SEGMENTS)
        if table is not None:
            table["link_id"] = table["link_id"].astype(str)
        return table

    @property
    def link_speed(self) -> pd.DataFrame | None:
        table = self._read(TOPIS_LINK_SPEED)
        if table is not None:
            table["link_id"] = table["link_id"].astype(str)
        return table

    def ready(self) -> dict:
        return {"route_links": (self.base / TOPIS_ROUTE_LINKS).exists(),
                "link_speed": (self.base / TOPIS_LINK_SPEED).exists(),
                "route_segments": (self.base / TOPIS_ROUTE_SEGMENTS).exists(),
                "api_key": self.client.ready}

    def _route_xy(self, route: str, geo: dict) -> np.ndarray:
        if route not in self._xy:
            self._xy[route] = path_xy(geo.get("path") or [])
        return self._xy[route]

    # ---------------- 받기 ----------------

    def codes(self) -> dict[str, str]:
        if self._codes is None:
            codes = {}
            for service, key, name in (("AccMainCode", "acc_type", "acc_type_nm"), ("AccSubCode", "acc_dtype", "acc_dtype_nm")):
                try:
                    codes |= {r[key]: r[name] for r in self.client.call_all(service) if r.get(key)}
                except topis.TopisError as e:
                    logger.warning("돌발 종류 이름을 받지 못했습니다: %s", e.detail)
            self._codes = codes
        return self._codes

    def fetch(self, routes: list[str]) -> dict:
        """실시간으로 받아 기록 형태로 돌려줌: 돌발(전체, 가공), 노선 링크의 지금 통행시간(초)"""
        from concurrent.futures import ThreadPoolExecutor

        warnings = []
        incidents = clean_incidents(self.client.call_all("AccInfo"), self.codes())
        links = self.route_links
        link_ids = sorted(set(links.loc[links["route"].isin(routes), "link_id"])) if links is not None else []

        def one(link_id: str):
            try:
                rows, _ = self.client.call("TrafficInfo", 1, 1, link_id)
                return link_id, float(rows[0]["prcs_trv_time"]) if rows else None
            except (topis.TopisError, KeyError, ValueError) as e:
                return link_id, e

        seconds, failed = {}, 0
        with ThreadPoolExecutor(max_workers=TOPIS_MAX_CONCURRENCY) as pool:
            for link_id, value in pool.map(one, link_ids):
                if isinstance(value, Exception):
                    failed += 1
                elif value is not None:
                    seconds[link_id] = value
        if failed:
            warnings.append(f"링크 {len(link_ids)}개 중 {failed}개의 소통 정보를 받지 못했습니다")
        now = self.clock().replace(microsecond=0)
        return {"fetched_at": now.isoformat(), "incidents": incidents, "link_seconds": seconds, "warnings": warnings}

    # ---------------- 계산 ----------------

    def build(self, data: dict, routes: list[str], source: str, snapshot: str | None) -> dict:
        now = dt.datetime.fromisoformat(data["fetched_at"])
        hour = now.hour
        daytype = daytype_of(pd.Timestamp(now.date()))
        warnings = list(data.get("warnings", []))
        links, speed, seg_table = self.route_links, self.link_speed, self.route_segments
        if links is None:
            warnings.append("노선 ↔ 링크 표가 없어 정체(추가 시간)는 계산하지 않았습니다. scripts/build_topis_links.sh 실행 필요")
        if speed is None:
            warnings.append("평소 링크 속도 표가 없어 정체(추가 시간)는 계산하지 않았습니다. scripts/build_topis_speed.sh 실행 필요")
        if links is not None and seg_table is None:
            warnings.append("노선 링크 ↔ 정류장 구간 표가 없어 지도 소통 띠는 그리지 않습니다. scripts/build_topis_links.sh 실행 필요")
        usual = {}
        if speed is not None:
            rows = speed[(speed["daytype"] == daytype) & (speed["hour"] == hour)]
            usual = dict(zip(rows["link_id"], rows["speed_kmh"].astype(float)))

        out_routes = []
        for route in routes:
            geo = self.geo_of(route)
            if geo is None:
                warnings.append(f"{route}번 경로 좌표가 없어 건너뜀")
                continue
            xy = self._route_xy(route, geo)
            mine = links[links["route"] == route] if links is not None else pd.DataFrame(columns=["link_id", "length_m"])
            incidents, long_term = route_incidents(data.get("incidents", []), geo, xy, set(mine["link_id"]), now)
            traffic, segments, slow = None, [], []
            seconds = data.get("link_seconds", {})
            if links is not None and speed is not None and not mine.empty:
                traffic = {"hour": hour, **extra_minutes(mine, seconds, usual)}
                slow = slow_links(mine, seconds, usual)
                if seg_table is not None:
                    lengths = dict(zip(mine["link_id"], mine["length_m"].astype(float)))
                    segments = segment_speeds(seg_table[seg_table["route"] == route], lengths, seconds, usual)
                plan = self.plan_of(route, now.date(), hour)
                if plan is not None:
                    traffic |= fleet_check(plan["trips"], plan["run"], hour, traffic["extra_minutes"], plan["authorized"])
                    traffic["basis"] = plan["basis"]
            out_routes.append({"route": route, "incidents": incidents, "long_term": long_term, "traffic": traffic,
                               "segments": segments, "slow_links": slow,
                               "messages": messages_for(route, incidents, traffic)})
        return {"source": source, "fetched_at": data["fetched_at"], "snapshot": snapshot, "daytype": daytype,
                "routes": out_routes, "warnings": warnings, "ready": self.ready(), "needs_review": True}

    def live(self, routes: list[str]) -> dict:
        """실시간. TOPIS_CACHE_SECONDS 안에는 직전 결과를 다시 씀. 받을 때마다 기록으로 남김"""
        if self._cache and time.monotonic() - self._cache[0] < TOPIS_CACHE_SECONDS \
                and set(routes) <= set(self._cache[1]["routes"]):
            data, name = self._cache[1]["data"], self._cache[1]["name"]
        else:
            data = self.fetch(routes)
            name = dt.datetime.fromisoformat(data["fetched_at"]).strftime("%Y%m%d_%H%M%S")
            try:
                topis.save_snapshot(self.snapshot_dir, name, data)
            except OSError as e:
                logger.warning("기록을 남기지 못했습니다: %s", e)
            self._cache = (time.monotonic(), {"data": data, "name": name, "routes": list(routes)})
        return self.build(data, routes, LIVE, name)

    def replay(self, routes: list[str], name: str | None) -> dict | None:
        """기록 다시 보기. name이 없으면 가장 최근 기록. 없으면 None"""
        if name is None:
            items = topis.list_snapshots(self.snapshot_dir, 1)
            if not items:
                return None
            name = items[0]["name"]
        data = topis.load_snapshot(self.snapshot_dir, name)
        return self.build(data, routes, REPLAY, name) if data else None


def plan_from(trips: list[float], run: list[int] | int, authorized, basis: str) -> dict:
    """plan_of가 돌려줄 형태로 맞춤 (운행시간 정수는 24칸으로)"""
    run = [int(run)] * HOURS_PER_DAY if isinstance(run, (int, float)) else [int(v) for v in run]
    return {"trips": [float(t) for t in trips], "run": run, "authorized": int(authorized or 0), "basis": basis}
