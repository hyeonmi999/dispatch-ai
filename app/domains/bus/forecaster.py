# ==================================================
# 버스 승객 수 예측 (XGBoost)
# ==================================================
# 학습/검증 분리 (시간 순서, 무작위 분할 금지)
#
#   |------------- train -------------|-- early stop --|------ valid ------|
#                                      (14일)           BUS_VALID_START~
#
# - train      : 트리 학습
# - early stop : 학습 구간 마지막 14일로 트리 개수만 결정 (검증 구간은 보지 않음)
# - valid      : 최종 평가. 모델 vs 베이스라인(전주 같은 요일·시간) MAE 비교
#
# 학습 산출물
#   models/bus/<mode>/model.json      XGBoost 모델
#   models/bus/<mode>/meta.json       피처·카테고리·분할일·지표
#   models/bus/<mode>/metrics.json    평가 지표
#   models/bus/<mode>/load_profile.parquet  승차 → 구간 재차인원 환산 비율
#   data/processed/bus_<mode>_store.parquet 전체 기간 실제값·예측값·베이스라인 + 향후 7일 예측 (API 조회용)
#
# 배치 추론 구조
#   API 서버는 XGBoost를 불러오지 않고 store만 읽습니다.
#   (macOS에서 xgboost와 torch가 서로 다른 OpenMP 런타임을 써서 한 프로세스에 함께 로드하면
#    segfault가 나므로, 예측은 학습 스크립트에서 미리 계산 → 서버는 조회만)
#   향후 7일 예측은 "1주 전 같은 요일의 운행횟수 = 배차 계획"으로 가정해 미리 생성합니다.

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd

from app.config import BUS_EARLY_STOP_DAYS, BUS_VALID_START, MODELS_DIR, PROCESSED_DIR
from app.core.forecaster import Forecaster, evaluate_mae
from app.domains.bus.data import BusDataset
from app.domains.bus.features import CATEGORICAL, FEATURES, LAG_DAYS, build_features, encode_categories

KEYS = ["route", "stop_id", "stop_seq", "hour"]
STORE_COLS = ["date", *KEYS, "boarding", "trips", "pred", "baseline", "daytype", "split"]
FORECAST_HORIZON_DAYS = 7


