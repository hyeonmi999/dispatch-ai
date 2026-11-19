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
# - valid      : 최종 평가. 모델 vs 베이스라인(작년 같은 요일·시간) MAE 비교
#                (참고로 '전주 같은 요일' MAE도 함께 기록 - 공개 시차 때문에 실제로는 못 쓰는 값)
#
# 학습 산출물
#   models/bus/<mode>/model.json      XGBoost 모델
#   models/bus/<mode>/meta.json       피처·카테고리·분할일·지표
#   models/bus/<mode>/metrics.json    평가 지표
#   models/bus/<mode>/load_profile.parquet  승차 → 구간 재차인원 환산 비율
#   data/processed/bus_<mode>_store.parquet 전체 기간 실제값·예측값·베이스라인 + 향후 21일 예측 (API 조회용)
#
# 배치 추론 구조
#   API 서버는 XGBoost를 불러오지 않고 store만 읽습니다.
#   (macOS에서 xgboost와 torch가 서로 다른 OpenMP 런타임을 써서 한 프로세스에 함께 로드하면
#    segfault가 나므로, 예측은 학습 스크립트에서 미리 계산 → 서버는 조회만)
#   향후 21일 예측은 "요일유형이 같은 가장 최근 날의 운행횟수 = 배차 계획"으로 가정해 미리 생성합니다.
#   (피처가 모두 21일 이상 과거 값이라 실측 마지막 날 + 21일까지 예측 가능)

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd

from app.config import (
    BUS_EARLY_STOP_DAYS, BUS_LOAD_WINDOW_HOURS, BUS_VALID_START, BUS_XGB_EARLY_STOP_ROUNDS, BUS_XGB_MAX_TREES,
    MODELS_DIR, PROCESSED_DIR,
)
from app.core.forecaster import Forecaster, evaluate_mae
from app.domains.bus.calibration import default_tune_end
from app.domains.bus.data import BusDataset
from app.domains.bus.features import (
    CATEGORICAL,
    FEATURES,
    HORIZON_DAYS,
    LAG_DAYS,
    REFERENCE_LAG_DAYS,
    add_lags,
    build_features,
    daytype_of,
    encode_categories,
    kr_holidays,
)

KEYS = ["route", "stop_id", "stop_seq", "hour"]
STORE_COLS = ["date", *KEYS, "boarding", "trips", "pred", "baseline", "daytype", "split"]
FORECAST_HORIZON_DAYS = HORIZON_DAYS


def trips_source_date(target: pd.Timestamp, daytype: str, calendar: pd.Series) -> pd.Timestamp | None:
    """예측일의 배차 계획으로 쓸 과거 날짜를 고릅니다. calendar: 날짜 → 요일유형 (실측이 있는 날만)

    평일·토요일은 같은 요일 중 요일유형이 같은 가장 최근 날, 일요일·공휴일은 가장 최근 일요일·공휴일.
    (휴일 배차가 평일 예측에 섞이면 승객이 15% 넘게 낮게 예측됨, README 참고)
    요일유형이 맞는 날이 없으면 가장 최근 같은 요일 값을 그대로 사용합니다.
    """
    if daytype == "holiday":
        holidays = calendar[calendar == "holiday"]
        if not holidays.empty:
            return holidays.index.max()
    backs = [target - pd.Timedelta(days=k) for k in range(7, 43, 7)]
    same_dow = [d for d in backs if d in calendar.index]
    return next((d for d in same_dow if calendar[d] == daytype), same_dow[0] if same_dow else None)


