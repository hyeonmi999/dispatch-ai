"""
고정 운행시간 ↔ 시간대별 운행시간 비교  (이슈 #42 방법 1, docs/plans/park-next.md Task P-7)

같은 날짜·같은 예측·같은 가중치(BUS_OPT_WEIGHTS)에서 운행시간만 바꿔 봅니다.
  fixed  노선 기본정보의 고정 운행시간 (바꾸기 전)
  timed  출발 시각별 한 바퀴 운행시간 표 (3주 전까지의 실측, 바꾼 뒤)
시간대별 값이 실제에 더 가깝다고 보고, 두 방식의 계획을 모두 timed 기준으로 다시 잽니다.

  1) 현행 운행: 노선별 하루 중 도로 위 버스가 가장 많은 순간 (fixed로 잰 값 / timed로 잰 값), 인가대수를 넘는 날
  2) 최적화: fixed로 만든 계획 / timed로 만든 계획의 총 운행·변경·과밀(실제 승객), 그리고 timed로 잰 인가대수 초과
     (fixed 계획이 timed 기준으로 새 초과를 만들면 = 정체를 모르고 버스를 너무 많이 쓰는 계획)

출력: eval/results/runtime_compare_<name>.json
사용: python scripts/compare_runtime.py [--name 1] [--start 2026-09-29] [--end 2026-11-01]
먼저 실행: bash scripts/build_runtime_table.sh, python scripts/calibrate_p90.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import BUS_OPT_WEIGHTS, BUS_RUNTIME_LAG_DAYS, BUS_RUNTIME_WEEKS, BUS_TUNE_END, EVAL_RESULTS_DIR, MODELS_DIR  # noqa: E402
from app.domains.bus.advisor import BusAdvisor  # noqa: E402
from app.domains.bus.backtest import ALL_HOURS, actual_peaks, current_plan, fleet_check, score_cells, summarize  # noqa: E402
from app.domains.bus.calibration import load_ratios  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.fleet import peak_on_road  # noqa: E402
from app.domains.bus.forecaster import BusForecaster  # noqa: E402
from app.domains.bus.optimizer import collect_inputs, solve_cpsat  # noqa: E402

PEAK_HOURS = [7, 8, 17, 18]     # 출발 시각별 필요 버스 수를 따로 보여 줄 출퇴근 시간대


def with_fixed_run(inputs: dict, dataset: BusDataset) -> dict:
    """같은 입력에서 운행시간만 노선 기본정보의 고정값으로 바꾼 사본"""
    routes = {}
    for route, r in inputs["routes"].items():
        fixed = dataset.route_info_for(route, inputs["daytype"]).get("run_minutes")
        routes[route] = {**r, "run_minutes": int(round(float(fixed or 0)))}
    return {**inputs, "routes": routes}


def needed_by_hour(inputs: dict, plan: dict, hours: list[int]) -> dict:
    """{(노선, 시간): 그 시간 출발이 영향을 주는 동안 도로 위 버스 최대 대수}"""
    out = {}
    for route, r in inputs["routes"].items():
        trips = [float(t) for t in r["current"]]
        for hour in r["cells"]:
            trips[hour] = plan[route, hour]
        for hour in hours:
            if hour in r["cells"]:
                out[route, hour] = peak_on_road(trips, r["run_minutes"], hour)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="1")
    parser.add_argument("--start", default=None, help="첫 날 (기본: 평가 구간 첫날 = BUS_TUNE_END 다음 날)")
    parser.add_argument("--end", default=None, help="마지막 날 (기본: 검증 구간 끝)")
    args = parser.parse_args()

    dataset = BusDataset("real")
    if dataset.runtime_table is None:
        raise SystemExit("운행시간 표가 없습니다. 먼저 실행: bash scripts/build_runtime_table.sh")
    forecaster = BusForecaster(dataset)
    if not forecaster.load():
        raise SystemExit("학습된 모델이 없습니다. 먼저 실행: python scripts/train_bus.py --mode real")
    table = load_ratios(MODELS_DIR / "bus" / "real" / "p90.json")
    if table is None:
        raise SystemExit("승객 수 상한 비율표가 없습니다. 먼저 실행: python scripts/calibrate_p90.py --mode real")
    advisor = BusAdvisor(None, forecaster, dataset)

    store = forecaster.store
    dates = sorted(store.loc[store["split"] == "valid", "date"].unique())
    start = pd.Timestamp(args.start) if args.start else pd.Timestamp(BUS_TUNE_END) + pd.Timedelta(days=1)
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp(dates[-1])
    dates = [pd.Timestamp(d) for d in dates if start <= pd.Timestamp(d) <= end]

    started = time.perf_counter()
    fleets = {k: [] for k in ("current_fixed", "current_timed", "fixed_plan_timed", "timed_plan_timed", "fixed_plan_fixed")}
    cells = {k: [] for k in ("current", "fixed_plan", "timed_plan")}
    needs, asofs, used_profile = [], set(), 0
    for date in dates:
        timed = collect_inputs(advisor, table, dataset.routes, date, ALL_HOURS)
        peaks = actual_peaks(advisor, timed)
        if not peaks:
            advisor.clear_cache()
            continue
        fixed = with_fixed_run(timed, dataset)
        used_profile += sum(1 for r in timed["routes"].values() if not isinstance(r["run_minutes"], int))
        for route in timed["routes"]:
            got = dataset.run_profile(route, timed["daytype"], date, dataset.route_info_for(route, timed["daytype"]).get("run_minutes"))
            if got:
                asofs.add(f"{got[1]:%Y-%m-%d}")

        now = current_plan(timed)
        plan_f = solve_cpsat(fixed, BUS_OPT_WEIGHTS)[0]
        plan_t = solve_cpsat(timed, BUS_OPT_WEIGHTS)[0]
        fleets["current_fixed"] += fleet_check(fixed, now)
        fleets["current_timed"] += fleet_check(timed, now)
        fleets["fixed_plan_fixed"] += fleet_check(fixed, plan_f)
        fleets["fixed_plan_timed"] += fleet_check(timed, plan_f)
        fleets["timed_plan_timed"] += fleet_check(timed, plan_t)
        cells["current"] += score_cells(timed, now, peaks)
        cells["fixed_plan"] += score_cells(timed, plan_f, peaks)
        cells["timed_plan"] += score_cells(timed, plan_t, peaks)
        for (route, hour), v in needed_by_hour(fixed, now, PEAK_HOURS).items():
            needs.append({"date": date, "route": route, "hour": hour, "fixed": v,
                          "timed": needed_by_hour(timed, now, [hour]).get((route, hour)),
                          "fleet": timed["routes"][route]["fleet"],
                          "run_fixed": fixed["routes"][route]["run_minutes"],
                          "run_timed": (timed["routes"][route]["run_minutes"][hour]
                                        if not isinstance(timed["routes"][route]["run_minutes"], int)
                                        else timed["routes"][route]["run_minutes"])})
        advisor.clear_cache()

    f = {k: pd.DataFrame(v) for k, v in fleets.items()}
    need = pd.DataFrame(needs)

    by_route = {}
    for route in sorted(f["current_timed"]["route"].unique()):
        pick = lambda k: f[k][f[k]["route"] == route]  # noqa: E731
        n = need[need["route"] == route]
        by_route[route] = {
            "fleet": int(pick("current_timed")["fleet"].iloc[0]),
            "days": int(len(pick("current_timed"))),
            "current_max_on_road_fixed_mean": round(float(pick("current_fixed")["max_on_road"].mean()), 1),
            "current_max_on_road_timed_mean": round(float(pick("current_timed")["max_on_road"].mean()), 1),
            "current_over_days_fixed": int(pick("current_fixed")["over"].sum()),
            "current_over_days_timed": int(pick("current_timed")["over"].sum()),
            "fixed_plan_new_over_days_timed": int(pick("fixed_plan_timed")["over_new"].sum()),
            "timed_plan_new_over_days_timed": int(pick("timed_plan_timed")["over_new"].sum()),
            "peak_hours": {
                str(h): {"run_fixed": int(g["run_fixed"].iloc[0]), "run_timed_median": float(g["run_timed"].median()),
                         "buses_needed_fixed_mean": round(float(g["fixed"].mean()), 1),
                         "buses_needed_timed_mean": round(float(g["timed"].mean()), 1)}
                for h, g in n.groupby("hour")
            },
        }

    def fleet_total(key: str) -> dict:
        x = f[key]
        return {"route_days": int(len(x)), "over_days": int(x["over"].sum()), "new_over_days": int(x["over_new"].sum())}

    out = {
        "window": {"start": f"{min(d for d in dates):%Y-%m-%d}", "end": f"{max(d for d in dates):%Y-%m-%d}",
                   "days": int(f["current_timed"]["date"].nunique())},
        "runtime_rule": f"날짜 D에는 기준일 ≤ D − {BUS_RUNTIME_LAG_DAYS}일인 표, 최근 {BUS_RUNTIME_WEEKS}주 같은 요일유형 중앙값",
        "runtime_asof_used": sorted(asofs),
        "route_days_with_profile": used_profile,
        "weights": BUS_OPT_WEIGHTS,
        "by_route": by_route,
        "fleet": {
            "current_measured_fixed": fleet_total("current_fixed"),
            "current_measured_timed": fleet_total("current_timed"),
            "fixed_plan_measured_fixed": fleet_total("fixed_plan_fixed"),
            "fixed_plan_measured_timed": fleet_total("fixed_plan_timed"),
            "timed_plan_measured_timed": fleet_total("timed_plan_timed"),
        },
        "plans": {
            "current": summarize(pd.DataFrame(cells["current"]), f["current_timed"]),
            "fixed_plan": summarize(pd.DataFrame(cells["fixed_plan"]), f["fixed_plan_timed"]),
            "timed_plan": summarize(pd.DataFrame(cells["timed_plan"]), f["timed_plan_timed"]),
        },
        "notes": [
            "plans의 인가대수 초과(fleet_over_*)는 세 계획 모두 시간대별 운행시간으로 잰 값",
            "새 초과(new_over) = 현행에는 없던 초과. 현행이 이미 넘는 순간은 현행 값까지 허용 (backtest.py와 같은 기준)",
            "과밀은 실제 승객 기준 최대 혼잡도 0.8 이상인 노선·시간 비율 (backtest.py와 같은 채점)",
            "peak_hours의 필요 버스 수는 현행 운행횟수를 그대로 둘 때 그 시각 출발이 영향을 주는 동안의 최대 대수",
        ],
    }
    path = EVAL_RESULTS_DIR / f"runtime_compare_{args.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{out['window']} · 표 기준일 {out['runtime_asof_used']} · {time.perf_counter() - started:.0f}초")
    print(f"\n{'노선':<5}{'인가':>5}{'현행 최대(고정→시간대별)':>22}{'현행 초과일':>12}{'고정 계획 새 초과':>14}{'새 계획 새 초과':>12}")
    for route, m in by_route.items():
        print(f"{route:<5}{m['fleet']:>5}{m['current_max_on_road_fixed_mean']:>12} → {m['current_max_on_road_timed_mean']:<8}"
              f"{m['current_over_days_fixed']:>5} → {m['current_over_days_timed']:<5}"
              f"{m['fixed_plan_new_over_days_timed']:>10}{m['timed_plan_new_over_days_timed']:>14}")
    print("\n계획          과밀 비율   총 운행   현행 대비   변경   인가 초과(시간대별로 잼)  새 초과")
    for name, m in out["plans"].items():
        print(f"{name:<12}{m['overcrowded_pct']:>8.2f}%{m['total_trips']:>10,.0f}{m['trips_vs_current_pct']:>+9.1f}%"
              f"{m['changed_cells']:>7,}{m['fleet_over_days']:>12}{m['fleet_over_new_days']:>14}")
    print(f"\n저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
