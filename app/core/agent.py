# ==================================================
# 배차 상담 챗봇 (F-25)
# ==================================================
# 질문 한 건을 받아 알맞은 기능을 골라 부르고 그 결과로 답합니다.
#
# 처리 흐름
#   질문
#     ↓ 의도 분류 (키워드 규칙. 작은 모델에 기능 선택을 맡기지 않음)
#   predict / advise / ask / simulate / optimize / 범위 밖
#     ↓ 값 뽑기 (노선 · 날짜 · 시간 · 배차간격). 이전 대화의 값을 이어 씀
#   빠진 값이 있으면 되묻기 (clarify)
#     ↓ 기능 호출 (어댑터의 기존 기능을 그대로 부름. 로직을 다시 만들지 않음)
#   답변에 쓸 값(facts)만 추림
#     ↓ 문장 틀에 값 채우기
#   숫자 검증: 답변 속 숫자가 모두 facts·질문 값에 있는지 확인
#
# 원칙
# - 숫자는 기능이 돌려준 값만 씁니다. 문장 틀에 고정 숫자를 적지 않습니다.
# - 모든 답변은 needs_review=True (담당자 검토 필요)

import datetime as dt
import json
import logging
import math
import re
import time

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import GUIDE_START_HOUR_NOTE, NOT_FOUND_ANSWER, RAG_TOP_K
from app.core import llm as llm_module
from app.core.domain import DomainAdapter, InvalidRequest
from app.core.llm import LLMConfigError
from app.core.prompts import CHAT_SYSTEM_MESSAGE

logger = logging.getLogger(__name__)

# ==================================================
# 1. 의도 분류 (키워드 규칙)
# ==================================================
# 위에서부터 차례로 확인합니다. 놓친 표현은 여기에 키워드를 추가합니다.

# 규정 질문이 분명한 단어: 노선이 함께 나와도 규정 질의응답으로 보냄
ASK_STRONG = ("규정", "조례", "규칙", "절차", "신고", "허가", "과징금", "과태료",
              "사업계획", "준공영", "려면", "시행령")
# 한 글자·두 글자 키워드는 다른 말에 섞여 들어가므로 앞뒤를 보고 판단
#   법: "법령·법에·운수사업법은"은 규정, "법원·방법"은 아님
#   인가: "인가를 받아야·인가 대상"은 규정, "몇 시인가요·인가대수"는 아님
_LAW_RE = re.compile(r"(?<!방)법(?:령|률|규|적|상|에|은|이|을|의|으로|\s|[?.!,]|$)")
_PERMIT_RE = re.compile(r"인가(?!요|대수|[?.!]|\s*$)")


def _is_regulation(text: str) -> bool:
    return _has(text, ASK_STRONG) or bool(_LAW_RE.search(text)) or bool(_PERMIT_RE.search(text))
# 노선이 없을 때만 규정 질문으로 보는 단어
# ("해야"는 넣지 않음: "증차해야 해?"는 규정이 아니라 배차 권고 질문)
ASK_WEAK = ("기준", "가능한가", "의무", "역할", "어떻게 구분")
OPTIMIZE_WORDS = ("최적화", "최적 배차", "전체 노선", "전 노선", "모든 노선")
SIMULATE_WORDS = ("시뮬", "바꾸면", "바꿨을 때", "바꾼다면", "으로 하면", "로 하면", "줄이면", "늘리면", "좁히면", "넓히면")
ADVISE_WORDS = ("권고", "추천", "증차", "감차", "늘려야", "줄여야", "늘릴까", "줄일까", "조정", "적당", "적정")
# 길안내: "A에서 B 어떻게 가?" (요금·규정을 묻는 말이 섞이면 규정 질문으로 봄)
GUIDE_WORDS = ("어떻게 가", "어떻게가", "가는 법", "가는법", "가는 방법", "가는 길", "가려면", "가고 싶", "갈 수 있",
               "갈래", "길안내", "길 안내", "길 알려", "환승", "몇 번 타", "뭐 타", "무슨 버스")
GUIDE_BLOCK = ("규정", "조례", "요금", "할인", "절차")
# 노선 기본 정보: "160번 노선 알려줘", "143번 첫차 몇 시야?" (노선을 말했을 때만)
ROUTE_INFO_WORDS = ("노선 알려", "노선알려", "노선 정보", "노선정보", "노선도", "노선 좀", "어디서 어디", "어디 가", "어디가",
                    "어디로 가", "어디까지", "어디를", "경유", "지나", "정류장", "첫차", "막차", "기점", "종점",
                    "몇 분마다", "몇분마다", "배차간격이", "배차 간격이", "배차간격은", "배차 간격은", "운행시간", "운행 시간",
                    "인가대수", "몇 대", "어떤 버스", "무슨 노선", "소개")
PREDICT_WORDS = ("몇 명", "몇명", "승객", "승차", "붐", "혼잡", "예측", "수요", "사람 많", "사람이 많", "여유", "한가", "탈까", "타나")
# 버스 업무와 관련된 질문인지 가리는 단어 (하나도 없고 노선도 없으면 범위 밖)
DOMAIN_WORDS = ("버스", "노선", "배차", "운행", "정류", "운수", "요금", "기사", "차량", "증차", "감차", "승객", "혼잡")

# 기능마다 꼭 있어야 하는 값
REQUIRED = {
    "predict": ("route", "date"),
    "advise": ("route", "date", "hour"),
    "simulate": ("route", "date", "hour", "interval"),
    "optimize": ("date", "hours"),
    "route_info": ("route",),
}
OUT_OF_SCOPE_ANSWER = "🙏 버스 승객 예측, 배차 권고, 노선 정보, 길안내, 운수 규정에 대한 질문에만 답할 수 있어요."

# 답변을 읽기 쉽게 하는 표시 (숫자가 아니므로 숫자 검증과 무관)
LEVEL_MARK = {"여유": "🟢", "보통": "🟡", "혼잡": "🟠", "매우혼잡": "🔴"}
ACTION_MARK = {"increase": "⬆️", "increase_limited": "⚠️", "decrease": "⬇️", "keep": "✅"}
OPTIMIZE_TOP_CHANGES = 6      # 최적화 답변에 보여 줄 변경 건수 (변경 폭이 큰 순)


def _has(text: str, words: tuple) -> bool:
    return any(w in text for w in words)


def classify(message: str, has_route: bool) -> str | None:
    """질문 한 건의 의도. 키워드가 하나도 안 맞으면 None"""
    if _has(message, GUIDE_WORDS):
        rest = message
        for word in GUIDE_WORDS:          # "가는 법"의 '법'을 규정 단어로 보지 않게 길안내 표현을 먼저 지움
            rest = rest.replace(word, " ")
        if not _has(rest, GUIDE_BLOCK) and not _LAW_RE.search(rest):
            return "guide"
    if _is_regulation(message):
        return "ask"
    if _has(message, OPTIMIZE_WORDS):
        return "optimize"
    if _has(message, SIMULATE_WORDS) and ("분" in message or "간격" in message):
        return "simulate"
    if not has_route and _has(message, ASK_WEAK):
        return "ask"                                   # "혼잡도는 어떻게 구분하나요?"
    if _has(message, ADVISE_WORDS):
        return "advise"
    if _has(message, PREDICT_WORDS):
        return "predict"
    if _has(message, ROUTE_INFO_WORDS):
        return "route_info"
    return None


# ==================================================
# 2. 값 뽑기 (노선 · 날짜 · 시간 · 배차간격)
# ==================================================

