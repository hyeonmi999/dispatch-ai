# ==================================================
# 예측 오차 점검 → 재학습 권고 (요청 R-3, docs/plans/park-next.md Task P-5)
# ==================================================
# 실제 운영 (기본): 새 주 데이터를 받은 뒤, **다시 학습하기 전에** 돌립니다.
#   저장소의 미래 예측(split=forecast, 3주 전에 미리 만든 값)과 새로 받은 실측을 주별로 견줍니다.
#     python scripts/download_seoul_bus.py --seqs <공개일> --append
#     python scripts/check_drift.py
#   → eval/results/drift/forward_<첫날>_<마지막 날>.json (주마다 하나)
#
# 다시 돌려 보기 (시연·기준 점검): 평가 구간(BUS_TUNE_END 다음 날 ~)의 주마다 같은 계산
#     python scripts/check_drift.py --replay
#   → eval/results/drift/replay_<첫날>_<마지막 날>.json
#
# 결과는 화면(분석 → 모델 점검)과 GET /api/bus/drift에서 보이고, 승인·반려는 담당자가 합니다.
# 학습은 자동으로 돌리지 않습니다. API 서버를 끄고 돌립니다 (실데이터를 같이 올리면 메모리가 모자람).

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import BUS_TUNE_END, DRIFT_RESULTS_DIR  # noqa: E402
from app.domains.bus.calibration import load_ratios  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.drift import ROW_COLS, judge, save_check, weeks  # noqa: E402
from app.domains.bus.forecaster import BusForecaster  # noqa: E402

JOIN = ["date", "route", "stop_id", "stop_seq", "hour"]


def forward_rows(forecaster: BusForecaster, dataset: BusDataset) -> pd.DataFrame:
    """미리 만든 미래 예측 + 새로 받은 실측 (둘 다 있는 행만)"""
    store = forecaster.store
    future = store[store["split"] == "forecast"].drop(columns=["boarding"])
    if future.empty:
        return future
    actual = dataset.load_long()
    actual = actual[actual["date"].between(future["date"].min(), future["date"].max()) & actual["boarding"].notna()]
    for frame in (future, actual):
        frame["route"] = frame["route"].astype(str)
        frame["stop_id"] = frame["stop_id"].astype(str)
    return future.merge(actual[JOIN + ["boarding"]], on=JOIN, how="inner")


def replay_rows(forecaster: BusForecaster) -> pd.DataFrame:
    store = forecaster.store
    rows = store[(store["split"] == "valid") & (store["date"] > pd.Timestamp(BUS_TUNE_END))].copy()
    rows["route"] = rows["route"].astype(str)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "sample"], default="real")
    ap.add_argument("--replay", action="store_true", help="평가 구간을 주별로 다시 돌려 봄 (시연·기준 점검)")
    ap.add_argument("--out", default=str(DRIFT_RESULTS_DIR), help="결과 폴더 (기본 eval/results/drift)")
    args = ap.parse_args()

    dataset = BusDataset(args.mode)
    forecaster = BusForecaster(dataset)
    if not forecaster.load():
        raise SystemExit(f"학습된 모델이 없습니다. 먼저 실행: python scripts/train_bus.py --mode {args.mode}")
    metrics_path = forecaster.model_dir / "metrics.json"
    reference = json.loads(metrics_path.read_text(encoding="utf-8"))["stop_hour"]["model_mae"]
    table = load_ratios(forecaster.model_dir / "p90.json")
    if table is None:
        print("⚠️ 승객 수 상한 비율표가 없어 P90 기준은 건너뜁니다 (python scripts/calibrate_p90.py)")

    kind = "replay" if args.replay else "forward"
    rows = replay_rows(forecaster) if args.replay else forward_rows(forecaster, dataset)
    rows = rows[ROW_COLS] if not rows.empty else rows
    if rows.empty:
        raise SystemExit("견줄 행이 없습니다. 실제 운영이면 새 주 데이터를 받았는지 확인하세요 "
                         "(python scripts/download_seoul_bus.py --seqs <공개일> --append)")

    out = Path(args.out)
    print(f"기준 MAE {reference:.3f}명 (평가, metrics.json) · 모델 {forecaster.model_version} · {kind}")
    for start, end in weeks(rows["date"]):
        week = rows[rows["date"].between(start, end)]
        result = judge(week, kind=kind, reference_mae=reference, table=table, model_version=forecaster.model_version,
                       start=start, end=end)
        path = save_check(result, out)
        m = result["metrics"]
        print(f"  {result['window']['start']}~{result['window']['end']} ({result['window']['days']}일) "
              f"MAE {m['stop_hour_mae']} (×{m['mae_ratio']}) · 작년 {m['baseline_mae']} · P90 {m['p90_coverage_pct']}% "
              f"→ {result['verdict']}  {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


if __name__ == "__main__":
    main()
