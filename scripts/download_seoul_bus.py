"""
서울 열린데이터광장 버스 승객/운행 데이터 다운로드 + 노선 필터링

- OA-21219 노선별 정류장별 승차 승객수 (1일 1시간 단위)
- OA-21220 노선별 정류장별 버스 운행횟수 (1일 1시간 단위)
- OA-21218 노선별 정류장 구간별 승객수(재차인원) (1일 1시간 단위)

zip 하나씩 받아서 대상 노선만 걸러 parquet 조각으로 저장하고, 원본 zip은 바로 삭제합니다.
조각이 이미 있으면 건너뛰므로 중간에 끊겨도 다시 실행하면 이어서 받습니다.

사용 예)
    python scripts/download_seoul_bus.py
    python scripts/download_seoul_bus.py --routes 143 272 --datasets OA-21219
"""

import argparse
import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

# =========================================================
# 1. 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PARTS_DIR = ROOT / "data" / "interim" / "parts"
OUT_DIR = ROOT / "data" / "interim"

DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
REFERER = "https://data.seoul.go.kr/dataList/{inf_id}/S/1/datasetView.do"
ROUTE_ID_FILE = RAW_DIR / "서울시버스노선ID정보(20260902).xlsx"

DEFAULT_ROUTES = ["143", "272", "160", "150", "271"]

# 월간 seq = YYMM (월간 zip 안에는 그 달에 공개된 주간 CSV 여러 개가 들어 있음)
# 주간 seq = 공개일 YYMMDD (최근 8주만 주간 파일로 제공)
MONTHLY_SEQS = ["2509", "2510", "2511", "2512", "2601", "2602", "2603",
                "2604", "2605", "2606", "2607", "2608"]
WEEKLY_SEQS = ["260907", "260914", "260921", "260928",
               "261005", "261012", "261019", "261026"]

DATASETS = {
    "OA-21219": {"name": "boarding", "seqs": MONTHLY_SEQS + WEEKLY_SEQS,
                 "key": ["기준_날짜", "노선_ID", "정류장_ID"]},
    "OA-21220": {"name": "dispatch", "seqs": MONTHLY_SEQS + WEEKLY_SEQS,
                 "key": ["기준_날짜", "노선_ID", "정류장_ID"]},
    "OA-21218": {"name": "section", "seqs": WEEKLY_SEQS,
                 "key": ["기준_날짜", "노선_ID", "출발_정류장_ID", "도착_정류장_ID"]},
}

ID_COLS = {"기준_날짜", "노선_ID", "정류장_ID", "출발_정류장_ID", "도착_정류장_ID"}
MIN_DATE = "20250929"  # 2025-10을 포함하는 첫 주(월요일)


# =========================================================
# 2. 노선번호 → ROUTE_ID
# =========================================================

def resolve_route_ids(route_names):
    ids = pd.read_excel(ROUTE_ID_FILE, dtype=str)
    mapping = dict(zip(ids["노선명"], ids["ROUTEID"]))
    missing = [r for r in route_names if r not in mapping]
    if missing:
        raise SystemExit(f"노선ID정보에 없는 노선: {missing}")
    return {mapping[r]: r for r in route_names}


# =========================================================
# 3. 다운로드 + 필터링
# =========================================================

def download_zip(inf_id, seq, retries=3):
    data = {"infId": inf_id, "seqNo": "", "seq": seq, "infSeq": "3"}
    headers = {"Referer": REFERER.format(inf_id=inf_id)}
    for attempt in range(1, retries + 1):
        try:
            res = requests.post(DOWNLOAD_URL, data=data, headers=headers, timeout=300)
            res.raise_for_status()
            if not res.content.startswith(b"PK"):
                raise ValueError(f"zip 아님 ({len(res.content)} bytes)")
            return res.content
        except Exception as e:
            print(f"  ⚠️ {inf_id} seq={seq} 시도 {attempt} 실패: {e}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"{inf_id} seq={seq} 다운로드 실패")


def filter_csv(raw_bytes, route_ids):
    """CSV를 청크로 읽어 대상 노선만 남깁니다. (원본은 cp949)"""
    frames = []
    reader = pd.read_csv(io.BytesIO(raw_bytes), encoding="cp949", dtype=str, chunksize=200_000)
    for chunk in reader:
        frames.append(chunk[chunk["노선_ID"].isin(route_ids)])
    return pd.concat(frames, ignore_index=True)


def process(inf_id, seq, route_ids):
    cfg = DATASETS[inf_id]
    part = PARTS_DIR / f"{cfg['name']}_{seq}.parquet"
    if part.exists():
        print(f"  ⏭️ {part.name} 이미 있음")
        return

    t0 = time.time()
    content = download_zip(inf_id, seq)
    size_mb = len(content) / 1e6

    frames = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member in zf.namelist():
            if member.endswith(".csv"):
                frames.append(filter_csv(zf.read(member), route_ids))
    del content  # 원본 zip은 디스크에 남기지 않음

    df = pd.concat(frames, ignore_index=True)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(part, index=False)
    print(f"  ✅ {part.name}: zip {size_mb:.0f}MB → {len(df):,}행 ({time.time() - t0:.0f}s)")


# =========================================================
# 4. 조각 병합 (중복 날짜 제거, 시간대 컬럼 숫자 변환)
# =========================================================

def merge_parts(inf_id, route_ids):
    cfg = DATASETS[inf_id]
    parts = sorted(PARTS_DIR.glob(f"{cfg['name']}_*.parquet"))
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)

    df = df[df["노선_ID"].isin(route_ids) & (df["기준_날짜"] >= MIN_DATE)]
    df = df.drop_duplicates(subset=cfg["key"], keep="last")

    num_cols = [c for c in df.columns if c not in ID_COLS]
    df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce")
    df.insert(1, "노선명", df["노선_ID"].map(route_ids))
    df = df.sort_values(cfg["key"]).reset_index(drop=True)

    out = OUT_DIR / f"{cfg['name']}.parquet"
    df.to_parquet(out, index=False)
    print(f"📦 {out.relative_to(ROOT)}: {len(df):,}행, "
          f"{df['기준_날짜'].min()} ~ {df['기준_날짜'].max()}, 날짜 {df['기준_날짜'].nunique()}일")


# =========================================================
# 5. 실행
# =========================================================

def main():
    parser = argparse.ArgumentParser(description="서울 버스 승객/운행 데이터 다운로드")
    parser.add_argument("--routes", nargs="+", default=DEFAULT_ROUTES, help="노선번호 (예: 143 272)")
    parser.add_argument("--datasets", nargs="+", default=list(DATASETS), choices=list(DATASETS))
    args = parser.parse_args()

    route_ids = resolve_route_ids(args.routes)
    print(f"대상 노선: {route_ids}")

    for inf_id in args.datasets:
        print(f"\n===== {inf_id} ({DATASETS[inf_id]['name']}) =====")
        for seq in DATASETS[inf_id]["seqs"]:
            process(inf_id, seq, route_ids)
        merge_parts(inf_id, route_ids)


if __name__ == "__main__":
    main()