# 위에서부터 확인: "내일모레"를 "내일"보다 먼저 봐야 모레로 읽음
RELATIVE_DAYS = {"그저께": -2, "어제": -1, "내일모레": 2, "낼모레": 2, "모레": 2, "오늘": 0, "내일": 1}
PM_WORDS = ("오후", "저녁", "밤", "낮")
NIGHT_PM_FROM = 6          # "밤 10시"는 22시, "밤 1시"는 1시 (밤은 6시부터만 오후로 봄)
COMMUTE_HOURS = {"출근": 8, "퇴근": 18}     # "출근 시간"처럼 시각 없이 물을 때의 대표 시간대
COMMUTE_RANGES = {"출근": [7, 8, 9], "퇴근": [17, 18, 19]}   # 여러 시간대를 받는 기능(전 노선 최적화)에 쓰는 범위
_HOUR_RANGE_RE = re.compile(r"(오전|오후|아침|낮|저녁|밤|새벽)?\s*(\d{1,2})\s*시?\s*(?:~|-|부터|에서)\s*(오전|오후|아침|낮|저녁|밤|새벽)?\s*(\d{1,2})\s*시")

_ROUTE_RE = re.compile(r"(\d{1,4}(?:-\d)?)\s*번")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_KOR_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SLASH_DATE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_HOUR_RE = re.compile(r"(오전|오후|아침|낮|저녁|밤|새벽)?\s*(\d{1,2})\s*시(?!간)")
_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,2}):\d{2}(?!\d)")
# "3시 30분"의 분은 시각. 단 "8시 10분 간격으로"처럼 뒤에 간격을 뜻하는 말이 오면 배차간격
_MINUTE_AFTER_HOUR_RE = re.compile(r"(\d{1,2}\s*시)\s*\d{1,2}\s*분(?!\s*(?:간격|마다|으로))")
_INTERVAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*분")
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣\-]+")
ROUTE_PARTICLES = ("번은", "번이", "번의", "번도", "번을", "번", "은", "는", "이", "가", "을", "를", "의", "도")


def _is_pm(word: str | None, hour: int) -> bool:
    if word == "밤":
        return NIGHT_PM_FROM <= hour < 12
    return word in PM_WORDS and hour < 12


def _named_route(message: str, names: list[str]) -> str | None:
    """숫자만으로 되지 않은 노선 이름 찾기: "N13번", "새벽A160번은", "01A" (160번으로 잘못 읽지 않게 먼저 확인)"""
    special = {n for n in names if not n.isdigit()}
    if not special:
        return None
    for token in _WORD_RE.findall(message):
        candidates = [token] + [token[: -len(p)] for p in ROUTE_PARTICLES if token.endswith(p) and len(token) > len(p)]
        hit = next((c for c in candidates if c in special), None)
        if hit:
            return hit
    return None


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def extract(message: str, routes: list[str], today: dt.date, all_routes: list[str] | None = None) -> dict:
    """질문에서 찾은 값만 담아 돌려줍니다 (없는 값은 키 자체가 없음)

    routes: 예측하는 노선. all_routes: 길안내가 아는 전체 노선 이름 (N13·01A처럼 숫자가 아닌 이름을 찾는 데 씀)
    """
    slots: dict = {}

    # 노선: 숫자가 아닌 이름(N13) → "160번" → 아는 노선번호가 단독으로 나온 경우
    named = _named_route(message, all_routes or [])
    m = _ROUTE_RE.search(message)
    if named:
        slots["route"] = named
    elif m:
        slots["route"] = m.group(1)
    else:
        for route in sorted(routes, key=len, reverse=True):
            if re.search(rf"(?<![\d.\-/:]){re.escape(route)}(?![\d.\-/:]|\s*[월일시분명회대년%])", message):
                slots["route"] = route
                break

    # 날짜
    found = None
    if m := _ISO_DATE_RE.search(message):
        found = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif m := _KOR_DATE_RE.search(message):
        found = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
    elif m := _SLASH_DATE_RE.search(message):
        found = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
    else:
        for word, offset in RELATIVE_DAYS.items():
            if word in message:
                found = today + dt.timedelta(days=offset)
                break
    if found:
        slots["date"] = found.isoformat()

    # 시간: "오후 3시" · "15시" · "15:00" · "출근 시간"
    hour = None
    if m := _HOUR_RE.search(message):
        hour = int(m.group(2))
        if _is_pm(m.group(1), hour):
            hour += 12
        elif m.group(1) in ("오전", "밤", "새벽") and hour == 12:
            hour = 0
    elif m := _CLOCK_RE.search(message):
        hour = int(m.group(1))
    else:
        for word, value in COMMUTE_HOURS.items():
            if word in message:
                hour = value
                break
    if hour is not None and 0 <= hour <= 23:
        slots["hour"] = hour

    # 시간 범위: "6시부터 9시", "7~9시", "출근 시간" → 여러 시간대 (전 노선 최적화용)
    def clock(word, value):
        value = int(value)
        return value + 12 if _is_pm(word, value) else value
    if m := _HOUR_RANGE_RE.search(message):
        start, end = clock(m.group(1), m.group(2)), clock(m.group(3) or m.group(1), m.group(4))
        if 0 <= start <= end <= 23:
            slots["hours"] = list(range(start, end + 1))
    elif "hour" in slots and not _HOUR_RE.search(message) and not _CLOCK_RE.search(message):
        word = next((w for w in COMMUTE_RANGES if w in message), None)
        if word:
            slots["hours"] = COMMUTE_RANGES[word]

    # 배차간격(분): "3시 30분"의 분은 간격이 아니므로 먼저 지움
    m = _INTERVAL_RE.search(_MINUTE_AFTER_HOUR_RE.sub(r"\1", message))
    if m:
        slots["interval"] = float(m.group(1))

    return slots


# 길안내의 출발·도착: "정릉우체국앞에서 종로3가역 어떻게 가?" / "여기서 신동중학교 가는 법"
FROM_SUFFIXES = ("에서", "부터")
HERE_WORDS = ("여기", "위치", "이곳", "지금", "내가 있는", "근처")
HERE_FROM = ("여기서", "거기서", "이곳서")      # '서'만 붙는 출발 표현 ("강남경찰서"의 '서'는 출발 조사가 아님)
# 장소가 아닌 말: 시간·날짜(조사가 붙은 것 포함)와 기능어
_NOT_A_PLACE_RE = re.compile(
    r"^(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}|[0-9]+(?:시|월|일|분|번)?|오전|오후|아침|낮|저녁|밤|새벽|그저께|어제|오늘|내일|내일모레|낼모레|모레|지금)"
    r"(?:에|엔|에는|은|는|쯤|쯤에|까지|부터)?$")
NOT_PLACE_WORDS = {"가는", "가려는", "가는데", "가려고", "갈", "가", "타고", "타는", "버스", "버스로", "버스는", "노선", "어떤", "무슨",
                   "몇", "번", "좀", "빨리", "제일", "가장", "그럼", "그러면", "아니", "혹시"}
# 문장에서 길안내 표현이 시작되는 곳을 찾을 때는 '환승'을 빼고 봄 ("서울역버스환승센터"에서 자르지 않게)
GUIDE_CUT_WORDS = tuple(w for w in GUIDE_WORDS if w != "환승")


