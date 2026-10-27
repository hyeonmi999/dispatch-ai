# ==================================================
# 버스 도메인 데이터 로드
# ==================================================
# 실데이터(data/interim)와 합성 데이터(data/sample)는 같은 스키마입니다.
#
#   boarding.parquet  OA-21219 정류장별 승차 승객수 (wide: 승차_승객_수_00시 ~ 23시)
#   dispatch.parquet  OA-21220 정류장별 운행횟수   (wide: 버스운행횟수_00시 ~ 23시)
#   section.parquet   OA-21218 구간별 재차인원     (wide: 재차인원합_0시 ~ 23시)
#   weather.parquet   기상청 ASOS 서울(108) 시간자료
#   geo/<노선>.json   노선 경로·정류소 좌표 (scripts/fetch_route_geo.py)
#   route_info        노선 기본정보 (배차간격, 최소/최대 배차, 운행시간)
#
# wide 형식을 (날짜, 노선, 정류장, 시간) long 형식으로 바꿔 모델 입력으로 씁니다.
# 원본에서 빈칸은 "그 시간대 0명"이고, 파일에 날짜 자체가 없으면 결측입니다.

import json
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import BUS_ROUTES, BUS_SAMPLE_ROUTES, INTERIM_DIR, RAW_DIR, SAMPLE_DIR, resolve_data_mode

ROUTE_INFO_XLSX = RAW_DIR / "서울시버스노선기본정보(20260902).xlsx"
DAYTYPE_SHEETS = {"weekday": "평일공동배차", "saturday": "토요일공동배차", "holiday": "공휴일공동배차"}

HOURS = list(range(24))


def wide_to_long(df: pd.DataFrame, columns: list[str], id_cols: dict[str, str], value_name: str) -> pd.DataFrame:
    """시간대 컬럼 24개를 (…, hour, value) long 형식으로 변환합니다. (melt보다 빠른 numpy 방식)"""
    n = len(df)
    values = df[columns].to_numpy(dtype="float32")
    out = {new: np.repeat(df[old].to_numpy(), 24) for old, new in id_cols.items()}
    out["hour"] = np.tile(np.arange(24, dtype="int8"), n)
    out[value_name] = np.nan_to_num(values, nan=0.0).ravel()   # 빈칸 = 0명/0회
    return pd.DataFrame(out)


class BusDataset:
    def __init__(self, mode: str | None = None):
        self.mode = mode or resolve_data_mode()
        self.dir: Path = INTERIM_DIR if self.mode == "real" else SAMPLE_DIR
        self.routes = BUS_ROUTES if self.mode == "real" else BUS_SAMPLE_ROUTES

    def exists(self) -> bool:
        return all((self.dir / f).exists() for f in ["boarding.parquet", "dispatch.parquet", "weather.parquet"])

    # ------------------------------------------------
    # 1. 승차 / 운행횟수 → long
    # ------------------------------------------------

    def load_long(self) -> pd.DataFrame:
        b = pd.read_parquet(self.dir / "boarding.parquet")
        d = pd.read_parquet(self.dir / "dispatch.parquet")
        ids = {"기준_날짜": "date", "노선명": "route", "정류장_ID": "stop_id", "정류장_순서": "stop_seq"}

        bl = wide_to_long(b, [f"승차_승객_수_{h:02d}시" for h in HOURS], ids, "boarding")
        dl = wide_to_long(d, [f"버스운행횟수_{h:02d}시" for h in HOURS], ids, "trips")

        keys = ["date", "route", "stop_id", "stop_seq", "hour"]
        df = bl.merge(dl, on=keys, how="outer")
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df["stop_seq"] = df["stop_seq"].astype(int)
        df["route"] = df["route"].astype(str)

        # 승차 데이터가 없는 날짜(원천 결측)는 정답이 없으므로 boarding=NaN 유지
        # 운행 데이터가 없는 날짜는 trips=NaN (XGBoost가 결측으로 처리)
        has_boarding = set(b["기준_날짜"].unique())
        has_dispatch = set(d["기준_날짜"].unique())
        date_str = df["date"].dt.strftime("%Y%m%d")
        df.loc[~date_str.isin(has_boarding), "boarding"] = np.nan
        df.loc[~date_str.isin(has_dispatch), "trips"] = np.nan

        # 운행도 승차도 없는 시간대(심야 등)는 예측 대상이 아니므로 제외
        df = df[(df["trips"].fillna(0) > 0) | (df["boarding"].fillna(0) > 0)]
        return df.sort_values(keys).reset_index(drop=True)

    # ------------------------------------------------
    # 2. 구간 재차인원 → long
    # ------------------------------------------------

    def load_section_long(self) -> pd.DataFrame:
        s = pd.read_parquet(self.dir / "section.parquet")
        ids = {"기준_날짜": "date", "노선명": "route", "출발_정류장_ID": "stop_id",
               "도착_정류장_ID": "to_stop_id", "정류장_순서": "stop_seq"}
        sl = wide_to_long(s, [f"재차인원합_{h}시" for h in HOURS], ids, "onboard")
        sl["date"] = pd.to_datetime(sl["date"], format="%Y%m%d")
        sl["stop_seq"] = sl["stop_seq"].astype(int)
        return sl

    # ------------------------------------------------
    # 3. 날씨 / 좌표 / 노선 기본정보
    # ------------------------------------------------

    @cached_property
    def weather(self) -> pd.DataFrame:
        w = pd.read_parquet(self.dir / "weather.parquet")
        w["date"] = w["datetime"].dt.normalize()
        w["hour"] = w["datetime"].dt.hour.astype("int8")
        return w[["date", "hour", "temp", "precip", "humidity"]]

    def geo(self, route: str) -> dict | None:
        path = self.dir / "geo" / f"{route}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    @cached_property
    def route_info(self) -> pd.DataFrame:
        """노선 × 요일유형별 기본 배차 정보 (분 단위)"""
        sample_path = self.dir / "route_info.parquet"
        if sample_path.exists():
            return pd.read_parquet(sample_path)

        frames = []
        for daytype, sheet in DAYTYPE_SHEETS.items():
            x = pd.read_excel(ROUTE_INFO_XLSX, sheet_name=sheet, dtype={"노선번호": str})
            x = x[x["노선번호"].isin(self.routes)]
            frames.append(pd.DataFrame({
                "route": x["노선번호"],
                "daytype": daytype,
                "company": x["업체명"],
                "route_type": x["유형"],
                "start": x["기점명"],
                "end": x["종점명"],
                "buses": x["인가대수"],
                "interval": x["배차간격"].astype(float),
                "distance_km": x["거리"],
                "run_minutes": x["운행시간"],
                "min_interval": x["최소배차"].astype(float),
                "max_interval": x["최대배차"].astype(float),
                "first_bus": x["첫차시간"],
                "last_bus": x["막차시간"],
            }))
        info = pd.concat(frames, ignore_index=True)
        # 인가대수가 비어 있으면 왕복 운행시간 ÷ 배차간격으로 추정
        info["buses_estimated"] = info["buses"].isna()
        info["buses"] = info["buses"].fillna(np.ceil(info["run_minutes"] / info["interval"]))
        return info

    def route_info_for(self, route: str, daytype: str) -> dict:
        info = self.route_info
        row = info[(info["route"] == route) & (info["daytype"] == daytype)]
        if row.empty:
            row = info[info["route"] == route]
        return row.iloc[0].to_dict() if not row.empty else {}
