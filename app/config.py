# ==================================================
# 공통 설정
# ==================================================
# 경로, 모델 이름, 임계값 등 서비스 전체에서 쓰는 설정을 한 곳에 모읍니다.
# 값은 .env로 덮어쓸 수 있습니다.

import os
from pathlib import Path

from dotenv import load_dotenv

# Tokenizer 병렬 처리 관련 경고 방지
os.environ["TOKENIZERS_PARALLELISM"] = "false"

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


# ==================================================
# 1. 경로
# ==================================================

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"     # 실데이터 (노선 필터링 후)
SAMPLE_DIR = DATA_DIR / "sample"       # 합성 데이터 (같은 스키마)
PROCESSED_DIR = DATA_DIR / "processed"
DOCS_DIR = ROOT / "docs"               # docs/<domain>/ 에 PDF·TXT를 넣으면 인덱싱
MODELS_DIR = ROOT / "models"
VECTORSTORE_DIR = ROOT / "vectorstore"
HF_CACHE_DIR = ROOT / "hf_models"

# 모델(Qwen3, bge-m3)을 한 번 받아 두면 Hugging Face Hub 접속 없이 로컬 캐시만 사용
# (Hub 확인 요청·safetensors 변환 스레드 때문에 시작/종료가 지연되는 문제 방지)
_llm_cache = os.getenv("LOCAL_MODEL_NAME", "Qwen/Qwen3-1.7B").replace("/", "--")
if all((HF_CACHE_DIR / f"models--{m}").exists() for m in ("BAAI--bge-m3", _llm_cache)):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


# ==================================================
# 2. API 키 / DB
# ==================================================

DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN") or None
ASYNC_DB_URL = os.getenv("ASYNC_DB_URL", "")


# ==================================================
# 3. LLM / Embedding (수업 표준과 동일)
# ==================================================

LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "Qwen/Qwen3-1.7B")
# LoRA 어댑터 (scripts/finetune_lora.py 결과). 비우면 사전학습 모델 그대로 사용
LOCAL_ADAPTER = os.getenv("LOCAL_ADAPTER", "") or None
OPENAI_MODEL_NAME = "gpt-4o-mini"

BGE_EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
OPENAI_EMBEDDING_MODEL_NAME = "text-embedding-3-small"

DEFAULT_LLM = os.getenv("DEFAULT_LLM", "qwen")              # qwen | openai
DEFAULT_EMBEDDING = os.getenv("DEFAULT_EMBEDDING", "bge")   # bge | openai

MAX_NEW_TOKENS = 384
REPETITION_PENALTY = 1.1

# RAG 검색: 코사인 유사도가 이 값보다 낮으면 "근거 없음"으로 판단
RAG_TOP_K = 4
RAG_SCORE_THRESHOLD = {"bge": 0.55, "openai": 0.35}
NOT_FOUND_ANSWER = "제공된 규정 문서에서 근거를 찾을 수 없음"


# ==================================================
# 4. 데이터 모드
# ==================================================
# auto  : data/interim 에 실데이터가 있으면 real, 없으면 sample
# real  : 서울 열린데이터광장 실데이터
# sample: scripts/make_sample_data.py 로 만든 합성 데이터

DATA_MODE = os.getenv("DATA_MODE", "auto")


def resolve_data_mode() -> str:
    if DATA_MODE in {"real", "sample"}:
        return DATA_MODE
    return "real" if (INTERIM_DIR / "boarding.parquet").exists() else "sample"


# ==================================================
# 5. 버스 도메인 설정
# ==================================================

BUS_ROUTES = ["143", "272", "160", "150", "271"]   # 실데이터 기본 노선
BUS_SAMPLE_ROUTES = ["901", "902", "903"]          # 합성 데이터 노선

# 학습/검증 분리일 (시간 순서) - 실데이터 기준
BUS_VALID_START = "2026-09-01"       # 이 날짜부터 검증
BUS_EARLY_STOP_DAYS = 14             # 학습 구간 마지막 14일은 early stopping 용

# 인가대수가 원본(서울시 버스노선 기본정보 엑셀)에 비어 있는 노선의 보완 값
#   160번 49대: 나무위키 "서울 버스 160" (2026-11-12 확인, 공식 자료 아님)
#   같은 출처의 143·150·272번 값은 원본 엑셀과 같고 271번만 1대 다름 (docs/decisions.md)
BUS_FLEET_OVERRIDES = {"160": 49}

# 차량 정원 (서울 일반 시내버스 대형 기준: 좌석 약 25 + 입석)
BUS_CAPACITY = 55
BUS_SEATS = 25

# 혼잡도 = 버스 1대당 평균 재차인원 ÷ 정원
#   여유: 좌석에 앉을 수 있는 수준 / 보통: 입석 손잡이 / 혼잡: 통로까지 입석
CONGESTION_LEVELS = [
    (0.45, "여유"),
    (0.80, "보통"),
    (1.00, "혼잡"),
    (float("inf"), "매우혼잡"),
]
CONGESTION_HIGH = 0.80     # 이 이상이면 배차간격 단축(증차) 권고
CONGESTION_LOW = 0.25      # 이 미만이면 감차 검토 권고
CONGESTION_TARGET = 0.70   # 권고 간격 계산 시 목표 혼잡도

# 검증 구간(BUS_VALID_START ~ 실측 마지막 날)을 둘로 나눔: 앞쪽에서 값을 고르고 뒤쪽에서는 채점만
BUS_TUNE_END = "2026-09-28"          # 이 날짜까지 튜닝 구간, 다음 날부터 평가 구간

# 승객 수 상한(P90): 예측 × (실제 ÷ 예측 비율의 90% 분위수)
BUS_P90_QUANTILE = 0.90
BUS_P90_MIN_SAMPLES = 10             # 노선·시간 표본(날짜 수)이 이보다 적으면 노선 전체 값 사용
BUS_P90_COVERAGE_RANGE = (88.0, 92.0)   # 합격 기준: 평가 구간에서 실제가 상한 이하인 비율(%)
BUS_P90_FIXED_RATIO = 1.10           # 비교용 후보: 전 노선 고정 배수

# 전 노선 배차 최적화 (F-13)
#   줄일 것 = overload × 과밀량(명) + trips × 총 운행횟수 + change × 현행과 달라진 운행횟수
#   change 0: 바꾸는 비용을 두지 않아, 붐비는 곳은 늘리고 한가한 곳은 줄임 (감차 허용)
#   평가 구간에서 증차만 하는 설정(change 1)과 과밀 비율은 같고 총 운행이 훨씬 적었음 (docs/decisions.md)
BUS_OPT_WEIGHTS = {"overload": 10, "trips": 1, "change": 0}
BUS_OPT_TIME_LIMIT_SEC = 10          # 풀이 시간 제한
BUS_OPT_SEED = 42

# 학기 중/방학 구분 (서울 초중고 대략 기준, 계절성 피처)
SCHOOL_VACATIONS = [
    ("2025-07-21", "2025-08-17"),
    ("2025-12-31", "2026-03-01"),
    ("2026-07-20", "2026-08-16"),
    ("2026-12-30", "2027-03-01"),
]