def extract_places(message: str) -> dict:
    """길안내 질문에서 출발·도착 이름을 뽑음. 조사는 떼지 않고 그대로 넘김 (정류장 이름 대조는 길안내 기능이 함)

    돌려주는 값: origin(출발 이름) · from_here(현재 위치에서 출발) · destination(도착 이름) 중 찾은 것만
    """
    positions = [message.find(w) for w in GUIDE_CUT_WORDS if w in message]
    head = message[:min(positions)] if positions else message
    out: dict = {}
    rest = []
    for token in head.split():
        suffix = next((s for s in FROM_SUFFIXES if token.endswith(s) and len(token) > len(s)), None)
        taken = "origin" in out or "from_here" in out
        if token in HERE_FROM and not taken:
            out["from_here"] = True
        elif suffix and not taken:
            name = token[: -len(suffix)]
            if _has(name, HERE_WORDS):
                out["from_here"] = True
            else:
                out["origin"] = name
        elif not _NOT_A_PLACE_RE.match(token) and token not in NOT_PLACE_WORDS and not _has(token, HERE_WORDS):
            rest.append(token)
    if rest:
        out["destination"] = rest[-1]
    return out


# ==================================================
# 3. 숫자 검증
# ==================================================

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers_in(text: str) -> list[tuple[float, int]]:
    """문장 속 숫자와 소수 자릿수: "1,234.5" → (1234.5, 1)"""
    out = []
    for token in _NUMBER_RE.findall(text):
        token = token.replace(",", "")
        out.append((float(token), len(token.split(".")[1]) if "." in token else 0))
    return out


def _collect_numbers(value) -> set[float]:
    """dict·list·문자열 안의 숫자를 모두 모음 (bool 제외)"""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return set() if isinstance(value, float) and math.isnan(value) else {float(value)}
    if isinstance(value, str):
        return {n for n, _ in _numbers_in(value)}
    if isinstance(value, dict):
        value = list(value.values())
    out: set[float] = set()
    for item in value:
        out |= _collect_numbers(item)
    return out


def verify_numbers(answer: str, *evidence) -> bool:
    """답변 속 숫자가 모두 근거 값에 있으면 True.

    표시 자릿수가 달라도 같은 값이면 통과합니다 (0.618을 0.61이나 0.62로 쓴 경우).
    """
    allowed = _collect_numbers(list(evidence))
    for shown, digits in _numbers_in(answer):
        scale = 10 ** digits
        if not any(
            abs(round(a, digits) - shown) < 1e-9 or abs(math.floor(a * scale + 1e-9) / scale - shown) < 1e-9
            for a in allowed
        ):
            return False
    return True


# --- LLM이 다시 쓴 문장의 숫자 확인 (verify_numbers보다 엄격함) ---
# verify_numbers는 "근거에 있는 숫자인가"만 봐서, 날짜의 20을 "20명"으로 쓰거나 0.618을 "1"로 쓰거나
# 예측과 작년 값을 바꿔 써도 통과함. 다시 쓴 문장은 문장 틀 답변과 "값 + 단위"가 같아야 하고, 순서와 설명도 같아야 함.
_UNIT_NUMBER_RE = re.compile(r"(\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(km|명|분|회|대|시(?!간)|번|개|곳|안|m|%|건|배)?")
_SENTENCE_END_RE = re.compile(r"[.!?\n]")
_CLAUSE_END_RE = re.compile(r"[.!?\n,]")
_SENTENCE_GAP_RE = re.compile(r"(?<=[.!?])\s+")       # 문장 사이. 소수점과 정류장 이름 속 마침표("가락시장.가락시장역")에서는 끊지 않음
_KOREAN_WORD_RE = re.compile(r"[가-힣]{2,}")
# 같은 뜻으로 바꿔 쓰는 설명은 한 가지 말로 봄 ("현재 6분"의 현재 = 문장 틀의 "지금")
LABEL_SYNONYMS = (("현재", "지금"), ("현행", "지금"), ("기존", "지금"), ("예측", "예상"), ("전망", "예상"),
                  ("지난해", "작년"), ("전년", "작년"), ("실측", "실제"), ("권장", "권고"))
# 어느 때의 값인지 알려 주는 말. 문장 틀에서 한 문장에만 나오면, 그 말이 붙은 숫자는 그 문장의 값이어야 함
FRAME_KEYS = ("지금", "예상", "작년", "실제", "그때")
# 숫자의 뜻을 바꾸는 말: 문장 틀에 없는데 다시 쓴 문장에 있으면 버림
_NUMBER_MODIFIER_RE = re.compile(r"[두세네몇]\s?배|절반|미만|이상|이하|초과")
_KOREAN_NUMBER_RE = re.compile(r"(?<![가-힣])(?:수|몇)?[일이삼사오육칠팔구십백천만]+\s?(?:명|회|퍼센트)(?![가-힣])")


def _unit_numbers(text: str) -> tuple[list[tuple[float, str, str, str]], set[str]]:
    """([(값, 단위, 앞 구절, 뒤 구절)], {날짜})

    앞 구절은 같은 문장에서 바로 앞 숫자 뒤부터 이 숫자 앞까지, 뒤 구절은 이 숫자 뒤부터 쉼표·문장 끝까지
    (다음 숫자가 먼저 나오면 그 말은 다음 숫자의 설명이므로 빈 문자열).
    날짜(2026-11-20, 11월 20일)는 숫자 목록에 넣지 않고 따로 모음 (날짜의 20이 "20명"의 근거가 되지 않게).
    연도를 적은 날짜는 연도까지("2026-11-20"), 월·일만 적은 날짜는 "11-20"으로 모음.
    """
    dates: set[str] = set()

    def take_date(match: re.Match) -> str:
        groups = match.groups()
        year = f"{int(groups[0]):04d}-" if len(groups) == 3 else ""
        dates.add(f"{year}{int(groups[-2]):02d}-{int(groups[-1]):02d}")
        return " " * len(match.group(0))       # 자리를 그대로 두어 앞 구절이 이어 붙지 않게
    text = _KOR_DATE_RE.sub(take_date, _ISO_DATE_RE.sub(take_date, text))
    matches = list(_UNIT_NUMBER_RE.finditer(text))
    out, last = [], 0
    for i, match in enumerate(matches):
        before = _SENTENCE_END_RE.split(text[last:match.start()])[-1]
        rest = text[match.end():matches[i + 1].start()] if i + 1 < len(matches) else text[match.end():]
        end = _CLAUSE_END_RE.search(rest)
        after = rest[:end.start()] if end else ("" if i + 1 < len(matches) else rest)
        out.append((float(match.group(1).replace(",", "")), match.group(2) or "", before, after))
        last = match.end()
    return out, dates


def _label_keys(phrase: str) -> list[str]:
    """구절에서 설명하는 말의 앞 두 글자: "작년 같은 요일은" → [작년, 같은, 요일] (조사가 달라도 같게 보려고 두 글자만)"""
    return [next((same for word_start, same in LABEL_SYNONYMS if word.startswith(word_start)), word[:2])
            for word in _KOREAN_WORD_RE.findall(phrase)]


def _frame_values(template: str) -> dict[str, set[tuple[float, str]]]:
    """문장 틀에서 한 문장에만 나오는 '때를 알리는 말' → 그 문장에 있는 (값, 단위)"""
    found: dict[str, list[set]] = {}
    for sentence in _SENTENCE_GAP_RE.split(template):
        numbers, _ = _unit_numbers(sentence)
        pairs = {(value, unit) for value, unit, _, _ in numbers}
        for key in set(_label_keys(sentence)) & set(FRAME_KEYS):
            found.setdefault(key, []).append(pairs)
    return {key: groups[0] for key, groups in found.items() if len(groups) == 1}


