"""
배차 방식 성능 비교  (F-14)

검증 구간 날짜마다 현행 / 규칙(F-04) / 그리디 / 최적화(F-13) 네 방식으로 운행횟수를 정하고,
실제 승객으로 계산한 혼잡도로 채점합니다. 운행횟수를 정할 때는 예측값만, 채점할 때만 실제값을 씁니다.

  1) 튜닝 구간: 최적화 가중치 9가지(overload 5·10·20 × change 0·1·2, trips 1)를 돌려 하나를 고름
     고르는 순서: 새로 생긴 인가대수 초과 0건(필수) → 과밀 비율 낮은 쪽 → 총 운행 적은 쪽 → 변경 건수 적은 쪽
  2) 평가 구간: 고른 가중치 하나만으로 네 방식을 채점 (발표·보고서에는 이 표만 사용)
  3) 참고 행: 감차도 하는 설정(change 0)을 평가 구간에 한 번 더 채점
     위 순서로 고른 설정이 아니라, 튜닝 구간에서 과밀 비율은 거의 같은데 총 운행이 훨씬 적은 것을 보고 나중에 추가했습니다.
     평가 구간을 한 번 더 본 결과이므로 보고서에는 그 사실과 함께 적습니다.

출력
  eval/results/backtest.json                       비교표와 가중치 선택 과정 (실데이터일 때만)
  data/processed/experiment/backtest/cells.parquet 노선·날짜·시간별 운행횟수와 실제 혼잡도 (방식별)

사용
  python scripts/backtest.py                 # DATA_MODE(.env) 기준, 약 2분
  python scripts/backtest.py --mode sample   # 합성 데이터
먼저 실행: python scripts/calibrate_p90.py (승객 수 상한 비율표)
"""
import argparse
import itertools
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import BUS_OPT_WEIGHTS, BUS_TUNE_END, CONGESTION_HIGH, MODELS_DIR, PROCESSED_DIR  # noqa: E402
from app.domains.bus.advisor import BusAdvisor  # noqa: E402
from app.domains.bus.backtest import (  # noqa: E402
    ALL_HOURS, METHOD_LABELS, actual_peaks, current_plan, decide, fleet_check, score_cells, summarize,
)
from app.domains.bus.calibration import load_ratios  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.forecaster import BusForecaster  # noqa: E402
from app.domains.bus.optimizer import collect_inputs, solve_cpsat  # noqa: E402

RESULT_PATH = ROOT / "eval" / "results" / "backtest.json"
DETAIL_DIR = PROCESSED_DIR / "experiment" / "backtest"
GRID = [{"overload": a, "trips": 1, "change": c} for a, c in itertools.product([5, 10, 20], [0, 1, 2])]
# 참고 행: 바꾸는 비용을 0으로 두어 필요 없는 운행은 줄이는 설정 (정수계획만 가능, 그리디는 늘리기만 함)
# 이 채점 결과를 보고 서비스 기본값(BUS_OPT_WEIGHTS)을 이 설정으로 바꿨습니다.
REFERENCE = {"name": "cpsat_cut", "label": "최적화 (감차 허용, 참고)", "weights": {"overload": 10, "trips": 1, "change": 0}}


def weight_name(w: dict) -> str:
    return f"overload {w['overload']:g} · trips {w['trips']:g} · change {w['change']:g}"


def score(day: dict, plan: dict) -> tuple[list[dict], list[dict]]:
    return score_cells(day["inputs"], plan, day["peaks"]), fleet_check(day["inputs"], plan)


def run_plans(days: list[dict], make_plan) -> tuple[pd.DataFrame, pd.DataFrame]:
    cells, fleets = [], []
    for day in days:
        c, f = score(day, make_plan(day))
        cells += c
        fleets += f
    return pd.DataFrame(cells), pd.DataFrame(fleets)


