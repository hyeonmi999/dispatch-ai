"""노선 ↔ TOPIS 링크 대응표 만들기 (이슈 #42 방법 2, docs/plans/park-next.md Task P-9)

먼저 scripts/build_topis_speed.py로 링크 목록(data/interim/topis/links.parquet)을 만든 뒤 실행합니다.
링크마다 꼭짓점(LinkVerInfo)을 받아, 노선 경로(data/interim/geo/<노선>.json)와 겹치는 링크를 고릅니다.

  겹침: 꼭짓점의 TOPIS_LINK_MIN_SHARE 이상이 경로에서 TOPIS_LINK_NEAR_M 안 + 링크 방향이 근처 경로 방향과 60° 안
        (왕복 경로라 같은 도로의 반대 방향 링크를 거르려고 방향을 봄)
  결과: data/interim/topis/route_links.parquet (route, link_id, length_m, share, path_index, road_name)
        data/interim/topis/route_link_segments.parquet (route, link_id, seq, share): 링크를 정류장 구간에 나눈 표 (지도 소통 띠)
        data/interim/topis/route_links_summary.json (노선별 링크 수, 링크 길이 합 ÷ 경로 길이)
  꼭짓점은 data/interim/topis/link_vertices.parquet에 모아 두고 다음 실행 때 건너뜀 (중간에 멈춰도 이어 받음)

키 필요: .env의 SEOUL_OPEN_API_KEY (서울 열린데이터광장 일반 인증키). 링크 수천 개를 한 번씩 부릅니다.
사용: python scripts/build_topis_links.py [--limit 100]   (--limit은 시험용: 앞의 링크 N개만 받음)
      python scripts/build_topis_links.py --segments-only  (키 없이: 받아 둔 꼭짓점·링크 표로 구간 표만 다시 만듦)
"""

import argparse
import json
import logging
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (  # noqa: E402
    INTERIM_DIR, TOPIS_DIR, TOPIS_LINK_MIN_SHARE, TOPIS_LINK_NEAR_M, TOPIS_LINKS, TOPIS_MAX_CONCURRENCY, TOPIS_ROUTE_LINKS,
    TOPIS_ROUTE_SEGMENTS,
)
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.topis import TopisClient, TopisError  # noqa: E402
from app.domains.bus.traffic import assign_segments, path_xy  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("build_topis_links")

OUT_DIR = INTERIM_DIR / TOPIS_DIR
VERTICES = OUT_DIR / "link_vertices.parquet"
SAVE_EVERY = 500              # 이만큼 받을 때마다 꼭짓점 파일에 저장
MAX_ANGLE = 60                # 링크 방향과 근처 경로 방향의 차이 허용(도)


def fetch_vertices(client: TopisClient, link_ids: list[str]) -> pd.DataFrame:
    done = pd.read_parquet(VERTICES) if VERTICES.exists() else pd.DataFrame(columns=["link_id", "ver_seq", "x", "y"])
    done["link_id"] = done["link_id"].astype(str)
    todo = [i for i in link_ids if i not in set(done["link_id"])]
    log.info("링크 %d개 중 %d개는 받아 둠, %d개 받기", len(link_ids), len(link_ids) - len(todo), len(todo))

    def one(link_id: str):
        for attempt in range(3):
            try:
                rows, _ = client.call("LinkVerInfo", 1, 1000, link_id)
                return [{"link_id": link_id, "ver_seq": int(r["ver_seq"]), "x": float(r["grs80tm_x"]),
                         "y": float(r["grs80tm_y"])} for r in rows]
            except TopisError as e:
                if e.status in {"topis_key_missing", "topis_key_invalid"}:
                    raise
                time.sleep(1 + attempt)
        log.warning("  %s 꼭짓점을 받지 못함", link_id)
        return []

    frames, started = [done], time.time()
    with ThreadPoolExecutor(max_workers=TOPIS_MAX_CONCURRENCY) as pool:
        for n, rows in enumerate(pool.map(one, todo), 1):
            if rows:
                frames.append(pd.DataFrame(rows))
            if n % SAVE_EVERY == 0 or n == len(todo):
                pd.concat(frames, ignore_index=True).to_parquet(VERTICES, index=False)
                log.info("  %d/%d (%.0f초)", n, len(todo), time.time() - started)
    # 빈 표(object 열)와 합치면 좌표가 object가 되어 numpy 계산이 실패함 → 형을 고정
    return pd.concat(frames, ignore_index=True).astype({"link_id": str, "ver_seq": int, "x": float, "y": float})