def same_numbers(answer: str, template: str) -> bool:
    """다시 쓴 답변의 숫자가 문장 틀 답변과 같은 뜻으로 쓰였으면 True.

    1) 값과 단위가 문장 틀에 있는 짝이어야 함 (3,133명 → "3,133명"만. "3,133회"나 "3,100명"은 안 됨)
    2) 같은 단위의 서로 다른 값은 문장 틀과 같은 순서 ("7.5분에서 7분으로"를 "7분에서 7.5분으로"로 바꾸면 안 됨)
    3) 숫자 앞의 설명이 다른 값의 설명이면 안 됨 ("작년 3,133명"인데 3,133은 예상 값인 경우)
    4) 때를 알리는 말(지금·예상·작년 등)이 붙은 숫자는 문장 틀에서 그 말이 있는 문장의 값이어야 함 ("현재 혼잡도는 0.73"의 0.73은 증차 뒤 예상)
    5) 문장 틀에 없는 오전·오후, "두 배·미만·이상", 한글로 쓴 숫자("삼천 명")가 있으면 안 됨
    뜻이 같아도 순서를 바꿔 쓴 답은 버려짐 → 문장 틀 답변을 그대로 씀 (틀린 답을 내보내는 것보다 안전).
    """
    wanted, wanted_dates = _unit_numbers(template)
    shown, shown_dates = _unit_numbers(answer)
    if not shown_dates <= wanted_dates | {date[-5:] for date in wanted_dates}:
        return False
    pairs = [(value, unit) for value, unit, _, _ in wanted]
    if any((value, unit) not in pairs for value, unit, _, _ in shown):
        return False
    # 5) 숫자의 뜻을 바꾸는 말
    squeeze = lambda found: {re.sub(r"\s", "", word) for word in found}
    half_days = {(m.group(1), int(m.group(2))) for m in _HOUR_RE.finditer(template) if m.group(1)}
    if any(m.group(1) and (m.group(1), int(m.group(2))) not in half_days for m in _HOUR_RE.finditer(answer)):
        return False
    for pattern in (_NUMBER_MODIFIER_RE, _KOREAN_NUMBER_RE):
        if not squeeze(pattern.findall(answer)) <= squeeze(pattern.findall(template)):
            return False
    # 4) 때를 알리는 말: 앞 구절에서는 숫자에 가장 가까운 말, 뒤 구절에서는 첫 말
    frames = _frame_values(template)
    for value, unit, before, after in shown:
        near = ([key for key in _label_keys(before) if key in frames][-1:]
                or [key for key in _label_keys(after) if key in frames][:1])
        if near and (value, unit) not in frames[near[0]]:
            return False

    # 단위별로: 문장 틀에서 값이 처음 나온 순서, 값마다 그 값에만 붙은 설명
    for unit in {unit for _, unit in pairs}:
        order: list[float] = []
        labels: dict[float, set[str]] = {}
        for value, u, before, _ in wanted:
            if u == unit:
                if value not in order:
                    order.append(value)
                labels.setdefault(value, set()).update(_label_keys(before))
        if len(order) < 2:
            continue
        own = {v: keys - set().union(*(labels[o] for o in order if o != v)) for v, keys in labels.items()}
        owner = {key: v for v, keys in own.items() for key in keys}
        seen: list[float] = []
        for value, u, before, _ in shown:
            if u != unit:
                continue
            if value not in seen:
                if any(order.index(value) < order.index(s) for s in seen):
                    return False                                   # 2) 순서가 바뀜
                seen.append(value)
            marks = [key for key in _label_keys(before) if key in owner]
            if marks and owner[marks[-1]] != value:
                return False                                       # 3) 가장 가까운 설명이 다른 값의 것
    return True


# --- LLM이 문장을 다듬다가 결론을 뒤집는 것을 막는 확인 (숫자는 같아도 "유지"를 "감차해도 됨"으로 바꾸는 경우) ---
# 권고마다 답변에 꼭 있어야 하는 말과, 있으면 안 되는 말 (다른 권고를 뜻하는 말)
ACTION_MUST = {
    "increase": ("단축", "증차", "늘리", "늘려"),
    "increase_limited": ("최소 배차간격", "별도 대책", "한계"),
    "decrease": ("감차", "줄이", "줄여"),
    "keep": ("유지",),
}
ACTION_FORBID = {
    "increase": ("유지", "감차", "줄이", "줄여"),
    "increase_limited": ("감차", "줄이", "줄여"),
    "decrease": ("증차", "늘리", "늘려", "단축", "유지"),
    "keep": ("증차", "감차", "단축", "늘리", "늘려", "줄이", "줄여"),
}
LEVELS = ("매우혼잡", "혼잡", "보통", "여유")
# 뒤에 오면 뜻이 뒤집히는 말: "감차하지 않아도", "유지보다는", "혼잡하지 않습니다", "여유가 없습니다", "증차는 보류"
# 같은 문장 끝까지 봄 (쉼표 뒤, 멀리 떨어진 부정도 잡음). "안"·"못"은 낱말일 때만 ("방안"·"잘못"은 아님)
_NEGATION_AFTER = (r"[^.!?\n]*?(?:않|없|말고|마세요|마십|보다|아니|아닙|아닌|어렵|불필요|보류|대신|반대"
                   r"|(?<![가-힣])안\s|(?<![가-힣])못|거리가\s?[멀멉]|풀렸|풀립|해소됐|해소되었)")
NOT_LEVEL_PHRASES = ("혼잡도", "혼잡 구간", "혼잡 단계")          # 단계를 말하는 것이 아닌 표현
_CROWDED_CLAIM_RE = re.compile(r"붐비(?!는 구간)|붐빕|붐벼|붐빌")   # "가장 붐비는 구간"은 문장 틀에도 있는 표현
CALM_CLAIMS = ("한산", "한가", "널널", "쾌적")
_OPTION_NO_RE = re.compile(r"(\d+)\s*안")


def _negated(text: str, words: tuple) -> bool:
    return any(re.search(re.escape(word) + _NEGATION_AFTER, text) for word in words)


def keeps_meaning(answer: str, facts: dict) -> bool:
    """다시 쓴 답변이 권고 방향·혼잡 단계·추천 경로를 그대로 전하면 True"""
    action = facts.get("action")
    if action:
        must = ACTION_MUST.get(action, ())
        if not _has(answer, must) or _negated(answer, must) or _has(answer, ACTION_FORBID.get(action, ())):
            return False
    level = facts.get("level")
    if level:
        rest = answer.replace("매우 혼잡", "매우혼잡")
        for phrase in NOT_LEVEL_PHRASES:
            rest = rest.replace(phrase, " ")
        if _negated(rest, (level,)):
            return False
        # 다른 단계는 적지 않아야 함 ("혼잡"은 "매우혼잡"에도 들어 있어 긴 말부터 지우며 확인)
        found = False
        for word in LEVELS:
            if word in rest:
                if word != level:
                    return False
                found = True
            rest = rest.replace(word, " ")
        # 단계를 적지 않았으면 붐빈다·한산하다는 말을 하지 않아야 하고, 적었으면 그 단계와 어긋나는 말을 하지 않아야 함
        crowded, calm = bool(_CROWDED_CLAIM_RE.search(rest)), _has(rest, CALM_CLAIMS)
        if not found and (crowded or calm):
            return False
        if (level == "여유" and crowded) or (level in ("혼잡", "매우혼잡") and calm):
            return False
    recommended = facts.get("recommended_no")
    if recommended:
        # 길안내: "추천"을 말한 문장의 "N안"은 추천 경로의 번호여야 함
        for sentence in _SENTENCE_END_RE.split(answer):
            if "추천" in sentence and any(int(n) != recommended for n in _OPTION_NO_RE.findall(sentence)):
                return False
    return True


# ==================================================
# 4. 기능 호출 → 답변에 쓸 값(facts) → 문장
# ==================================================

