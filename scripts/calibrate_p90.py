"""
승객 수 상한(P90) 비율표 만들기  (F-11)

예측값은 평균 수준이라, 증차 판단에 쓸 "많이 몰려도 10번 중 9번은 이보다 적게 탄다"는 값을 따로 만듭니다.
검증 구간을 둘로 나눠 앞쪽(튜닝 구간)에서 비율을 구하고 뒤쪽(평가 구간)에서 채점합니다.

  예측 그대로   비율 1.0                        (기준선)
  C 고정 배수   전 노선 × 1.1
  C 전체 한 값  튜닝 구간 전체 비율의 90% 분위수 하나
  A 노선·시간   노선·시간별 90% 분위수, 표본 10개 미만은 노선 전체 값   ← 저장하는 비율표

합격 기준: 평가 구간에서 실제 승객이 상한 이하인 비율이 88~92%. 그 안에서는 여유(상한 − 예측)가 작은 쪽.

출력
  models/bus/<mode>/p90.json           비율표 (튜닝 구간으로만 만든 값)
  eval/results/p90_calibration.json    방식별 채점 결과 (실데이터일 때만)

사용
  python scripts/calibrate_p90.py                 # DATA_MODE(.env) 기준
  python scripts/calibrate_p90.py --mode sample   # 합성 데이터 (튜닝 구간 = 검증 구간 앞쪽 절반)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import (  # noqa: E402
    BUS_P90_COVERAGE_RANGE, BUS_P90_FIXED_RATIO, BUS_P90_MIN_SAMPLES, BUS_P90_QUANTILE,
    MODELS_DIR, PROCESSED_DIR, resolve_data_mode,
)
from app.domains.bus.calibration import (  # noqa: E402
    coverage, default_tune_end, fit_ratios, route_hour_totals, split_by_date, upper_bound,
)

RESULT_PATH = ROOT / "eval" / "results" / "p90_calibration.json"
STORE_COLS = ["date", "route", "hour", "boarding", "pred", "daytype", "split"]
CHOSEN = "A 노선·시간"


def window(frame: pd.DataFrame) -> dict:
    return {"start": f"{frame['date'].min():%Y-%m-%d}", "end": f"{frame['date'].max():%Y-%m-%d}",
            "days": int(frame["date"].nunique())}


def passed(score: dict) -> bool:
    low, high = BUS_P90_COVERAGE_RANGE
    return low <= score["covered_pct"] <= high


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["real", "sample"], default=None)
    parser.add_argument("--tune-end", default=None, help="튜닝 구간 마지막 날 (YYYY-MM-DD)")
    args = parser.parse_args()

    mode = args.mode or resolve_data_mode()
    store_path = PROCESSED_DIR / f"bus_{mode}_store.parquet"
    if not store_path.exists():
        raise SystemExit(f"{store_path} 가 없습니다. 먼저 실행: python scripts/train_bus.py --mode {mode}")

    store = pd.read_parquet(store_path, columns=STORE_COLS)
    totals = route_hour_totals(store)
    if totals.empty:
        raise SystemExit("검증 구간(split == 'valid')에 실제값이 있는 행이 없습니다.")
    tune_end = pd.Timestamp(args.tune_end) if args.tune_end else default_tune_end(totals["date"], mode)
    tune, evaluation = split_by_date(totals, tune_end)
    if tune.empty or evaluation.empty:
        raise SystemExit(f"튜닝 구간 또는 평가 구간이 비었습니다 (튜닝 끝 {tune_end:%Y-%m-%d}).")

    # 1) 비율은 튜닝 구간으로만 구함
    table = fit_ratios(tune)
    overall = float((tune.loc[tune["pred"] > 0, "boarding"] / tune.loc[tune["pred"] > 0, "pred"])
                    .quantile(BUS_P90_QUANTILE))
    uppers = {
        "예측 그대로": lambda frame: frame["pred"] * 1.0,
        f"C 고정 배수 ×{BUS_P90_FIXED_RATIO}": lambda frame: frame["pred"] * BUS_P90_FIXED_RATIO,
        f"C 전체 한 값 ×{overall:.3f}": lambda frame: frame["pred"] * overall,
        CHOSEN: lambda frame: upper_bound(frame, table),
    }

    # 2) 평가 구간에서 채점 (튜닝 구간 점수는 참고용)
    scores = {name: {"eval": coverage(evaluation, fn(evaluation)), "tune": coverage(tune, fn(tune))}
              for name, fn in uppers.items()}
    low, high = BUS_P90_COVERAGE_RANGE
    print(f"\n[{mode}] 튜닝 {window(tune)['start']}~{window(tune)['end']} ({window(tune)['days']}일) | "
          f"평가 {window(evaluation)['start']}~{window(evaluation)['end']} ({window(evaluation)['days']}일)")
    print(f"합격 기준: 평가 구간에서 실제 ≤ 상한 비율 {low:.0f}~{high:.0f}%\n")
    print(f"{'방식':<22}{'실제≤상한(평가)':>14}{'여유(평가)':>12}{'실제≤상한(튜닝)':>16}  합격")
    for name, score in scores.items():
        mark = "✅" if passed(score["eval"]) else "❌"
        print(f"{name:<22}{score['eval']['covered_pct']:>13.1f}%{score['eval']['margin_pct']:>+11.1f}%"
              f"{score['tune']['covered_pct']:>15.1f}%  {mark}")

    # 3) 채택한 방식(A)을 요일유형·노선별로도 확인
    daytype = store.drop_duplicates("date").set_index("date")["daytype"]
    detail = evaluation.assign(upper=upper_bound(evaluation, table), daytype=evaluation["date"].map(daytype))
    detail["covered"] = detail["boarding"] <= detail["upper"]
    by_daytype = (detail.groupby("daytype")["covered"].mean() * 100).round(1).to_dict()
    by_route = (detail.groupby("route")["covered"].mean() * 100).round(1).to_dict()
    print(f"\n{CHOSEN} · 요일유형별: {by_daytype}")
    print(f"{CHOSEN} · 노선별: {by_route}")
    fallback = sorted(f"{route} {hour}시" for (route, hour), n in tune.groupby(["route", "hour"]).size().items()
                      if n < BUS_P90_MIN_SAMPLES)
    print(f"표본 {BUS_P90_MIN_SAMPLES}개 미만이라 노선 전체 값을 쓰는 시간대: {fallback or '없음'}")

    # 4) 저장
    meta = {
        "method": "route_hour_ratio",
        "quantile": BUS_P90_QUANTILE,
        "min_samples": BUS_P90_MIN_SAMPLES,
        "fit_window": window(tune),
        "eval_window": window(evaluation),
        "eval": scores[CHOSEN]["eval"],
        "passed": passed(scores[CHOSEN]["eval"]),
    }
    model_dir = MODELS_DIR / "bus" / mode
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "p90.json").write_text(json.dumps({**meta, **table}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n비율표 저장: {(model_dir / 'p90.json').relative_to(ROOT)}")

    if mode == "real":
        result = {**meta, "coverage_range": list(BUS_P90_COVERAGE_RANGE), "scores": scores,
                  "by_daytype": by_daytype, "by_route": by_route, "fallback_cells": fallback}
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"채점 결과 저장: {RESULT_PATH.relative_to(ROOT)}")

    if not meta["passed"]:
        print(f"\n⚠️  {CHOSEN} 방식이 합격 기준을 벗어났습니다. 비율표는 저장했지만 그대로 쓰기 전에 검토가 필요합니다.")


if __name__ == "__main__":
    main()
