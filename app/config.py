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
EVAL_RESULTS_DIR = ROOT / "eval" / "results"
BACKTEST_RESULT_PATH = EVAL_RESULTS_DIR / "backtest.json"   # 배차 방식 성능 비교 (scripts/backtest.py가 만듦, F-14)
DRIFT_RESULTS_DIR = EVAL_RESULTS_DIR / "drift"              # 예측 오차 점검 결과 (scripts/check_drift.py가 만듦, 요청 R-3)

# 모델(Qwen3, bge-m3)을 한 번 받아 두면 Hugging Face Hub 접속 없이 로컬 캐시만 사용
# (Hub 확인 요청·safetensors 변환 스레드 때문에 시작/종료가 지연되는 문제 방지)
_llm_cache = os.getenv("LOCAL_MODEL_NAME", "Qwen/Qwen3-1.7B").replace("/", "--")
if all((HF_CACHE_DIR / f"models--{m}").exists() for m in ("BAAI--bge-m3", _llm_cache)):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


# ==================================================
# 2. API 키 / DB
# ==================================================

DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY", "")
SEOUL_OPEN_API_KEY = os.getenv("SEOUL_OPEN_API_KEY", "")   # 서울 열린데이터광장 일반 인증키 (TOPIS 실시간 돌발·소통, Task P-9)
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
# 검색용 세부 글: 조각(근거 단위)은 그대로 두고, 이 글자 수보다 긴 조각은 항·호·목 단위의 글도 함께 임베딩해
# 조각의 점수를 그중 최댓값으로 보는 기능. 0이면 끔 (조각 전체 글만 임베딩, 지금 방식).
# **지금은 끔.** 250으로 켜면 40문항 검색은 20/24 → 22/24로 오르지만, Qwen3-1.7B의 답변은 정답 18/24 → 15/24,
# 지어낸 답 1 → 3으로 나빠졌음 (eval/results/rag40_answer_exp.json, 2026-11-26). 더 큰 모델로 다시 채점한 뒤 켤지 정함.
# 비교 실험: python scripts/experiment_retrieval.py
RAG_SEARCH_UNIT_LIMIT = {"bge": 0, "openai": 0}
NOT_FOUND_ANSWER = "제공된 규정 문서에서 근거를 찾을 수 없음"
# 역할별 검색: 볼 수 없는 등급의 조각을 거르면 결과가 모자랄 수 있어 처음에 k의 몇 배를 가져올지.
# 그래도 모자라면 허용된 조각이 k개 찰 때까지 두 배씩 늘려 다시 가져옴 (색인 전체까지)
RAG_ROLE_OVERFETCH = 3
RAG_DOCUMENT_SUFFIXES = (".pdf", ".txt")     # 규정 문서로 읽는 파일 (출처 목록 같은 .md는 읽지 않음)

# 목록 API가 한 번에 돌려주는 최대 개수
LIST_LIMIT_MAX = 100

# 배차 상담 챗봇 (app/core/agent.py)
CHAT_MESSAGE_MAX = 500      # 질문 한 건의 최대 글자 수
CHAT_HISTORY_MAX = 10       # 이어 묻기에 쓰는 이전 대화 개수 상한
CHAT_HISTORY_ITEM_MAX = 2000  # 이전 대화 한 건의 최대 글자 수
CHAT_NOTE_MAX = 80          # use_llm일 때 문장 틀 답변 아래에 LLM이 덧붙이는 한 줄의 최대 글자 수
CHAT_NOTE_MARK = "💬"       # 그 한 줄 앞에 붙는 표시 (문장 틀 답변과 구분)


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
# 트리 개수 상한. 실제 개수는 early stopping이 정하고, 상한은 그보다 넉넉해야 함
#   상한 2,000일 때는 1,999에서 끊겨 학습이 덜 끝났음. 풀어 보니 2,797에서 스스로 멈춤 (docs/decisions.md)
BUS_XGB_MAX_TREES = 10000
BUS_XGB_EARLY_STOP_ROUNDS = 50

# 인가대수가 원본(서울시 버스노선 기본정보 엑셀)에 비어 있는 노선의 보완 값
#   160번 49대: 나무위키 "서울 버스 160" (2026-11-12 확인, 공식 자료 아님)
#   같은 출처의 143·150·272번 값은 원본 엑셀과 같고 271번만 1대 다름 (docs/decisions.md)
BUS_FLEET_OVERRIDES = {"160": 49}

# 차량 정원 (서울 일반 시내버스 대형 기준: 좌석 약 25 + 입석)
BUS_CAPACITY = 55
BUS_SEATS = 25