def _people(value) -> str:
    return f"{int(round(value)):,}명"


def _short(value) -> str:
    """7.0 → 7, 7.5 → 7.5"""
    return f"{value:g}"


def _floor2(value) -> str:
    """혼잡도는 화면(fmtCongestion)과 같게 소수 둘째 자리에서 버림 (0.798을 0.80으로 올리면 단계와 어긋남)"""
    return f"{math.floor(value * 100 + 1e-9) / 100:.2f}"


def _sources(items: list[dict] | None) -> list[dict]:
    keys = ("id", "document", "article", "page", "url", "score", "snippet")
    return [{k: s.get(k) for k in keys} for s in items or []]


def _predict(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("route", "date", "hour") if k in slots}
    result = adapter.predict(dict(args))
    route, date = result["route"], result["date"]

    if "hour" not in args:
        peak = max(result["hourly"], key=lambda h: h["pred"] or 0)
        s = result["summary"]
        facts = {"route": route, "date": date, "pred_total": s["pred_total"], "baseline_total": s["baseline_total"],
                 "actual_total": s["actual_total"], "peak_hour": peak["hour"], "peak_pred": peak["pred"]}
        answer = f"🚌 {route}번 {date} 하루 예상 승차는 {_people(s['pred_total'])}입니다."
        if s["baseline_total"] is not None:
            answer += f" 작년 같은 요일은 {_people(s['baseline_total'])}이었습니다."
        if s["actual_total"] is not None and not result["is_forecast"]:
            answer += f" 실제 승차는 {_people(s['actual_total'])}이었습니다."
        answer += f"\n⏰ 승차가 가장 많은 시간대는 {peak['hour']}시({_people(peak['pred'])})입니다."
        return args, answer, [], facts

    hour = args["hour"]
    row = next((h for h in result["hourly"] if h["hour"] == hour), None)
    if row is None or row["pred"] is None:
        raise HTTPException(422, f"{route}번 {hour}시는 운행 데이터가 없는 시간대입니다")
    congestion = result.get("congestion") or {}
    facts = {"route": route, "date": date, "hour": hour, "pred": row["pred"], "baseline": row["baseline"],
             "actual": row["actual"], "congestion": congestion.get("ratio"), "level": congestion.get("level")}
    answer = f"🚌 {route}번 {date} {hour}시 예상 승차는 {_people(row['pred'])}입니다."
    if row["baseline"] is not None:
        answer += f" 작년 같은 요일·시간대는 {_people(row['baseline'])}이었습니다."
    if row["actual"] is not None and not result["is_forecast"]:
        answer += f" 실제 승차는 {_people(row['actual'])}이었습니다."
    if congestion.get("ratio") is not None:
        answer += (f"\n{LEVEL_MARK.get(congestion['level'], '')} 가장 붐비는 구간의 혼잡도는"
                   f" {_floor2(congestion['ratio'])}({congestion['level']})입니다.")
    return args, answer, [], facts


def _advise(adapter: DomainAdapter, slots: dict, llm: str, embedding: str, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("route", "date", "hour")}
    # 규정 요약은 LLM 없이 근거 조항만 붙임 (빠름). 문장이 필요하면 규정을 따로 물으면 됨
    result = adapter.advise(dict(args), llm, embedding, False)
    rec, cur, con = result["recommendation"], result["current"], result["congestion"]
    peak = result["prediction"]["peak_segment"]
    facts = {
        "route": result["route"], "date": result["date"], "hour": result["hour"],
        "action": rec["action"], "label": rec["label"],
        "current_interval_min": cur["interval_min"], "current_trips_per_hour": cur["trips_per_hour"],
        "congestion": con["ratio"], "level": con["level"],
        "peak_from": peak["from_name"], "peak_to": peak["to_name"],
        "recommended_interval_min": rec["recommended_interval_min"],
        "expected_congestion": rec["expected_congestion"], "extra_buses": rec["extra_buses"],
    }
    answer = (
        f"{ACTION_MARK.get(rec['action'], '')} {facts['route']}번 {facts['date']} {facts['hour']}시 권고는 '{rec['label']}'입니다."
        f"\n🚌 지금은 약 {_short(cur['interval_min'])}분 간격(시간당 {_short(cur['trips_per_hour'])}회)이고,"
        f"\n{LEVEL_MARK.get(con['level'], '')} 가장 붐비는 구간({peak['from_name']} → {peak['to_name']})의"
        f" 혼잡도는 {_floor2(con['ratio'])}({con['level']})입니다."
    )
    if rec["action"] == "keep":
        answer += "\n지금 배차간격을 유지해도 됩니다."
    else:
        answer += (f"\n👉 권고 배차간격은 {_short(rec['recommended_interval_min'])}분이고,"
                   f" 그때 예상 혼잡도는 {_floor2(rec['expected_congestion'])}입니다.")
        if rec["extra_buses"] > 0:
            answer += f" 버스가 약 {rec['extra_buses']}대 더 필요합니다."
    answer += "\n📝 최종 결정은 담당자 검토가 필요합니다."
    return args, answer, _sources(result["evidence"].get("sources")), facts


def _ask(adapter: DomainAdapter, message: str, llm: str, embedding: str, use_llm: bool, role: str = "citizen", **_) -> tuple[dict, str, list[dict], dict]:
    # role(문서 열람 역할)에 따라 볼 수 없는 등급의 문서는 검색에서 빠짐 (F-37)
    result = adapter.knowledge_base.ask(message, llm, embedding, RAG_TOP_K, use_llm, role=role)
    sources = _sources(result.get("sources"))
    if not result["found"]:
        return {"question": message}, NOT_FOUND_ANSWER, [], {}
    if result.get("answer"):
        answer = result["answer"]
    else:   # use_llm=False: 근거 조항만
        lines = "\n".join(f"[{s['id']}] {s['document']} · {s['article']}" for s in sources)
        answer = f"📚 관련 조항 {len(sources)}건을 찾았습니다. 아래 근거를 확인하세요.\n{lines}"
    return {"question": message}, answer, sources, {"sources": sources, "llm_answer": bool(result.get("answer"))}


