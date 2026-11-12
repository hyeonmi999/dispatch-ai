# 테스트는 합성 데이터(sample)로만 실행합니다. DB 저장은 끕니다.
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["DATA_MODE"] = "sample"
os.environ["ASYNC_DB_URL"] = ""


@pytest.fixture(scope="session", autouse=True)
def sample_model():
    """합성 데이터 생성 + 학습 (xgboost는 torch와 같은 프로세스에 못 올려서 별도 프로세스)"""
    if not (ROOT / "data/sample/boarding.parquet").exists():
        subprocess.run([sys.executable, "scripts/make_sample_data.py"], cwd=ROOT, check=True)
    if not (ROOT / "data/processed/bus_sample_store.parquet").exists():
        subprocess.run([sys.executable, "scripts/train_bus.py", "--mode", "sample"], cwd=ROOT, check=True)
    if not (ROOT / "models/bus/sample/p90.json").exists():
        subprocess.run([sys.executable, "scripts/calibrate_p90.py", "--mode", "sample"], cwd=ROOT, check=True)


@pytest.fixture(scope="session")
def client(sample_model):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
