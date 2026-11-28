# ==================================================
# 예측 오차 점검 → 재학습 권고 (요청 R-3, docs/plans/park-next.md Task P-5)
# ==================================================
# 미리 만들어 둔 예측(3주 앞)과 나중에 들어온 실측을 한 주씩 견주어, 기준을 넘으면 "재학습 권고"를 냅니다.
# - 판정은 scripts/check_drift.py가 하고 결과를 eval/results/drift/<id>.json에 남깁니다. 서버는 읽기만 합니다.
# - 자동 재학습은 하지 않습니다. 권고를 받은 담당자가 승인·보류·반려하고, 승인하면 실행할 명령을 안내합니다.
#
# 기준 (app/config.py, 하나라도 걸리면 권고)
#   ① 정류장·시간 MAE가 평가 MAE의 DRIFT_MAE_RATIO배 초과
#   ② 그 주에 모델이 작년 같은 요일(베이스라인)보다 못함
#   ③ 노선·시간 합계가 P90 상한 이하인 비율이 DRIFT_P90_COVERAGE_MIN(%) 미만

import json
import re
from pathlib import Path

import pandas as pd

from app.config import (
    DRIFT_MAE_RATIO,
    DRIFT_MIN_DAYS,
    DRIFT_P90_COVERAGE_MIN,
    DRIFT_RESULTS_DIR,
    DRIFT_RETRAIN_STEPS,
)
from app.domains.bus.calibration import ratio_for

RESULTS_DIR = DRIFT_RESULTS_DIR      # 테스트에서 바꿀 수 있게 모듈 변수로 둠
KINDS = {"forward": "실제 예측", "replay": "다시 돌려 봄"}
CHECK_ID_RE = re.compile(r"^(forward|replay)_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$")
ROW_COLS = ["date", "route", "stop_id", "hour", "boarding", "pred", "baseline", "daytype"]

CAUTION = {
    "forward": "3주 전에 미리 만든 예측을 나중에 공개된 실측과 견준 값입니다. 날씨는 작년 같은 시기 평균, 운행횟수는 계획값을 썼습니다.",
    "replay": ("평가 구간의 예측을 주별로 다시 채점한 값입니다. 승객 수 입력은 실제 운영과 같이 3주 이상 지난 값만 썼지만, "
               "날씨·운행횟수는 실제값을 써서 실제 예측보다 오차가 조금 작게 나옵니다. 기준 MAE도 같은 구간에서 나온 값입니다."),
}


def check_id(kind: str, start, end) -> str:
    return f"{kind}_{pd.Timestamp(start):%Y-%m-%d}_{pd.Timestamp(end):%Y-%m-%d}"


# ==================================================
# 1. 지표
# ==================================================

def _metrics(rows: pd.DataFrame, table: dict | None) -> dict:
    """정류장·시간 행 묶음의 MAE(모델·베이스라인)와 노선·시간 합계의 P90 상한 적중률"""
    err = (rows["pred"] - rows["boarding"]).abs()
    with_base = rows["baseline"].notna()
    mae = float(err.mean()) if len(rows) else None
    base = float((rows.loc[with_base, "baseline"] - rows.loc[with_base, "boarding"]).abs().mean()) if with_base.any() else None
    model_on_base = float(err[with_base].mean()) if with_base.any() else None   # 베이스라인과 같은 행에서 비교
    coverage, cells = None, 0
    if table is not None and len(rows):
        totals = rows.groupby(["date", "route", "hour"], observed=True)[["boarding", "pred"]].sum().reset_index()
        upper = [p * ratio_for(table, r, h) for p, r, h in zip(totals["pred"], totals["route"], totals["hour"])]
        cells = len(totals)
        coverage = float((totals["boarding"] <= pd.Series(upper, index=totals.index)).mean() * 100) if cells else None
    return {
        "rows": int(len(rows)),
        "stop_hour_mae": round(mae, 3) if mae is not None else None,
        "baseline_mae": round(base, 3) if base is not None else None,
        "model_mae_on_baseline_rows": round(model_on_base, 3) if model_on_base is not None else None,
        "route_hour_cells": int(cells),
        "p90_coverage_pct": round(coverage, 1) if coverage is not None else None,
    }


# ==================================================
# 2. 판정
# ==================================================