def _guide(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("origin", "destination", "lat", "lng", "date", "hour") if slots.get(k) is not None}
    result = adapter.guide(dict(args))
    origin, dest, options = result["origin"], result["destination"], result["options"]
    history_note = (f"↩️ 출발지는 앞에서 말씀하신 {origin['stop']}(으)로 했습니다. 다른 곳이면 '○○에서' 또는 '여기서'라고 알려 주세요."
                    if slots.get("origin_from_history") else None)

    lines = [f"🧭 {origin['stop']} → {dest['stop']} ({result['date']} {result['hour']}시 기준)"]
    facts = {"date": result["date"], "hour": result["hour"], "origin": origin["stop"], "destination": dest["stop"],
             "numbers": list(range(1, len(options) + 1)), "options": []}
    if history_note:
        lines.append(history_note)
    if result.get("requested_date"):
        facts["requested_date"] = result["requested_date"]
        lines.append(f"📅 {result['requested_date']}은 예측 범위 밖이라, 같은 요일인 {result['date']}의 혼잡도·배차간격으로 안내합니다.")
    if origin["far"]:
        km = round(origin["walk_m"] / 1000, 1)
        facts["origin_km"] = km
        lines.append(f"⚠️ 현재 위치에서 가장 가까운 정류장({origin['stop']})까지 약 {km}km입니다. 그 정류장에서 타는 경로로 안내합니다.")
    elif origin["walk_m"]:
        facts["origin_walk_m"] = origin["walk_m"]
        lines.append(f"📍 가장 가까운 정류장은 {origin['stop']}(약 {origin['walk_m']}m)입니다.")

    same_name_notes = [
        f"ℹ️ '{asked or place['stop']}'(으)로 찾은 정류장이 멀리 떨어진 여러 곳에 있어, {other}에서 가까운 곳부터 찾아 {place['stop']}(으)로 안내합니다."
        for place, asked, other in ((origin, slots.get("origin"), "도착지"), (dest, slots.get("destination"), "출발지"))
        if place.get("ambiguous")]
    lines += same_name_notes
    walk = result.get("walk_only")
    if walk:
        facts["walk_only"] = walk
        start = "현재 위치" if origin.get("label") == "현재 위치" else origin["stop"]
        # 어느 정류장으로 잡았는지 알리는 줄은 남김: 다른 곳에 있는 사용자가 "바로 근처"를 그대로 믿지 않게 (이슈 #22)
        lines = [f"🧭 {start} → {dest['stop']}", *([history_note] if history_note else []), *same_name_notes,
                 f"🚶 바로 근처입니다. 걸어서 약 {walk['walk_m']}m(약 {walk['walk_min']}분)라 버스를 탈 필요가 없습니다."
                 if walk["walk_m"] else "📍 이미 도착 정류장에 있습니다. 버스를 탈 필요가 없습니다."]
        return args, "\n".join(lines), [], {**facts, "data": result}
    if not options:
        lines.append("😢 두 정류장을 잇는 경로를 찾지 못했습니다 (직행·환승 한 번 기준).")
        return args, "\n".join(lines), [], {**facts, "data": result}

    for number, option in enumerate(options, start=1):
        kind = "직행" if option["kind"] == "direct" else "환승"
        tag = " · 가장 빠름" if option["fastest"] else ""
        tag += " · 덜 붐빔" if option["least_crowded"] and not option["fastest"] else ""
        lines.append(f"{'🚌' if option['kind'] == 'direct' else '🔁'} {number}안 {kind} · 약 {option['total_min']}분{tag}")
        for leg in option["legs"]:
            crowd = (f", 혼잡도 {_floor2(leg['congestion'])}({leg['level']}) {LEVEL_MARK.get(leg['level'], '')}"
                     if leg["congestion"] is not None else ", 혼잡도 예측 없음")
            walk = f" (걸어서 약 {leg['walk_m']}m)" if leg["walk_m"] else ""
            exit_walk = f" (내려서 약 {leg['exit_walk_m']}m 걷기)" if leg.get("exit_walk_m") else ""
            distance = f"({_short(leg['distance_km'])}km)" if leg.get("distance_km") else ""
            start_hour = leg.get("start_hour")   # 키 누락·None(예측 없는 노선) 모두 안전
            basis = (" " + GUIDE_START_HOUR_NOTE.format(hour=start_hour)
                     if start_hour is not None and start_hour != result["hour"] else "")
            lines.append(
                f"   {leg['route']}번: {leg['board']['name']}{walk} → {leg['alight']['name']}{exit_walk}"
                f" · 정류장 {leg['stops']}개{distance} · 약 {leg['ride_min']}분 · 배차간격 약 {_short(leg['interval_min'])}분{crowd}{basis}"
            )
        facts["options"].append({
            "total_min": option["total_min"],
            "legs": [{k: leg[k] for k in ("route", "stops", "walk_m", "exit_walk_m", "ride_min", "distance_km", "interval_min", "congestion", "start_hour")
                      if k in leg}
                     | {"board": leg["board"]["name"], "alight": leg["alight"]["name"]} for leg in option["legs"]],
        })

    rec = result["recommendation"]
    if rec:
        number = rec["index"] + 1
        facts["recommended_no"] = number
        if rec["reason"] == "less_crowded":
            facts["slower_by_min"] = rec["slower_by_min"]
            lines.append(f"👉 추천은 {number}안입니다. 약 {rec['slower_by_min']}분 더 걸리지만 덜 붐빕니다.")
        else:
            lines.append(f"👉 추천은 {number}안입니다. 가장 빨리 도착합니다.")
    lines.append("ℹ️ 버스만 안내합니다(지하철 제외). 시간은 버스 시간표 기준이라 실제 교통 상황에 따라 달라질 수 있습니다.")
    return args, "\n".join(lines), [], {**facts, "data": result}