# 혼잡도의 시간 기준 = 버스가 기점을 출발한 시각 (docs/decisions.md 2026-11-13)
#   원본 구간 재차인원(OA-21218)의 "N시"는 기점을 N시대에 출발한 버스에 탄 인원이고,
#   승차·운행횟수(OA-21219·21220)의 "N시"는 그 정류장을 N시대에 지난 값입니다.
#   그래서 분모는 '기점 출발 횟수'(앞쪽 정류장 몇 곳의 운행횟수)로 맞추고,
#   승객 수는 출발 뒤 몇 시간 동안의 승차 합으로 반영합니다.
BUS_DEPARTURE_STOPS = 5      # 기점 출발 횟수 = 정류장 순서가 가장 앞인 5곳의 시간별 운행횟수 중앙값
BUS_LOAD_WINDOW_HOURS = 3    # N시 출발 버스의 재차인원 ∝ N시부터 3시간 동안의 노선 승차 합

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
CONGESTION_CUT_MAX = 0.60  # 최적화의 감차 조건: 줄인 뒤에도 상한 기준 혼잡도가 이 값 이하일 때만 줄임
#   목표(0.7)와 같게 두면 튜닝 구간에서 감차한 918칸 중 30칸이 실제로 과밀이 됨. 0.6이면 607칸 중 3칸 (docs/decisions.md)

# 시간대별 한 바퀴 운행시간 (이슈 #42 방법 1, docs/plans/park-next.md Task P-7)
#   OA-21217 구간 운행시간(N시 = 그 구간을 지난 시각)으로 기점 출발 시각별 한 바퀴 시간을 만들어
#   도로 위 버스 대수(fleet.py)에 씀. 표가 없거나 칸이 비면 노선 기본정보의 고정 운행시간
#   만들기: bash scripts/build_runtime_table.sh  (→ data/interim/runtime_table.parquet)
BUS_RUNTIME_TABLE = "runtime_table.parquet"   # INTERIM_DIR 아래 (커밋 제외)
BUS_RUNTIME_TRIPS = "runtime_trip.parquet"    # 하루별 한 바퀴 시간 (표의 원재료)
BUS_RUNTIME_WEEKS = 8            # 표 한 칸 = 기준일까지 최근 8주, 같은 요일유형·출발 시각의 중앙값
BUS_RUNTIME_MIN_DAYS = 3         # 중앙값에 쓴 날이 이보다 적으면 그 칸은 비움 (→ 고정 운행시간)
BUS_RUNTIME_LAG_DAYS = 21        # 날짜 D에는 기준일 ≤ D − 21일인 표만 씀 (3주 앞 계획의 예측 시점 규칙)
BUS_RUNTIME_START_MINUTE = 30    # 'N시 출발'을 N시 30분에 떠난 버스로 따라가며 구간 시간을 더함
BUS_RUNTIME_MAX_FILL = 0.2       # 그 시간대 기록이 없어 구간 하루 평균으로 채운 구간이 이 비율을 넘으면 그날 값은 버림

