# ==================================================
# 공통 설정
# ==================================================
# 경로, 모델 이름, 임계값 등 서비스 전체에서 쓰는 설정을 한 곳에 모읍니다.
# 값은 .env로 덮어쓸 수 있습니다.

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Tokenizer 병렬 처리 관련 경고 방지
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Windows에서 출력을 파일·파이프로 보내면 기본 인코딩(cp949)이 로그의 이모지를 못 써서
# 서버·스크립트가 UnicodeEncodeError로 멈춤 → 못 쓰는 글자만 '?'로 바꿔 계속 실행
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

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

# 목록 API가 한 번에 돌려주는 최대 개수
LIST_LIMIT_MAX = 100

# 배차 상담 챗봇 (app/core/agent.py)
CHAT_MESSAGE_MAX = 500      # 질문 한 건의 최대 글자 수
CHAT_HISTORY_MAX = 10       # 이어 묻기에 쓰는 이전 대화 개수 상한
CHAT_HISTORY_ITEM_MAX = 2000  # 이전 대화 한 건의 최대 글자 수


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

# 길안내 (app/domains/bus/guide.py)
GUIDE_WALK_M = 500             # 이 거리 안의 정류장을 출발 후보로 봄 (걸어서 약 7분)
GUIDE_TRANSFER_M = 150         # 갈아탈 때 걸어서 옮길 수 있는 정류장 사이 거리
GUIDE_WALK_M_PER_MIN = 70      # 걷는 속도 (분당 m)
GUIDE_MAX_OPTIONS = 3          # 보여 줄 경로 수
GUIDE_NEAR_STOPS = 5           # 주변에 정류장이 없을 때 살펴볼 가까운 정류장 수
GUIDE_CROWD_TRADEOFF_MIN = 10  # 덜 붐비는 경로가 이 시간(분) 이하로 더 걸리면 그쪽을 추천
GUIDE_SEARCH_LIMIT_MAX = 20    # 정류장 검색 결과 최대 개수
GUIDE_DATE_FALLBACK_WEEKS = 8  # 요청한 날이 예측 범위 밖이면 같은 요일로 몇 주까지 거슬러 찾을지
ROUTE_INFO_KEY_STOPS = 8       # 노선 정보에 보여 줄 주요 정류장 수

# 학기 중/방학 구분 (서울 초중고 대략 기준, 계절성 피처)
SCHOOL_VACATIONS = [
    ("2025-07-21", "2025-08-17"),
    ("2025-12-31", "2026-03-01"),
    ("2026-07-20", "2026-08-16"),
    ("2026-12-30", "2027-03-01"),
]