def pick_weights(tuning: list[dict]) -> dict:
    """합격 기준 순서대로 고름. 모두 같으면 기본 가중치에 가까운 쪽"""
    def key(row):
        m, w = row["metrics"], row["weights"]
        return (m["fleet_over_new_days"] > 0, m["overcrowded_pct"], m["total_trips"], m["changed_cells"],
                w != BUS_OPT_WEIGHTS)
    return min(tuning, key=key)["weights"]


def print_table(title: str, rows: dict[str, dict]) -> None:
    print(f"\n{title}")
    print(f"{'방식':<34}{'과밀 비율':>9}{'총 운행':>10}{'현행 대비':>9}{'변경':>7}{'인가 초과':>9}{'새 초과':>8}")
    for name, m in rows.items():
        print(f"{name:<34}{m['overcrowded_pct']:>8.2f}%{m['total_trips']:>11,.0f}{m['trips_vs_current_pct']:>+8.1f}%"
              f"{m['changed_cells']:>8,}{m['fleet_over_days']:>10}{m['fleet_over_new_days']:>10}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["real", "sample"], default=None)
    parser.add_argument("--tune-end", default=None, help="튜닝 구간 마지막 날 (YYYY-MM-DD)")
    args = parser.parse_args()

    dataset = BusDataset(args.mode)
    forecaster = BusForecaster(dataset)
    if not forecaster.load():
        raise SystemExit(f"학습된 모델이 없습니다. 먼저 실행: python scripts/train_bus.py --mode {dataset.mode}")
    table = load_ratios(MODELS_DIR / "bus" / dataset.mode / "p90.json")
    if table is None:
        raise SystemExit(f"승객 수 상한 비율표가 없습니다. 먼저 실행: python scripts/calibrate_p90.py --mode {dataset.mode}")
    advisor = BusAdvisor(None, forecaster, dataset)      # 규정 근거는 쓰지 않음

    store = forecaster.store
    dates = sorted(store.loc[store["split"] == "valid", "date"].unique())
    if args.tune_end:
        tune_end = pd.Timestamp(args.tune_end)
    elif dataset.mode == "real":
        tune_end = pd.Timestamp(BUS_TUNE_END)
    else:
        tune_end = pd.Timestamp(table["fit_window"]["end"])     # 합성 데이터: 상한 비율표를 만든 구간과 같게
    if pd.Timestamp(table["fit_window"]["end"]) > tune_end:
        raise SystemExit("상한 비율표가 평가 구간 날짜까지 써서 만들어졌습니다. calibrate_p90.py 를 같은 튜닝 구간으로 다시 실행하세요.")

    # 1) 날짜마다 결정 입력(예측값)과 채점 입력(실제값)을 따로 준비
    started = time.perf_counter()
    days = []
    for date in dates:
        inputs = collect_inputs(advisor, table, dataset.routes, date, ALL_HOURS)
        peaks = actual_peaks(advisor, inputs)
        if peaks:
            days.append({"date": pd.Timestamp(date), "inputs": inputs, "peaks": peaks})
        advisor.clear_cache()
    tune = [d for d in days if d["date"] <= tune_end]
    evaluation = [d for d in days if d["date"] > tune_end]
    if not tune or not evaluation:
        raise SystemExit(f"튜닝 구간 또는 평가 구간이 비었습니다 (튜닝 끝 {tune_end:%Y-%m-%d}).")
    assert max(d["date"] for d in tune) < min(d["date"] for d in evaluation)
    span = lambda ds: {"start": f"{ds[0]['date']:%Y-%m-%d}", "end": f"{ds[-1]['date']:%Y-%m-%d}", "days": len(ds)}  # noqa: E731
    print(f"[{dataset.mode}] 튜닝 {span(tune)} | 평가 {span(evaluation)} | 준비 {time.perf_counter() - started:.0f}초")

    # 2) 튜닝 구간에서 가중치 고르기
    tuning = []
    for weights in GRID:
        cells, fleets = run_plans(tune, lambda day, w=weights: solve_cpsat(day["inputs"], w)[0])
        tuning.append({"weights": weights, "metrics": summarize(cells, fleets)})
    chosen = pick_weights(tuning)
    print_table("튜닝 구간 · 최적화 가중치 9가지 (발표에는 쓰지 않음)",
                {weight_name(t["weights"]) + (" ←" if t["weights"] == chosen else ""): t["metrics"] for t in tuning})
    cells, fleets = run_plans(tune, lambda day: current_plan(day["inputs"]))
    print_table("튜닝 구간 · 현행 (참고)", {"현행": summarize(cells, fleets)})

    # 3) 평가 구간: 고른 가중치 하나만으로 네 방식 채점
    labels = {**METHOD_LABELS, REFERENCE["name"]: REFERENCE["label"]}
    per_method: dict[str, tuple[list, list]] = {name: ([], []) for name in labels}
    for day in evaluation:
        plans = decide(advisor, day["inputs"], chosen)
        plans[REFERENCE["name"]] = solve_cpsat(day["inputs"], REFERENCE["weights"])[0]
        for name, plan in plans.items():
            c, f = score(day, plan)
            per_method[name][0].extend({**row, "method": name} for row in c)
            per_method[name][1].extend(f)
        advisor.clear_cache()
    results = {name: summarize(pd.DataFrame(c), pd.DataFrame(f)) for name, (c, f) in per_method.items()}
    main_rows = {METHOD_LABELS[name]: results[name] for name in METHOD_LABELS}
    print_table(f"평가 구간 · 가중치 {weight_name(chosen)}", main_rows)
    print_table(f"평가 구간 · 참고 행 (나중에 추가, 가중치 {weight_name(REFERENCE['weights'])})",
                {REFERENCE["label"]: results[REFERENCE["name"]]})

    # 4) 저장
    detail = pd.concat([pd.DataFrame(c) for c, _ in per_method.values()], ignore_index=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    detail_path = DETAIL_DIR / f"cells_{dataset.mode}.parquet"
    detail.to_parquet(detail_path, index=False)
    print(f"\n노선·시간별 상세 저장: {detail_path.relative_to(ROOT)}")

    if dataset.mode == "real":
        out = {
            "tune_window": span(tune), "eval_window": span(evaluation),
            "crowded_threshold": CONGESTION_HIGH,
            "p90_fit_window": table["fit_window"],
            "weights_grid": tuning, "weights_chosen": chosen,
            "pick_order": ["새로 생긴 인가대수 초과 0건", "과밀 비율 낮은 쪽", "총 운행 적은 쪽", "변경 건수 적은 쪽"],
            "eval": main_rows,
            "eval_reference": {
                "label": REFERENCE["label"], "weights": REFERENCE["weights"], "metrics": results[REFERENCE["name"]],
                "why": "미리 정한 순서로 고른 설정이 아님. 튜닝 구간에서 과밀 비율은 거의 같고 총 운행이 훨씬 적어서 "
                       "평가 구간에 한 번 더 채점한 참고 값 (2026-11-12 추가)",
            },
            "notes": [
                "운행횟수는 예측값(승객 수 상한 포함)만으로 정하고, 혼잡도 채점에만 실제 승객을 사용",
                "혼잡도는 실제 승차 × 구간 환산 비율 ÷ (운행횟수 × 정원). 운행횟수를 바꿔도 승객 수는 그대로라고 가정",
                "인가대수 초과는 '최근 왕복 운행시간 동안 출발한 버스 수 > 인가대수'인 순간이 있는 노선·일",
                "새 초과는 현행에는 없던 초과. 현행이 이미 넘는 순간은 현행 값까지 허용",
                "튜닝 구간 점수(weights_grid)는 가중치를 고르는 데만 쓰고 발표에는 쓰지 않음",
            ],
        }
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"비교표 저장: {RESULT_PATH.relative_to(ROOT)}")
    print(f"전체 {time.perf_counter() - started:.0f}초")


if __name__ == "__main__":
    main()