class BusForecaster(Forecaster):
    def __init__(self, dataset: BusDataset):
        self.ds = dataset
        self.model_dir = MODELS_DIR / "bus" / dataset.mode
        self.store_path = PROCESSED_DIR / f"bus_{dataset.mode}_store.parquet"
        self.model = None
        self.meta: dict = {}
        self.store: pd.DataFrame | None = None
        self.profile: pd.DataFrame | None = None

    @property
    def model_version(self) -> str:
        return self.meta.get("model_version", "")

    # ==================================================
    # 1. 학습
    # ==================================================

    def split_dates(self, max_date: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
        if self.ds.mode == "real":
            valid_start = pd.Timestamp(BUS_VALID_START)
        else:
            valid_start = max_date - pd.Timedelta(days=20)   # 합성 데이터: 마지막 3주 검증
        es_start = valid_start - pd.Timedelta(days=BUS_EARLY_STOP_DAYS)
        return es_start, valid_start

    def fit(self) -> dict:
        from xgboost import XGBRegressor

        t0 = time.time()
        print(f"[1/5] 데이터 로드 ({self.ds.mode})")
        long = self.ds.load_long()
        print(f"      {len(long):,}행 | {long['date'].min():%Y-%m-%d} ~ {long['date'].max():%Y-%m-%d} | 노선 {sorted(long['route'].unique())}")

        print("[2/5] 피처 생성 (달력·날씨·lag)")
        feats = build_features(long, long, self.ds.weather)
        route_cats = sorted(feats["route"].unique().tolist())
        stop_cats = sorted(feats["stop_id"].unique().tolist())
        feats = encode_categories(feats, route_cats, stop_cats)

        es_start, valid_start = self.split_dates(feats["date"].max())
        labeled = feats[feats["boarding"].notna()]
        train = labeled[labeled["date"] < es_start]
        early = labeled[(labeled["date"] >= es_start) & (labeled["date"] < valid_start)]
        valid = labeled[labeled["date"] >= valid_start]

        # 미래 데이터 누수 검사: 학습에 쓰인 마지막 날짜 < 검증 첫 날짜
        assert train["date"].max() < early["date"].min() <= early["date"].max() < valid["date"].min()
        print(f"[3/5] 시간 순서 분할 | train {train['date'].min():%Y-%m-%d}~{train['date'].max():%Y-%m-%d} ({len(train):,}) "
              f"| early-stop ~{early['date'].max():%Y-%m-%d} ({len(early):,}) "
              f"| valid {valid['date'].min():%Y-%m-%d}~{valid['date'].max():%Y-%m-%d} ({len(valid):,})")

        model = XGBRegressor(
            n_estimators=2000,
            learning_rate=0.05,
            max_depth=8,
            min_child_weight=5,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            objective="reg:squarederror",
            eval_metric="mae",
            tree_method="hist",
            enable_categorical=True,
            max_cat_to_onehot=1,
            early_stopping_rounds=50,
            n_jobs=-1,
        )
        model.fit(train[FEATURES], train["boarding"], eval_set=[(early[FEATURES], early["boarding"])], verbose=False)
        print(f"[4/5] XGBoost 학습 완료 | best_iteration={model.best_iteration}")

        feats["pred"] = np.clip(model.predict(feats[FEATURES]), 0, None).astype("float32")
        feats["split"] = np.where(feats["date"] >= valid_start, "valid",
                                  np.where(feats["date"] >= es_start, "early_stop", "train"))

        # 정답이 없는 행(원천 결측·이상일)은 평가에서 제외
        metrics = self._evaluate(feats[(feats["split"] == "valid") & feats["boarding"].notna()])
        metrics.update({
            "data_mode": self.ds.mode,
            "train_period": [f"{train['date'].min():%Y-%m-%d}", f"{train['date'].max():%Y-%m-%d}"],
            "early_stop_period": [f"{early['date'].min():%Y-%m-%d}", f"{early['date'].max():%Y-%m-%d}"],
            "valid_period": [f"{valid['date'].min():%Y-%m-%d}", f"{valid['date'].max():%Y-%m-%d}"],
            "best_iteration": int(model.best_iteration),
            "feature_importance": dict(sorted(
                ((f, round(float(v), 4)) for f, v in zip(FEATURES, model.feature_importances_)),
                key=lambda x: -x[1])),
        })

        print("[5/5] 저장")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(self.model_dir / "model.json")
        self.meta = {
            "model_version": f"xgb-bus-{self.ds.mode}-{datetime.now():%Y%m%d%H%M}",
            "features": FEATURES,
            "route_categories": route_cats,
            "stop_categories": stop_cats,
            "valid_start": f"{valid_start:%Y-%m-%d}",
            "es_start": f"{es_start:%Y-%m-%d}",
            "metrics": metrics,
            # 원천 이상으로 학습·평가에서 뺀 노선·날짜 (화면에 사유 표시)
            "excluded_route_days": self.ds.anomalies.to_dict("records"),
        }
        (self.model_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2))
        (self.model_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))

        self.model, self.store = model, feats[STORE_COLS].copy()
        self.meta["last_observed_date"] = f"{self.store['date'].max():%Y-%m-%d}"
        forecasts = [self._future_frame(self.store["date"].max() + pd.Timedelta(days=k))
                     for k in range(1, FORECAST_HORIZON_DAYS + 1)]
        store = pd.concat([self.store, *forecasts], ignore_index=True)
        store.to_parquet(self.store_path, index=False)
        (self.model_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2))
        print(f"      향후 {FORECAST_HORIZON_DAYS}일 예측 {sum(len(f) for f in forecasts):,}행 생성 (배치 추론)")
        profile = self._build_load_profile(long, feats)
        profile.to_parquet(self.model_dir / "load_profile.parquet", index=False)

        self._print_metrics(metrics)
        print(f"⏱️ 총 {time.time() - t0:.0f}s")

        self.store, self.profile = store, profile
        return metrics

    # ------------------------------------------------
    # 평가: 정류장·시간 단위 + 노선·시간 합계 단위
    # ------------------------------------------------

    @staticmethod
    def _evaluate(valid: pd.DataFrame) -> dict:
        comp = valid[valid["baseline"].notna()]   # 베이스라인이 있는 행에서 공정 비교
        out = {
            "unit": "명 (정류장·시간대별 승차 승객 수)",
            "stop_hour": evaluate_mae(comp["boarding"], comp["pred"], comp["baseline"]),
            "model_mae_all_valid_rows": round(float(np.mean(np.abs(valid["boarding"] - valid["pred"]))), 4),
        }

        # 배차 판단 단위인 "노선 전체 × 시간대" 합계 기준 MAE
        agg = comp.groupby(["date", "route", "hour"], observed=True)[["boarding", "pred", "baseline"]].sum()
        out["route_hour"] = evaluate_mae(agg["boarding"], agg["pred"], agg["baseline"])

        out["by_route"] = {}
        for route, g in agg.groupby(level="route"):
            m = evaluate_mae(g["boarding"], g["pred"], g["baseline"])
            m["mean_actual"] = round(float(g["boarding"].mean()), 2)
            out["by_route"][str(route)] = m

        out["by_daytype"] = {
            dt: evaluate_mae(g["boarding"], g["pred"], g["baseline"])
            for dt, g in comp.groupby("daytype")
        }
        return out

    @staticmethod
    def _print_metrics(m: dict) -> None:
        print("=" * 60)
        print("📊 검증 결과 (시간 순서 분할, 검증 구간 " + " ~ ".join(m["valid_period"]) + ")")
        for name, key in [("정류장·시간대", "stop_hour"), ("노선·시간대 합계", "route_hour")]:
            x = m[key]
            print(f"  [{name}] 모델 MAE {x['model_mae']:.3f}명 | 베이스라인 MAE {x['baseline_mae']:.3f}명 "
                  f"| 개선율 {x['improvement_pct']:.1f}% (n={x['n']:,})")
        for route, x in m["by_route"].items():
            print(f"    노선 {route}: 모델 {x['model_mae']:.1f} vs 베이스라인 {x['baseline_mae']:.1f} ({x['improvement_pct']:+.1f}%)")
        print("=" * 60)

    # ------------------------------------------------
    # 승차 → 구간 재차인원 환산 비율
    # ------------------------------------------------
    # ratio = Σ 구간 재차인원(시간대) ÷ Σ 노선 전체 승차(시간대)
    # 예측한 노선 전체 승차에 곱하면 구간별 예상 재차인원(혼잡도 분자)이 됩니다.

    def _build_load_profile(self, long: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
        section = self.ds.load_section_long()
        totals = long.groupby(["date", "route", "hour"], as_index=False)["boarding"].sum(min_count=1)
        totals = totals.rename(columns={"boarding": "route_total"}).dropna()
        dt = feats[["date", "daytype"]].drop_duplicates()

        x = section.merge(totals, on=["date", "route", "hour"]).merge(dt, on="date")
        x = x[x["route_total"] > 0]
        keys = ["route", "stop_id", "stop_seq", "to_stop_id", "hour"]
        by_dt = x.groupby(keys + ["daytype"], as_index=False)[["onboard", "route_total"]].sum()
        all_dt = x.groupby(keys, as_index=False)[["onboard", "route_total"]].sum().assign(daytype="all")
        profile = pd.concat([by_dt, all_dt], ignore_index=True)
        profile["ratio"] = (profile["onboard"] / profile["route_total"]).astype("float32")
        return profile[keys + ["daytype", "ratio"]]

    # ==================================================
    # 2. 로드
    # ==================================================

    def load(self) -> bool:
        """서버용 로드: XGBoost 없이 예측 결과(store)와 메타만 읽습니다."""
        if not (self.store_path.exists() and (self.model_dir / "meta.json").exists()):
            return False
        self.meta = json.loads((self.model_dir / "meta.json").read_text())
        self.store = pd.read_parquet(self.store_path)
        self.profile = pd.read_parquet(self.model_dir / "load_profile.parquet")
        print(f"✅ 버스 예측 모델 로드: {self.model_version} | store {len(self.store):,}행")
        return True

    def ready(self) -> bool:
        return self.store is not None

    def metrics(self) -> dict:
        return self.meta.get("metrics", {})

    # ==================================================
    # 3. 예측 조회
    # ==================================================

    def date_range(self) -> dict:
        d = self.store["date"]
        # 원천 파일에 없는 날짜 (화면 달력에서 선택 불가 처리)
        have = set(d.dt.normalize().unique())
        observed = pd.date_range(d.min(), pd.Timestamp(self.meta["last_observed_date"]))
        return {
            "missing_dates": [f"{x:%Y-%m-%d}" for x in observed if x not in have],
            "min_date": f"{d.min() + pd.Timedelta(days=max(LAG_DAYS)):%Y-%m-%d}",
            "max_date": self.meta["last_observed_date"],
            "max_forecast_date": f"{d.max():%Y-%m-%d}",
            "valid_start": self.meta.get("valid_start"),
            "excluded": self.excluded_by_date(),
        }

    def excluded_by_date(self) -> dict:
        """{'2026-09-29': {'143': '승차 집계 누락', ...}, ...}"""
        out: dict = {}
        for r in self.meta.get("excluded_route_days", []):
            day = f"{r['date'][:4]}-{r['date'][4:6]}-{r['date'][6:]}"
            out.setdefault(day, {})[r["route"]] = r["reason"]
        return out

    def day_frame(self, date: pd.Timestamp, route: str | None = None) -> tuple[pd.DataFrame, bool]:
        """하루치 정류장·시간대 실제값/예측값/베이스라인. (frame, is_future)"""
        date = pd.Timestamp(date).normalize()
        if date > self.store["date"].max():
            raise ValueError(f"예측 가능 범위를 벗어났습니다 (최대 {self.store['date'].max():%Y-%m-%d})")
        frame = self.store[self.store["date"] == date]
        is_future = date > pd.Timestamp(self.meta["last_observed_date"])

        if route is not None:
            frame = frame[frame["route"] == route]
        return frame, is_future

    def _future_frame(self, date: pd.Timestamp) -> pd.DataFrame:
        """(학습 시 배치 추론) 데이터 마지막 날 이후 날짜: 1주 전 같은 요일의 정류장·시간대 구성과 운행횟수를 계획값으로 사용"""
        template = pd.DataFrame()
        for back in (7, 14, 21):
            template = self.store[self.store["date"] == date - pd.Timedelta(days=back)]
            if not template.empty:
                break
        rows = template[KEYS + ["trips"]].assign(date=date, boarding=np.nan)

        feats = build_features(rows, self.store, self.ds.weather)
        feats = encode_categories(feats, self.meta["route_categories"], self.meta["stop_categories"])
        feats["pred"] = np.clip(self.model.predict(feats[FEATURES]), 0, None).astype("float32")
        feats["split"] = "forecast"
        frame = feats[STORE_COLS]
        return frame

    def profile_for(self, route: str, daytype: str) -> pd.DataFrame:
        p = self.profile[self.profile["route"] == route]
        by_dt = p[p["daytype"] == daytype]
        return by_dt if not by_dt.empty else p[p["daytype"] == "all"]


__all__ = ["BusForecaster", "CATEGORICAL"]
