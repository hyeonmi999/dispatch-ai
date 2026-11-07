"""
단기 모델 실험: 3주 전(현재) vs 2주 전 입력 (서비스 코드·모델은 건드리지 않음)

  E 현재     lag 21·28·35·42 + 작년 같은 주
  F 단기     lag 14·21·28·35 + 작년 같은 주   (공개 시차상 오늘 예측에 쓸 수 있는 가장 최근 값)
  S 참고     lag  7·14·21·28 + 작년 같은 주   (데이터가 더 빨리 공개된다면 얼마나 좋아질지 보는 상한선)

세 모델 모두 학습 구간·하이퍼파라미터는 정식 모델과 동일하고, 같은 검증 행에서 비교합니다.
모델 자체의 우연한 차이를 보려고 시드를 바꿔 반복할 수 있습니다 (--seeds).

평가 방식 3가지 (컬럼 접두사)
  pred: 실제 운행횟수 입력 (README 지표와 같은 방식)
  op:   운행횟수를 모른다고 보고 14일 전 값 사용 (현재 서비스처럼 휴일 여부를 따지지 않음)
  fix:  14일 이상 지난 날 중 요일유형이 같은 가장 최근 값 사용 (휴일 배차가 평일에 섞이지 않게)
  모두 실측 끝 + 8~14일, 즉 '오늘'을 예측하는 상황. 날씨는 관측값 사용.

결과: data/processed/experiment/short_lag<tag>/
  preds_stophour.parquet  정류장·시간별 실제값·예측값
  preds_routehour.parquet 노선·시간 합계
  summary.json            구간별 MAE

사용: python scripts/experiment_short_lag.py --models E F --seeds 42 43 --tag _v2   (약 25분)
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from xgboost import XGBRegressor  # noqa: E402

from app.config import BUS_VALID_START  # noqa: E402
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.features import add_calendar, add_lags, add_weather, encode_categories, kr_holidays  # noqa: E402

TRAIN_START = "2024-10-07"   # 정식 모델과 같은 학습 시작일
EARLY_START = "2026-08-18"
YEAR_LAGS = [357, 364, 371]
CONFIGS = {
    "E": [21, 28, 35, 42],
    "F": [14, 21, 28, 35],
    "S": [7, 14, 21, 28],
}
COMMON = ["route_code", "stop_code", "stop_seq", "hour", "dow", "is_weekend", "is_holiday", "is_holiday_eve",
          "is_school_vacation", "temp", "precip", "humidity", "is_rain", "trips",
          "lag357", "lag364", "lag371", "year_mean", "lag364_holiday"]
KEYS = ["route", "stop_id", "stop_seq", "hour"]


def mae(df, col):
    """(정류장·시간 MAE, 노선·시간 합계 MAE)"""
    agg = df.groupby(["date", "route", "hour"], observed=True)[["boarding", col]].sum()
    return round(float((df["boarding"] - df[col]).abs().mean()), 4), round(float((agg["boarding"] - agg[col]).abs().mean()), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-estimators", type=int, default=2000)
    ap.add_argument("--tag", default="")
    ap.add_argument("--models", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--seeds", nargs="+", type=int, default=[42])
    args = ap.parse_args()
    out_dir = ROOT / f"data/processed/experiment/short_lag{args.tag}"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    ds = BusDataset("real")
    long = ds.load_long()
    print(f"데이터 {long['date'].min():%Y-%m-%d} ~ {long['date'].max():%Y-%m-%d}, {len(long):,}행", flush=True)

    all_lags = sorted({k for lags in CONFIGS.values() for k in lags} | set(YEAR_LAGS))
    f = add_lags(add_weather(add_calendar(long), ds.weather), long, all_lags)
    f["year_mean"] = f[[f"lag{k}" for k in YEAR_LAGS]].mean(axis=1)
    hol = kr_holidays(range(2023, 2028))
    f["lag364_holiday"] = (f["date"] - pd.Timedelta(days=364)).dt.date.isin(hol).astype("int8")
    for name, lags in CONFIGS.items():
        f[f"recent_{name}"] = f[[f"lag{k}" for k in lags]].mean(axis=1)
    f = encode_categories(f, sorted(f["route"].unique()), sorted(f["stop_id"].unique()))

    # 운영 방식의 운행횟수: 14일 전 같은 요일 값
    trips = long[KEYS + ["date", "trips"]].dropna(subset=["trips"])
    trips = trips.assign(date=trips["date"] + pd.Timedelta(days=14)).rename(columns={"trips": "trips14"})
    f = f.merge(trips, on=KEYS + ["date"], how="left")

    # 운행횟수 보정: 14일 이상 지난 날 중 요일유형이 같은 가장 최근 값
    cal = f[["date", "daytype"]].drop_duplicates().set_index("date")["daytype"].sort_index()
    sources = {}
    for target, kind in cal.items():
        if kind == "holiday":
            older = cal[(cal.index <= target - pd.Timedelta(days=14)) & (cal == "holiday")]
            sources[target] = older.index.max() if not older.empty else pd.NaT
        else:
            backs = (target - pd.Timedelta(days=k) for k in range(14, 43, 7))
            sources[target] = next((d for d in backs if cal.get(d) == kind), pd.NaT)
    src = pd.Series(sources, name="trips_src").rename_axis("date").reset_index()
    f = f.merge(src, on="date", how="left")
    picked = long[KEYS + ["date", "trips"]].dropna(subset=["trips"]).rename(columns={"date": "trips_src", "trips": "trips_fix"})
    f = f.merge(picked, on=KEYS + ["trips_src"], how="left")
    f["trips_fix"] = f["trips_fix"].fillna(f["trips14"])

    lab = f[f["boarding"].notna() & (f["date"] >= TRAIN_START)]
    train = lab[lab["date"] < EARLY_START]
    early = lab[(lab["date"] >= EARLY_START) & (lab["date"] < BUS_VALID_START)]
    valid = lab[lab["date"] >= BUS_VALID_START]
    assert train["date"].max() < early["date"].min() <= early["date"].max() < valid["date"].min()

    # 공정 비교: 베이스라인과 운영 방식 입력이 모두 있는 같은 행만
    comp = valid[valid[["lag364", "lag14", "lag21", "trips14"]].notna().all(axis=1)].copy()
    print(f"학습 {len(train):,} | early {len(early):,} | 검증 {len(valid):,} → 비교 {len(comp):,}행", flush=True)

    summary = {"rows": len(comp), "models": {}}
    runs = [(name, seed) for seed in args.seeds for name in args.models]
    for name, seed in runs:
        lags = CONFIGS[name]
        tag = f"{name}_{seed}"
        t = time.time()
        feats = COMMON + [f"lag{k}" for k in lags] + [f"recent_{name}"]
        model = XGBRegressor(
            n_estimators=args.n_estimators, learning_rate=0.05, max_depth=8, min_child_weight=5, subsample=0.8,
            colsample_bytree=0.8, reg_lambda=1.0, objective="reg:squarederror", eval_metric="mae",
            tree_method="hist", enable_categorical=True, max_cat_to_onehot=1, early_stopping_rounds=50, n_jobs=-1,
            random_state=seed,
        )
        model.fit(train[feats], train["boarding"], eval_set=[(early[feats], early["boarding"])], verbose=False)
        comp[f"pred_{tag}"] = np.clip(model.predict(comp[feats]), 0, None).astype("float32")
        for kind, column in [("op", "trips14"), ("fix", "trips_fix")]:
            x = comp[feats].copy()
            x["trips"] = comp[column]
            comp[f"{kind}_{tag}"] = np.clip(model.predict(x), 0, None).astype("float32")
        top = sorted(zip(feats, model.feature_importances_), key=lambda x: -x[1])[:4]
        summary["models"][tag] = {
            "lags": lags, "best_iteration": int(model.best_iteration),
            "pred": mae(comp, f"pred_{tag}"), "op": mae(comp, f"op_{tag}"), "fix": mae(comp, f"fix_{tag}"),
            "top_features": [(a, round(float(b), 3)) for a, b in top],
        }
        m = summary["models"][tag]
        print(f"{tag} lag{lags} | 실제 운행 {m['pred']} | 2주 전 운행 {m['op']} | 보정 운행 {m['fix']} "
              f"| best {model.best_iteration} | {time.time() - t:.0f}s", flush=True)
    summary["baseline"] = mae(comp, "lag364")
    print("베이스라인(작년 같은 요일)", summary["baseline"], flush=True)

    # 구간 표시
    comp["week"] = comp["date"].dt.to_period("W-SUN").astype(str)
    comp["lag14_on_holiday"] = (comp["date"] - pd.Timedelta(days=14)).dt.date.isin(hol)
    comp["lag21_on_holiday"] = (comp["date"] - pd.Timedelta(days=21)).dt.date.isin(hol)
    # 변화 직후: 휴일이 끼지 않은 날 중 2주 전 노선·일 합계가 3주 전보다 10% 이상 달라진 경우 (입력만으로 판단)
    day = comp.groupby(["date", "route"], observed=True)[["lag14", "lag21"]].sum()
    day["shift"] = (day["lag14"] / day["lag21"] - 1).abs()
    comp = comp.merge(day[["shift"]].reset_index(), on=["date", "route"], how="left")
    normal = ~comp["is_holiday"].astype(bool) & ~comp["lag14_on_holiday"] & ~comp["lag21_on_holiday"]

    segments = {
        "일반일 (본인·2주 전·3주 전 모두 비휴일)": normal,
        "일반일 중 변화 있음 (2주 전 vs 3주 전 ≥10%)": normal & (comp["shift"] >= 0.10),
        "일반일 중 변화 있음 (≥5%)": normal & (comp["shift"] >= 0.05),
        "예측일이 휴일": comp["is_holiday"].astype(bool),
        "2주 전이 휴일 (예측일은 비휴일)": ~comp["is_holiday"].astype(bool) & comp["lag14_on_holiday"],
    }
    columns = ["lag364"] + [f"{kind}_{name}_{seed}" for kind in ("pred", "op", "fix") for name, seed in runs]
    summary["segments"] = {}
    for label, mask in segments.items():
        g = comp[mask]
        if g.empty:
            continue
        summary["segments"][label] = {"rows": len(g), "days": int(g["date"].nunique()), **{c: mae(g, c) for c in columns}}
        print(label, json.dumps(summary["segments"][label], ensure_ascii=False), flush=True)
    summary["weeks"] = {w: {c: mae(g, c) for c in columns} for w, g in comp.groupby("week")}
    summary["by_route"] = {r: {c: mae(g, c) for c in columns} for r, g in comp.groupby("route", observed=True)}

    # 두 모델을 이어 붙일 때 경계에서 생기는 차이의 크기 (같은 날짜·노선의 예측 합계 차이)
    if {"E", "F"} <= set(args.models):
        e, f_ = f"fix_E_{args.seeds[0]}", f"fix_F_{args.seeds[0]}"
        gap = comp.groupby(["date", "route"], observed=True)[[e, f_]].sum()
        gap = (gap[f_] / gap[e] - 1).abs() * 100
        summary["model_gap_pct"] = {"median": round(float(gap.median()), 2), "p90": round(float(gap.quantile(0.9)), 2), "max": round(float(gap.max()), 2)}
        print("E·F 노선·일 합계 차이(%)", summary["model_gap_pct"], flush=True)

    keep = ["date", "route", "stop_id", "stop_seq", "hour", "boarding", "trips", "trips14", "trips_fix", "trips_src",
            "lag14", "lag21", "lag364", "week", "shift"] + columns[1:]
    comp[keep].to_parquet(out_dir / "preds_stophour.parquet", index=False)
    comp.groupby(["date", "route", "hour"], observed=True)[["boarding", "trips", "trips14", "trips_fix", "lag14", "lag21", "lag364"] + columns[1:]] \
        .sum().reset_index().to_parquet(out_dir / "preds_routehour.parquet", index=False)
    summary["seconds"] = round(time.time() - t0)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"저장: {out_dir} | 총 {summary['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