def forward_window(by_hour: pd.DataFrame, hours: int = BUS_LOAD_WINDOW_HOURS) -> pd.DataFrame:
    """열이 시간(0~23)인 표 → 각 시각부터 hours시간 동안의 합. 날짜를 넘어가지 않습니다.

    N시에 출발한 버스는 그 뒤 몇 시간 동안 승객을 태우므로, 재차인원은 이 합에 비례한다고 봅니다.
    창 안의 값이 모두 비어 있으면 결과도 비워 둡니다.
    """
    full = by_hour.reindex(columns=range(24))
    total = sum(full.shift(-k, axis=1).fillna(0.0) for k in range(hours))
    filled = sum(full.shift(-k, axis=1).notna().astype(int) for k in range(hours))
    return total.where(filled > 0)


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

        print("[2/5] 피처 생성 (달력·날씨·3~6주 전·작년 같은 주)")
        feats = build_features(long, long, self.ds.weather)
        feats = add_lags(feats, long, REFERENCE_LAG_DAYS)   # 평가 참고용 '전주 같은 요일' (피처 아님)
        route_cats = sorted(feats["route"].unique().tolist())
        stop_cats = sorted(feats["stop_id"].unique().tolist())
        feats = encode_categories(feats, route_cats, stop_cats)

        es_start, valid_start = self.split_dates(feats["date"].max())
        # 최근 lag(최대 42일 전)가 생기는 날부터 학습
        first_usable = feats["date"].min() + pd.Timedelta(days=max(LAG_DAYS))
        labeled = feats[feats["boarding"].notna() & (feats["date"] >= first_usable)]
        train = labeled[labeled["date"] < es_start]
        early = labeled[(labeled["date"] >= es_start) & (labeled["date"] < valid_start)]
        valid = labeled[labeled["date"] >= valid_start]

        # 미래 데이터 누수 검사: 학습에 쓰인 마지막 날짜 < 검증 첫 날짜
        assert train["date"].max() < early["date"].min() <= early["date"].max() < valid["date"].min()
        print(f"[3/5] 시간 순서 분할 | train {train['date'].min():%Y-%m-%d}~{train['date'].max():%Y-%m-%d} ({len(train):,}) "
              f"| early-stop ~{early['date'].max():%Y-%m-%d} ({len(early):,}) "
              f"| valid {valid['date'].min():%Y-%m-%d}~{valid['date'].max():%Y-%m-%d} ({len(valid):,})")

        model = XGBRegressor(
            n_estimators=BUS_XGB_MAX_TREES,
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
            early_stopping_rounds=BUS_XGB_EARLY_STOP_ROUNDS,
            n_jobs=-1,
        )
        model.fit(train[FEATURES], train["boarding"], eval_set=[(early[FEATURES], early["boarding"])], verbose=False)
        hit_cap = model.best_iteration >= BUS_XGB_MAX_TREES - 1
        print(f"[4/5] XGBoost 학습 완료 | best_iteration={model.best_iteration}")
        if hit_cap:
            print(f"⚠️ 트리 개수가 상한({BUS_XGB_MAX_TREES})에 걸려 학습이 덜 끝났습니다. app/config.py 의 BUS_XGB_MAX_TREES 를 올리세요.")

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
            "max_trees": BUS_XGB_MAX_TREES,
            "hit_tree_cap": bool(hit_cap),
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
        (self.model_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.model_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

        self.model, self.store = model, feats[STORE_COLS].copy()
        last = self.store["date"].max()
        self.meta["last_observed_date"] = f"{last:%Y-%m-%d}"
        forecasts = self._future_frames([last + pd.Timedelta(days=k) for k in range(1, FORECAST_HORIZON_DAYS + 1)])
        store = pd.concat([self.store, forecasts], ignore_index=True)
        store.to_parquet(self.store_path, index=False)
        (self.model_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      향후 {FORECAST_HORIZON_DAYS}일 예측 {len(forecasts):,}행 생성 (배치 추론, ~{last + pd.Timedelta(days=FORECAST_HORIZON_DAYS):%Y-%m-%d})")
        profile = self._save_load_profile(long, feats, valid["date"])

        self._print_metrics(metrics)
        print(f"⏱️ 총 {time.time() - t0:.0f}s")

        self.store, self.profile = store, profile
        return metrics

    # ------------------------------------------------
    # 평가: 정류장·시간 단위 + 노선·시간 합계 단위
    # ------------------------------------------------

    @staticmethod
    def _evaluate(valid: pd.DataFrame) -> dict:
        # 베이스라인(작년 같은 요일)과 참고용 '전주 같은 요일'이 모두 있는 행에서 공정 비교
        comp = valid[valid["baseline"].notna() & valid["lag7"].notna()]
        out = {
            "unit": "명 (정류장·시간대별 승차 승객 수)",
            "baseline_name": "작년 같은 요일·시간 (364일 전)",
            "horizon_days": FORECAST_HORIZON_DAYS,
            "stop_hour": evaluate_mae(comp["boarding"], comp["pred"], comp["baseline"]),
            "model_mae_all_valid_rows": round(float(np.mean(np.abs(valid["boarding"] - valid["pred"]))), 4),
        }

        # 배차 판단 단위인 "노선 전체 × 시간대" 합계 기준 MAE
        agg = comp.groupby(["date", "route", "hour"], observed=True)[["boarding", "pred", "baseline", "lag7"]].sum()
        out["route_hour"] = evaluate_mae(agg["boarding"], agg["pred"], agg["baseline"])

        # 참고: 전주 같은 요일 (공개 시차 때문에 실제 운영에서는 쓸 수 없는 값)
        out["reference_last_week"] = {
            "stop_hour_mae": round(float(np.mean(np.abs(comp["boarding"] - comp["lag7"]))), 4),
            "route_hour_mae": round(float(np.mean(np.abs(agg["boarding"] - agg["lag7"]))), 4),
        }

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
        ref = m.get("reference_last_week")
        if ref:
            print(f"  [참고] 전주 같은 요일 MAE: 정류장·시간대 {ref['stop_hour_mae']:.3f}명 | 노선·시간대 {ref['route_hour_mae']:.1f}명")
        for route, x in m["by_route"].items():
            print(f"    노선 {route}: 모델 {x['model_mae']:.1f} vs 베이스라인 {x['baseline_mae']:.1f} ({x['improvement_pct']:+.1f}%)")
        print("=" * 60)

    # ------------------------------------------------
    # 승차 → 구간 재차인원 환산 비율
    # ------------------------------------------------
    # ratio = Σ 구간 재차인원(N시 출발 버스) ÷ Σ 노선 전체 승차(N시부터 BUS_LOAD_WINDOW_HOURS시간)
    # 예측한 노선 전체 승차의 같은 구간 합에 곱하면 N시 출발 버스의 구간별 예상 재차인원(혼잡도 분자)이 됩니다.
    # 원본 재차인원의 "N시"는 기점 출발 시각 기준이라, 같은 시각의 승차와 바로 나누지 않습니다 (app/config.py).
    # 튜닝 구간 마지막 날(until)까지의 자료만 씁니다. 평가 구간의 실제 재차인원이 비율에 섞이면
    # 그 구간의 예상 혼잡도가 정답을 미리 본 값이 되어 배차 방식 성능 비교 점수가 부풀려집니다.

    def _save_load_profile(self, long: pd.DataFrame, calendar: pd.DataFrame, valid_dates) -> pd.DataFrame:
        profile_end = default_tune_end(valid_dates, self.ds.mode)
        profile, window = self._build_load_profile(long, calendar, profile_end)
        profile.to_parquet(self.model_dir / "load_profile.parquet", index=False)
        self.meta["load_profile_window"] = {**window, "window_hours": BUS_LOAD_WINDOW_HOURS}
        (self.model_dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      환산 비율: {window['start']} ~ {window['end']} ({window['days']}일) 자료로 계산")
        return profile

    def rebuild_load_profile(self) -> None:
        """모델은 그대로 두고 환산 비율만 다시 만듭니다 (계산식이나 구간 자료가 바뀌었을 때)."""
        if not self.load():
            raise FileNotFoundError("학습된 모델이 없습니다. 먼저 전체 학습을 실행하세요.")
        calendar = self.store[["date", "daytype"]].drop_duplicates()
        valid_dates = self.store.loc[(self.store["split"] == "valid") & self.store["boarding"].notna(), "date"]
        self.profile = self._save_load_profile(self.ds.load_long(), calendar, valid_dates)

    def _build_load_profile(self, long: pd.DataFrame, feats: pd.DataFrame,
                            until: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
        section = self.ds.load_section_long()
        section = section[section["date"] <= until]
        if section.empty:
            raise ValueError(f"{until:%Y-%m-%d} 이전의 구간 재차인원 자료가 없어 환산 비율을 만들 수 없습니다.")
        window = {"start": f"{section['date'].min():%Y-%m-%d}", "end": f"{section['date'].max():%Y-%m-%d}",
                  "days": int(section["date"].nunique())}
        totals = long[long["date"].isin(section["date"].unique())]
        totals = totals.groupby(["date", "route", "hour"], observed=True)["boarding"].sum(min_count=1).unstack("hour")
        totals = forward_window(totals).rename_axis(columns="hour").stack().rename("route_total").reset_index()
        dt = feats[["date", "daytype"]].drop_duplicates()

        x = section.merge(totals, on=["date", "route", "hour"]).merge(dt, on="date")
        x = x[(x["route_total"] > 0) & x["onboard"].notna()]      # 그 시각에 출발한 버스가 없던 날은 뺌
        keys = ["route", "stop_id", "stop_seq", "to_stop_id", "hour"]
        by_dt = x.groupby(keys + ["daytype"], as_index=False)[["onboard", "route_total"]].sum()
        all_dt = x.groupby(keys, as_index=False)[["onboard", "route_total"]].sum().assign(daytype="all")
        profile = pd.concat([by_dt, all_dt], ignore_index=True)
        profile["ratio"] = (profile["onboard"] / profile["route_total"]).astype("float32")
        return profile[keys + ["daytype", "ratio"]], window

    # ==================================================
    # 2. 로드
    # ==================================================

    def load(self) -> bool:
        """서버용 로드: XGBoost 없이 예측 결과(store)와 메타만 읽습니다."""
        if not (self.store_path.exists() and (self.model_dir / "meta.json").exists()):
            return False
        self.meta = json.loads((self.model_dir / "meta.json").read_text(encoding="utf-8"))
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

    def _future_frames(self, dates: list[pd.Timestamp]) -> pd.DataFrame:
        """(학습 시 배치 추론) 실측 마지막 날 이후 날짜들

        정류장·시간대 구성과 운행횟수(배차 계획)는 노선별로 요일유형이 같은 가장 최근 날의 값을 사용
        (운행 중단·집계 누락일은 승객·운행 값이 비어 있어 후보에서 제외)
        """
        hol = kr_holidays(range(min(dates).year, max(dates).year + 1))
        rows = []
        for route, g in self.store.groupby("route", observed=True):
            usable = g.groupby("date").filter(lambda d: d["boarding"].notna().any() and d["trips"].notna().any())
            calendar = usable.groupby("date")["daytype"].first()
            for date in dates:
                src = trips_source_date(date, daytype_of(date, hol), calendar)
                if src is not None:
                    rows.append(usable.loc[usable["date"] == src, KEYS + ["trips"]].assign(date=date, boarding=np.nan))
        rows = pd.concat(rows, ignore_index=True)

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