# 실시간 사고·정체 알림 (이슈 #42 방법 2, docs/plans/park-next.md Task P-9)
#   TOPIS 실시간 돌발(AccInfo)·소통(TrafficInfo)을 관제 화면에 알려 줌. 배차·최적화·권고 값은 바꾸지 않음 (needs_review)
#   준비: bash scripts/build_topis_speed.sh (평소 링크 속도) → bash scripts/build_topis_links.sh (노선 ↔ 링크, 키 필요)
TOPIS_BASE_URL = "http://openapi.seoul.go.kr:8088"   # 서울 열린데이터광장 Open API (XML만 됨, JSON은 ERROR-301)
TOPIS_TIMEOUT = 5.0              # 외부 호출 한 번의 제한 시간(초)
TOPIS_PAGE_ROWS = 1000           # 한 번에 받을 수 있는 최대 행 수
TOPIS_MAX_CONCURRENCY = 8        # 링크 소통 정보를 동시에 부르는 수
TOPIS_CACHE_SECONDS = 300        # 같은 실시간 결과를 다시 쓰는 시간 (TOPIS 속도는 5분 단위)
TOPIS_DIR = "topis"              # INTERIM_DIR 아래 (커밋 제외)
TOPIS_ROUTE_LINKS = "route_links.parquet"   # 노선 ↔ 링크 대응표 (scripts/build_topis_links.py)
TOPIS_LINK_SPEED = "link_speed.parquet"     # 링크 × 요일유형 × 시간대 평소 속도 (scripts/build_topis_speed.py)
TOPIS_LINKS = "links.parquet"               # TOPIS 링크 목록 (같은 스크립트, 링크 대응표의 후보)
TOPIS_SNAPSHOT_DIR = "snapshots"            # TOPIS_DIR 아래. 실시간으로 받을 때마다 가공 결과를 남김 (기록 다시 보기)
TOPIS_SNAPSHOT_MAX = 2000        # 기록 파일을 이보다 많이 두지 않음 (오래된 것부터 지움)
TOPIS_SNAPSHOT_LIMIT = 50        # GET /api/bus/traffic/snapshots 기본 개수 (최대 LIST_LIMIT_MAX)
TOPIS_LINK_NEAR_M = 30           # 링크 꼭짓점이 노선 경로에서 이 거리 안이면 경로 위
TOPIS_LINK_MIN_SHARE = 0.7       # 꼭짓점의 이 비율 이상이 경로 위인 링크를 그 노선 링크로 봄
TOPIS_ACC_NEAR_M = 150           # 돌발 지점이 노선 경로에서 이 거리 안이면 "이 노선 경로에 사고"
TOPIS_LONG_TERM_DAYS = 7         # 해제 예정이 이보다 먼 돌발(장기 공사 등)은 따로 묶음 (평소 운행시간에 이미 들어 있음)
TOPIS_MIN_COVERAGE = 0.7         # 노선 링크 길이의 이 비율 이상에서 지금·평소 값을 모두 받았을 때만 추가 시간을 계산
TOPIS_EXTRA_WARN_RATIO = 0.15    # 한 바퀴 추가 시간이 평소 한 바퀴의 15% 이상이면 경고 (143번 233분 → 35분, 정류장당 약 19초)
#   10분 고정이었을 때는 정류장당 5초만 늦어도 경고라 2026-11-28 토 18시에 5개 노선이 모두 경고 (승용차 기준 +20~37분)
TOPIS_SPEED_MONTHS = 2           # 평소 속도 = 최근 이 개월 같은 요일유형·시간대의 중앙값

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
OPTIMIZE_RUNS_LIMIT = 20               # GET /api/bus/optimize/runs 기본 개수 (최대 LIST_LIMIT_MAX)

# 예측 오차 점검 → 재학습 권고 (요청 R-3, docs/plans/park-next.md Task P-5). 셋 중 하나라도 걸리면 권고, 결정은 담당자
DRIFT_MAE_RATIO = 1.2               # 주간 정류장·시간 MAE가 평가 MAE(metrics.json stop_hour)의 이 배수를 넘으면
DRIFT_P90_COVERAGE_MIN = 85.0       # 노선·시간 합계가 P90 상한 이하인 비율(%)이 이보다 낮으면 (평가 구간 합격 범위 88~92%)
DRIFT_MIN_DAYS = 3                  # 실측이 있는 날이 이보다 적으면 판정 보류
DRIFT_LIST_LIMIT = 20               # GET /api/bus/drift 기본 개수
DRIFT_RETRAIN_STEPS = [
    "학습 구간을 새 실측까지 옮길지 정하기: app/config.py의 BUS_VALID_START·BUS_TUNE_END (옮기면 평가 구간이 바뀌어 성능 비교·오차 설명을 다시 채점해야 함)",
    "python scripts/train_bus.py --mode real",
    "python scripts/calibrate_p90.py",
    "python scripts/backtest.py",
    "python scripts/explain_prediction_error.py --name <다음 번호>",
    "서버 재시작",
]