def judge(rows: pd.DataFrame, *, kind: str, reference_mae: float, table: dict | None,
          model_version: str = "", start=None, end=None) -> dict:
    """한 주(또는 한 구간)의 예측·실측 행 → 지표와 재학습 권고 여부

    rows: ROW_COLS 열. boarding(실측)이 없는 행은 뺌. start·end를 주면 기간 표시에 씀 (없으면 행의 첫날·마지막 날)
    """
    if kind not in KINDS:
        raise ValueError(f"kind는 {', '.join(KINDS)} 중 하나입니다: {kind}")
    rows = rows[rows["boarding"].notna() & rows["pred"].notna()].copy()
    rows["date"] = pd.to_datetime(rows["date"])
    start = pd.Timestamp(start) if start is not None else (rows["date"].min() if len(rows) else None)
    end = pd.Timestamp(end) if end is not None else (rows["date"].max() if len(rows) else None)
    if start is None or end is None:
        raise ValueError("판정할 행이 없습니다 (실측과 예측이 함께 있는 행 0개)")
    days = int(rows["date"].nunique())

    metrics = _metrics(rows, table)
    mae, base = metrics["stop_hour_mae"], metrics["baseline_mae"]
    metrics["mae_ratio"] = round(mae / reference_mae, 3) if mae is not None and reference_mae else None

    checks = [
        {"name": "mae_ratio", "label": f"오차가 평가 때의 {DRIFT_MAE_RATIO}배 이하",
         "value": metrics["mae_ratio"], "limit": DRIFT_MAE_RATIO,
         "failed": metrics["mae_ratio"] is not None and metrics["mae_ratio"] > DRIFT_MAE_RATIO},
        {"name": "vs_baseline", "label": "작년 같은 요일보다 나음",
         "value": metrics["model_mae_on_baseline_rows"], "limit": base,
         "failed": base is not None and metrics["model_mae_on_baseline_rows"] is not None
                   and metrics["model_mae_on_baseline_rows"] >= base},
        {"name": "p90_coverage", "label": f"실제가 상한(P90) 이하인 비율 {DRIFT_P90_COVERAGE_MIN:g}% 이상",
         "value": metrics["p90_coverage_pct"], "limit": DRIFT_P90_COVERAGE_MIN,
         "failed": metrics["p90_coverage_pct"] is not None and metrics["p90_coverage_pct"] < DRIFT_P90_COVERAGE_MIN},
    ]
    failed = [c for c in checks if c["failed"]]
    if days < DRIFT_MIN_DAYS:
        verdict, reasons = "insufficient", [f"실측이 있는 날이 {days}일이라 판정하지 않습니다 (최소 {DRIFT_MIN_DAYS}일)"]
    elif failed:
        verdict, reasons = "retrain", [_reason(c, reference_mae) for c in failed]
    else:
        verdict, reasons = "ok", ["세 기준을 모두 통과했습니다. 지금 모델을 그대로 써도 됩니다."]
    recommend = verdict == "retrain"

    return {
        "id": check_id(kind, start, end),
        "kind": kind,
        "kind_label": KINDS[kind],
        "window": {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}", "days": days},
        "model_version": model_version,
        "reference_mae": round(float(reference_mae), 3),
        "metrics": metrics,
        "by_daytype": {k: _metrics(g, table) for k, g in rows.groupby("daytype", observed=True)},
        "by_route": {str(k): _metrics(g, table) for k, g in rows.groupby("route", observed=True)},
        "checks": checks,
        "verdict": verdict,
        "recommend": recommend,
        "reasons": reasons,
        "next_steps": list(DRIFT_RETRAIN_STEPS) if recommend else [],
        "caution": CAUTION[kind],
        "needs_review": True,
    }


def _reason(check: dict, reference_mae: float) -> str:
    if check["name"] == "mae_ratio":
        return (f"정류장·시간 오차가 평가 때({reference_mae:.2f}명)의 {check['value']:.2f}배로, "
                f"기준 {check['limit']}배를 넘었습니다")
    if check["name"] == "vs_baseline":
        return f"모델 오차({check['value']:.2f}명)가 작년 같은 요일 값({check['limit']:.2f}명)보다 크거나 같습니다"
    return f"실제가 상한(P90) 이하인 비율이 {check['value']:.1f}%로, 기준 {check['limit']:g}%보다 낮습니다"


# ==================================================
# 3. 주 나누기 (월~일)
# ==================================================

def weeks(dates) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """날짜들을 월~일 주로 묶어 (그 주의 첫날, 마지막 날) — 자료에 있는 날만 기준"""
    s = pd.Series(sorted(pd.to_datetime(pd.Series(list(dates))).unique()))
    if s.empty:
        return []
    period = s.dt.to_period("W-SUN")
    return [(g.min(), g.max()) for _, g in s.groupby(period)]


# ==================================================
# 4. 결과 파일
# ==================================================

def save_check(result: dict, results_dir: Path | None = None) -> Path:
    results_dir = results_dir or RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{result['id']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_check(check_id_: str, results_dir: Path | None = None) -> dict | None:
    results_dir = results_dir or RESULTS_DIR
    if not CHECK_ID_RE.match(check_id_):
        return None
    path = results_dir / f"{check_id_}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_checks(limit: int, results_dir: Path | None = None) -> list[dict]:
    """최근 기간부터. 실제 예측(forward)이 같은 기간의 다시 돌려 보기보다 앞"""
    results_dir = results_dir or RESULTS_DIR
    if not results_dir.exists():
        return []
    items = []
    for path in results_dir.glob("*.json"):
        if not CHECK_ID_RE.match(path.stem):
            continue
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda r: (r["window"]["end"], r["kind"] == "forward"), reverse=True)
    return items[:limit]
