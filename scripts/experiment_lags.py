"""
설정 비교 실험: 최근 3~6주 lag / 작년 같은 주 lag / 학습 기간 1년·2년 (README 5장 표)

같은 검증 행에서 A~E 설정과 세 가지 베이스라인(전주·3주 전·작년 같은 요일)의 MAE를 비교하고,
설정별 예측값(노선·날짜·시간 합계)을 data/processed/experiment/preds_routehour.parquet 로 저장합니다.
사전 조건: data/interim/*_history.parquet (README 3-3의 과거 1년치 다운로드)

사용: python scripts/experiment_lags.py   (약 15분)
"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np, pandas as pd
from xgboost import XGBRegressor
from app.domains.bus.data import BusDataset
from app.domains.bus.features import add_calendar, add_weather, encode_categories, kr_holidays

# ---- 데이터: 과거 1년(history) + 현재 1년 합치기 ----
exp_dir = ROOT / "data/processed/experiment"; exp_dir.mkdir(parents=True, exist_ok=True)
for name in ["boarding", "dispatch"]:
    cur = pd.read_parquet(ROOT / f"data/interim/{name}.parquet")
    old = pd.read_parquet(ROOT / f"data/interim/{name}_history.parquet")
    pd.concat([old, cur], ignore_index=True).drop_duplicates(["기준_날짜", "노선_ID", "정류장_ID", "정류장_순서"], keep="last") \
        .to_parquet(exp_dir / f"{name}.parquet", index=False)
(exp_dir / "weather.parquet").write_bytes((ROOT / "data/interim/weather.parquet").read_bytes())

ds = BusDataset("real"); ds.dir = exp_dir
long = ds.load_long()
print(f"데이터 {long.date.min():%Y-%m-%d} ~ {long.date.max():%Y-%m-%d}, {len(long):,}행 | 이상일 {len(ds.anomalies)}건", flush=True)
base = add_weather(add_calendar(long), ds.weather)
keys = ["route", "stop_id", "stop_seq", "hour"]
hist = long[keys + ["date", "boarding"]].dropna(subset=["boarding"])
hol = kr_holidays(range(2023, 2028))

def lag(df, k, name=None):
    sh = hist.assign(date=hist["date"] + pd.Timedelta(days=k)).rename(columns={"boarding": name or f"lag{k}"})
    return df.merge(sh, on=keys + ["date"], how="left")

f = base
for k in [7, 21, 28, 35, 42, 357, 364, 371]:
    f = lag(f, k)
f["recent_mean"] = f[["lag21", "lag28", "lag35", "lag42"]].mean(axis=1)
f["year_mean"] = f[["lag357", "lag364", "lag371"]].mean(axis=1)
f["lag364_holiday"] = (f["date"] - pd.Timedelta(days=364)).dt.date.isin(hol).astype("int8")
f = encode_categories(f, sorted(f.route.unique()), sorted(f.stop_id.unique()))

common = ["route_code", "stop_code", "stop_seq", "hour", "dow", "is_weekend", "is_holiday", "is_holiday_eve",
          "is_school_vacation", "temp", "precip", "humidity", "is_rain", "trips"]
RECENT = ["lag21", "lag28", "lag35", "lag42", "recent_mean"]
YEAR = ["lag357", "lag364", "lag371", "year_mean", "lag364_holiday"]
configs = {
    "A 최근(3~6주 전)":            (common + RECENT, "2025-11-10"),
    "B 최근 + 작년 같은 주":        (common + RECENT + YEAR, "2025-11-10"),
    "C 작년 같은 주만":             (common + YEAR, "2025-11-10"),
    "D 최근, 학습 2년":             (common + RECENT, "2024-10-07"),
    "E 최근+작년, 학습 2년":        (common + RECENT + YEAR, "2024-10-07"),
}
lab = f[f.boarding.notna()]
va = lab[lab.date >= "2026-09-01"]
es = lab[(lab.date >= "2026-08-18") & (lab.date < "2026-09-01")]
# 공정 비교: 세 베이스라인이 모두 있는 검증 행만
comp_mask = va[["lag7", "lag21", "lag364"]].notna().all(axis=1)
print(f"검증 행 {len(va):,} → 비교 행 {comp_mask.sum():,}", flush=True)

def maes(df, col):
    a = df.groupby(["date", "route", "hour"])[["boarding", col]].sum()
    return float(np.mean(np.abs(df.boarding - df[col]))), float(np.mean(np.abs(a.boarding - a[col])))

vc = va[comp_mask].copy()
for b, name in [("lag7", "베이스라인: 전주 같은 요일"), ("lag21", "베이스라인: 3주 전 같은 요일"), ("lag364", "베이스라인: 작년 같은 요일")]:
    s, r = maes(vc, b); print(f"{name:28s} 정류장·시간 {s:6.3f} | 노선·시간 {r:7.1f}", flush=True)

for name, (feats, start) in configs.items():
    t = time.time()
    tr = lab[(lab.date >= start) & (lab.date < "2026-08-18")]
    m = XGBRegressor(n_estimators=1500, learning_rate=0.08, max_depth=8, min_child_weight=5, subsample=0.8,
                     colsample_bytree=0.8, tree_method="hist", enable_categorical=True, max_cat_to_onehot=1,
                     early_stopping_rounds=50, eval_metric="mae", n_jobs=-1)
    m.fit(tr[feats], tr.boarding, eval_set=[(es[feats], es.boarding)], verbose=False)
    vc["p"] = np.clip(m.predict(vc[feats]), 0, None)
    vc["pred_" + name[0]] = vc["p"]
    s, r = maes(vc, "p")
    chuseok = vc[vc.date.between("2026-09-23", "2026-09-27")]
    cs, cr = maes(chuseok, "p")
    top = sorted(zip(feats, m.feature_importances_), key=lambda x: -x[1])[:3]
    print(f"{name:28s} 정류장·시간 {s:6.3f} | 노선·시간 {r:7.1f} | 추석주간 노선·시간 {cr:6.1f} | 학습 {len(tr):,}행 {time.time()-t:.0f}s | 중요 {[(a, round(float(b), 2)) for a, b in top]}", flush=True)
cs, cr = maes(chuseok, "lag364"); cs7, cr7 = maes(chuseok, "lag7")
print(f"(참고) 추석주간 베이스라인 노선·시간: 전주 {cr7:.1f} / 작년 같은 요일 {cr:.1f}")

# 설정별 예측값 저장 (노선·날짜·시간 합계) - 실제값과 직접 비교용
cols = ["boarding", "lag7", "lag21", "lag364"] + [c for c in vc.columns if c.startswith("pred_")]
out = vc.groupby(["date", "route", "hour"], observed=True)[cols].sum().reset_index()
out.to_parquet(exp_dir / "preds_routehour.parquet", index=False)
print("saved", len(out))
