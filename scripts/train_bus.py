"""
버스 승객 수 예측 모델 학습 (XGBoost)

사용
  python scripts/train_bus.py               # DATA_MODE(.env) 기준 (auto: 실데이터 있으면 real)
  python scripts/train_bus.py --mode sample  # 합성 데이터로 학습 (make_sample_data.py 먼저 실행)

출력: 모델 MAE, 베이스라인(전주 같은 요일·시간) MAE, 개선율(%) → models/bus/<mode>/metrics.json
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domains.bus.data import BusDataset  # noqa: E402
from app.domains.bus.forecaster import BusForecaster  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["real", "sample"], default=None)
    args = parser.parse_args()

    dataset = BusDataset(args.mode)
    if not dataset.exists():
        hint = "python scripts/make_sample_data.py" if dataset.mode == "sample" else "README의 데이터 다운로드 절차"
        raise SystemExit(f"{dataset.dir} 에 데이터가 없습니다. 먼저 실행: {hint}")
    BusForecaster(dataset).fit()


if __name__ == "__main__":
    main()