def _simulate(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {k: slots[k] for k in ("route", "date", "hour", "interval")}
    r = adapter.simulate(dict(args))
    cur, sim, fleet, peak = r["current"], r["simulated"], r["fleet"], r["peak_segment"]
    facts = {"route": r["route"], "date": r["date"], "hour": r["hour"], "current": cur, "simulated": sim,
             "fleet": fleet, "peak_from": peak["from_name"], "level": sim["level"]}
    lines = [
        f"🔧 {r['route']}번 {r['date']} {r['hour']}시에 배차간격을 약 {_short(cur['interval'])}분에서 {_short(sim['interval'])}분으로 바꾸면",
        f"{LEVEL_MARK.get(sim['level'], '')} 가장 붐비는 구간({peak['from_name']} 부근)의 혼잡도가 "
        f"{_floor2(cur['max_congestion'])} → {_floor2(sim['max_congestion'])}({sim['level']})로 바뀝니다.",
        f"🚌 시간당 운행은 {_short(cur['trips_per_hour'])}회 → {_short(sim['trips_per_hour'])}회, "
        f"필요한 버스는 {fleet['buses_needed_before']}대 → {fleet['buses_needed']}대입니다."
        + (f" (인가 {fleet['authorized_buses']}대)" if fleet.get("authorized_buses") is not None else ""),
    ]
    if fleet.get("over"):
        lines.append("⚠️ 필요한 버스가 인가대수를 넘습니다.")
    lines.append("📝 승객 수는 그대로라고 가정한 계산입니다. 최종 결정은 담당자 검토가 필요합니다.")
    return args, "\n".join(lines), [], facts


def _optimize(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {"date": slots["date"], "hours": slots["hours"]}
    r = adapter.optimize(dict(args))
    kpi, plan = r["kpi"], r["plan"]
    changed = sorted((p for p in plan if p["change"] != 0), key=lambda p: -abs(p["change"]))[:OPTIMIZE_TOP_CHANGES]
    facts = {"date": r["date"], "hours": args["hours"], "kpi": kpi, "top": changed, "binding": r["binding"]}
    span = f"{args['hours'][0]}시" if len(args["hours"]) == 1 else f"{args['hours'][0]}~{args['hours'][-1]}시"
    lines = [
        f"🧮 {r['date']} {span} 전 노선 배차 최적화 결과입니다.",
        f"🔴 붐비는 노선·시간대는 {kpi['overcrowded_before']}곳 → {kpi['overcrowded_after']}곳으로 줄어듭니다.",
        f"🚌 운행횟수는 모두 {kpi['total_trips_before']}회 → {kpi['total_trips_after']}회 "
        f"(늘리는 곳 {kpi['increased']}곳, 줄이는 곳 {kpi['decreased']}곳)입니다.",
    ]
    for p in changed:
        lines.append(f"   {ACTION_MARK.get(p['action'], '')} {p['route']}번 {p['hour']}시: {p['current_trips']}회 → {p['recommended_trips']}회")
    if r["binding"]:
        lines.append("⚠️ 버스가 모자라 더 늘리지 못한 곳: " + ", ".join(r["binding"][:OPTIMIZE_TOP_CHANGES]))
    if r.get("cut_notice"):
        lines.append(f"ℹ️ {r['cut_notice']}")
    lines.append("📝 자세한 표는 '배차 계획' 화면에서 볼 수 있습니다. 최종 결정은 담당자 검토가 필요합니다.")
    return args, "\n".join(lines), [], facts


def _route_info(adapter: DomainAdapter, slots: dict, **_) -> tuple[dict, str, list[dict], dict]:
    args = {"route": slots["route"]}
    r = adapter.route_info(dict(args))
    kind = " · ".join(x for x in (r.get("route_type"), r.get("company")) if x)
    lines = [f"🚌 {r['route']}번" + (f" ({kind})" if kind else "")]
    if r.get("start") and r.get("end"):
        length = f" · 약 {_short(r['distance_km'])}km" if r.get("distance_km") else ""
        lines.append(f"📍 {r['start']} ↔ {r['end']} · 정류장 {r['stops_count']}개{length}")
    if r.get("first_bus") and r.get("last_bus"):
        lines.append(f"⏰ 첫차 {r['first_bus']} · 막차 {r['last_bus']}")
    if r.get("interval_weekday") is not None:
        line = f"⏱️ 평일 배차간격 약 {_short(r['interval_weekday'])}분"
        if r.get("min_interval") is not None and r.get("max_interval") is not None:
            line += f" (시간대에 따라 {_short(r['min_interval'])}~{_short(r['max_interval'])}분)"
        lines.append(line)
    extra = []
    if r.get("run_minutes") is not None:
        extra.append(f"한 바퀴 약 {_short(r['run_minutes'])}분")
    if r.get("authorized_buses") is not None:
        extra.append(f"인가대수 {_short(r['authorized_buses'])}대" + (" (추정)" if r.get("authorized_estimated") else ""))
    if extra:
        lines.append("🚏 " + " · ".join(extra))
    if r.get("key_stops"):
        lines.append("🗺️ 주요 정류장: " + " → ".join(r["key_stops"]))
    if r.get("predicted") is False:
        lines.append("ℹ️ 이 노선은 승객 수·혼잡도 예측을 하지 않습니다. 기본 정보만 안내합니다.")
    return args, "\n".join(lines), [], dict(r)


HANDLERS = {"predict": _predict, "advise": _advise, "ask": _ask, "guide": _guide, "route_info": _route_info,
            "simulate": _simulate, "optimize": _optimize}


def _rewrite(llm: str, message: str, template: str, facts: dict) -> str | None:
    """문장 틀 답변을 LLM이 자연스럽게 다시 씀. 숫자나 결론이 문장 틀 답변과 달라지면 None (문장 틀 답변을 그대로 씀)"""
    shown = {k: v for k, v in facts.items() if k != "data"}
    user = f"[기능 결과]{chr(10)}{json.dumps(shown, ensure_ascii=False)}{chr(10)}{chr(10)}[기본 답변]{chr(10)}{template}{chr(10)}{chr(10)}[질문]{chr(10)}{message}"
    try:
        answer = llm_module.generate(llm, CHAT_SYSTEM_MESSAGE, user).strip()
    except LLMConfigError:
        raise
    except Exception as error:      # 모델 로드 실패·메모리 부족 등: 문장 틀 답변으로 계속
        logger.warning("챗봇 LLM 문장 작성 실패, 문장 틀 답변 사용: %s", error)
        return None
    # "�": 모델이 글자를 깨뜨려 낸 자리 (예: "가장 � boom하는 구간")
    ok = bool(answer) and "�" not in answer and same_numbers(answer, template) and keeps_meaning(answer, shown)
    return answer if ok else None


# ==================================================
# 5. 실행
# ==================================================

def _clarify(intent: str, slots: dict, routes: list[str]) -> str | None:
    """꼭 있어야 하는 값이 빠졌으면 되묻는 문장, 다 있으면 None"""
    ask_for = {
        # 노선 정보는 전체 노선을 알고, 예측·권고·시뮬레이션은 예측하는 노선만 가능
        "route": "노선 번호(예: 143번, N13번)" if intent == "route_info" or not routes else f"노선(가능: {', '.join(routes)})",
        "date": "날짜(예: 내일, 11월 20일)",
        "hour": "시간(예: 오후 3시)",
        "interval": "바꿔 볼 배차간격(예: 5분)",
        "hours": "시간대(예: 8시, 7시부터 9시)",
    }
    missing = [ask_for[k] for k in REQUIRED.get(intent, ()) if k not in slots]
    return f"🙋 다음 값을 알려 주세요: {' · '.join(missing)}" if missing else None


def run(adapter: DomainAdapter, message: str, history: list[dict] | None = None,
        llm: str = "qwen", embedding: str = "bge", use_llm: bool = False,
        location: dict | None = None, now: dt.datetime | None = None, role: str = "citizen") -> dict:
    """location: 화면에서 보낸 현재 위치 {"lat", "lng"} (길안내에 사용). now: 테스트에서 '지금'을 고정할 때.
    role: 문서 열람 역할 (규정 검색에 전달)"""
    t0 = time.time()
    now = now or dt.datetime.now()
    today = now.date()
    routes = adapter.targets()
    names = adapter.route_names()
    message = message.strip()

    # 이전 질문의 값과 의도를 이어 씀: "그럼 4시는?" → 앞에서 말한 노선·날짜 유지
    slots: dict = {}
    places: dict = {}
    guide_time: dict = {}        # 길안내 질문에서 말한 날짜·시간 (다른 기능의 날짜·시간과 섞지 않음)
    previous_intent = None
    for turn in history or []:
        if turn.get("role") != "user":
            continue
        past = extract(turn["content"], routes, today, names)
        past_intent = classify(turn["content"], "route" in past)
        if past_intent == "guide":
            places.update(extract_places(turn["content"]))
            guide_time.update({k: past[k] for k in ("date", "hour") if k in past})
            past = {}                                 # 길안내 문장 속 숫자·시간은 다른 기능으로 이어 쓰지 않음
        elif previous_intent == "guide" and past_intent is None and not past and _guide_pending(places, location):
            _fill_place(places, turn["content"])      # 되묻기에 정류장 이름만 답한 경우
        elif past_intent is None and not (past and _is_followup(turn["content"])):
            continue                                  # 버스와 무관한 말은 이어 쓰지 않음
        slots.update(past)
        previous_intent = past_intent or previous_intent
    current = extract(message, routes, today, names)
    slots.update(current)

    intent = classify(message, "route" in current)
    followup = _is_followup(message)
    said: dict = {}          # 이번 질문에서 직접 말한 출발·도착
    if intent == "guide":
        said = extract_places(message)
        places.update(said)
    elif intent is None and previous_intent == "guide" and not current and _guide_pending(places, location) \
            and _looks_like_place(message):
        intent = "guide"                              # 길안내 되묻기에 정류장 이름으로 답한 경우 ("고속버스터미널")
        _fill_place(places, message)
    elif intent is None and current and previous_intent and followup:
        intent = previous_intent                      # 값만 말한 경우: "그럼 4시는?", "160번은?"
    if intent is None:
        intent = "ask" if _has(message, DOMAIN_WORDS) else "out_of_scope"
    elif intent in ("predict", "advise"):
        related = _has(message, DOMAIN_WORDS) or "hour" in current or followup
        if "route" not in current and not related:
            intent = "out_of_scope"                   # "점심 메뉴 추천해줘": 앞에서 말한 노선을 붙여 답하지 않음
        elif "route" not in slots:
            if not _has(message, DOMAIN_WORDS):
                intent = "out_of_scope"
            elif "date" not in current and "hour" not in current:
                intent = "ask"                        # 노선·날짜·시간이 없는 일반 질문은 규정에서 찾음

    def done(intent, answer, tools=(), sources=(), verified=None, used_llm=None, rejected=False, data=None):
        return {
            "answer": answer, "intent": intent, "slots": slots, "tools": list(tools), "sources": list(sources),
            "numbers_verified": verified, "llm": used_llm, "llm_rejected": rejected, "data": data,
            "elapsed_sec": round(time.time() - t0, 2), "needs_review": True,
        }

    if intent == "out_of_scope":
        return done(intent, OUT_OF_SCOPE_ANSWER)
    if intent == "optimize" and "hours" not in slots and "hour" in slots:
        slots["hours"] = [slots["hour"]]              # 시간대 하나만 말한 경우

    if intent == "guide":
        # 길안내는 '지금'이 기본: 길안내 질문에서 날짜·시간을 말하지 않았으면 오늘 이 시각
        # (앞에서 다른 기능에 물은 "내일 8시"를 가져오지 않음)
        guide_time.update({k: current[k] for k in ("date", "hour") if k in current})
        slots = {"date": guide_time.get("date", today.isoformat()), "hour": guide_time.get("hour", now.hour)}
        if places.get("destination"):
            slots["destination"] = places["destination"]
        # 출발지 우선순위: ① 이번 질문에서 말한 정류장 ② 이번 질문의 "여기서·내 위치에서" ③ 켜 둔 현재 위치
        #                 ④ 앞 대화에서 말한 정류장 (이때는 답변에 그 사실을 적음)
        wants_here = bool(said.get("from_here")) or (not said.get("origin") and places.get("from_here") and not places.get("origin"))
        if said.get("origin"):
            slots["origin"] = said["origin"]
        elif wants_here or location:
            if location:
                slots["lat"], slots["lng"] = location["lat"], location["lng"]
            else:
                places["from_here"] = True
                places.pop("origin", None)
        elif places.get("origin"):
            slots["origin"] = places["origin"]
            slots["origin_from_history"] = True
        if "destination" not in slots:
            return done("clarify", "🙋 어디로 가시나요? 도착 정류장 이름을 알려 주세요. (예: 종로3가역 어떻게 가?)")
        if "origin" not in slots and "lat" not in slots:
            if places.get("from_here"):
                slots["from_here"] = True     # 화면이 이 값을 보고 현재 위치를 얻어 다시 물음
                return done("clarify", "📍 현재 위치가 필요합니다. '현재 위치 사용'을 켜거나 출발 정류장 이름을 알려 주세요.")
            return done("clarify", "🙋 어디에서 출발하시나요? 출발 정류장 이름을 알려 주시거나 '현재 위치 사용'을 켜 주세요.")
    elif question := _clarify(intent, slots, routes):
        return done("clarify", question)
    try:
        args, answer, sources, facts = HANDLERS[intent](
            adapter, slots=slots, message=message, llm=llm, embedding=embedding, use_llm=use_llm, role=role)
    except (HTTPException, InvalidRequest, ValidationError) as error:
        # 없는 노선·범위를 벗어난 날짜 등: 기능이 알려 준 이유를 그대로 답함
        keys = REQUIRED.get(intent) or ("origin", "destination", "lat", "lng", "date", "hour")
        args = {k: slots[k] for k in keys if k in slots} or {"question": message}
        if isinstance(error, HTTPException):
            reason = error.detail
        elif isinstance(error, ValidationError):          # 받을 수 없는 값 (예: 배차간격 0분)
            first = error.errors(include_url=False)[0]
            reason = f"입력값을 확인해 주세요 ({'.'.join(str(x) for x in first['loc'])}: {first['msg']})"
        else:
            reason = str(error)                           # 범위 밖의 배차간격 등
        return done(intent, f"⚠️ {reason}", tools=[{"name": intent, "args": args, "ok": False}])

    tools = [{"name": intent, "args": args, "ok": True}]
    if intent == "ask":
        # 규정 답변의 숫자는 근거 조항 원문에서 오므로 여기서 대조하지 않음 (출처로 확인)
        used_llm = llm if facts.get("llm_answer") else None
        return done(intent, answer, tools, sources, verified=None, used_llm=used_llm)

    data = facts.get("data")          # 길안내 결과 전체 (화면이 지도에 그림)
    evidence = {k: v for k, v in facts.items() if k != "data"}
    # 문장 틀 답변이 기본. use_llm이면 LLM이 다시 쓰되, 숫자가 달라지면 버리고 문장 틀 답변을 씀
    if use_llm and intent in ("predict", "advise"):   # 길안내·노선 정보는 줄 단위 목록이라 다시 쓰지 않음
        rewritten = _rewrite(llm, message, answer, evidence)
        if rewritten:
            return done(intent, rewritten, tools, sources, verified=True, used_llm=llm, data=data)
        return done(intent, answer, tools, sources, verified=verify_numbers(answer, evidence, slots), rejected=True, data=data)
    return done(intent, answer, tools, sources, verified=verify_numbers(answer, evidence, slots), data=data)


_FOLLOWUP_SLOT_RES = (_ISO_DATE_RE, _KOR_DATE_RE, _SLASH_DATE_RE, _HOUR_RANGE_RE, _HOUR_RE, _CLOCK_RE, _INTERVAL_RE,
                      re.compile(r"[0-9A-Za-z가-힣\-]*\d[0-9A-Za-z\-]*\s*번"))
FOLLOWUP_FILLERS = ("그럼", "그러면", "그리고", "그때", "그날", "어때", "어떄", "어떤가", "출근", "퇴근", "시간", "오전", "오후",
                    "아침", "저녁", "새벽", "밤", "낮") + tuple(RELATIVE_DAYS)
FOLLOWUP_MAX_LEFT = 6      # 값과 군말을 지운 뒤 남은 말이 이보다 길면 새 주제의 문장으로 봄


def _is_followup(message: str) -> bool:
    """값만 바꿔 다시 묻는 짧은 말인지: "그럼 4시는?", "160번은?", "내일은 어때?" (새 주제의 문장은 False)"""
    text = message
    for pattern in _FOLLOWUP_SLOT_RES:
        text = pattern.sub(" ", text)
    for word in sorted(FOLLOWUP_FILLERS, key=len, reverse=True):
        text = text.replace(word, " ")
    left = re.sub(r"[\s?!.,~은는이가을를도요에엔면]", "", text)
    # 아무것도 안 남거나("그럼 4시는?"), 남은 말이 예측·권고를 묻는 짧은 말일 때만 ("그럼 4시는 붐벼?").
    # "오늘 날씨 어때?"처럼 다른 주제의 말이 남으면 이어 묻기가 아님
    return not left or (len(left) <= FOLLOWUP_MAX_LEFT and _has(left, PREDICT_WORDS + ADVISE_WORDS))


def _guide_pending(places: dict, location: dict | None) -> bool:
    """길안내가 출발이나 도착을 되물은 상태인지 (둘 다 정해졌으면 False)"""
    has_origin = bool(places.get("origin") or places.get("from_here") or location)
    return "destination" not in places or not has_origin


def _looks_like_place(text: str) -> bool:
    """되묻기에 대한 답으로 볼 만한 짧은 말 (정류장 이름)"""
    return len(text) <= 30 and len(text.split()) <= 3


def _fill_place(places: dict, text: str) -> None:
    """정류장 이름만 답한 문장을 빠진 자리(도착 → 출발 순)에 넣음"""
    name = text.strip().rstrip("?.!요")
    if not name:
        return
    if "destination" not in places:
        places["destination"] = name
    elif "origin" not in places and "from_here" not in places:
        places["origin"] = name
