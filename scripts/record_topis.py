"""시연용 실시간 기록 모으기 (이슈 #42 방법 2, docs/plans/park-next.md Task P-9)

시연 때 사고·정체가 없을 수 있어, 미리 일정 간격으로 TOPIS 실시간 돌발·소통을 받아 기록으로 남깁니다.
기록은 data/interim/topis/snapshots/<YYYYMMDD_HHMMSS>.json (키·주소 없음). 화면의 "기록 다시 보기"가 읽습니다.
API 서버가 실시간으로 받을 때도 같은 곳에 남으므로, 이 스크립트는 서버 없이 모을 때만 씁니다.

키 필요: .env의 SEOUL_OPEN_API_KEY. 먼저 build_topis_speed·build_topis_links를 실행해야 소통 정보가 들어갑니다.
사용: python scripts/record_topis.py [--every 5] [--minutes 120]
"""

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TOPIS_DIR  # noqa: E402
from app.domains.bus import topis  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.traffic import TrafficAlerts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("record_topis")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--every", type=float, default=5, help="몇 분마다 받을지 (기본 %(default)s)")
    ap.add_argument("--minutes", type=float, default=120, help="몇 분 동안 (기본 %(default)s)")
    args = ap.parse_args()

    ds = BusDataset()
    alerts = TrafficAlerts(ds.dir / TOPIS_DIR, ds.geo, lambda *_: None)
    if not alerts.client.ready:
        raise SystemExit("[실패] .env에 SEOUL_OPEN_API_KEY(서울 열린데이터광장 일반 인증키)가 없습니다")
    if alerts.route_links is None:
        log.warning("노선 ↔ 링크 표가 없어 돌발만 남깁니다 (scripts/build_topis_links.sh)")

    end = time.monotonic() + args.minutes * 60
    while True:
        try:
            data = alerts.fetch(list(ds.routes))
            name = dt.datetime.fromisoformat(data["fetched_at"]).strftime("%Y%m%d_%H%M%S")
            topis.save_snapshot(alerts.snapshot_dir, name, data)
            log.info("%s 돌발 %d건, 소통 링크 %d개%s", name, len(data["incidents"]), len(data["link_seconds"]),
                     f" ({'; '.join(data['warnings'])})" if data["warnings"] else "")
        except topis.TopisError as e:
            log.warning("받지 못함: %s", e.detail)
            if e.status in {"topis_key_missing", "topis_key_invalid"}:
                raise SystemExit(1)
        if time.monotonic() + args.every * 60 > end:
            break
        time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