# 길안내 (app/domains/bus/guide.py)
GUIDE_WALK_M = 500             # 이 거리 안의 정류장을 출발 후보로 봄 (걸어서 약 7분)
GUIDE_TRANSFER_M = 150         # 갈아탈 때 걸어서 옮길 수 있는 정류장 사이 거리
GUIDE_PLACE_M = 300            # 이름으로 찾은 정류장 주변 이 거리 안의 정류장도 타고 내릴 곳으로 봄 (길 건너·다른 출구)
GUIDE_WALK_M_PER_MIN = 70      # 걷는 속도 (분당 m)
GUIDE_MAX_OPTIONS = 3          # 보여 줄 경로 수
GUIDE_CANDIDATES = 12          # 시간·혼잡도를 계산해 볼 경로 후보 수 (정류장 수가 적은 순)
GUIDE_SCAN_FACTOR = 10         # 운행하지 않는 노선을 건너뛰며 후보를 최대 몇 배까지 더 볼지
GUIDE_DEFAULT_MIN_PER_STOP = 2.5   # 운행시간 정보가 없는 노선의 정류장 하나당 시간(분)
# 탑승 시간 계산 (정류장별 공식 시간표 기준)
GUIDE_EARLY_HOUR = 5           # 이 시각 이하는 첫차 시간표(도로가 빈 시간), 그 뒤는 막차 시간표(저녁)를 기준으로 봄
GUIDE_PEAK_HOURS = (7, 8, 9, 17, 18, 19)
GUIDE_PEAK_FACTOR = 1.15       # 출퇴근 시간에 시간표보다 더 걸린다고 보는 비율 (가정값. 실측으로 정한 값이 아님)
GUIDE_SPEED_RANGE_KMH = (6, 60)    # 시간표로 계산한 속도가 이 범위를 벗어나면 시간표 값이 잘못된 것으로 보고 쓰지 않음
GUIDE_DEFAULT_KMH = 17         # 시간표를 쓸 수 없는 노선의 평균 속도. 시간표가 있는 451개 노선의 중앙값 17.1km/h (정차 포함)
GUIDE_SHORT_LEG_MIN = 3        # 시간표는 분 단위라, 이보다 짧은 구간은 거리 ÷ 노선 평균 속도로 계산
GUIDE_NEAR_STOPS = 5           # 주변에 정류장이 없을 때 살펴볼 가까운 정류장 수
GUIDE_CROWD_TRADEOFF_MIN = 10  # 덜 붐비는 경로가 이 시간(분) 이하로 더 걸리면 그쪽을 추천
GUIDE_CROWD_MIN_GAP = 0.1      # 혼잡도 차이가 이보다 작으면 '덜 붐빔'으로 보지 않음 (0.355와 0.35는 같은 수준)
GUIDE_SAME_PLACE_M = 1500      # 같은 이름의 정류장이 이보다 멀리 떨어져 있으면 다른 곳으로 봄 (농수산물센터가 3곳 등)
GUIDE_PLACE_TRIES = 3           # 같은 이름이 여러 곳일 때, 가까운 곳부터 몇 곳까지 경로를 찾아볼지
GUIDE_SEARCH_LIMIT_MAX = 20    # 정류장 검색 결과 최대 개수
GUIDE_DATE_FALLBACK_WEEKS = 8  # 요청한 날이 예측 범위 밖이면 같은 요일로 몇 주까지 거슬러 찾을지
GUIDE_START_HOUR_NOTE = "(기점 {hour}시대 출발 버스 기준)"   # 혼잡도 시간대가 물어본 시각과 다를 때 구간 줄 끝에 붙이는 문구
GUIDE_CLOCK_MINUTE = 30        # 'N시에 출발'을 N시 30분에 출발하는 것으로 봄 (시간대의 가운데). 탈 정류장에 닿는 시각 계산용
GUIDE_FIRST_BUS_WAIT_MAX_MIN = 60   # 첫차가 아직 오지 않은 정류장: 이 시간(분) 안에 첫차가 오면 기다려 타는 것으로 안내, 더 뒤면 그 노선은 뺌
ROUTE_INFO_KEY_STOPS = 8       # 노선 정보에 보여 줄 주요 정류장 수

# 학기 중/방학 구분 (서울 초중고 대략 기준, 계절성 피처)
SCHOOL_VACATIONS = [
    ("2025-07-21", "2025-08-17"),
    ("2025-12-31", "2026-03-01"),
    ("2026-07-20", "2026-08-16"),
    ("2026-12-30", "2027-03-01"),
]

# 문서 관리 (로컬 시연)
UPLOADED_DOCS_DIR = DATA_DIR / "docs"
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_LIST_LIMIT = 100
DOCUMENT_LIST_MAX_LIMIT = 1000
ROLE_GRADES = {
    "citizen": {"공개"},
    "staff": {"공개", "내부"},
    "admin": {"공개", "내부", "제한"},
}
# 외부 전송 정책 (이슈 #28): 이 등급의 문서만 외부 서비스(OpenAI)로 보낼 수 있음. 볼 권한과 외부 전송 허용은 별개
DOCUMENT_PUBLIC_GRADE = "공개"
EXTERNAL_LLM = "openai"
EXTERNAL_EMBEDDING = "openai"
EXTERNAL_BLOCK_NOTE = "공개 등급이 아닌 문서가 근거에 있어 외부 LLM을 부르지 않고 근거 조항만 돌려줍니다."
EXTERNAL_EMBEDDING_BLOCK_MESSAGE = (
    "공개 등급이 아닌 문서가 등록되어 있어 OpenAI 임베딩을 쓸 수 없습니다. embedding은 bge(로컬)를 사용하세요."
)
