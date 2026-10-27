# ==================================================
# 수요 예측 공통 인터페이스 (Forecaster)
# ==================================================
# 도메인마다 예측 대상은 다르지만(버스 승객 수, 물류 화물량, 출동 건수)
# "학습 → 평가(모델 vs 베이스라인) → 예측" 흐름은 같습니다.

from abc import ABC, abstractmethod

import numpy as np


class Forecaster(ABC):
    """도메인 공통 수요 예측기"""

    model_version: str = ""

    @abstractmethod
    def fit(self) -> dict:
        """학습 후 평가 지표(dict)를 반환"""

    @abstractmethod
    def load(self) -> bool:
        """저장된 모델 로드. 성공 여부 반환"""

    @abstractmethod
    def metrics(self) -> dict:
        """마지막 학습의 평가 지표"""


def evaluate_mae(y_true, y_model, y_baseline) -> dict:
    """모델과 베이스라인의 MAE 및 개선율(%)

    개선율 = (베이스라인 MAE - 모델 MAE) / 베이스라인 MAE × 100
    """
    y_true = np.asarray(y_true, dtype=float)
    mae_model = float(np.mean(np.abs(y_true - np.asarray(y_model, dtype=float))))
    mae_base = float(np.mean(np.abs(y_true - np.asarray(y_baseline, dtype=float))))
    improvement = (mae_base - mae_model) / mae_base * 100 if mae_base > 0 else 0.0
    return {
        "n": int(len(y_true)),
        "model_mae": round(mae_model, 4),
        "baseline_mae": round(mae_base, 4),
        "improvement_pct": round(improvement, 2),
    }