def bearing(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360


def match_route(xy: np.ndarray, vertices: pd.DataFrame, links: pd.DataFrame) -> pd.DataFrame:
    a, b = xy[:-1], xy[1:]
    ab = b - a
    denom = np.where((ab ** 2).sum(axis=1) > 0, (ab ** 2).sum(axis=1), 1)
    seg_bearing = np.array([bearing(dx, dy) for dx, dy in ab])
    lo, hi = xy.min(axis=0) - TOPIS_LINK_NEAR_M, xy.max(axis=0) + TOPIS_LINK_NEAR_M
    box = vertices[(vertices["x"].between(lo[0], hi[0])) & (vertices["y"].between(lo[1], hi[1]))]
    rows = []
    for link_id, v in box.groupby("link_id"):
        pts = v.sort_values("ver_seq")[["x", "y"]].to_numpy()
        if len(pts) < 2:
            continue
        # 꼭짓점마다 경로 선분까지 거리
        ap = pts[:, None, :] - a[None, :, :]
        t = np.clip((ap * ab[None, :, :]).sum(axis=2) / denom[None, :], 0, 1)
        near = a[None, :, :] + ab[None, :, :] * t[:, :, None]
        d = np.hypot(near[..., 0] - pts[:, None, 0], near[..., 1] - pts[:, None, 1])
        share = float((d.min(axis=1) <= TOPIS_LINK_NEAR_M).mean())
        if share < TOPIS_LINK_MIN_SHARE:
            continue
        link_b = bearing(*(pts[-1] - pts[0]))
        close = np.where(d.min(axis=0) <= TOPIS_LINK_NEAR_M)[0]
        diff = np.abs((seg_bearing[close] - link_b + 180) % 360 - 180)
        if len(close) == 0 or diff.min() > MAX_ANGLE:
            continue
        rows.append({"link_id": link_id, "share": round(share, 3), "path_index": int(close[diff.argmin()])})
    out = pd.DataFrame(rows, columns=["link_id", "share", "path_index"])
    return out.merge(links[["link_id", "length_m", "road_name"]], on="link_id", how="left").sort_values("path_index")


def build_segments(ds: BusDataset, table: pd.DataFrame, vertices: pd.DataFrame) -> pd.DataFrame:
    """노선 링크를 정류장 구간(geo segments, 상·하행 모두)에 나눔 → route_link_segments.parquet"""
    vg = {k: g.sort_values("ver_seq")[["x", "y"]].to_numpy(dtype=float) for k, g in vertices.groupby("link_id")}
    frames = []
    for route in ds.routes:
        geo = ds.geo(route)
        mine = table[table["route"] == route] if not table.empty else table
        if not geo or mine.empty:
            continue
        segments = [(int(s["seq"]), path_xy(s["coords"])) for s in geo.get("segments", []) if s.get("coords")]
        out = assign_segments(segments, {i: vg[i] for i in mine["link_id"] if i in vg})
        out.insert(0, "route", route)
        frames.append(out)
        log.info("%s번: 정류장 구간 %d개 중 %d개에 링크가 걸침", route, len(segments), out["seq"].nunique())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["route", "link_id", "seq", "share"])


def path_length(xy: np.ndarray) -> float:
    return float(np.hypot(*np.diff(xy, axis=0).T).sum()) if len(xy) > 1 else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="시험용: 앞의 링크 N개만 받음")
    ap.add_argument("--segments-only", action="store_true", help="키 없이: 받아 둔 꼭짓점·링크 표로 구간 표만 다시 만듦")
    args = ap.parse_args()

    if args.segments_only:
        if not VERTICES.exists() or not (OUT_DIR / TOPIS_ROUTE_LINKS).exists():
            raise SystemExit("[실패] link_vertices.parquet 또는 route_links.parquet가 없습니다. 먼저 키를 넣고 이 스크립트를 실행하세요")
        vertices = pd.read_parquet(VERTICES).astype({"link_id": str, "ver_seq": int, "x": float, "y": float})
        table = pd.read_parquet(OUT_DIR / TOPIS_ROUTE_LINKS)
        table["link_id"] = table["link_id"].astype(str)
        build_segments(BusDataset(), table, vertices).to_parquet(OUT_DIR / TOPIS_ROUTE_SEGMENTS, index=False)
        log.info("→ %s", OUT_DIR / TOPIS_ROUTE_SEGMENTS)
        return

    links_path = OUT_DIR / TOPIS_LINKS
    if not links_path.exists():
        raise SystemExit("[실패] 링크 목록이 없습니다. 먼저  python scripts/build_topis_speed.py  를 실행하세요")
    links = pd.read_parquet(links_path)
    links["link_id"] = links["link_id"].astype(str)
    client = TopisClient()
    if not client.ready:
        raise SystemExit("[실패] .env에 SEOUL_OPEN_API_KEY(서울 열린데이터광장 일반 인증키)가 없습니다")

    ids = links["link_id"].tolist()[: args.limit] if args.limit else links["link_id"].tolist()
    try:
        vertices = fetch_vertices(client, ids)
    except TopisError as e:
        raise SystemExit(f"[실패] {e.detail}")
    vertices["link_id"] = vertices["link_id"].astype(str)

    ds = BusDataset()
    frames, summary = [], {}
    for route in ds.routes:
        geo = ds.geo(route)
        if not geo or not geo.get("path"):
            log.warning("%s번 경로 좌표가 없어 건너뜀", route)
            continue
        xy = path_xy(geo["path"])
        m = match_route(xy, vertices, links)
        m.insert(0, "route", route)
        frames.append(m)
        length = path_length(xy)
        summary[route] = {"links": int(len(m)), "link_km": round(float(m["length_m"].sum()) / 1000, 1),
                          "path_km": round(length / 1000, 1),
                          "ratio": round(float(m["length_m"].sum()) / length, 3) if length else None}
        log.info("%s번: 링크 %d개, 링크 길이 %.1fkm / 경로 %.1fkm", route, len(m), summary[route]["link_km"], summary[route]["path_km"])

    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    table.to_parquet(OUT_DIR / TOPIS_ROUTE_LINKS, index=False)
    build_segments(ds, table, vertices).to_parquet(OUT_DIR / TOPIS_ROUTE_SEGMENTS, index=False)
    (OUT_DIR / "route_links_summary.json").write_text(
        json.dumps({"links_fetched": int(vertices["link_id"].nunique()), "routes": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    log.info("→ %s, %s", OUT_DIR / TOPIS_ROUTE_LINKS, OUT_DIR / TOPIS_ROUTE_SEGMENTS)


if __name__ == "__main__":
    main()
